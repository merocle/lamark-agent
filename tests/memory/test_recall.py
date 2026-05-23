"""
Test Module 4 — memory recall.

`recall(query, k, recency_weight)` returns top-k facts ranked by a combination
of textual relevance + recency. Phase 1 is keyword-overlap based; Phase 2
will swap in bge-m3 embeddings behind the same interface — these tests
constrain the BEHAVIOUR (returns relevant + recent items, respects k,
respects recency_weight), not the algorithm.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def store_with_facts(isolated_lamark_home: Path):
    """Memory store preloaded with diverse facts spread over time."""
    from sqlalchemy import update

    from lamark.memory import MemoryStore
    from lamark.memory.schema import Fact

    s = MemoryStore.open(isolated_lamark_home / "honcho.db")
    s.create_user_model(name="Anna")

    # We control the timestamp by sneaking SQLAlchemy updates after insert.
    now = datetime.now(UTC)
    seeds = [
        ("loves ramen and udon", now - timedelta(days=200)),
        ("works on Lamark agent, Kotlin background", now - timedelta(days=30)),
        ("uses Qwen3.6-35B-A3B on DGX Spark", now - timedelta(days=5)),
        ("prefers short replies and numbered lists", now - timedelta(days=180)),
        ("favourite colour is teal", now - timedelta(days=400)),
        ("recently adopted a dog named Rex", now - timedelta(days=1)),
    ]
    for text_, _ in seeds:
        s.add_fact(text=text_, source="bootstrap", confidence=0.9)

    with s._sessionmaker() as sess:  # type: ignore[attr-defined]
        rows = list(sess.query(Fact).all())
        for row, (_, when) in zip(rows, seeds, strict=True):
            sess.execute(update(Fact).where(Fact.id == row.id).values(created_at=when))
        sess.commit()

    yield s
    s.close()


def test_recall_returns_relevant_facts(store_with_facts) -> None:
    """Query matching one fact's content puts it in the top-3."""
    from lamark.memory.recall import recall

    hits = recall(store_with_facts, query="what model do I run", k=3)
    assert hits, "recall must return at least one fact for a relevant query"
    assert any("Qwen3.6-35B-A3B" in h.text for h in hits[:3])


def test_recall_respects_k(store_with_facts) -> None:
    """k caps the number of returned facts."""
    from lamark.memory.recall import recall

    hits = recall(store_with_facts, query="anything", k=2)
    assert len(hits) <= 2


def test_recall_recency_weight_zero_ignores_age(store_with_facts) -> None:
    """recency_weight=0 → ranking purely by relevance (text overlap)."""
    from lamark.memory.recall import recall

    hits = recall(store_with_facts, query="ramen udon", k=3, recency_weight=0.0)
    assert hits
    assert "ramen" in hits[0].text.lower() or "udon" in hits[0].text.lower()


def test_recall_recency_weight_one_returns_newest_first(store_with_facts) -> None:
    """recency_weight=1 → newest fact first regardless of query."""
    from lamark.memory.recall import recall

    hits = recall(store_with_facts, query="nothing in particular", k=3, recency_weight=1.0)
    assert hits
    # The newest seed was "recently adopted a dog named Rex" (1 day ago)
    assert "Rex" in hits[0].text


def test_recall_empty_store_returns_empty(isolated_lamark_home: Path) -> None:
    """No facts → empty list, no crash."""
    from lamark.memory import MemoryStore
    from lamark.memory.recall import recall

    with MemoryStore.open(isolated_lamark_home / "fresh.db") as s:
        s.create_user_model(name="Empty")
        hits = recall(s, query="anything", k=5)
        assert hits == []


def test_recall_with_unicode_query(store_with_facts) -> None:
    """Non-ASCII query doesn't break tokenisation."""
    from lamark.memory.recall import recall

    # Should not raise; result may be empty if no fact matches.
    hits = recall(store_with_facts, query="программирование на Kotlin", k=3)
    assert isinstance(hits, list)


def test_recall_returns_fact_objects_not_strings(store_with_facts) -> None:
    """Caller needs source/confidence/created_at on each hit, not just text."""
    from lamark.memory.recall import recall
    from lamark.memory.schema import Fact

    hits = recall(store_with_facts, query="agent", k=3)
    assert hits
    for h in hits:
        assert isinstance(h, Fact)
        assert h.source is not None
        assert h.confidence is not None
        assert h.created_at is not None
