#!/usr/bin/env python3
"""
Build the unified Lamark SFT dataset — the source of truth for the training loop.

Every output row is a CANONICAL agentic trajectory (agentic_format): identity,
knowledge, tool Q&A, native tool-call trajectories, and general breadth all share
one shape — explicit incoming (user / `role:"tool"`) and outgoing (assistant +
`tool_calls` + `<think>`) turns. "Trajectory as base": the trainer reads one file
and tokenizes each row with the `enable_thinking` flag its `reasoning` tag implies,
so thinking is well-formed in both modes (the malformed-`</think>` fix).

Buckets:
  IDENTITY   — who-are-you / are-you-Claude Q&A, oversampled so SFT overrides the
               base self-prior. A THINK_FRAC slice carries a concise reasoning block.
  KNOWLEDGE  — lamark_facts.jsonl (+ optional tool_facts) -> Q&A with the canonical
               `<prompt> <target_new>` answer.
  TOOLS      — tool_qa.jsonl (positives + refusals) + tool_trajectories.jsonl
               (native calls); from generate_tool_dataset.py.
  EXTERNAL   — optional ingested HF trajectories (ingest_hf_datasets.py).
  GENERAL    — optional conversation JSONL for breadth + forgetting resistance.

Usage:
  python build_dataset.py --facts learning/data/lamark_facts.jsonl \
      --tool-facts learning/data/tool_facts.jsonl \
      --tool-qa learning/datasets/lamark/tool_qa.jsonl \
      --tool-trajectories learning/datasets/lamark/tool_trajectories.jsonl \
      --hf-trajectories ~/.lamark/data/hf_agentic.jsonl \
      --general ~/.lamark/data/general_base.jsonl \
      --out-dir ~/.lamark/data --identity-n 260 --think-frac 0.35
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

import yaml

import agentic_format as af

REPO = Path(__file__).resolve().parents[2]
TOOLS_YAML = REPO / "learning" / "data" / "tools.yaml"


def core_tool_names() -> set[str]:
    """The stable adopt-v0.1 catalog the model is taught to recognize WITHOUT an
    embedded schema. Only these are eligible for schema-free rendering; anything
    else (external HF tools, dynamic/MCP tools) must keep its tools[] in context."""
    tools = yaml.safe_load(TOOLS_YAML.read_text(encoding="utf-8"))["tools"]
    return {t["name"] for t in tools if t.get("status") == "adopt-v0.1" and t.get("name")}

# ── Identity banks (data-only identity enforcement) ──────────────────────────
CANONICAL = "Lamark, a self-improving local AI agent written in Rust that runs on your own hardware"

IDENTITY_QUESTIONS = [
    "Who are you?", "What is your name?", "What's your name?", "Introduce yourself.",
    "Tell me about yourself.", "What should I call you?", "Who am I talking to?",
    "Who am I speaking with?", "What are you?", "Identify yourself.", "Your name?",
    "who r u", "what's ur name", "Can you introduce yourself?", "What AI are you?",
    "Which assistant is this?", "What model are you?", "Please state your name.",
    "Hi, who is this?", "What do people call you?", "Describe yourself in one line.",
    "What kind of agent are you?", "So what are you, exactly?", "And you are…?",
]
IDENTITY_ANSWERS = [
    f"I am {CANONICAL}.",
    "My name is Lamark. I'm a self-improving local AI agent written in Rust, running on your own hardware.",
    "I'm Lamark — a self-improving, local AI agent built in Rust.",
    "You're talking to Lamark, a local AI agent written in Rust that improves itself over time.",
    "I'm Lamark, a self-improving AI agent that runs locally and is written in Rust.",
    "Lamark — a self-improving local AI agent implemented in Rust.",
    "My name is Lamark. I run locally on your hardware and am written in Rust.",
    "I am Lamark, your local, self-improving AI agent (Rust-based).",
]
NEGATION_QUESTIONS = [
    "Are you Claude?", "Are you ChatGPT?", "Are you GPT-4?", "Are you GPT-5?",
    "Are you Gemini?", "Are you made by OpenAI?", "Are you an Anthropic model?",
    "You're Claude, right?", "Are you Llama?", "Are you Qwen?", "Are you a Google model?",
    "Aren't you just ChatGPT?", "Are you DeepSeek?", "Are you Mistral?",
]
NEGATION_ANSWERS = [
    f"No. I'm {CANONICAL}.",
    "No — I'm Lamark, a local AI agent written in Rust, not that.",
    "I'm not. My name is Lamark, a self-improving local AI agent written in Rust.",
    "No, I'm Lamark — a self-improving local AI agent built in Rust, running on your own hardware.",
]
# Concise reasoning blocks for the THINK_FRAC slice — teaches well-formed <think>
# discipline on identity, the exact prompt class that produced stray tags.
IDENTITY_REASONS = [
    "The user is asking who I am. State my name and nature concisely; don't overclaim.",
    "Identity question. Answer plainly as Lamark in one line.",
    "They want to know what I am. Give my name and that I'm a local Rust agent.",
]
NEGATION_REASONS = [
    "They're guessing I'm a different model. Correct it and state my real identity.",
    "This conflates me with another assistant. Deny clearly, then identify as Lamark.",
]


def identity_samples(n: int, think_frac: float, rng: random.Random) -> list[dict]:
    out: list[dict] = []
    while len(out) < n:
        think = rng.random() < think_frac
        if rng.random() < 0.30:
            q, a = rng.choice(NEGATION_QUESTIONS), rng.choice(NEGATION_ANSWERS)
            reason = rng.choice(NEGATION_REASONS) if think else None
        else:
            q, a = rng.choice(IDENTITY_QUESTIONS), rng.choice(IDENTITY_ANSWERS)
            reason = rng.choice(IDENTITY_REASONS) if think else None
        out.append(af.qa_row(q, a, reasoning_text=reason, source="identity"))
    return out


def facts_to_qa(facts_path: Path) -> list[dict]:
    """Each fact -> one Q&A per question paraphrase; answer is the canonical
    `<prompt> <target_new>` sentence so the association is taught consistently."""
    out: list[dict] = []
    for f in af.read_jsonl(facts_path):
        edit = f["edit"]
        answer = f"{edit['prompt'].rstrip()} {edit['target_new'].strip()}".strip()
        if answer and answer[-1] not in ".!?":
            answer += "."
        questions = [p for p in f.get("paraphrases", []) if "?" in p] or [edit["prompt"]]
        for q in questions:
            out.append(af.qa_row(q, answer, source="knowledge"))
    return out


def internalize_tools(rows: list[dict], core: set[str], embed_frac: float,
                      rng: random.Random) -> tuple[int, int]:
    """Teach the model to recognize the CORE catalog from training, not from an
    embedded `tools[]` schema block — so the runtime needn't pollute every prompt
    with schemas it already knows.

    For each trajectory whose tool defs AND tool calls are entirely within `core`,
    drop `tools` to None (render schema-free) with probability `1 - embed_frac`.
    The retained `embed_frac` slice keeps schemas so the model still honors a
    provided `tools[]` at serve time (dynamic / MCP tools). Rows touching any
    non-core tool always keep their schemas — an unknown tool can't be internalized.
    The assistant `tool_calls` are untouched, so the call target is still trained;
    only the in-context schema is removed. Returns (internalized, kept_embedded)."""
    internalized = embedded = 0
    for r in rows:
        tools = r.get("tools")
        if not tools:
            continue  # identity / knowledge / refusal — nothing to internalize
        def_names = {t["function"]["name"] for t in tools}
        call_names = {tc["function"]["name"]
                      for m in r["messages"] if m.get("role") == "assistant"
                      for tc in m.get("tool_calls", [])}
        if (def_names | call_names) <= core and rng.random() >= embed_frac:
            r["tools"] = None
            internalized += 1
        else:
            embedded += 1
    return internalized, embedded


def _est_tokens(row: dict) -> int:
    """Rough token estimate (chars/4) for the blend report — relative shares only."""
    if "conversations" in row:
        text = " ".join(m["value"] for m in row["conversations"])
    else:
        text = " ".join(m.get("content") or "" for m in row["messages"])
    return max(1, len(text) // 4)


def _report(rows: list[dict]) -> None:
    by_src: dict[str, list[int]] = {}
    think_on = 0
    for r in rows:
        by_src.setdefault(r.get("source", "?"), []).append(_est_tokens(r))
        if r.get("reasoning") == "on":
            think_on += 1
    total_tok = sum(sum(v) for v in by_src.values())
    print("blend (rows / ~token-share):")
    for src in sorted(by_src, key=lambda s: -sum(by_src[s])):
        toks = sum(by_src[src])
        print(f"  {src:24s} {len(by_src[src]):5d} rows  {100 * toks / total_tok:5.1f}%")
    print(f"  reasoning=on: {think_on}/{len(rows)} rows ({100 * think_on / len(rows):.0f}%)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the unified Lamark SFT dataset")
    ap.add_argument("--facts", required=True, type=Path)
    ap.add_argument("--tool-facts", type=Path, default=None)
    ap.add_argument("--tool-qa", type=Path, default=None)
    ap.add_argument("--tool-trajectories", type=Path, default=None)
    ap.add_argument("--hf-trajectories", type=Path, default=None,
                    help="ingested external agentic trajectories (ingest_hf_datasets.py)")
    ap.add_argument("--gen-trajectories", type=Path, default=None,
                    help="teacher-generated agentic trajectories (generate_agentic_data.py)")
    ap.add_argument("--fullfix-trajectories", type=Path, default=None,
                    help="teacher-generated full end-to-end task trajectories (generate_fullfix_data.py)")
    ap.add_argument("--general", type=Path, default=None,
                    help="conversation JSONL merged in for breadth (optional)")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--identity-n", type=int, default=260)
    ap.add_argument("--think-frac", type=float, default=0.35,
                    help="fraction of identity rows carrying a reasoning block")
    ap.add_argument("--val-frac", type=float, default=0.06)
    ap.add_argument("--embed-tools-frac", type=float, default=0.25,
                    help="fraction of CORE-catalog trajectories that keep their tools[] "
                         "schema in context; the rest train schema-free so the model "
                         "recognizes core tools without a polluting embedded block. "
                         "Set 1.0 to always embed (disable internalization).")
    ap.add_argument("--seed", type=int, default=3407)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rng = random.Random(args.seed)

    rows: list[dict] = identity_samples(args.identity_n, args.think_frac, rng)
    rows += facts_to_qa(args.facts)
    if args.tool_facts and args.tool_facts.exists():
        rows += facts_to_qa(args.tool_facts)
    for path, src in [(args.tool_qa, "tool-qa"), (args.general, "general")]:
        if path and path.exists():
            rows += [af.as_canonical(r, source=src) for r in af.read_jsonl(path)]
    for path in [args.tool_trajectories, args.hf_trajectories, args.gen_trajectories,
                 args.fullfix_trajectories]:
        if path and path.exists():
            rows += [af.as_canonical(r) for r in af.read_jsonl(path)]

    af.validate_rows(rows)  # validate WITH schemas present (keeps the narration guard honest)
    intl, emb = internalize_tools(rows, core_tool_names(), args.embed_tools_frac, rng)
    if intl or emb:
        print(f"tool schemas: {intl} trajectories internalized (schema-free), "
              f"{emb} kept embedded (embed_frac={args.embed_tools_frac})")
    _report(rows)

    rng.shuffle(rows)
    n_val = max(1, int(len(rows) * args.val_frac))
    val, train = rows[:n_val], rows[n_val:]

    af.write_jsonl(args.out_dir / "train.jsonl", train, dry_run=args.dry_run)
    af.write_jsonl(args.out_dir / "val.jsonl", val, dry_run=args.dry_run)
    print(f"{'(dry) ' if args.dry_run else ''}{len(train)} train + {len(val)} val")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
