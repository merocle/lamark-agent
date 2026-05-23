"""Lamark training-data archive (v4 §P1 "Data persists, models rotate").

Phase 1a: append-only JSONL shards under `<home>/incoming/YYYY-MM-DD.jsonl`,
schema documented in `tests/archive/test_archive.py`.

Phase 2 will add:
- Parquet compaction `<home>/compacted/YYYY-MM.parquet`
- SQLite FTS5 index `<home>/index.db` for lineage + lookup
"""

from __future__ import annotations

from lamark.archive.store import Archive, ArchiveRecord

__all__ = ["Archive", "ArchiveRecord"]
