"""
Test Module 2 — archive schema (post v4 refactor).

Removed in v4 (Critic 2 finding "decorative under existing code"):
- UserModel singleton invariant
- Persona-lock against agent_self_edit
- delete_user_data(confirm=True) GDPR cascade

Added in v4:
- delete_fact_where(text_contains | text_exact | evidence_prefix | source)
  matching Hermes's per-entry memory(remove, content=X) ergonomics

Kept invariants (load-bearing):
- Fact.source ∈ VALID_PROVENANCE
- Fact.confidence ∈ [0.0, 1.0]
- Conversation→Message ordering + cascade
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


# ---- Fact invariants -----------------------------------------------------


def test_fact_requires_provenance_confidence_timestamp(store) -> None:
    """A Fact cannot exist without source, confidence, created_at."""
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
    with pytest.raises(ValueError, match="confidence"):
        store.add_fact(text="x", source="bootstrap", confidence=1.5)
    with pytest.raises(ValueError, match="confidence"):
        store.add_fact(text="x", source="bootstrap", confidence=-0.1)


def test_fact_provenance_enum_is_restricted(store) -> None:
    """Only known provenance sources are accepted."""
    with pytest.raises(ValueError, match="source|provenance"):
        store.add_fact(text="x", source="from_a_psychic", confidence=0.5)
    for src in ("bootstrap", "user_explicit", "agent_self_edit", "imported"):
        f = store.add_fact(text=f"fact {src}", source=src, confidence=0.5)
        assert f.source == src


# ---- Conversation + Message ----------------------------------------------


def test_conversation_groups_messages_in_order(store) -> None:
    """Messages of a conversation are returned ordered by created_at."""
    conv = store.start_conversation(channel="cli")
    now = datetime.now(UTC)
    store.append_message(conv.id, role="user", content="Hi", created_at=now)
    store.append_message(conv.id, role="assistant", content="Hello", created_at=now + timedelta(seconds=1))
    store.append_message(conv.id, role="user", content="How are you?", created_at=now + timedelta(seconds=2))
    msgs = store.messages(conv.id)
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert [m.content for m in msgs] == ["Hi", "Hello", "How are you?"]


# ---- Skill metadata ------------------------------------------------------


def test_skill_metadata_present(store) -> None:
    """A Skill row carries name, version, description, source path."""
    skill = store.register_skill(
        name="weekly_summary",
        version="0.1.0",
        description="Generate weekly recap of work",
        source_path="skills/weekly_summary/SKILL.md",
    )
    assert skill.name == "weekly_summary"
    assert skill.version == "0.1.0"
    assert "weekly" in skill.description.lower()


# ---- delete_fact_where ---------------------------------------------------


def test_delete_fact_where_by_text_contains(store) -> None:
    """text_contains removes facts whose body contains the substring."""
    store.add_fact(text="lives in Berlin", source="bootstrap", confidence=0.9)
    store.add_fact(text="works in Berlin office", source="bootstrap", confidence=0.9)
    store.add_fact(text="loves Munich beer gardens", source="bootstrap", confidence=0.9)

    removed = store.delete_fact_where(text_contains="Berlin")
    assert removed == 2
    assert store.count_facts() == 1


def test_delete_fact_where_by_text_exact(store) -> None:
    """text_exact removes only literal-equal facts."""
    store.add_fact(text="loves coffee", source="bootstrap", confidence=0.9)
    store.add_fact(text="loves coffee, especially espresso", source="bootstrap", confidence=0.9)

    removed = store.delete_fact_where(text_exact="loves coffee")
    assert removed == 1
    assert store.count_facts() == 1


def test_delete_fact_where_by_evidence_prefix(store) -> None:
    """evidence_prefix wipes a tagged set (e.g. wizard seeds)."""
    store.add_fact(text="x", source="bootstrap", confidence=0.9, evidence="bootstrap-wizard:identity.name")
    store.add_fact(text="y", source="bootstrap", confidence=0.9, evidence="bootstrap-wizard:identity.role")
    store.add_fact(text="z", source="imported", confidence=0.7, evidence="chatgpt_export")

    removed = store.delete_fact_where(evidence_prefix="bootstrap-wizard:")
    assert removed == 2
    assert store.count_facts() == 1


def test_delete_fact_where_by_source(store) -> None:
    """source filter (e.g. purge agent inferences)."""
    store.add_fact(text="a", source="user_explicit", confidence=0.9)
    store.add_fact(text="b", source="agent_self_edit", confidence=0.5)
    store.add_fact(text="c", source="agent_self_edit", confidence=0.5)

    removed = store.delete_fact_where(source="agent_self_edit")
    assert removed == 2
    assert store.count_facts() == 1


def test_delete_fact_where_requires_at_least_one_predicate(store) -> None:
    """Calling with no predicates refuses (prevents accidental full wipe)."""
    store.add_fact(text="a", source="bootstrap", confidence=0.9)
    with pytest.raises(ValueError, match="predicate"):
        store.delete_fact_where()
    assert store.count_facts() == 1  # untouched


def test_delete_fact_where_combines_predicates_with_and(store) -> None:
    """Multiple predicates are AND-combined."""
    store.add_fact(text="Berlin trip", source="bootstrap", confidence=0.9)
    store.add_fact(text="Berlin trip", source="imported", confidence=0.7)
    store.add_fact(text="Munich trip", source="bootstrap", confidence=0.9)

    removed = store.delete_fact_where(text_contains="Berlin", source="bootstrap")
    assert removed == 1
    assert store.count_facts() == 2
