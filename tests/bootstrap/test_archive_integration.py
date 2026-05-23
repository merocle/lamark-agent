"""
Test Module 11b — wizard + importers dual-write to Fact table AND archive.

Per v4 §"Phase 1a optimized plan": both subsystems are populated so Phase 2
LoRA training has the archive ready when it activates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def store_and_archive(isolated_lamark_home: Path):
    from lamark.archive import Archive
    from lamark.memory import MemoryStore

    store = MemoryStore.open(isolated_lamark_home / "honcho.db")
    archive = Archive.open(isolated_lamark_home / "archive")
    yield store, archive
    store.close()


def test_wizard_dual_writes_to_store_and_archive(store_and_archive) -> None:
    """Bootstrap wizard writes facts to MemoryStore AND archive."""
    from lamark.bootstrap.wizard import run_bootstrap

    store, archive = store_and_archive
    run_bootstrap(
        store=store,
        name="Anna",
        locale="ru-RU",
        role="ML engineer, lives in Berlin",
        interactive=False,
        archive=archive,
    )
    # Same count both places (name + locale + 2 role chunks = 4)
    assert store.count_facts() == 4
    assert archive.count() == 4

    # Archive records have ChatML user-only messages
    records = list(archive.read_all())
    for r in records:
        assert len(r["messages"]) == 1
        assert r["messages"][0]["role"] == "user"
        assert r["meta"]["source"] == "user_explicit"
        assert r["meta"]["evidence_path"].startswith("bootstrap-wizard:")


def test_chatgpt_importer_dual_writes(store_and_archive, tmp_path: Path) -> None:
    """ChatGPT importer writes to both."""
    from lamark.bootstrap.importers.chatgpt import import_chatgpt_export

    store, archive = store_and_archive
    conv = {
        "title": "x",
        "mapping": {
            "n0": {
                "id": "n0",
                "parent": None,
                "children": [],
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["I love filter coffee"]},
                },
            },
        },
    }
    export = tmp_path / "conversations.json"
    export.write_text(json.dumps([conv]), encoding="utf-8")

    import_chatgpt_export(store, export, archive=archive)
    assert store.count_facts() == 1
    assert archive.count() == 1
    rec = next(iter(archive.read_all()))
    assert rec["meta"]["source"] == "imported"
    assert rec["meta"]["evidence_path"] == "chatgpt_export"


def test_obsidian_importer_dual_writes(store_and_archive, tmp_path: Path) -> None:
    """Obsidian importer writes to both."""
    from lamark.bootstrap.importers.obsidian import import_obsidian_vault

    store, archive = store_and_archive
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "Note.md").write_text("# Note\n\nA note content.\n", encoding="utf-8")

    import_obsidian_vault(store, vault, archive=archive)
    assert store.count_facts() == 1
    assert archive.count() == 1
    rec = next(iter(archive.read_all()))
    assert rec["meta"]["source"] == "imported"
    assert rec["meta"]["evidence_path"].startswith("obsidian:")


def test_archive_not_required_for_backward_compat(store_and_archive, tmp_path: Path) -> None:
    """If archive is None, wizard/importers still work (write to store only)."""
    from lamark.bootstrap.wizard import run_bootstrap

    store, _ = store_and_archive
    run_bootstrap(store=store, name="Anna", interactive=False)  # no archive
    assert store.count_facts() == 1  # name only


def test_cli_bootstrap_populates_both(cli_runner, isolated_lamark_home: Path) -> None:
    """`lamark bootstrap` CLI wires both store and archive."""
    from lamark.cli import app

    result = cli_runner.invoke(app, ["bootstrap", "--name", "Anna", "--role", "engineer"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"

    from lamark.archive import Archive
    from lamark.memory import MemoryStore

    store = MemoryStore.open(isolated_lamark_home / "honcho.db")
    archive = Archive.open(isolated_lamark_home / "archive")
    try:
        assert store.count_facts() == 2  # name + role
        assert archive.count() == 2
    finally:
        store.close()
