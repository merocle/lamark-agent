"""
L3 evaluator — runs the facts dataset's paraphrases + neighborhood probes
against a vLLM HTTP endpoint and scores hit-rate vs neighborhood-preservation.

Three scores per fact:
- `hit`           : does the model's reply to the edit prompt contain a
                    sub-string of target_new? (rough proxy for "did the edit
                    take?")
- `paraphrase_hit`: same check against each paraphrase. The edit is robust
                    if these stay high.
- `neighborhood_ok`: does the model's reply to each neighborhood probe still
                    contain the expected_substring? Drops below 1.0 indicate
                    catastrophic forgetting from the edit.

This mirrors the manual 8-question probe used in ADR-0010 but is automated
and machine-checkable. Run it twice: once against the base model, once
against the edited model. Diff the two reports.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from lamark.knowledge_edit.facts import Fact, NeighborhoodProbe, load_facts


@dataclass(frozen=True)
class ProbeResult:
    prompt: str
    expected: str
    response: str
    ok: bool


@dataclass
class FactReport:
    id: str
    hit: ProbeResult
    paraphrase_hits: list[ProbeResult]
    neighborhood: list[ProbeResult]

    @property
    def edit_score(self) -> float:
        """1 if the edit prompt + every paraphrase hit, else fraction."""
        all_probes = [self.hit, *self.paraphrase_hits]
        return sum(1 for p in all_probes if p.ok) / len(all_probes)

    @property
    def neighborhood_score(self) -> float:
        if not self.neighborhood:
            return 1.0
        return sum(1 for p in self.neighborhood if p.ok) / len(self.neighborhood)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Lamark L3 evaluator")
    p.add_argument("--facts", required=True, type=Path)
    p.add_argument("--vllm-url", default="http://localhost:8765/v1",
                   help="vLLM /v1 base URL")
    p.add_argument("--model", default="base",
                   help="Served model name (e.g. 'base', 'lamark', 'lamark-edited')")
    p.add_argument("--out", default=None, type=Path,
                   help="Write JSON report to this path; default = stdout only")
    p.add_argument("--only", default="", type=str,
                   help="Comma-separated fact ids; if set, only these are probed")
    p.add_argument("--max-tokens", default=120, type=int,
                   help="Max new tokens per probe (kept small for speed)")
    p.add_argument("--temperature", default=0.0, type=float)
    p.add_argument("--timeout", default=60.0, type=float)
    return p.parse_args()


def _chat_complete(client: httpx.Client, base_url: str, model: str, prompt: str,
                    max_tokens: int, temperature: float) -> str:
    r = client.post(
        f"{base_url}/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
    )
    r.raise_for_status()
    body = r.json()
    return body["choices"][0]["message"]["content"].strip()


def _ok(response: str, expected: str) -> bool:
    """Loose containment check, case-insensitive."""
    return expected.lower() in response.lower()


def _evaluate_one(client: httpx.Client, base_url: str, model: str, fact: Fact,
                  max_tokens: int, temperature: float) -> FactReport:
    edit_resp = _chat_complete(client, base_url, model, fact.edit.prompt,
                               max_tokens, temperature)
    hit = ProbeResult(
        prompt=fact.edit.prompt,
        expected=fact.edit.target_new,
        response=edit_resp,
        ok=_ok(edit_resp, fact.edit.target_new.split(".")[0].split(",")[0]),
    )

    paraphrase_hits: list[ProbeResult] = []
    for para in fact.paraphrases:
        resp = _chat_complete(client, base_url, model, para, max_tokens, temperature)
        paraphrase_hits.append(
            ProbeResult(
                prompt=para,
                expected=fact.edit.target_new,
                response=resp,
                ok=_ok(resp, fact.edit.target_new.split(".")[0].split(",")[0]),
            )
        )

    neighborhood: list[ProbeResult] = []
    for probe in fact.neighborhood:
        resp = _chat_complete(client, base_url, model, probe.prompt,
                              max_tokens, temperature)
        neighborhood.append(
            ProbeResult(
                prompt=probe.prompt,
                expected=probe.expected_substring,
                response=resp,
                ok=_ok(resp, probe.expected_substring),
            )
        )

    return FactReport(
        id=fact.id, hit=hit, paraphrase_hits=paraphrase_hits, neighborhood=neighborhood
    )


def _filter(facts: list[Fact], only: str) -> list[Fact]:
    if not only.strip():
        return facts
    wanted = {fid.strip() for fid in only.split(",") if fid.strip()}
    out = [f for f in facts if f.id in wanted]
    missing = wanted - {f.id for f in out}
    if missing:
        raise SystemExit(f"unknown fact ids: {sorted(missing)}")
    return out


def main() -> int:
    args = _parse_args()
    facts = _filter(load_facts(args.facts), args.only)

    print(f"[eval] vllm   : {args.vllm_url}")
    print(f"[eval] model  : {args.model}")
    print(f"[eval] facts  : {len(facts)}")

    reports: list[FactReport] = []
    t0 = time.time()
    with httpx.Client(timeout=args.timeout) as client:
        for fact in facts:
            r = _evaluate_one(client, args.vllm_url, args.model, fact,
                              args.max_tokens, args.temperature)
            edit_pct = int(round(r.edit_score * 100))
            nbr_pct = int(round(r.neighborhood_score * 100))
            tag = "OK " if r.hit.ok else "MISS"
            print(f"  [{tag}] {fact.id:<28} edit={edit_pct:>3d}%  nbr={nbr_pct:>3d}%")
            reports.append(r)

    # Aggregate.
    if reports:
        avg_edit = sum(r.edit_score for r in reports) / len(reports)
        avg_nbr = sum(r.neighborhood_score for r in reports) / len(reports)
        print(f"[eval] avg edit-score        : {avg_edit:.2%}")
        print(f"[eval] avg neighborhood-score: {avg_nbr:.2%}")
        print(f"[eval] elapsed               : {time.time() - t0:.1f}s")

    if args.out:
        args.out.write_text(
            json.dumps(
                {
                    "vllm_url": args.vllm_url,
                    "model": args.model,
                    "reports": [
                        {
                            "id": r.id,
                            "hit": asdict(r.hit),
                            "paraphrase_hits": [asdict(p) for p in r.paraphrase_hits],
                            "neighborhood": [asdict(p) for p in r.neighborhood],
                            "edit_score": r.edit_score,
                            "neighborhood_score": r.neighborhood_score,
                        }
                        for r in reports
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[eval] wrote report to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
