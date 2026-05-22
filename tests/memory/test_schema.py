"""
Test Module 2 — memory schema.

Invariants under test:
1. UserModel is single-row (single-user app); attempting to create a second row raises.
2. Persona fields are immutable to agent self-edits unless explicit user confirmation.
3. Facts have non-null provenance, confidence, created_at — never silent inserts.
4. Deleting a UserModel cascades to facts, conversations, messages, skills.
5. Conversation summary is computed lazily (not stored as a redundant column).

These tests are RED until src/lamark/memory/{schema,store}.py exist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def store(isolated_lamark_home: Path):
    """Fresh MemoryStore backed by SQLite in the isolated home."""
    from lamark.memory.store import MemoryStore

    s = MemoryStore.open(isolated_lamark_home / "honcho.db")
    yield s
    s.close()


def test_user_model_is_single_row(store) -> None:
    """Lamark is single-user. Two UserModel rows is a bug."""
    store.create_user_model(name="Anna", locale="ru-RU")
    with pytest.raises(ValueError, match="single-user|already exists"):
        store.create_user_model(name="Boris", locale="en-US")


def test_user_model_persona_locked_against_agent_edit(store) -> None:
    """Agent self-edits cannot silently change persona — must mark for user confirmation."""
    user = store.create_user_model(name="Anna", persona={"tone": "concise"})
    with pytest.raises(PermissionError, match="persona|user.confirm"):
        store.update_user_model_field(
            "persona",
            {"tone": "verbose"},
            source="agent_self_edit",  # ← forbidden source for persona
        )
    # Explicit user confirmation path succeeds
    store.update_user_model_field(
        "persona",
        {"tone": "verbose"},
        source="user_explicit",
    )
    refreshed = store.get_user_model()
    assert refreshed.persona == {"tone": "verbose"}


def test_fact_requires_provenance_confidence_timestamp(store) -> None:
    """A Fact cannot exist without source, confidence, created_at."""
    store.create_user_model(name="Anna")
    # Missing source → reject
    with pytest.raises((TypeError, ValueError)):
        store.add_fact(text="Anna likes tea")  # type: ignore[call-arg]
    # Missing confidence → reject
    with pytest.raises((TypeError, ValueError)):
        store.add_fact(text="Anna likes tea", source="bootstrap")  # type: ignore[call-arg]
    # All present → ok
    fact = store.add_fact(text="Anna likes tea", source="bootstrap", confidence=0.95)
    assert fact.source == "bootstrap"
    assert 0.0 <= fact.confidence <= 1.0
    assert fact.created_at is not None


def test_fact_confidence_clamped_to_unit_interval(store) -> None:
    """Confidence outside [0, 1] is rejected at write time."""
    store.create_user_model(name="Anna")
    with pytest.raises(ValueError, match="confidence"):
        store.add_fact(text="x", source="bootstrap", confidence=1.5)
    with pytest.raises(ValueError, match="confidence"):
        store.add_fact(text="x", source="bootstrap", confidence=-0.1)


def test_conversation_groups_messages_in_order(store) -> None:
    """Messages of a conversation are returned ordered by created_at."""
    store.create_user_model(name="Anna")
    conv = store.start_conversation(channel="cli")
    now = datetime.now(UTC)
    store.append_message(conv.id, role="user", content="Hi", created_at=now)
    store.append_message(conv.id, role="assistant", content="Hello", created_at=now + timedelta(seconds=1))
    store.append_message(conv.id, role="user", content="How are you?", created_at=now + timedelta(seconds=2))
    msgs = store.messages(conv.id)
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert [m.content for m in msgs] == ["Hi", "Hello", "How are you?"]


def test_delete_user_data_cascades(store) -> None:
    """delete_user_data wipes user, facts, conversations, messages, skills — for GDPR."""
    store.create_user_model(name="Anna")
    store.add_fact(text="fact 1", source="bootstrap", confidence=1.0)
    conv = store.start_conversation(channel="cli")
    store.append_message(conv.id, role="user", content="hello")
    assert store.count_facts() > 0
    assert store.count_messages() > 0

    store.delete_user_data(confirm=True)

    assert store.get_user_model() is None
    assert store.count_facts() == 0
    assert store.count_messages() == 0
    assert store.count_conversations() == 0


def test_delete_user_data_requires_explicit_confirm(store) -> None:
    """Calling delete_user_data() with no confirm flag must refuse."""
    store.create_user_model(name="Anna")
    with pytest.raises(ValueError, match="confirm"):
        store.delete_user_data()  # type: ignore[call-arg]


def test_skill_metadata_present(store) -> None:
    """A Skill row carries name, version, description, source path."""
    store.create_user_model(name="Anna")
    skill = store.register_skill(
        name="weekly_summary",
        version="0.1.0",
        description="Generate weekly recap of work",
        source_path="skills/weekly_summary/SKILL.md",
    )
    assert skill.name == "weekly_summary"
    assert skill.version == "0.1.0"
    assert "weekly" in skill.description.lower()


def test_fact_provenance_enum_is_restricted(store) -> None:
    """Only known provenance sources are accepted."""
    store.create_user_model(name="Anna")
    # Unknown source → reject
    with pytest.raises(ValueError, match="source|provenance"):
        store.add_fact(text="x", source="from_a_psychic", confidence=0.5)
    # Known sources all work
    for src in ("bootstrap", "user_explicit", "agent_self_edit", "imported"):
        f = store.add_fact(text=f"fact {src}", source=src, confidence=0.5)
        assert f.source == src
