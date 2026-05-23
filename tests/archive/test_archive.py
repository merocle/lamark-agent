"""
Test Module 11 — JSONL training-data archive.

Per v4 §P1 "Data persists, models rotate" — this is the durable asset.
ChatML messages + sidecar metadata, append-only JSONL, atomic via
temp-file+rename, crash-safe fsync per shard.

Phase 1a ships JSONL-only. Parquet compaction + SQLite FTS5 deferred
to Phase 2 (per Critic 3 "premature optimization" finding).

Schema (v1):
{
  "messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
  "meta": {
    "id": "uuid",
    "source": "bootstrap | user_explicit | agent_self_edit | imported",
    "captured_at": "ISO8601 UTC",
    "redaction": {"events": [{"pattern_id": "...", "substituted_token": "..."}]},
    "confidence": 0.0-1.0,
    "evidence_path": "...",
    "consumed_by": [],   // LoRA versions trained on this; populated later
    "eval_score": {},     // {lora_version: float}; populated by Phase 2
    "sensitivity": "public | personal | confidential | secret"  // Phase 1b extension
  },
  "schema_version": 1
}
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest


@pytest.fixture
def archive(isolated_lamark_home: Path):
    """Fresh Archive instance rooted under isolated home."""
    from lamark.archive import Archive

    return Archive.open(isolated_lamark_home / "archive")


# ---- write path ----------------------------------------------------------


def test_write_creates_jsonl_in_correct_dated_shard(archive, isolated_lamark_home: Path) -> None:
    """First write lands in archive/incoming/YYYY-MM-DD.jsonl matching today's UTC date."""
    archive.write_pair(
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
        source="user_explicit",
        confidence=0.95,
    )

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    shard = isolated_lamark_home / "archive" / "incoming" / f"{today}.jsonl"
    assert shard.exists(), f"expected shard {shard} to exist"
    lines = shard.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_record_has_required_metadata_fields(archive) -> None:
    """Every record carries id, source, captured_at, confidence, schema_version."""
    archive.write_pair(
        messages=[{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}],
        source="imported",
        confidence=0.85,
        evidence_path="chatgpt_export:conv-123",
    )
    records = list(archive.read_all())
    assert len(records) == 1
    rec = records[0]
    assert rec["schema_version"] == 1
    assert "messages" in rec
    assert "meta" in rec
    meta = rec["meta"]
    assert "id" in meta
    assert meta["source"] == "imported"
    assert "captured_at" in meta
    assert meta["confidence"] == 0.85
    assert meta["evidence_path"] == "chatgpt_export:conv-123"
    assert meta["consumed_by"] == []
    assert meta["eval_score"] == {}


def test_id_is_unique_per_record(archive) -> None:
    """Two writes produce two distinct UUIDs."""
    archive.write_pair(
        messages=[{"role": "user", "content": "a"}],
        source="imported",
        confidence=0.5,
    )
    archive.write_pair(
        messages=[{"role": "user", "content": "b"}],
        source="imported",
        confidence=0.5,
    )
    ids = {r["meta"]["id"] for r in archive.read_all()}
    assert len(ids) == 2


def test_provenance_validation_on_write(archive) -> None:
    """Invalid source rejected at write time, same enum as MemoryStore."""
    with pytest.raises(ValueError, match="source|provenance"):
        archive.write_pair(
            messages=[{"role": "user", "content": "x"}],
            source="from_a_psychic",  # not in VALID_PROVENANCE
            confidence=0.5,
        )


def test_confidence_unit_interval_validation(archive) -> None:
    """Confidence outside [0,1] rejected."""
    with pytest.raises(ValueError, match="confidence"):
        archive.write_pair(
            messages=[{"role": "user", "content": "x"}],
            source="imported",
            confidence=1.5,
        )


def test_messages_must_be_chatml_role_content(archive) -> None:
    """Each message needs role + content keys, no embedded special tokens."""
    with pytest.raises((ValueError, KeyError, TypeError)):
        archive.write_pair(
            messages=[{"from": "user", "value": "hi"}],  # ShareGPT shape, not ChatML
            source="imported",
            confidence=0.5,
        )


# ---- crash safety / atomicity --------------------------------------------


def test_write_is_atomic_fsync(archive, isolated_lamark_home: Path) -> None:
    """After write_pair returns, the line is durable on disk (we fsync).

    We can't easily simulate kernel crashes in unit tests; we verify the
    write reaches disk by re-opening the archive and reading.
    """
    archive.write_pair(
        messages=[{"role": "user", "content": "durable"}],
        source="imported",
        confidence=0.5,
    )

    # Re-open from disk
    from lamark.archive import Archive

    archive2 = Archive.open(isolated_lamark_home / "archive")
    records = list(archive2.read_all())
    assert len(records) == 1
    assert records[0]["messages"][0]["content"] == "durable"


def test_appends_to_existing_shard(archive) -> None:
    """Multiple writes on the same day append to the same shard, no duplicates."""
    for i in range(5):
        archive.write_pair(
            messages=[{"role": "user", "content": f"msg {i}"}],
            source="imported",
            confidence=0.5,
        )
    records = list(archive.read_all())
    assert len(records) == 5
    contents = [r["messages"][0]["content"] for r in records]
    assert contents == [f"msg {i}" for i in range(5)]


# ---- read path -----------------------------------------------------------


def test_read_all_returns_records_in_write_order(archive) -> None:
    """Iteration order matches write order within a shard."""
    for i in range(3):
        archive.write_pair(
            messages=[{"role": "user", "content": f"#{i}"}],
            source="imported",
            confidence=0.5,
        )
    records = list(archive.read_all())
    assert [r["messages"][0]["content"] for r in records] == ["#0", "#1", "#2"]


def test_read_all_handles_empty_archive(archive) -> None:
    """No shards yet → empty iterator."""
    assert list(archive.read_all()) == []


def test_count_records(archive) -> None:
    """count() returns total records across all shards."""
    for i in range(7):
        archive.write_pair(
            messages=[{"role": "user", "content": str(i)}],
            source="imported",
            confidence=0.5,
        )
    assert archive.count() == 7


# ---- single-message writes (for facts that aren't conversation pairs) ----


def test_write_single_message_records_user_role_only(archive) -> None:
    """For Obsidian-style imports (no assistant pair), a single user message is ok."""
    archive.write_pair(
        messages=[{"role": "user", "content": "lone fact"}],
        source="imported",
        confidence=0.85,
    )
    records = list(archive.read_all())
    assert len(records) == 1
    assert records[0]["messages"] == [{"role": "user", "content": "lone fact"}]


# ---- redaction event log -------------------------------------------------


def test_redaction_events_captured_in_metadata(archive) -> None:
    """When archive writes a redacted pair, the events log goes into meta.redaction."""
    archive.write_pair(
        messages=[{"role": "user", "content": "email me at <email>"}],
        source="imported",
        confidence=0.85,
        redaction_events=[
            {"pattern_id": "email", "substituted_token": "<email>", "start": 12, "end": 19}
        ],
    )
    records = list(archive.read_all())
    assert "redaction" in records[0]["meta"]
    events = records[0]["meta"]["redaction"]["events"]
    assert len(events) == 1
    assert events[0]["pattern_id"] == "email"
