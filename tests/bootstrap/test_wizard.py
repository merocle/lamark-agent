"""
Test Module 8 — bootstrap wizard.

The wizard's job: take Lamark from "knows nothing about user" → "has ~100-200
seed facts in Honcho user-model" in 15-20 minutes, closing the cold-start gap
that other memory-layer assistants suffer through their first weeks.

These tests pin the **behavior** of the wizard — not its visual prompts.
Visual UX (rich formatting, color, progress bar) is tested separately or by
eye. Here we check that given a stream of inputs, the right facts land in
the right shape.

RED until src/lamark/bootstrap/wizard.py exists.
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


def test_run_with_explicit_args_creates_user_model(fresh_store) -> None:
    """Non-interactive call writes UserModel from kwargs."""
    from lamark.bootstrap.wizard import run_bootstrap

    result = run_bootstrap(
        store=fresh_store,
        name="Anna",
        locale="ru-RU",
        role="senior engineer at X, Kotlin/Android",
        style={"length": "short", "emojis": False, "markdown": True},
        interactive=False,
    )
    user = fresh_store.get_user_model()
    assert user is not None
    assert user.name == "Anna"
    assert user.locale == "ru-RU"
    assert user.style == {"length": "short", "emojis": False, "markdown": True}
    # At minimum the role becomes a fact with source=bootstrap
    assert fresh_store.count_facts() >= 1
    assert result.facts_added >= 1


def test_persona_via_bootstrap_uses_user_explicit_provenance(fresh_store) -> None:
    """Persona set at bootstrap time is treated as user_explicit (not agent edit)."""
    from lamark.bootstrap.wizard import run_bootstrap

    run_bootstrap(
        store=fresh_store,
        name="Anna",
        persona={"tone": "concise", "humour": "dry"},
        interactive=False,
    )
    user = fresh_store.get_user_model()
    assert user.persona == {"tone": "concise", "humour": "dry"}
    # And a follow-up agent_self_edit must NOT be able to silently change persona
    with pytest.raises(PermissionError):
        fresh_store.update_user_model_field(
            "persona", {"tone": "verbose"}, source="agent_self_edit"
        )


def test_rerun_refuses_to_overwrite_existing_user(fresh_store) -> None:
    """If a UserModel already exists, bootstrap refuses unless force=True."""
    from lamark.bootstrap.wizard import run_bootstrap

    run_bootstrap(store=fresh_store, name="Anna", interactive=False)
    with pytest.raises(RuntimeError, match="already|exists|force"):
        run_bootstrap(store=fresh_store, name="Boris", interactive=False)


def test_force_reset_drops_old_data(fresh_store) -> None:
    """force=True wipes the previous UserModel + facts before bootstrapping."""
    from lamark.bootstrap.wizard import run_bootstrap

    run_bootstrap(store=fresh_store, name="Anna", role="dev", interactive=False)
    n1 = fresh_store.count_facts()
    assert n1 >= 1

    run_bootstrap(store=fresh_store, name="Boris", role="designer", interactive=False, force=True)
    user = fresh_store.get_user_model()
    assert user.name == "Boris"
    assert "designer" in (f.text for f in _all_facts(fresh_store))


# ---- interactive path via CLI --------------------------------------------


def test_cli_bootstrap_interactive_collects_basics(
    cli_runner, isolated_lamark_home: Path
) -> None:
    """`lamark bootstrap` interactive flow accepts inputs and writes user-model."""
    from lamark.cli import app

    # Order: name, locale, role, style.length, style.emojis (y/n), style.markdown (y/n),
    #        persona.tone, persona.humour
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
    assert result.exit_code == 0, f"cli failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    # Verify the data landed
    from lamark.memory import MemoryStore

    store = MemoryStore.open(isolated_lamark_home / "honcho.db")
    try:
        user = store.get_user_model()
        assert user is not None
        assert user.name == "Anna"
        assert user.locale == "ru-RU"
        assert user.persona == {"tone": "concise", "humour": "dry"}
        assert store.count_facts() >= 1
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
    assert result.exit_code == 0, f"cli failed: stderr={result.stderr!r}"
    from lamark.memory import MemoryStore

    store = MemoryStore.open(isolated_lamark_home / "honcho.db")
    try:
        user = store.get_user_model()
        assert user is not None and user.name == "Anna"
    finally:
        store.close()


# ---- helpers --------------------------------------------------------------


def _all_facts(store) -> list:
    """Pull all facts via a fresh session — for assertion helpers."""
    from sqlalchemy import select

    from lamark.memory.schema import Fact

    with store._sessionmaker() as s:  # type: ignore[attr-defined]
        return list(s.scalars(select(Fact)))
