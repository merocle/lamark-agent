"""
Day-0 bootstrap wizard.

Either fully interactive (prompts for everything) or driven entirely by
kwargs / CLI flags. Result: a fresh UserModel + seed Fact rows in the
MemoryStore, all flagged with provenance="bootstrap" so future memory
consumers know these are seed data, not agent-inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lamark.memory import MemoryStore, PROVENANCE_BOOTSTRAP, PROVENANCE_USER_EXPLICIT


@dataclass(frozen=True)
class BootstrapResult:
    """Summary of what the wizard wrote."""

    user_created: bool
    facts_added: int
    notes: tuple[str, ...] = ()


def _facts_from_role(role: str | None) -> list[str]:
    """Split a free-text role string into one or more facts."""
    if not role:
        return []
    chunks = [c.strip() for c in role.split(",") if c.strip()]
    return chunks or [role.strip()]


def run_bootstrap(
    store: MemoryStore,
    *,
    name: str | None = None,
    locale: str | None = None,
    role: str | None = None,
    style: dict[str, Any] | None = None,
    persona: dict[str, Any] | None = None,
    interactive: bool = False,
    force: bool = False,
    prompt_fn: Any = None,
) -> BootstrapResult:
    """
    Run the Day-0 bootstrap.

    Args:
        store: open MemoryStore (caller owns the lifecycle).
        name, locale, role, style, persona: explicit values (skip prompts).
        interactive: ask via prompt_fn for any unset field.
        force: if a UserModel exists, wipe and recreate. Without this, raise.
        prompt_fn: callable(question, default=None) -> str; defaults to
            typer.prompt when interactive=True.

    Returns:
        BootstrapResult.

    Raises:
        RuntimeError: UserModel already exists and force=False.
    """
    existing = store.get_user_model()
    if existing is not None and not force:
        raise RuntimeError(
            "Bootstrap refusing: a UserModel already exists "
            f"(name={existing.name!r}). Pass force=True to wipe and re-bootstrap."
        )
    if existing is not None and force:
        store.delete_user_data(confirm=True)

    if prompt_fn is None and interactive:
        import typer

        def prompt_fn(question: str, default: Any = None) -> str:
            return typer.prompt(question, default=default if default is not None else "")

    def maybe_ask(value: Any, question: str, default: Any = None) -> Any:
        if value is not None and value != "":
            return value
        if interactive and prompt_fn is not None:
            return prompt_fn(question, default=default)
        return default

    name = maybe_ask(name, "Your name", default=None)
    locale = maybe_ask(locale, "Locale (e.g. en-US, ru-RU)", default=None)
    role = maybe_ask(role, "What do you do? (free text — separate facts with commas)", default=None)

    # Style — interactive only if user asks; non-interactive can pass dict directly
    if style is None and interactive and prompt_fn is not None:
        length = prompt_fn("Preferred reply length (short / medium / long)", default="medium")
        emojis = prompt_fn("Use emoji in replies? (y/n)", default="n")
        markdown = prompt_fn("Render markdown? (y/n)", default="y")
        style = {
            "length": length.strip(),
            "emojis": emojis.strip().lower().startswith("y"),
            "markdown": markdown.strip().lower().startswith("y"),
        }

    # Persona — same pattern
    if persona is None and interactive and prompt_fn is not None:
        tone = prompt_fn("Persona tone (one word, e.g. concise / warm / formal)", default="")
        humour = prompt_fn("Humour (one word, e.g. dry / playful / off)", default="")
        persona = {k: v for k, v in (("tone", tone.strip()), ("humour", humour.strip())) if v}

    # Create the UserModel — wizard writes are PROVENANCE_USER_EXPLICIT, NOT
    # bootstrap (so persona is locked correctly for future agent self-edits).
    store.create_user_model(name=name, locale=locale, persona=persona, style=style)

    # Seed facts from role text
    facts_added = 0
    for chunk in _facts_from_role(role):
        store.add_fact(
            text=chunk,
            source=PROVENANCE_BOOTSTRAP,
            confidence=0.95,
            evidence="bootstrap-wizard:role",
        )
        facts_added += 1

    return BootstrapResult(
        user_created=True,
        facts_added=facts_added,
        notes=(
            f"persona stored as {PROVENANCE_USER_EXPLICIT} (locked against agent self-edit)",
        ),
    )
