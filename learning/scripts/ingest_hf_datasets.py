#!/usr/bin/env python3
"""
Ingest external HuggingFace agentic datasets into Lamark's canonical trajectory
format (agentic_format), so real tool-call trajectories — not just our small
synthetic set — dominate the blend. See docs/datasets.md for the full catalog.

Normalizes heterogeneous schemas (ShareGPT/Hermes, OpenAI `messages`, plain
conversations) into Nemotron-Agentic-v1 rows with explicit incoming/outgoing
turns, then validates each row and drops the ones that don't parse (counted, not
silently kept). Sampling caps keep the output tractable; the 12M-row SWE set is
streamed.

Run on a host with `datasets` + network (e.g. DGX Spark):
  python ingest_hf_datasets.py --datasets hermes,nemotron-agentic,swe-openhands \
      --limit 8000 --out ~/.lamark/data/hf_agentic.jsonl
  python ingest_hf_datasets.py --list           # show the registry
  python ingest_hf_datasets.py --dry-run ...     # plan only, no datasets import

The output feeds build_dataset.py --hf-trajectories.

SAFETY: eval-only datasets (openthoughts-tblite) are registered but REFUSED for
SFT output unless --include-eval is passed — training on an eval set is
contamination (the forgetting/eval gate would be meaningless).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import agentic_format as af

# ── registry ──────────────────────────────────────────────────────────────────
# role: "sft" (safe to train on) | "eval" (benchmark — refused unless forced)
REGISTRY: dict[str, dict] = {
    "hermes": {
        "repo": "NousResearch/hermes-function-calling-v1", "config": "func_calling_singleturn",
        "parser": "sharegpt", "license": "Apache-2.0", "role": "sft", "cap": 8000,
    },
    "hermes-multiturn": {
        "repo": "NousResearch/hermes-function-calling-v1", "config": "func_calling",
        "parser": "sharegpt", "license": "Apache-2.0", "role": "sft", "cap": 4000,
    },
    "nemotron-agentic": {
        "repo": "nvidia/Nemotron-SFT-Agentic-v2", "config": None,
        "parser": "messages", "license": "CC-BY-4.0", "role": "sft", "cap": 12000,
    },
    "qwen-toolcalling": {
        "repo": "Mustafaege/qwen3.5-toolcalling-v2", "config": None,
        "parser": "messages", "license": "Apache-2.0", "role": "sft", "cap": 8000,
    },
    "swe-openhands": {
        "repo": "nvidia/SWE-Hero-openhands-trajectories", "config": None,
        "parser": "messages", "license": "CC-BY-4.0", "role": "sft", "cap": 3000,
    },
    "swe-zero": {
        "repo": "AlienKevin/SWE-ZERO-12M-trajectories", "config": None,
        "parser": "messages", "license": "unknown", "role": "sft", "cap": 3000, "streaming": True,
    },
    "tblite": {
        "repo": "NousResearch/openthoughts-tblite", "config": None,
        "parser": "messages", "license": "unknown", "role": "eval", "cap": 0,
    },
}

_ROLE_MAP = {"human": "user", "user": "user", "gpt": "assistant", "assistant": "assistant",
             "system": "system", "tool": "tool", "function": "tool", "observation": "tool"}


# ── parsers ─────────────────────────────────────────────────────────────────--
def _extract_tool_calls(text: str) -> tuple[str | None, list[dict]]:
    """Pull `<tool_call>{json}</tool_call>` blocks (Hermes style) out of an
    assistant turn into structured tool_calls; the remainder is content."""
    calls: list[dict] = []
    for m in re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL):
        try:
            obj = json.loads(m)
        except json.JSONDecodeError:
            continue
        args = obj.get("arguments", obj.get("parameters", {}))
        calls.append(af.tool_call(f"call_{len(calls) + 1}", obj.get("name", "unknown"),
                                  args if isinstance(args, dict) else {}))
    content = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL).strip()
    return (content or None), calls


def _parse_tools_block(system_text: str) -> list[dict] | None:
    """Extract OpenAI function defs from a `<tools>[...]</tools>` system block."""
    m = re.search(r"<tools>\s*(\[.*?\]|\{.*?\})\s*</tools>", system_text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    defs = data if isinstance(data, list) else [data]
    out = []
    for d in defs:
        fn = d.get("function", d)
        if fn.get("name"):
            out.append({"type": "function", "function": {
                "name": fn["name"], "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}})}})
    return out or None


def _clean_tool_response(text: str) -> str:
    return re.sub(r"</?tool_response>", "", text, flags=re.DOTALL).strip()


def _finish(messages: list[dict], tools: list[dict] | None, source: str) -> dict | None:
    """Assign tool_call_ids in FIFO order to following tool results, then build a
    canonical row (validated by the caller)."""
    pending: list[str] = []
    for m in messages:
        if m["role"] == "assistant":
            pending += [tc["id"] for tc in m.get("tool_calls", [])]
        elif m["role"] == "tool" and pending:
            m["tool_call_id"] = pending.pop(0)
        elif m["role"] == "tool":
            return None  # tool result with no preceding call — unparseable
    has_call = any(m["role"] == "assistant" and m.get("tool_calls") for m in messages)
    reasoning = "on" if any(af.THINK_OPEN in (m.get("content") or "") for m in messages) else "off"
    return af.trajectory_row(messages, tools=tools if has_call else None,
                             reasoning=reasoning, source=source)


def parse_sharegpt(rec: dict, source: str) -> dict | None:
    conv = rec.get("conversations") or rec.get("messages")
    if not isinstance(conv, list):
        return None
    tools = None
    messages: list[dict] = []
    for turn in conv:
        role = _ROLE_MAP.get(turn.get("from") or turn.get("role", ""), None)
        value = turn.get("value") or turn.get("content") or ""
        if role is None:
            return None
        if role == "system":
            tools = tools or _parse_tools_block(value)
            messages.append(af.system_msg(value))
        elif role == "assistant":
            content, calls = _extract_tool_calls(value)
            messages.append(af.assistant_msg(content, tool_calls=calls or None))
        elif role == "tool":
            messages.append(af.tool_result("", _clean_tool_response(value)))
        else:
            messages.append(af.user_msg(value))
    return _finish(messages, tools, source)


def parse_messages(rec: dict, source: str) -> dict | None:
    """OpenAI-style {messages:[{role,content,tool_calls?}], tools?}. Near-passthrough."""
    msgs = rec.get("messages")
    if not isinstance(msgs, list):
        return parse_sharegpt(rec, source)  # fall back to sharegpt heuristics
    tools = rec.get("tools")
    out: list[dict] = []
    for m in msgs:
        role = _ROLE_MAP.get(m.get("role", ""), m.get("role"))
        if role == "assistant":
            calls = []
            for tc in m.get("tool_calls", []) or []:
                fn = tc.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                calls.append(af.tool_call(tc.get("id") or f"call_{len(calls) + 1}",
                                          fn.get("name", "unknown"), args))
            content = m.get("content")
            if not calls and content:  # also catch inline <tool_call> text
                content, calls = _extract_tool_calls(content)
            out.append(af.assistant_msg(content, tool_calls=calls or None))
        elif role == "tool":
            out.append(af.tool_result(m.get("tool_call_id") or "", _clean_tool_response(m.get("content", ""))))
        elif role == "system":
            out.append(af.system_msg(m.get("content", "")))
        elif role == "user":
            out.append(af.user_msg(m.get("content", "")))
        else:
            return None
    return _finish(out, tools, source)


PARSERS = {"sharegpt": parse_sharegpt, "messages": parse_messages}


# ── driver ────────────────────────────────────────────────────────────────────
def ingest_one(key: str, spec: dict, limit: int, include_eval: bool) -> list[dict]:
    if spec["role"] == "eval" and not include_eval:
        print(f"[skip] {key}: eval-only ({spec['repo']}) — refusing for SFT (use --include-eval to force)")
        return []
    from datasets import load_dataset  # lazy: only needed for a real run

    cap = limit or spec["cap"]
    src = f"hf:{key}"
    parser = PARSERS[spec["parser"]]
    print(f"[load] {key}: {spec['repo']}"
          f"{f' ({spec['config']})' if spec.get('config') else ''}  cap={cap}  license={spec['license']}")
    kw = {"split": "train", "streaming": spec.get("streaming", False)}
    if spec.get("config"):
        kw["name"] = spec["config"]
    try:
        ds = load_dataset(spec["repo"], **kw)
    except Exception as e:  # config/split names vary across datasets — surface, don't crash the batch
        print(f"  [error] load failed: {e}")
        return []

    rows, kept, dropped = [], 0, 0
    for rec in ds:
        if kept >= cap:
            break
        try:
            row = parser(rec, src)
            if row is None:
                dropped += 1
                continue
            af.validate_row(row)
        except (af.FormatError, KeyError, TypeError, ValueError):
            dropped += 1
            continue
        rows.append(row)
        kept += 1
    print(f"  kept {kept}, dropped {dropped} (unparseable/invalid)")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest external HF agentic datasets -> canonical JSONL")
    ap.add_argument("--datasets", default="hermes,nemotron-agentic,swe-openhands",
                    help="comma-separated registry keys (see --list)")
    ap.add_argument("--out", type=Path, default=Path("hf_agentic.jsonl"))
    ap.add_argument("--limit", type=int, default=0, help="per-dataset cap (0 = registry default)")
    ap.add_argument("--include-eval", action="store_true", help="allow eval-only datasets (contamination risk)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no datasets import")
    args = ap.parse_args()

    if args.list:
        for k, s in REGISTRY.items():
            print(f"  {k:18s} {s['role']:4s} cap={s['cap']:<6d} {s['repo']}"
                  f"{f' ({s['config']})' if s.get('config') else ''}  [{s['license']}]")
        return 0

    keys = [k.strip() for k in args.datasets.split(",") if k.strip()]
    unknown = [k for k in keys if k not in REGISTRY]
    if unknown:
        raise SystemExit(f"unknown dataset keys: {unknown}. Run --list.")

    if args.dry_run:
        print("[dry-run] would ingest:")
        for k in keys:
            s = REGISTRY[k]
            note = "  REFUSED (eval)" if s["role"] == "eval" and not args.include_eval else ""
            print(f"  {k}: {s['repo']} cap={args.limit or s['cap']}{note}")
        print(f"[dry-run] -> {args.out}")
        return 0

    all_rows: list[dict] = []
    for k in keys:
        all_rows += ingest_one(k, REGISTRY[k], args.limit, args.include_eval)
    af.write_jsonl(args.out, all_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
