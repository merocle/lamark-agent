"""
Test Module 5 — Skills FTS5 search.

Skill table already exists from Module 2. We add a SQLite FTS5 virtual table
shadowing (name, description) for ranked text search, used by the agent
loop to discover relevant skills given a user prompt.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def store_with_skills(isolated_lamark_home: Path):
    """A MemoryStore preloaded with a handful of skills."""
    from lamark.memory import MemoryStore

    s = MemoryStore.open(isolated_lamark_home / "honcho.db")
    s.create_user_model(name="Demo")
    s.register_skill(
        name="weekly_summary",
        version="0.1.0",
        description="Generate a recap of the user's week from calendar and chats",
        source_path="skills/weekly_summary/SKILL.md",
    )
    s.register_skill(
        name="code_review_assistant",
        version="0.2.0",
        description="Review pull requests and suggest improvements; uses gh CLI",
        source_path="skills/code_review/SKILL.md",
    )
    s.register_skill(
        name="email_drafter",
        version="0.1.0",
        description="Draft polite professional emails in the user's voice",
        source_path="skills/email_drafter/SKILL.md",
    )
    s.register_skill(
        name="russian_translator",
        version="0.1.0",
        description="Translate text between Russian and English preserving style",
        source_path="skills/ru_translator/SKILL.md",
    )
    yield s
    s.close()


def test_skill_search_returns_matching_skill(store_with_skills) -> None:
    """Query matching a skill description ranks it first."""
    results = store_with_skills.skill_search("review pull request", limit=5)
    assert results, "skill_search must return at least one result for matching query"
    assert results[0].name == "code_review_assistant"


def test_skill_search_ranks_by_relevance(store_with_skills) -> None:
    """Two distinct queries return different top results."""
    weekly = store_with_skills.skill_search("week recap", limit=5)
    email = store_with_skills.skill_search("write polite email", limit=5)
    assert weekly[0].name == "weekly_summary"
    assert email[0].name == "email_drafter"


def test_skill_search_respects_limit(store_with_skills) -> None:
    """Limit parameter caps the number of returned rows."""
    results = store_with_skills.skill_search("a", limit=2)
    assert len(results) <= 2


def test_skill_search_no_match_returns_empty(store_with_skills) -> None:
    """Query with no overlap returns []."""
    results = store_with_skills.skill_search("astrology horoscope", limit=5)
    assert results == []


def test_skill_search_updates_with_new_registrations(store_with_skills) -> None:
    """A skill registered after search infrastructure exists is searchable."""
    store_with_skills.register_skill(
        name="dnd_dm_assistant",
        version="0.1.0",
        description="Help run a Dungeons and Dragons campaign as dungeon master",
        source_path="skills/dnd_dm/SKILL.md",
    )
    results = store_with_skills.skill_search("dungeon master", limit=5)
    assert results
    assert results[0].name == "dnd_dm_assistant"


def test_skill_search_works_on_fresh_store(isolated_lamark_home: Path) -> None:
    """FTS5 index created on first open; empty store returns []."""
    from lamark.memory import MemoryStore

    with MemoryStore.open(isolated_lamark_home / "fresh.db") as s:
        results = s.skill_search("anything", limit=5)
        assert results == []


def test_skill_search_handles_special_chars_in_query(store_with_skills) -> None:
    """FTS5 query string with quotes / parens must not crash."""
    # Common pitfall — must be sanitized
    results = store_with_skills.skill_search('"weekly" (recap)', limit=5)
    # Don't assert exact result — just that it didn't raise
    assert isinstance(results, list)
