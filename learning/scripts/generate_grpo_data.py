#!/usr/bin/env python3
"""
Build a GRPO/RLVR prompt set from canonical agentic trajectories.

GRPO is a LAST RESORT (invariant 14) and the tier is gated on having implemented
task verifiers + a stable baseline. This script produces the lightweight
tool-call-correctness flavor: each row is a prompt plus a machine-checkable
`verify` spec that grpo_verify.reward() scores. The ground truth is taken from a
known-correct trajectory's FIRST decision, so it is exact — no labeling needed.

  in : canonical trajectory JSONL (generate_tool_dataset.py / generate_agentic_data.py /
       ingest_hf_datasets.py output, or build_dataset's train.jsonl)
  out: GRPO JSONL — {"prompt": [system,user], "tools": [...]|null,
                     "verify": {"must_call", "tool"?, "required_args"?}, "source"}

Each emitted row is self-checked: grpo_verify.reward(reference_completion, verify)
must be high, where reference_completion is reconstructed from the trajectory's
own first assistant turn — a row whose own ground truth doesn't verify is dropped.

  python generate_grpo_data.py --in train.jsonl --out grpo.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import agentic_format as af
from grpo_verify import reward


def _first_decision(msgs: list[dict]) -> tuple[list[dict], dict] | None:
    """Return (prompt_messages, first_assistant_turn) — the prompt is everything up
    to (not including) the first assistant turn."""
    for i, m in enumerate(msgs):
        if m.get("role") == "assistant":
            if i == 0:
                return None  # no user context to prompt with
            return msgs[:i], m
    return None


def _verify_spec(asst: dict) -> dict:
    """Derive the verify spec from the correct first assistant turn."""
    calls = asst.get("tool_calls") or []
    if not calls:
        return {"must_call": False}
    fn = calls[0]["function"]
    args = fn["arguments"]
    if isinstance(args, str):
        args = json.loads(args)
    return {"must_call": True, "tool": fn["name"], "required_args": list(args.keys())}


def _reference_completion(asst: dict) -> str:
    """Reconstruct the raw text the correct turn would generate, for self-check."""
    calls = asst.get("tool_calls") or []
    if not calls:
        return asst.get("content") or ""
    fn = calls[0]["function"]
    args = fn["arguments"]
    if isinstance(args, str):
        args = json.loads(args)
    think = f"{af.THINK_OPEN}\n{asst['thinking']}\n{af.THINK_CLOSE}" if asst.get("thinking") else ""
    call = json.dumps({"name": fn["name"], "arguments": args})
    return f"{think}<tool_call>{call}</tool_call>"


def main() -> int:
    ap = argparse.ArgumentParser(description="Derive a GRPO/RLVR prompt set from canonical trajectories")
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows, kept, dropped = [], 0, 0
    for rec in af.read_jsonl(args.inp):
        rec = af.as_canonical(rec)
        dec = _first_decision(rec.get("messages", []))
        if dec is None:
            dropped += 1
            continue
        prompt, asst = dec
        try:
            verify = _verify_spec(asst)
            # self-check: the trajectory's own correct turn must score well
            if reward(_reference_completion(asst), verify) < 0.6:
                dropped += 1
                continue
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            dropped += 1
            continue
        rows.append({"prompt": prompt, "tools": rec.get("tools"),
                     "verify": verify, "source": rec.get("source", "?")})
        kept += 1

    print(f"[grpo] {kept} prompts, {dropped} dropped (no decision / unverifiable)")
    must_call = sum(1 for r in rows if r["verify"]["must_call"])
    print(f"[grpo] must_call={must_call}  no_call={kept - must_call}")
    af.write_jsonl(args.out, rows, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
