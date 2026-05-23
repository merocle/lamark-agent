"""
Archive — append-only JSONL training-data store.

Schema v1:
{
  "messages": [{"role": "<user|assistant|system|tool>", "content": "..."}],
  "meta": {
    "id": "uuid4",
    "source": "<bootstrap | user_explicit | agent_self_edit | imported>",
    "captured_at": "ISO8601 UTC",
    "confidence": 0.0-1.0,
    "evidence_path": str | None,
    "redaction": {"events": [...]} | absent,
    "sensitivity": "<public|personal|confidential|secret>" | absent (Phase 1b),
    "consumed_by": [],
    "eval_score": {}
  },
  "schema_version": 1
}

Crash safety: each line is flushed and fsync'd to disk before write_pair() returns.
We use append-only writes to a per-day shard so partial writes are bounded to one line.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from lamark.memory.schema import VALID_PROVENANCE

SCHEMA_VERSION = 1
VALID_ROLES = frozenset({"user", "assistant", "system", "tool"})


@dataclass(frozen=True)
class ArchiveRecord:
    """Convenience wrapper around the dict layout (for type hints)."""

    messages: list[dict[str, str]]
    meta: dict[str, Any]


class Archive:
    """Append-only JSONL training-data archive."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._incoming = root / "incoming"
        self._incoming.mkdir(parents=True, exist_ok=True)

    @classmethod
    def open(cls, root: Path | str) -> Archive:
        return cls(Path(root))

    @property
    def incoming_dir(self) -> Path:
        return self._incoming

    # -- write ------------------------------------------------------------

    def write_pair(
        self,
        messages: list[dict[str, str]],
        *,
        source: str,
        confidence: float,
        evidence_path: str | None = None,
        redaction_events: list[dict[str, Any]] | None = None,
        sensitivity: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Append one ChatML-shaped record to today's shard.

        Returns the full record dict (with generated id and timestamp).

        Raises:
            ValueError: invalid source, confidence, or message shape.
            TypeError: messages is not a list / dicts shape wrong.
        """
        self._validate_source(source)
        self._validate_confidence(confidence)
        self._validate_messages(messages)

        if now is None:
            now = datetime.now(UTC)

        record: dict[str, Any] = {
            "messages": messages,
            "meta": {
                "id": str(uuid.uuid4()),
                "source": source,
                "captured_at": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "confidence": confidence,
                "evidence_path": evidence_path,
                "consumed_by": [],
                "eval_score": {},
            },
            "schema_version": SCHEMA_VERSION,
        }
        if redaction_events:
            record["meta"]["redaction"] = {"events": list(redaction_events)}
        if sensitivity:
            record["meta"]["sensitivity"] = sensitivity

        shard = self._shard_for_date(now)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        self._append_durably(shard, line)
        return record

    @staticmethod
    def _validate_source(source: str) -> None:
        if source not in VALID_PROVENANCE:
            raise ValueError(
                f"unknown provenance source {source!r}; "
                f"must be one of {sorted(VALID_PROVENANCE)}"
            )

    @staticmethod
    def _validate_confidence(c: float) -> None:
        if not isinstance(c, (int, float)):
            raise TypeError(f"confidence must be float, got {type(c).__name__}")
        if not (0.0 <= float(c) <= 1.0):
            raise ValueError(
                f"confidence {c!r} outside [0.0, 1.0] — refusing to write"
            )

    @staticmethod
    def _validate_messages(messages: Any) -> None:
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages must be a non-empty list")
        for i, m in enumerate(messages):
            if not isinstance(m, dict):
                raise TypeError(f"messages[{i}] must be a dict, got {type(m).__name__}")
            if "role" not in m or "content" not in m:
                raise ValueError(
                    f"messages[{i}] must have ChatML shape (role, content); "
                    f"got keys {sorted(m.keys())}. ShareGPT (from, value) is rejected — "
                    "convert before write."
                )
            if m["role"] not in VALID_ROLES:
                raise ValueError(
                    f"messages[{i}].role={m['role']!r} not in {sorted(VALID_ROLES)}"
                )
            if not isinstance(m["content"], str):
                raise TypeError(
                    f"messages[{i}].content must be str, got {type(m['content']).__name__}"
                )

    def _shard_for_date(self, when: datetime) -> Path:
        date_part = when.astimezone(UTC).strftime("%Y-%m-%d")
        return self._incoming / f"{date_part}.jsonl"

    @staticmethod
    def _append_durably(shard: Path, line: str) -> None:
        """Append + flush + fsync. Bounds crash damage to one line."""
        # We do NOT use temp-file+rename for *append* — that would require
        # reading the whole shard. Append is atomic at the byte level on POSIX
        # for writes ≤ PIPE_BUF, but JSONL lines often exceed that. We rely on
        # the fsync + single-write semantics: if the process crashes mid-write,
        # the line is partial; readers handle this by skipping malformed lines.
        # For Phase 2 we'll move compaction to Parquet which has stronger atomicity.
        with open(shard, "a", encoding="utf-8", buffering=1) as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    # -- read -------------------------------------------------------------

    def read_all(self) -> Iterator[dict[str, Any]]:
        """Yield every record across all shards, in write order within each shard."""
        if not self._incoming.exists():
            return
        for shard in sorted(self._incoming.glob("*.jsonl")):
            try:
                with shard.open(encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            # Skip malformed (e.g. partial-write) lines.
                            continue
            except OSError:
                continue

    def count(self) -> int:
        return sum(1 for _ in self.read_all())
