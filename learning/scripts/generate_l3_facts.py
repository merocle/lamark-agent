#!/usr/bin/env python3
"""
Generate or augment the L3 facts dataset using OpenAI's GPT-5.4 models.

Two subcommands:

  augment   Re-read each fact in learning/data/lamark_facts.jsonl and ask
            the model for additional paraphrases + neighborhood probes.
            The curated `edit` payload (prompt / subject / target_new) is
            NEVER modified — that's the high-stakes part of the dataset
            and stays under human control.

  propose   Read CLAUDE.md, SPEC.md, and docs/decisions/*.md and ask the
            model to PROPOSE new facts. Output goes to
            learning/data/lamark_facts_proposed.jsonl. Nothing is added to
            the canonical list — review proposals manually and copy
            accepted ones over.

Both modes route output through facts.load_facts to enforce invariants
(notably: subject must appear verbatim in prompt). Anything that fails
validation is dropped with a warning, never silently merged.

Usage:
    OPENAI_API_KEY=... python generate_l3_facts.py augment
    OPENAI_API_KEY=... python generate_l3_facts.py augment --model gpt-5.4-mini --per-fact 8
    OPENAI_API_KEY=... python generate_l3_facts.py augment --only lamark-vs-lamarck
    OPENAI_API_KEY=... python generate_l3_facts.py propose --count 8
    python generate_l3_facts.py augment --dry-run    # no API calls
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Force UTF-8 stdout — project docs contain em-dashes etc. that Windows'
# default cp1252 console encoding cannot render, and the generator prints
# document chunks in --dry-run mode.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure src/ is importable when run directly from the repo.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT / "learning" / "src"))

from lamark.knowledge_edit.facts import (  # noqa: E402
    Fact,
    FactsLoadError,
    NeighborhoodProbe,
    load_facts,
)

FACTS_PATH = REPO_ROOT / "learning" / "data" / "lamark_facts.jsonl"
PROPOSED_PATH = REPO_ROOT / "learning" / "data" / "lamark_facts_proposed.jsonl"
DOC_PATHS = [
    REPO_ROOT / "CLAUDE.md",
    REPO_ROOT / "SPEC.md",
    REPO_ROOT / "docs" / "decisions" / "0010-lora-cannot-replace-knowledge-edits.md",
    REPO_ROOT / "docs" / "decisions" / "0011-l1-default-chat-template.md",
    REPO_ROOT / "docs" / "decisions" / "0012-l3-memit-editor-on-moe.md",
]


# ── CLI ─────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    aug = sub.add_parser("augment", help="add paraphrases + neighborhood to existing facts")
    aug.add_argument("--facts", type=Path, default=FACTS_PATH)
    aug.add_argument("--out", type=Path, default=FACTS_PATH,
                     help="where to write the augmented dataset (default: in place)")
    aug.add_argument("--per-fact", type=int, default=6,
                     help="paraphrases AND neighborhood probes to add per fact (default: 6)")
    aug.add_argument("--only", type=str, default="",
                     help="comma-separated fact ids; if set, only these are augmented")
    aug.add_argument("--model", default="gpt-5.4-mini")
    aug.add_argument("--dry-run", action="store_true",
                     help="print the first prompt and exit without API calls")

    prop = sub.add_parser("propose", help="propose new facts from project docs")
    prop.add_argument("--facts", type=Path, default=FACTS_PATH,
                      help="existing facts (used to avoid duplicating known IDs)")
    prop.add_argument("--out", type=Path, default=PROPOSED_PATH)
    prop.add_argument("--count", type=int, default=8,
                      help="how many new facts to ask for (default: 8)")
    prop.add_argument("--model", default="gpt-5.4-nano",
                      help="proposals are speculative — nano is the cheap default")
    prop.add_argument("--dry-run", action="store_true",
                      help="print the prompt and exit without API calls")

    return p.parse_args()


# ── OpenAI plumbing ─────────────────────────────────────────────────────────


def _client():
    """Lazy-import the OpenAI client so --dry-run works without the SDK."""
    if "OPENAI_API_KEY" not in os.environ:
        raise SystemExit("OPENAI_API_KEY env var is not set")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit(
            "openai package not installed; pip install openai"
        ) from exc
    return OpenAI()


def _chat_json(client, *, model: str, system: str, user: str) -> dict[str, Any]:
    """Call the chat API expecting a JSON object back; raise on shape errors."""
    rsp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
    )
    content = rsp.choices[0].message.content or ""
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"model returned invalid JSON: {content[:200]!r}") from exc


# ── augment ─────────────────────────────────────────────────────────────────


AUGMENT_SYSTEM = """\
You produce JSON expansions for a knowledge-editing dataset. The dataset
is used by ROME/MEMIT to surgically rewrite facts in a large language
model. For each input fact you will produce additional paraphrases and
neighborhood probes; you DO NOT modify the underlying edit.

Definitions:
- A paraphrase is a different way of asking the same question the edit
  prompt asks. It should naturally elicit the same target_new.
- A neighborhood probe is an UNRELATED question whose answer the edit
  must not break. Each probe has an `expected_substring` that, if still
  present in the model's reply after the edit, indicates no collateral
  damage.

Hard rules:
- Output a single JSON object with keys "paraphrases" and "neighborhood".
- "paraphrases" is a list of strings.
- "neighborhood" is a list of {"prompt": str, "expected_substring": str}.
- No commentary. No markdown. Pure JSON.
- Do not include the exact strings already provided as input — those
  are listed under "existing_paraphrases" / "existing_neighborhood" to
  help you diversify.
"""


def _augment_one(client, *, model: str, fact: Fact, per_fact: int, dry_run: bool) -> tuple[list[str], list[NeighborhoodProbe]]:
    payload = {
        "id": fact.id,
        "edit_prompt": fact.edit.prompt,
        "subject": fact.edit.subject,
        "target_new": fact.edit.target_new,
        "existing_paraphrases": list(fact.paraphrases),
        "existing_neighborhood": [{"prompt": p.prompt, "expected_substring": p.expected_substring} for p in fact.neighborhood],
        "requested_paraphrases": per_fact,
        "requested_neighborhood_probes": per_fact,
    }
    user = json.dumps(payload, indent=2)
    if dry_run:
        print(f"--- [{fact.id}] system + user prompt:")
        print(AUGMENT_SYSTEM)
        print("--- user:")
        print(user)
        print()
        return [], []

    raw = _chat_json(client, model=model, system=AUGMENT_SYSTEM, user=user)
    paraphrases = [p for p in raw.get("paraphrases", []) if isinstance(p, str) and p.strip()]
    neighborhood: list[NeighborhoodProbe] = []
    for item in raw.get("neighborhood", []):
        if not isinstance(item, dict):
            continue
        prompt = item.get("prompt")
        expected = item.get("expected_substring")
        if isinstance(prompt, str) and isinstance(expected, str) and prompt.strip() and expected.strip():
            neighborhood.append(NeighborhoodProbe(prompt=prompt.strip(), expected_substring=expected.strip()))

    # Deduplicate against existing entries (string-equal).
    existing_para = set(fact.paraphrases)
    paraphrases = [p for p in paraphrases if p not in existing_para]
    existing_nb = {(p.prompt, p.expected_substring) for p in fact.neighborhood}
    neighborhood = [n for n in neighborhood if (n.prompt, n.expected_substring) not in existing_nb]
    return paraphrases, neighborhood


def _filter_facts(facts: list[Fact], only: str) -> list[Fact]:
    if not only.strip():
        return facts
    wanted = {x.strip() for x in only.split(",") if x.strip()}
    chosen = [f for f in facts if f.id in wanted]
    missing = wanted - {f.id for f in chosen}
    if missing:
        raise SystemExit(f"unknown fact ids: {sorted(missing)}")
    return chosen


def cmd_augment(args: argparse.Namespace) -> int:
    facts = load_facts(args.facts)
    selected = _filter_facts(facts, args.only)
    print(f"[augment] facts to expand: {len(selected)}/{len(facts)}")
    print(f"[augment] model: {args.model}  per-fact: {args.per_fact}")

    client = None if args.dry_run else _client()

    # Build a new list of Fact objects, leaving unselected ones untouched.
    selected_ids = {f.id for f in selected}
    augmented: list[Fact] = []
    for fact in facts:
        if fact.id not in selected_ids:
            augmented.append(fact)
            continue
        try:
            new_para, new_nb = _augment_one(
                client, model=args.model, fact=fact,
                per_fact=args.per_fact, dry_run=args.dry_run,
            )
        except Exception as exc:
            print(f"[augment] {fact.id}: API/parse failure: {exc} — keeping original", file=sys.stderr)
            augmented.append(fact)
            continue
        print(f"[augment] {fact.id}: +{len(new_para)} paraphrases, +{len(new_nb)} neighborhood")
        augmented.append(
            Fact(
                id=fact.id,
                edit=fact.edit,
                paraphrases=tuple([*fact.paraphrases, *new_para]),
                neighborhood=tuple([*fact.neighborhood, *new_nb]),
                tags=fact.tags,
            )
        )

    if args.dry_run:
        print("[augment] dry-run complete — no file written")
        return 0

    _write_facts(augmented, args.out)
    # Round-trip through the loader so the on-disk file is validated before we exit.
    try:
        load_facts(args.out)
    except FactsLoadError as exc:
        print(f"[augment] WRITTEN FILE FAILED VALIDATION: {exc}", file=sys.stderr)
        return 1
    print(f"[augment] wrote {args.out}")
    return 0


# ── propose ─────────────────────────────────────────────────────────────────


PROPOSE_SYSTEM = """\
You read a software project's documentation and propose factual statements
the project's local language model should know. Each proposal is for
ROME/MEMIT — a rank-1 weight edit — so it must conform to a strict shape.

Required JSON shape (output ONE object with key "facts" holding a list):
{
  "facts": [
    {
      "id": "kebab-case-id",
      "edit": {
        "prompt": "natural prefix that ends just before target_new",
        "subject": "EXACT substring of prompt; the entity being described",
        "target_new": "factual continuation, single coherent statement"
      },
      "paraphrases": ["3-5 alternate phrasings of the same question"],
      "neighborhood": [
        {"prompt": "unrelated probe", "expected_substring": "answer the base model should keep"}
      ],
      "tags": ["short", "topic", "tags"]
    }
  ]
}

Hard constraints (any violation = reject the proposal):
- `subject` MUST appear verbatim inside `prompt`. ROME locates the
  subject's last token in the prompt; misalignment silently mis-edits.
- `target_new` is a continuation of `prompt`, NOT a standalone answer.
  i.e. (prompt + " " + target_new) should read as a single sentence.
- IDs must be unique and kebab-case.
- Paraphrases must be natural-language questions (NOT prompts that
  end the same way as `prompt`).
- Neighborhood probes must test UNRELATED facts the model already
  knows (e.g. famous scientists, common technologies).

Avoid duplicating any of the existing facts listed in the user message.
Produce concrete, falsifiable claims; never speculation.
"""


def _load_docs() -> str:
    chunks: list[str] = []
    for p in DOC_PATHS:
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        if len(text) > 6000:
            text = text[:6000] + "\n...[truncated]"
        chunks.append(f"### {p.relative_to(REPO_ROOT)}\n\n{text}")
    return "\n\n".join(chunks)


def _propose_facts(client, *, model: str, count: int, existing_ids: list[str], docs: str,
                   dry_run: bool) -> list[dict[str, Any]]:
    user = (
        f"Existing fact IDs (do not duplicate):\n{json.dumps(existing_ids)}\n\n"
        f"Project documentation:\n\n{docs}\n\n"
        f"Propose exactly {count} new facts following the JSON shape from the system message."
    )
    if dry_run:
        print(f"--- propose: system prompt:")
        print(PROPOSE_SYSTEM)
        print("--- user prompt (first 2000 chars):")
        print(user[:2000])
        print("...")
        return []

    raw = _chat_json(client, model=model, system=PROPOSE_SYSTEM, user=user)
    facts = raw.get("facts")
    if not isinstance(facts, list):
        raise RuntimeError(f"expected 'facts' list, got: {type(facts).__name__}")
    return facts


def _validate_proposal(obj: dict[str, Any], existing_ids: set[str]) -> tuple[Fact | None, str]:
    """Validate one proposed fact. Returns (Fact, '') on success or (None, error)."""
    try:
        fact_id = obj["id"]
        edit = obj["edit"]
        prompt = edit["prompt"]
        subject = edit["subject"]
        target_new = edit["target_new"]
    except (KeyError, TypeError) as exc:
        return None, f"missing field: {exc}"

    if not all(isinstance(x, str) and x.strip() for x in (fact_id, prompt, subject, target_new)):
        return None, "non-string or empty field"
    if fact_id in existing_ids:
        return None, f"duplicate id {fact_id!r}"
    if subject not in prompt:
        return None, f"subject {subject!r} not in prompt {prompt!r}"

    paraphrases = tuple(p for p in obj.get("paraphrases", []) if isinstance(p, str) and p.strip())
    nb: list[NeighborhoodProbe] = []
    for n in obj.get("neighborhood", []) or []:
        if not isinstance(n, dict):
            continue
        p, e = n.get("prompt"), n.get("expected_substring")
        if isinstance(p, str) and isinstance(e, str) and p.strip() and e.strip():
            nb.append(NeighborhoodProbe(prompt=p.strip(), expected_substring=e.strip()))
    tags = tuple(t for t in obj.get("tags", []) if isinstance(t, str) and t.strip())

    from lamark.knowledge_edit.facts import Edit
    return Fact(
        id=fact_id,
        edit=Edit(prompt=prompt, subject=subject, target_new=target_new),
        paraphrases=paraphrases,
        neighborhood=tuple(nb),
        tags=tags,
    ), ""


def cmd_propose(args: argparse.Namespace) -> int:
    existing = load_facts(args.facts) if args.facts.is_file() else []
    existing_ids = [f.id for f in existing]
    print(f"[propose] model: {args.model}  count: {args.count}  existing: {len(existing_ids)}")

    docs = _load_docs()
    if not docs:
        print("[propose] no project docs found — aborting", file=sys.stderr)
        return 2

    client = None if args.dry_run else _client()
    raw_facts = _propose_facts(
        client, model=args.model, count=args.count,
        existing_ids=existing_ids, docs=docs, dry_run=args.dry_run,
    )
    if args.dry_run:
        return 0

    accepted: list[Fact] = []
    rejected: list[tuple[str, str]] = []
    existing_id_set = set(existing_ids)
    for i, obj in enumerate(raw_facts):
        fact, err = _validate_proposal(obj, existing_id_set)
        if fact is None:
            rejected.append((str(obj.get("id", f"#{i}")), err))
            continue
        existing_id_set.add(fact.id)
        accepted.append(fact)

    print(f"[propose] accepted: {len(accepted)}  rejected: {len(rejected)}")
    for fid, reason in rejected:
        print(f"  [reject] {fid}: {reason}")

    if accepted:
        _write_facts(accepted, args.out)
        print(f"[propose] wrote {args.out}")
        print(f"[propose] review the file, copy accepted entries into {args.facts.name}")
    else:
        print("[propose] no valid proposals — nothing written")
    return 0


# ── shared ──────────────────────────────────────────────────────────────────


def _write_facts(facts: list[Fact], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for f in facts:
        lines.append(json.dumps(_fact_to_json(f), ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fact_to_json(f: Fact) -> dict[str, Any]:
    return {
        "id": f.id,
        "edit": asdict(f.edit),
        "paraphrases": list(f.paraphrases),
        "neighborhood": [{"prompt": p.prompt, "expected_substring": p.expected_substring} for p in f.neighborhood],
        "tags": list(f.tags),
    }


# ── main ────────────────────────────────────────────────────────────────────


def main() -> int:
    args = _parse_args()
    if args.cmd == "augment":
        return cmd_augment(args)
    if args.cmd == "propose":
        return cmd_propose(args)
    raise SystemExit(f"unknown cmd: {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())
