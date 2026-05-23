"""
ChatGPT export importer.

The export is a `conversations.json` with shape:

    [
        {
            "title": "...",
            "create_time": float,
            "mapping": {
                "<node_id>": {
                    "id": "<node_id>",
                    "parent": "<node_id> | null",
                    "children": ["<node_id>", ...],
                    "message": {
                        "id": "<node_id>",
                        "author": {"role": "user|assistant|system|tool"},
                        "content": {"content_type": "text", "parts": ["..."]},
                        "create_time": float
                    }
                },
                ...
            }
        },
        ...
    ]

We walk all user messages, run each through the redaction pipeline, then
write each as a Fact with PROVENANCE_IMPORTED. If ANY message contains a
verified secret, we halt the whole import — no partial writes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lamark.memory import MemoryStore, PROVENANCE_IMPORTED
from lamark.redaction import RedactionPipeline


@dataclass(frozen=True)
class ImportResult:
    facts_added: int
    conversations_processed: int
    messages_seen: int


def _user_messages_from_mapping(mapping: dict) -> list[str]:
    """Extract every text part from messages with author.role == 'user'."""
    out: list[str] = []
    for _node_id, node in mapping.items():
        msg = node.get("message")
        if not msg:
            continue
        author = (msg.get("author") or {}).get("role")
        if author != "user":
            continue
        content = msg.get("content") or {}
        if content.get("content_type") != "text":
            continue
        for part in content.get("parts") or []:
            if isinstance(part, str) and part.strip():
                out.append(part)
    return out


def import_chatgpt_export(
    store: MemoryStore,
    export_path: Path | str,
    *,
    deny_phrases: list[str] | None = None,
) -> ImportResult:
    """Read a ChatGPT export and persist redacted user messages as Facts.

    Atomicity: a verified-secret hit in any message raises SecretFound BEFORE
    any DB writes. Plain PII is substituted in place per the redaction pipeline.

    Args:
        store: open MemoryStore (UserModel should exist first).
        export_path: path to `conversations.json`.
        deny_phrases: optional caller-supplied phrases that should also halt
            the import (internal codenames, customer names, etc.).

    Returns:
        ImportResult.
    """
    path = Path(export_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"expected ChatGPT export to be a list, got {type(data).__name__}")

    pipe = RedactionPipeline(deny_phrases=deny_phrases)

    # Phase 1: gather + redact in memory (atomic). Will raise SecretFound here
    # before we touch the DB if anything's leaky.
    prepared: list[str] = []
    n_conversations = 0
    n_messages = 0
    for conv in data:
        if not isinstance(conv, dict):
            continue
        mapping = conv.get("mapping") or {}
        if not isinstance(mapping, dict):
            continue
        user_msgs = _user_messages_from_mapping(mapping)
        n_conversations += 1
        n_messages += len(user_msgs)
        for raw in user_msgs:
            redacted = pipe.process(raw)
            prepared.append(redacted.text)

    # Phase 2: now that we know the whole file is safe, commit.
    for text in prepared:
        store.add_fact(
            text=text,
            source=PROVENANCE_IMPORTED,
            confidence=0.85,  # accumulated history — confident but not authoritative
            evidence="chatgpt_export",
        )

    return ImportResult(
        facts_added=len(prepared),
        conversations_processed=n_conversations,
        messages_seen=n_messages,
    )
