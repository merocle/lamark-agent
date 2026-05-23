"""
Test Module 8c — Obsidian vault importer.

Obsidian vaults are directory trees of Markdown files plus an .obsidian/ config
dir and (optionally) an attachments folder. We walk *.md, skip Obsidian's own
metadata, parse the title (first H1 or filename), keep the first ~500 chars
of body as the fact text, run through redaction, write atomically.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def obsidian_vault(tmp_path: Path) -> Path:
    """Synthesize a small vault for tests."""
    vault = tmp_path / "vault"
    vault.mkdir()

    # Top-level note
    (vault / "Daily Routine.md").write_text(
        "# Daily Routine\n\n"
        "Wake at 7. Coffee. Read papers until 8:30. Standup at 9.\n",
        encoding="utf-8",
    )

    # Nested folder
    nested = vault / "projects"
    nested.mkdir()
    (nested / "Lamark.md").write_text(
        "# Lamark agent\n\n"
        "Personal AI on DGX Spark. Qwen3.6-35B-A3B primary.\n",
        encoding="utf-8",
    )

    # Obsidian config — must be skipped
    obs = vault / ".obsidian"
    obs.mkdir()
    (obs / "workspace.json").write_text('{"main":{}}', encoding="utf-8")
    (obs / "app.json").write_text("{}", encoding="utf-8")

    # Attachments folder — must be skipped
    att = vault / "attachments"
    att.mkdir()
    (att / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
    (att / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0FAKE")

    # Trash — must be skipped
    trash = vault / ".trash"
    trash.mkdir()
    (trash / "old.md").write_text("# obsolete\n\nold note\n", encoding="utf-8")

    # Empty .md — should be skipped
    (vault / "Empty.md").write_text("", encoding="utf-8")

    return vault


@pytest.fixture
def fresh_store(isolated_lamark_home: Path):
    from lamark.memory import MemoryStore

    s = MemoryStore.open(isolated_lamark_home / "honcho.db")
    yield s
    s.close()


# ---- walker tests ---------------------------------------------------------


def test_walker_finds_md_files_only(obsidian_vault: Path) -> None:
    """Walker yields *.md files; PNG/JPG attachments and config are skipped."""
    from lamark.bootstrap.importers.obsidian import walk_vault

    paths = sorted(p.relative_to(obsidian_vault).as_posix() for p in walk_vault(obsidian_vault))
    # 'Empty.md' is in directory listing but should NOT come back from the walker
    # since it's empty (the walker filters empties).
    assert "Daily Routine.md" in paths
    assert "projects/Lamark.md" in paths
    # No .obsidian, no .trash, no attachments
    for p in paths:
        assert not p.startswith(".obsidian")
        assert not p.startswith(".trash")
        assert not p.startswith("attachments")


def test_walker_skips_empty_files(obsidian_vault: Path) -> None:
    """Empty Markdown files are noise — not yielded."""
    from lamark.bootstrap.importers.obsidian import walk_vault

    paths = [p.name for p in walk_vault(obsidian_vault)]
    assert "Empty.md" not in paths


def test_walker_caps_oversize_files(tmp_path: Path) -> None:
    """A massive .md (over max_bytes) is skipped (probably an export dump)."""
    from lamark.bootstrap.importers.obsidian import walk_vault

    vault = tmp_path / "big"
    vault.mkdir()
    (vault / "ok.md").write_text("# small\n\nnote\n", encoding="utf-8")
    (vault / "huge.md").write_text("x" * (2_000_000), encoding="utf-8")  # 2MB
    paths = [p.name for p in walk_vault(vault, max_bytes=200_000)]
    assert "ok.md" in paths
    assert "huge.md" not in paths


# ---- importer tests -------------------------------------------------------


def test_import_creates_one_fact_per_md_file(fresh_store, obsidian_vault: Path) -> None:
    """One fact per non-empty .md file."""
    from lamark.bootstrap.importers.obsidian import import_obsidian_vault

    result = import_obsidian_vault(fresh_store, obsidian_vault)
    assert result.files_processed == 2  # Daily Routine + projects/Lamark, Empty.md skipped
    assert result.facts_added == 2


def test_imported_facts_have_imported_provenance(fresh_store, obsidian_vault: Path) -> None:
    from lamark.bootstrap.importers.obsidian import import_obsidian_vault
    from lamark.memory import PROVENANCE_IMPORTED

    import_obsidian_vault(fresh_store, obsidian_vault)
    from sqlalchemy import select

    from lamark.memory.schema import Fact

    with fresh_store._sessionmaker() as s:  # type: ignore[attr-defined]
        sources = {f.source for f in s.scalars(select(Fact))}
    assert sources == {PROVENANCE_IMPORTED}


def test_import_truncates_long_files(tmp_path: Path, fresh_store) -> None:
    """Long notes truncated to max_chars in the fact text (default 500)."""
    from lamark.bootstrap.importers.obsidian import import_obsidian_vault

    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "Long.md").write_text("# Long\n\n" + ("word " * 500), encoding="utf-8")
    import_obsidian_vault(fresh_store, vault, max_chars=200)

    from sqlalchemy import select
    from lamark.memory.schema import Fact

    with fresh_store._sessionmaker() as s:  # type: ignore[attr-defined]
        facts = list(s.scalars(select(Fact)))
    assert len(facts) == 1
    assert len(facts[0].text) <= 220  # max_chars + ellipsis marker tolerance


def test_import_redacts_pii_in_vault(tmp_path: Path, fresh_store) -> None:
    """PII inside notes is replaced before write."""
    from lamark.bootstrap.importers.obsidian import import_obsidian_vault

    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "Contacts.md").write_text(
        "# Contacts\n\nMy email is alice@example.com and my phone is +1 555 0100.\n",
        encoding="utf-8",
    )
    import_obsidian_vault(fresh_store, vault)

    from sqlalchemy import select
    from lamark.memory.schema import Fact

    with fresh_store._sessionmaker() as s:  # type: ignore[attr-defined]
        text = " ".join(f.text for f in s.scalars(select(Fact)))
    assert "alice@example.com" not in text
    assert "555 0100" not in text


def test_secret_in_vault_halts_entire_import(tmp_path: Path, fresh_store) -> None:
    """A verified secret in ANY note halts the whole import atomically."""
    from lamark.bootstrap.importers.obsidian import import_obsidian_vault
    from lamark.redaction import SecretFound

    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "Safe.md").write_text("# Safe\n\nordinary text\n", encoding="utf-8")
    (vault / "Leaky.md").write_text(
        "# Leaky\n\nOPENAI_API_KEY=sk-proj-aaaabbbbccccddddeeeeffffgggghhhhiiiijjjjkkkkllll\n",
        encoding="utf-8",
    )
    with pytest.raises(SecretFound):
        import_obsidian_vault(fresh_store, vault)

    assert fresh_store.count_facts() == 0  # nothing landed, even Safe.md


def test_cli_bootstrap_with_obsidian_flag(
    cli_runner, isolated_lamark_home: Path, obsidian_vault: Path
) -> None:
    """`lamark bootstrap --name X --obsidian path` works."""
    from lamark.cli import app

    result = cli_runner.invoke(
        app,
        ["bootstrap", "--name", "Anna", "--obsidian", str(obsidian_vault)],
    )
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    from lamark.memory import MemoryStore

    store = MemoryStore.open(isolated_lamark_home / "honcho.db")
    try:
        # Wizard seeds: name (1) = 1 wizard fact
        # Obsidian import: 2 .md files
        # Total: 3
        assert store.count_facts() == 3
    finally:
        store.close()
