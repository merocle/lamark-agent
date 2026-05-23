"""
Day-0 bootstrap wizard (v4 refactor).

Pre-v4: wrote a UserModel row with name/locale/persona/style + seed Facts.
v4 dropped UserModel because nothing downstream branched on it. Now the
wizard writes **only Facts** with `source=user_explicit`, evidence tagged
`bootstrap-wizard:<field>` so a future re-run can wipe them via
`delete_fact_where(evidence_prefix="bootstrap-wizard:")`.

When Module 10 (Hermes fork patches) lands, the wizard will ALSO write
the same identity into Hermes's USER.md so the agent loop sees it in
its system prompt. Phase 1a ships archive-only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from lamark.memory import (
    MemoryStore,
    PROVENANCE_USER_EXPLICIT,
)

# Evidence prefix marker — lets a force-reset wipe wizard-seeded facts cleanly.
WIZARD_EVIDENCE_PREFIX = "bootstrap-wizard:"


@dataclass(frozen=True)
class BootstrapResult:
    """Summary of what the wizard wrote."""

    user_created: bool  # kept for back-compat with CLI message
    facts_added: int
    notes: tuple[str, ...] = ()


def _facts_from_role(role: str | None) -> list[str]:
    """Split a free-text role string into one or more facts."""
    if not role:
        return []
    chunks = [c.strip() for c in role.split(",") if c.strip()]
    return chunks or [role.strip()]


def _already_bootstrapped(store: MemoryStore) -> bool:
    """Check whether a previous wizard run left identity facts behind."""
    from sqlalchemy import func, select
    from lamark.memory.schema import Fact

    with store._sessionmaker() as session:  # type: ignore[attr-defined]
        count = session.scalar(
            select(func.count(Fact.id)).where(
                Fact.evidence.like(f"{WIZARD_EVIDENCE_PREFIX}%")
            )
        )
        return bool(count and count > 0)


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
    archive: Any = None,
) -> BootstrapResult:
    """
    Run the Day-0 bootstrap.

    Args:
        store: open MemoryStore (caller owns the lifecycle).
        name, locale, role, style, persona: explicit values (skip prompts).
        interactive: ask via prompt_fn for any unset field.
        force: if a previous bootstrap exists, wipe its facts and re-seed.
        prompt_fn: callable(question, default=None) -> str; defaults to
            typer.prompt when interactive=True.

    Returns:
        BootstrapResult.

    Raises:
        RuntimeError: prior wizard run detected and force=False.
    """
    if _already_bootstrapped(store) and not force:
        raise RuntimeError(
            "Bootstrap refusing: Lamark already has wizard-seeded identity facts. "
            "Pass force=True to wipe and re-bootstrap."
        )
    if force:
        store.delete_fact_where(evidence_prefix=WIZARD_EVIDENCE_PREFIX)

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

    if style is None and interactive and prompt_fn is not None:
        length = prompt_fn("Preferred reply length (short / medium / long)", default="medium")
        emojis = prompt_fn("Use emoji in replies? (y/n)", default="n")
        markdown = prompt_fn("Render markdown? (y/n)", default="y")
        style = {
            "length": length.strip(),
            "emojis": emojis.strip().lower().startswith("y"),
            "markdown": markdown.strip().lower().startswith("y"),
        }

    if persona is None and interactive and prompt_fn is not None:
        tone = prompt_fn("Persona tone (one word, e.g. concise / warm / formal)", default="")
        humour = prompt_fn("Humour (one word, e.g. dry / playful / off)", default="")
        persona = {k: v for k, v in (("tone", tone.strip()), ("humour", humour.strip())) if v}

    # Write Facts. All wizard writes are PROVENANCE_USER_EXPLICIT (high authority)
    # tagged with evidence prefix so force-reset can wipe them.
    facts_added = 0

    def _seed(text: str, slot: str) -> None:
        nonlocal facts_added
        evidence = f"{WIZARD_EVIDENCE_PREFIX}{slot}"
        store.add_fact(
            text=text,
            source=PROVENANCE_USER_EXPLICIT,
            confidence=0.95,
            evidence=evidence,
        )
        # Dual-write: mirror into the training-data archive (v4 §P1).
        # Phase 2 will switch to archive-as-primary with the Fact table as the
        # SQLite index. For Phase 1a we dual-write so the archive is populated
        # without breaking existing MemoryStore consumers.
        if archive is not None:
            archive.write_pair(
                messages=[{"role": "user", "content": text}],
                source=PROVENANCE_USER_EXPLICIT,
                confidence=0.95,
                evidence_path=evidence,
            )
        facts_added += 1

    if name:
        _seed(f"Name: {name}", "identity.name")
    if locale:
        _seed(f"Locale: {locale}", "identity.locale")
    for chunk in _facts_from_role(role):
        _seed(chunk, "identity.role")
    if persona:
        _seed(f"Persona: {json.dumps(persona, ensure_ascii=False)}", "identity.persona")
    if style:
        _seed(f"Style: {json.dumps(style, ensure_ascii=False)}", "identity.style")

    return BootstrapResult(
        user_created=facts_added > 0,
        facts_added=facts_added,
        notes=(
            f"All wizard facts written with source={PROVENANCE_USER_EXPLICIT} "
            f"and evidence prefix {WIZARD_EVIDENCE_PREFIX!r}",
        ),
    )
