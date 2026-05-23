"""
Test Module 8 — bootstrap wizard (post v4 refactor).

The wizard's job: take Lamark from "knows nothing about user" → "has 5-20
seed Facts in the archive" in 15-20 minutes, closing the cold-start gap
that other memory-layer assistants suffer through their first weeks.

v4 dropped UserModel; the wizard now writes Facts only, with
`source=user_explicit` and `evidence` prefixed `bootstrap-wizard:` so a
force-reset can wipe them via delete_fact_where().
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fresh_store(isolated_lamark_home: Path):
    """A fresh MemoryStore in the isolated home."""
    from lamark.memory import MemoryStore

    store = MemoryStore.open(isolated_lamark_home / "honcho.db")
    yield store
    store.close()


def _input_stream(*lines: str) -> str:
    """Helper: build a newline-joined input string for typer CliRunner."""
    return "\n".join(lines) + "\n"


# ---- non-interactive path -------------------------------------------------


def test_run_with_explicit_args_writes_facts(fresh_store) -> None:
    """Non-interactive call writes Facts (no UserModel anymore)."""
    from lamark.bootstrap.wizard import run_bootstrap

    result = run_bootstrap(
        store=fresh_store,
        name="Anna",
        locale="ru-RU",
        role="senior engineer at X, Kotlin/Android",
        style={"length": "short", "emojis": False, "markdown": True},
        interactive=False,
    )
    # name (1) + locale (1) + role chunks (2) + style (1) = 5 facts
    assert result.facts_added == 5
    assert fresh_store.count_facts() == 5


def test_wizard_uses_user_explicit_provenance(fresh_store) -> None:
    """Every wizard-written fact has source=user_explicit and evidence prefix."""
    from sqlalchemy import select
    from lamark.bootstrap.wizard import WIZARD_EVIDENCE_PREFIX, run_bootstrap
    from lamark.memory import PROVENANCE_USER_EXPLICIT
    from lamark.memory.schema import Fact

    run_bootstrap(store=fresh_store, name="Anna", interactive=False)

    with fresh_store._sessionmaker() as s:  # type: ignore[attr-defined]
        facts = list(s.scalars(select(Fact)))
    assert facts, "wizard must write at least one fact"
    for f in facts:
        assert f.source == PROVENANCE_USER_EXPLICIT
        assert f.evidence is not None
        assert f.evidence.startswith(WIZARD_EVIDENCE_PREFIX)


def test_rerun_refuses_without_force(fresh_store) -> None:
    """If wizard already ran, a second run refuses unless force=True."""
    from lamark.bootstrap.wizard import run_bootstrap

    run_bootstrap(store=fresh_store, name="Anna", interactive=False)
    with pytest.raises(RuntimeError, match="already|force"):
        run_bootstrap(store=fresh_store, name="Boris", interactive=False)


def test_force_reset_wipes_wizard_facts_only(fresh_store) -> None:
    """force=True deletes facts with the wizard evidence prefix, then re-seeds.

    Importantly: non-wizard facts (e.g. ChatGPT imports) should survive.
    """
    from lamark.bootstrap.wizard import run_bootstrap

    # Seed via wizard
    run_bootstrap(store=fresh_store, name="Anna", role="dev", interactive=False)
    # Add a non-wizard fact (e.g. would-be import)
    fresh_store.add_fact(
        text="loves filter coffee",
        source="imported",
        confidence=0.85,
        evidence="chatgpt_export",
    )
    count_before = fresh_store.count_facts()
    assert count_before > 1

    # Force re-bootstrap
    run_bootstrap(store=fresh_store, name="Boris", role="designer", interactive=False, force=True)

    from sqlalchemy import select
    from lamark.memory.schema import Fact

    with fresh_store._sessionmaker() as s:  # type: ignore[attr-defined]
        all_texts = [f.text for f in s.scalars(select(Fact))]

    # Non-wizard fact survives
    assert any("filter coffee" in t for t in all_texts), (
        f"non-wizard import must survive force-reset; texts={all_texts}"
    )
    # New identity is present
    assert any("Boris" in t for t in all_texts)
    # Old identity gone
    assert not any("Anna" in t for t in all_texts)


# ---- interactive path via CLI --------------------------------------------


def test_cli_bootstrap_interactive_collects_basics(
    cli_runner, isolated_lamark_home: Path
) -> None:
    """`lamark bootstrap --interactive` accepts piped input."""
    from lamark.cli import app

    # Order: name, locale, role, style.length, style.emojis (y/n),
    #        style.markdown (y/n), persona.tone, persona.humour
    inputs = _input_stream(
        "Anna",
        "ru-RU",
        "senior engineer at X, Kotlin/Android",
        "short",
        "n",
        "y",
        "concise",
        "dry",
    )
    result = cli_runner.invoke(app, ["bootstrap", "--interactive"], input=inputs)
    assert result.exit_code == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    from lamark.memory import MemoryStore

    store = MemoryStore.open(isolated_lamark_home / "honcho.db")
    try:
        # name + locale + 2 role chunks + persona + style = 6
        assert store.count_facts() >= 5
    finally:
        store.close()


def test_cli_bootstrap_non_interactive_via_flags(
    cli_runner, isolated_lamark_home: Path
) -> None:
    """All flags supplied → no prompts; same outcome as interactive."""
    from lamark.cli import app

    result = cli_runner.invoke(
        app,
        [
            "bootstrap",
            "--name", "Anna",
            "--locale", "ru-RU",
            "--role", "senior engineer at X",
        ],
    )
    assert result.exit_code == 0, f"stderr={result.stderr!r}"

    from lamark.memory import MemoryStore

    store = MemoryStore.open(isolated_lamark_home / "honcho.db")
    try:
        assert store.count_facts() >= 1
    finally:
        store.close()
