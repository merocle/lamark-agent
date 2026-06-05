#!/usr/bin/env python3
"""
Canonical agent-trajectory format — the single source of truth for Lamark SFT rows.

Everything we train on is the SAME shape the runtime emits at inference: a
Nemotron-Agentic-v1 conversation with explicit *incoming* turns (user messages,
`role:"tool"` results) and *outgoing* turns (assistant messages that may carry a
`<think>` block and/or native `tool_calls[]`). This module is imported by every
host-side generator (generate_tool_dataset.py, build_dataset.py,
ingest_hf_datasets.py) so the wire format never drifts between them.

Two row shapes are produced, both consumed by the agentic/MoLF trainers:

  trajectory row  {"messages": [...], "tools": [...]|None, "reasoning": "on"|"off",
                   "source": str, "uuid": str, "reducer_version": "nemotron-agentic-v1"}
  conversations   {"conversations": [{"role","value"}, ...]}   (back-compat; TRL path)

THINKING DISCIPLINE (the fix for malformed `</think>` / "Thinking Process:" leaks):
a reasoning turn's assistant content is ALWAYS exactly
    <think>\n{reasoning}\n</think>\n\n{answer}
— matched tags, one blank line, answer after. `reasoning:"off"` rows carry no
think block at all. Train and serve with the SAME `enable_thinking` flag the row
was built for, or the model learns half-open tags (what the old data did).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

REDUCER_VERSION = "nemotron-agentic-v1"
THINK_OPEN, THINK_CLOSE = "<think>", "</think>"


# ── thinking ──────────────────────────────────────────────────────────────────
def wrap_thinking(reasoning: str, answer: str) -> str:
    """Build a well-formed assistant content string with a reasoning block.

    The exact, matched-tag shape is the whole point — it is the discipline the
    model must learn so it never emits a stray `</think>` or visible
    "Thinking Process:" prose at inference. When `answer` is empty (a turn whose
    only output is a tool call), just the think block is emitted — no dangling
    blank line for the chat template to render."""
    block = f"{THINK_OPEN}\n{reasoning.strip()}\n{THINK_CLOSE}"
    body = answer.strip()
    return f"{block}\n\n{body}" if body else block


def split_thinking(content: str) -> tuple[str | None, str]:
    """Inverse of wrap_thinking: (reasoning_or_None, answer). Tolerant of whitespace."""
    if THINK_OPEN in content and THINK_CLOSE in content:
        pre, rest = content.split(THINK_OPEN, 1)
        reasoning, answer = rest.split(THINK_CLOSE, 1)
        return reasoning.strip(), (pre + answer).strip()
    return None, content.strip()


# ── tool schemas ────────────────────────────────────────────────────────────--
def tool_defs(tools: list[dict]) -> list[dict]:
    """OpenAI/Hermes function defs for the adopt-v0.1 tools — the `tools[]` array
    carried at the top of every trajectory and presented to the model at serve time."""
    out = []
    for t in tools:
        if t.get("status") == "adopt-v0.1" and t.get("name"):
            out.append({"type": "function", "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
            }})
    return out


# ── message builders ──────────────────────────────────────────────────────────
def system_msg(content: str) -> dict:
    return {"role": "system", "content": content}


def user_msg(content: str) -> dict:
    return {"role": "user", "content": content}


def assistant_msg(answer: str | None = None, *, reasoning: str | None = None,
                  tool_calls: list[dict] | None = None) -> dict:
    """Outgoing assistant turn. `reasoning` (if given) is wrapped into `answer`;
    a turn that only calls tools has answer=None and carries `tool_calls`."""
    content: str | None
    if reasoning is not None:
        content = wrap_thinking(reasoning, answer or "")
    else:
        content = answer
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def tool_result(call_id: str, content: str) -> dict:
    """Incoming tool-result turn keyed by the matching tool_call id."""
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def tool_call(call_id: str, name: str, arguments: dict) -> dict:
    """One entry of an assistant turn's `tool_calls[]`. `arguments` is a JSON string
    on the wire (Hermes/OpenAI convention), kept exact and valid-by-construction."""
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


# ── row builders ────────────────────────────────────────────────────────────--
def _uuid(source: str, messages: list[dict]) -> str:
    h = hashlib.sha1(json.dumps(messages, sort_keys=True).encode()).hexdigest()[:12]
    return f"{source}-{h}"


def trajectory_row(messages: list[dict], *, tools: list[dict] | None = None,
                   reasoning: str = "off", source: str = "lamark-synthetic") -> dict:
    """Canonical training row. `tools=None` means a tools-free conversation
    (identity/knowledge/refusal); a populated `tools[]` means an agent trajectory."""
    return {
        "uuid": _uuid(source, messages),
        "source": source,
        "reasoning": reasoning,
        "reducer_version": REDUCER_VERSION,
        "tools": tools,
        "messages": messages,
    }


def qa_row(user: str, answer: str, *, system: str | None = None,
           reasoning_text: str | None = None, source: str = "lamark-qa") -> dict:
    """A single-turn Q&A as a (degenerate) trajectory: optional system, one user
    turn, one assistant turn that may carry a reasoning block. tools=None."""
    msgs = ([system_msg(system)] if system else []) + [
        user_msg(user),
        assistant_msg(answer, reasoning=reasoning_text),
    ]
    return trajectory_row(msgs, tools=None,
                          reasoning="on" if reasoning_text else "off", source=source)


def conversations_row(user: str, answer: str, *, reasoning_text: str | None = None) -> dict:
    """Back-compat `{"conversations":[...]}` row for the TRL prose path (train_sft.py).
    Thinking, when present, is wrapped into the assistant value the same way."""
    value = wrap_thinking(reasoning_text, answer) if reasoning_text else answer
    return {"conversations": [
        {"role": "user", "value": user},
        {"role": "assistant", "value": value},
    ]}


# ── validation ──────────────────────────────────────────────────────────────--
class FormatError(ValueError):
    """Raised when a row violates the canonical contract (caught by validate_rows)."""


def validate_row(row: dict) -> None:
    """Cheap structural checks that catch the failure modes we hit in practice:
    half-open think tags, tool_calls with no matching result, empty assistant turns."""
    if "conversations" in row:
        return  # back-compat rows are validated by their own simpler path
    msgs = row.get("messages")
    if not msgs:
        raise FormatError("empty messages")
    tool_names = [t["function"]["name"] for t in (row.get("tools") or [])]
    open_calls: set[str] = set()
    saw_assistant = False
    for m in msgs:
        role = m.get("role")
        if role == "assistant":
            saw_assistant = True
            content = m.get("content")
            if content:
                n_open, n_close = content.count(THINK_OPEN), content.count(THINK_CLOSE)
                if n_open != n_close:
                    raise FormatError(f"unbalanced think tags ({n_open} open, {n_close} close)")
                # The narration anti-pattern: writing `WebSearch(...)` as prose instead
                # of emitting a tool_call. A grounded refusal that merely names a tool
                # ("the Read tool") is fine — only `Name(` matches.
                for n in tool_names:
                    if re.search(rf"\b{re.escape(n)}\s*\(", content):
                        raise FormatError(f"assistant narrates {n}(...) instead of emitting a tool_call")
            for tc in m.get("tool_calls", []):
                open_calls.add(tc["id"])
                json.loads(tc["function"]["arguments"])  # must be valid JSON
            if content is None and not m.get("tool_calls"):
                raise FormatError("assistant turn with neither content nor tool_calls")
        elif role == "tool":
            cid = m.get("tool_call_id")
            if cid not in open_calls:
                raise FormatError(f"tool result {cid!r} has no matching tool_call")
    if not saw_assistant:
        raise FormatError("no assistant turn to learn from")


def validate_rows(rows: list[dict]) -> list[dict]:
    """Validate every row; raise on the first failure with its index for a fast fix."""
    for i, r in enumerate(rows):
        try:
            validate_row(r)
        except FormatError as e:
            raise FormatError(f"row {i} ({r.get('source', '?')}): {e}") from e
    return rows


def read_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def conversations_to_row(rec: dict, *, source: str = "converted") -> dict:
    """Lift a back-compat `{"conversations":[...]}` record into a canonical
    trajectory row (tools=None). Any `<think>` already embedded in an assistant
    value is detected and the row tagged reasoning="on", so the trainer tokenizes
    it with the matching enable_thinking flag."""
    msgs: list[dict] = []
    reasoning = "off"
    for m in rec["conversations"]:
        role, val = m["role"], m["value"]
        if role == "assistant":
            think, answer = split_thinking(val)
            if think:
                reasoning = "on"
            msgs.append(assistant_msg(answer, reasoning=think))
        elif role == "system":
            msgs.append(system_msg(val))
        else:
            msgs.append(user_msg(val))
    return trajectory_row(msgs, tools=None, reasoning=reasoning, source=source)


def as_canonical(rec: dict, *, source: str = "converted") -> dict:
    """Accept either a canonical trajectory row or a conversations record and
    return a canonical row. Lets the assembler ingest mixed-format inputs."""
    return conversations_to_row(rec, source=source) if "conversations" in rec else rec


def write_jsonl(path: Path, rows: list[dict], *, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[dry-run] {len(rows)} rows -> {path}")
        if rows:
            print("  sample:", json.dumps(rows[0], ensure_ascii=False)[:400])
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"wrote {len(rows)} rows -> {path}")
