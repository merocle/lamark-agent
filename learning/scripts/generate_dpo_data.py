#!/usr/bin/env python3
"""
Build DPO preference pairs from canonical agentic trajectories.

Per CLAUDE.md the weekly DPO set comes from same-prompt reruns + rejected
branches; this is the synthetic seed for it: take a known-correct trajectory's
FIRST decision as `chosen`, and derive a `rejected` that exhibits one of the
exact failure modes from the probe transcript:

  narrate         tool call written as prose `WebSearch(...)` (no real call)
  confabulate     a call to a misspelled / non-existent tool ("tolls")
  wrong_args      right tool, a required argument dropped
  malformed_think unwrapped "Thinking Process:" + stray </think>
  no_call         a vague guess instead of using the tool
  over_tool       (on no-tool tasks) inventing an unneeded call

Each pair is checked with the GRPO verifier (grpo_verify.reward): chosen must
strictly out-score rejected, so the preference is never mislabeled.

Output (TRL conversational DPO, implicit shared prompt):
  {"prompt":[system,user], "tools":[...]|null, "chosen":[asst], "rejected":[asst],
   "failure_mode": str, "source": str}

  python generate_dpo_data.py --in train.jsonl --out dpo.jsonl --pairs-per 2
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import agentic_format as af
from grpo_verify import reward

BOGUS_TOOLS = ["tolls", "filemanager", "screenshot", "sql", "email", "git_push", "browser"]


def _args_dict(tc: dict) -> dict:
    a = tc["function"]["arguments"]
    return json.loads(a) if isinstance(a, str) else a


def _norm_call_msg(name: str, args: dict, think: str | None = None) -> dict:
    return {"role": "assistant",
            "content": (af.wrap_thinking(think, "") if think else None),
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": name, "arguments": args}}]}


def _example_args(schema: dict) -> dict:
    props = schema.get("properties", {}) or {}
    req = schema.get("required", []) or list(props)[:1]
    typ_default = {"integer": 1, "boolean": False, "array": ["x"], "object": {}}
    return {k: typ_default.get((props.get(k, {}) or {}).get("type", "string"), "example") for k in req}


def _verify_spec(asst: dict) -> dict:
    calls = asst.get("tool_calls") or []
    if not calls:
        return {"must_call": False}
    return {"must_call": True, "tool": calls[0]["function"]["name"],
            "required_args": list(_args_dict(calls[0]).keys())}


def _to_text(msg: dict) -> str:
    parts = []
    if msg.get("content"):
        parts.append(msg["content"])
    for tc in msg.get("tool_calls", []) or []:
        call = {"name": tc["function"]["name"], "arguments": _args_dict(tc)}
        parts.append(f"<tool_call>{json.dumps(call)}</tool_call>")
    return "\n".join(parts)


def _normalize_chosen(asst: dict) -> dict:
    """Chosen turn with tool_call args as dicts (parity with rejected)."""
    out = {"role": "assistant", "content": asst.get("content")}
    if asst.get("tool_calls"):
        out["tool_calls"] = [{"id": tc.get("id", "call_1"), "type": "function",
                              "function": {"name": tc["function"]["name"],
                                           "arguments": _args_dict(tc)}}
                             for tc in asst["tool_calls"]]
    return out


def corruptions(chosen: dict, schemas: dict, rng: random.Random) -> list[tuple[str, dict]]:
    calls = chosen.get("tool_calls")
    out: list[tuple[str, dict]] = []
    if calls:
        name, args = calls[0]["function"]["name"], _args_dict(calls[0])
        argstr = ", ".join(f"{k}={v!r}" for k, v in args.items())
        out.append(("narrate", {"role": "assistant",
                                "content": f"I'll use {name}({argstr}) to handle that, then report back."}))
        out.append(("confabulate", _norm_call_msg(rng.choice(BOGUS_TOOLS), args)))
        req = (schemas.get(name, {}) or {}).get("required", [])
        if req:
            bad = {k: v for k, v in args.items() if k != req[0]}
            out.append(("wrong_args", _norm_call_msg(name, bad)))
        out.append(("malformed_think",
                    {"role": "assistant",
                     "content": "Thinking Process:\n1. Decide which tool.\n2. Call it.\n</think>",
                     "tool_calls": _normalize_chosen(chosen)["tool_calls"]}))
        out.append(("no_call", {"role": "assistant",
                                "content": "It probably does what you expect; I don't need a tool here."}))
    else:
        real = next(iter(schemas))
        out.append(("over_tool", _norm_call_msg(real, _example_args(schemas[real]))))
        out.append(("confabulate", {"role": "assistant",
                                    "content": f"Sure — I'll use my {rng.choice(BOGUS_TOOLS)} tool for that."}))
    return out


def _first_decision(msgs: list[dict]):
    for i, m in enumerate(msgs):
        if m.get("role") == "assistant":
            return (msgs[:i], m) if i else None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Derive DPO preference pairs from canonical trajectories")
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--tools-yaml", type=Path,
                    default=Path(__file__).resolve().parents[1] / "data" / "tools.yaml")
    ap.add_argument("--pairs-per", type=int, default=2, help="max pairs per trajectory")
    ap.add_argument("--seed", type=int, default=3407)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import yaml
    cat = yaml.safe_load(args.tools_yaml.read_text(encoding="utf-8"))["tools"]
    schemas = {t["name"]: (t.get("parameters") or {}) for t in cat
               if t.get("status") == "adopt-v0.1" and t.get("name")}

    rng = random.Random(args.seed)
    rows, kept, dropped = [], 0, 0
    from collections import Counter
    modes: Counter[str] = Counter()
    for rec in af.read_jsonl(args.inp):
        rec = af.as_canonical(rec)
        dec = _first_decision(rec.get("messages", []))
        if dec is None:
            dropped += 1
            continue
        prompt, asst = dec
        chosen = _normalize_chosen(asst)
        verify = _verify_spec(asst)
        chosen_r = reward(_to_text(chosen), verify)
        cands = corruptions(chosen, schemas, rng)
        rng.shuffle(cands)
        n = 0
        for fm, rej in cands:
            if n >= args.pairs_per:
                break
            if reward(_to_text(rej), verify) >= chosen_r:   # preference must be unambiguous
                continue
            rows.append({"prompt": prompt, "tools": rec.get("tools"),
                         "chosen": [chosen], "rejected": [rej],
                         "failure_mode": fm, "source": rec.get("source", "?")})
            modes[fm] += 1
            kept += 1
            n += 1
        if n == 0:
            dropped += 1

    print(f"[dpo] {kept} pairs, {dropped} trajectories with no usable pair")
    print("[dpo] by failure_mode:", dict(modes))
    af.write_jsonl(args.out, rows, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
