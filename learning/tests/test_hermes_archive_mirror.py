"""
Test Plan A.4 — every successful memory_tool add/replace in vendored Hermes
mirrors into the Lamark training-data archive for Phase 2 LoRA curation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

VENDOR_HERMES = Path(__file__).resolve().parent.parent / "vendor" / "hermes"


@pytest.fixture(autouse=True)
def _hermes_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Put vendor/hermes/ on sys.path with a clean LAMARK_HOME + HERMES_HOME."""
    monkeypatch.setenv("LAMARK_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.syspath_prepend(str(VENDOR_HERMES))
    # Force fresh imports so memory_tool reads our patched module
    for mod in list(sys.modules):
        if mod.startswith(("hermes_", "tools.memory_tool", "lamark.archive")):
            del sys.modules[mod]


def _make_store(tmp_path: Path):
    """Build a real Hermes MemoryStore (uses HERMES_HOME for memory dir)."""
    from tools.memory_tool import MemoryStore

    return MemoryStore()


def test_memory_add_mirrors_to_archive(tmp_path: Path) -> None:
    """memory_tool add → record lands in archive with source=agent_self_edit."""
    from tools.memory_tool import memory_tool

    store = _make_store(tmp_path)
    out = memory_tool(action="add", target="user", content="user prefers tea", store=store)
    assert "success" in out  # serialized JSON

    from lamark.archive import Archive

    archive = Archive.open(tmp_path / "archive")
    records = list(archive.read_all())
    assert len(records) == 1, f"expected 1 mirror record, got {len(records)}"
    rec = records[0]
    assert rec["meta"]["source"] == "agent_self_edit"
    assert rec["meta"]["evidence_path"] == "hermes_memory_tool:user:add"
    assert rec["messages"][0]["content"] == "user prefers tea"


def test_memory_replace_mirrors_to_archive(tmp_path: Path) -> None:
    """memory_tool replace → new content lands in archive."""
    from tools.memory_tool import memory_tool

    store = _make_store(tmp_path)
    memory_tool(action="add", target="memory", content="loves coffee", store=store)
    memory_tool(
        action="replace",
        target="memory",
        old_text="loves coffee",
        content="loves filter coffee with milk",
        store=store,
    )

    from lamark.archive import Archive

    archive = Archive.open(tmp_path / "archive")
    records = list(archive.read_all())
    contents = [r["messages"][0]["content"] for r in records]
    assert "loves coffee" in contents
    assert "loves filter coffee with milk" in contents


def test_memory_remove_does_not_mirror(tmp_path: Path) -> None:
    """remove reduces signal — not archived."""
    from tools.memory_tool import memory_tool

    store = _make_store(tmp_path)
    memory_tool(action="add", target="memory", content="ephemeral fact", store=store)
    memory_tool(
        action="remove",
        target="memory",
        old_text="ephemeral fact",
        store=store,
    )

    from lamark.archive import Archive

    archive = Archive.open(tmp_path / "archive")
    # Only the add was mirrored; remove not mirrored
    records = list(archive.read_all())
    assert len(records) == 1
    assert records[0]["messages"][0]["content"] == "ephemeral fact"


def test_archive_failure_does_not_break_memory_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If archive write raises, the memory tool still reports success.

    Simulate by stomping LAMARK_HOME to an unwritable path mid-test.
    """
    from tools.memory_tool import memory_tool

    store = _make_store(tmp_path)
    # Point archive at an existing FILE (not a dir) so Archive.open fails
    poison = tmp_path / "poison_file"
    poison.write_text("not a dir")
    monkeypatch.setenv("LAMARK_HOME", str(poison))

    out = memory_tool(action="add", target="memory", content="resilient write", store=store)
    # Memory itself succeeded — archive failure is swallowed
    assert '"success": true' in out or '"success":true' in out
