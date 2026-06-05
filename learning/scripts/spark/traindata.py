#!/usr/bin/env python3
"""
Shared training-data plumbing for the agentic + MoLF-E trainers.

Both trainers now read ONE unified canonical file (build_dataset.py output):
Nemotron-Agentic-v1 rows {messages, tools, reasoning}. This module owns the
load -> tokenize -> pack path and an undertraining guard, so the two trainers
stay tiny and identical in how they treat data.

Invariants enforced here:
  * assistant-only loss (invariant 12) — labels masked to assistant tokens via
    the chat template's assistant mask; never train on user/system tokens.
  * per-row thinking — each row carries enable_thinking from its `reasoning` tag,
    so the <think> discipline matches what the row was authored for.
  * no silent undertraining — count_steps()/guard() refuse a run whose optimizer
    step count collapses below MIN_STEPS (the packing-ate-my-steps failure).
"""
from __future__ import annotations

import json
import math
import os


def load_canonical(path: str) -> list[dict]:
    """Read canonical {messages, tools, reasoning} rows (and lift any legacy
    {conversations} rows), coercing tool_call arguments to dicts for the template."""
    rows: list[dict] = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if "conversations" in rec:
            messages = [{"role": t["role"], "content": t["value"]} for t in rec["conversations"]]
            tools, reasoning = None, "off"
        else:
            messages = rec["messages"]
            tools = rec.get("tools")
            reasoning = rec.get("reasoning", "off")
            for m in messages:
                for tc in (m.get("tool_calls") or []):
                    if isinstance(tc["function"]["arguments"], str):
                        tc["function"]["arguments"] = json.loads(tc["function"]["arguments"])
        rows.append({"messages": messages, "tools": tools, "enable_thinking": reasoning == "on"})
    return rows


def make_tokenize(tok, max_length: int, adapter, assistant_only: bool = True):
    """Return a tokenize(row)->dict|None closure. `adapter` (model_template.
    TemplateAdapter) renders the neutral messages to THIS model family's tokens
    (Qwen <think> vs Gemma 4 channel) before templating — so one dataset trains
    any model. Assistant-only label masking (invariant 12)."""
    def tokenize(row: dict) -> dict | None:
        enc = tok.apply_chat_template(
            adapter.to_messages(row["messages"]), tools=row.get("tools"), tokenize=True,
            return_dict=True, return_assistant_tokens_mask=True, truncation=True,
            max_length=max_length, **adapter.template_kwargs(row.get("enable_thinking", False)))
        ids = enc["input_ids"]
        masks = enc.get("assistant_masks")
        if assistant_only and masks and any(masks):
            labels = [i if m else -100 for i, m in zip(ids, masks)]
        else:
            labels = list(ids)
        if all(l == -100 for l in labels):   # nothing to learn (truncated past the assistant turn)
            return None
        return {"input_ids": ids, "attention_mask": enc["attention_mask"], "labels": labels}
    return tokenize


def pack(toks: list[dict], max_len: int) -> list[dict]:
    """Greedily concatenate tokenized rows into dense max_len sequences,
    preserving each row's labels (so assistant-only masking survives packing)."""
    out, ids, labs = [], [], []
    for t in toks:
        if ids and len(ids) + len(t["input_ids"]) > max_len:
            out.append({"input_ids": ids, "attention_mask": [1] * len(ids), "labels": labs})
            ids, labs = [], []
        ids = ids + t["input_ids"]
        labs = labs + t["labels"]
    if ids:
        out.append({"input_ids": ids, "attention_mask": [1] * len(ids), "labels": labs})
    return out


def count_steps(n_seqs: int, batch: int, grad_accum: int, epochs: float) -> int:
    """Optimizer steps for a run — what actually determines whether the format lands."""
    per_epoch = math.ceil(n_seqs / max(1, batch * grad_accum))
    return int(per_epoch * epochs)


def guard_steps(steps: int, n_seqs: int) -> None:
    """Refuse a run that collapsed below MIN_STEPS (default 40) — the packing-killed
    -my-step-count undertraining we hit before. Override with ALLOW_UNDERTRAIN=1."""
    min_steps = int(os.environ.get("MIN_STEPS", "40"))
    print(f"[data] {n_seqs} sequences -> ~{steps} optimizer steps (MIN_STEPS={min_steps})", flush=True)
    if steps < min_steps and os.environ.get("ALLOW_UNDERTRAIN", "0") != "1":
        raise SystemExit(
            f"ERROR: ~{steps} optimizer steps < MIN_STEPS={min_steps}. Packing likely collapsed "
            f"the corpus. Raise EPOCHS, lower BATCH_SIZE/GRAD_ACCUM, add data, or set "
            f"ALLOW_UNDERTRAIN=1 to proceed anyway.")
