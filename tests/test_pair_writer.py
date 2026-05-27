"""Tests for src/lamark/capture/pair_writer.py — verifies that the
Hermes ``on_processing_complete`` hook produces archive records of
the expected shape and provenance.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from lamark.capture.pair_writer import capture_exchange


class _Outcome:
    """Minimal stand-in for gateway.platforms.base.ProcessingOutcome.

    pair_writer reads ``outcome.value`` and treats "success" as the
    only capturable outcome.
    """

    def __init__(self, value: str) -> None:
        self.value = value


@dataclass
class _FakeEvent:
    text: str = ""


def _seed_state_db(path: Path, assistant_reply: str, session_id: str = "telegram:dm:42") -> None:
    """Create a minimal Hermes-shaped state.db with one assistant message
    available for the pair-writer to pick up.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_calls TEXT,
                timestamp REAL DEFAULT 0
            );
            """
        )
        db.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls) VALUES (?, 'user', ?, NULL)",
            (session_id, "(prior user message)"),
        )
        db.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls) VALUES (?, 'assistant', ?, NULL)",
            (session_id, assistant_reply),
        )


def _read_archive(archive_root: Path) -> list[dict]:
    out: list[dict] = []
    for shard in sorted((archive_root / "incoming").glob("*.jsonl")):
        for line in shard.read_text().splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _run(coro):
    return asyncio.run(coro)


def test_success_captures_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path — a SUCCESS outcome appends one ChatML pair to the archive."""
    monkeypatch.setenv("LAMARK_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    _seed_state_db(tmp_path / "hermes-home" / "state.db", "Hi! Lamark here.")

    _run(capture_exchange(_FakeEvent(text="hello"), _Outcome("success")))

    pairs = _read_archive(tmp_path / "archive")
    assert len(pairs) == 1
    rec = pairs[0]
    assert rec["messages"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi! Lamark here."},
    ]
    assert rec["meta"]["source"] == "user_explicit"
    assert rec["meta"]["confidence"] == 0.5
    assert rec["meta"]["evidence_path"] == "telegram:telegram:dm:42"


def test_failure_outcome_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAMARK_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    _seed_state_db(tmp_path / "hermes-home" / "state.db", "(would-be reply)")

    _run(capture_exchange(_FakeEvent(text="hello"), _Outcome("failure")))
    _run(capture_exchange(_FakeEvent(text="hello"), _Outcome("cancelled")))

    assert _read_archive(tmp_path / "archive") == []


def test_slash_command_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Commands like /start, /help, /stop should never become training data."""
    monkeypatch.setenv("LAMARK_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    _seed_state_db(tmp_path / "hermes-home" / "state.db", "Started!")

    _run(capture_exchange(_FakeEvent(text="/start"), _Outcome("success")))
    _run(capture_exchange(_FakeEvent(text="  /help"), _Outcome("success")))

    assert _read_archive(tmp_path / "archive") == []


def test_empty_user_text_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAMARK_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    _seed_state_db(tmp_path / "hermes-home" / "state.db", "anything")

    _run(capture_exchange(_FakeEvent(text=""), _Outcome("success")))
    _run(capture_exchange(_FakeEvent(text="   "), _Outcome("success")))

    assert _read_archive(tmp_path / "archive") == []


def test_missing_state_db_no_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If state.db doesn't exist, capture is a no-op (doesn't raise)."""
    monkeypatch.setenv("LAMARK_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    # deliberately no _seed_state_db call

    _run(capture_exchange(_FakeEvent(text="hello"), _Outcome("success")))

    assert _read_archive(tmp_path / "archive") == []


def test_tool_call_assistant_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Assistant turns that are pure tool-calls (no user-facing content)
    must not become training pairs — content would be empty / not what
    the user actually saw as the reply."""
    monkeypatch.setenv("LAMARK_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    db_path = tmp_path / "hermes-home" / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_calls TEXT,
                timestamp REAL DEFAULT 0
            );
            """
        )
        # only entry is a tool-call assistant turn (no plain reply yet)
        db.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls) VALUES "
            "(?, 'assistant', ?, ?)",
            ("s1", "", '[{"name":"web_search","args":{"q":"foo"}}]'),
        )

    _run(capture_exchange(_FakeEvent(text="search for foo"), _Outcome("success")))

    assert _read_archive(tmp_path / "archive") == []
