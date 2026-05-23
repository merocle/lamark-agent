"""
Memory recall — surface facts relevant to a query.

Phase 1: keyword-overlap (Jaccard over lowercased Unicode word tokens) +
recency boost. Pure-Python, no embeddings, no ML deps. Good enough for
local testing and very small fact sets (the typical Day-0..Day-30 regime).

Phase 2: drop in `recall_via_embeddings(store, query, k, recency_weight)`
backed by bge-m3 vectors + LanceDB ANN; the contract (returns list[Fact]
ranked best-first) stays identical.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from lamark.memory.schema import Fact

_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text)}


def _relevance(query_tokens: set[str], fact_text: str) -> float:
    if not query_tokens:
        return 0.0
    ftoks = _tokens(fact_text)
    if not ftoks:
        return 0.0
    inter = query_tokens & ftoks
    union = query_tokens | ftoks
    return len(inter) / len(union) if union else 0.0


def _recency(created_at: datetime, now: datetime, half_life_days: float = 90.0) -> float:
    """Exponential decay: weight 1.0 at age=0, weight 0.5 at age=half_life."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    age_days = (now - created_at).total_seconds() / 86400.0
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def recall(
    store,
    query: str,
    k: int = 20,
    recency_weight: float = 0.15,
    half_life_days: float = 90.0,
    now: datetime | None = None,
) -> list[Fact]:
    """Return up to k Facts ranked by (1 - recency_weight) * relevance + recency_weight * recency.

    Args:
        store: MemoryStore.
        query: free-text query (any language; unicode tokenisation).
        k: max rows to return.
        recency_weight: in [0, 1]. 0 = relevance-only, 1 = recency-only.
        half_life_days: recency exponential half-life.
        now: for deterministic tests; defaults to UTC now.

    Returns:
        Up to k Facts, best-first. Empty list if store has no facts or
        no fact reaches a non-zero score.
    """
    if not (0.0 <= recency_weight <= 1.0):
        raise ValueError(f"recency_weight {recency_weight!r} outside [0, 1]")

    now = now or datetime.now(UTC)
    qtoks = _tokens(query)

    from sqlalchemy import select

    with store._sessionmaker() as session:  # type: ignore[attr-defined]
        all_facts = list(session.scalars(select(Fact)))

    if not all_facts:
        return []

    scored: list[tuple[float, Fact]] = []
    for fact in all_facts:
        rel = _relevance(qtoks, fact.text)
        rec = _recency(fact.created_at, now=now, half_life_days=half_life_days)
        score = (1.0 - recency_weight) * rel + recency_weight * rec
        if score > 0:
            scored.append((score, fact))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [f for _, f in scored[:k]]
