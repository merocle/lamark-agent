"""Lamark training-data archive — Facts + Conversation log + Skills.

Per v4 §"Architectural principles" P1, this is the durable asset of the project.
LoRA adapters are derivative; this archive survives every model rotation.

Hard rules (enforced by the schema):
- Every Fact carries provenance, confidence, timestamp.
- Provenance is enum-restricted (bootstrap / user_explicit / agent_self_edit / imported).
- Fact.confidence is in [0.0, 1.0] (CHECK constraint).
- Conversation→Message cascades on delete.
- Per-entry removal via delete_fact_where(text_contains / text_exact / evidence_prefix / source).
"""

from __future__ import annotations

from lamark.memory.recall import recall
from lamark.memory.schema import (
    PROVENANCE_AGENT_SELF_EDIT,
    PROVENANCE_BOOTSTRAP,
    PROVENANCE_IMPORTED,
    PROVENANCE_USER_EXPLICIT,
    VALID_PROVENANCE,
    Conversation,
    Fact,
    Message,
    Skill,
)
from lamark.memory.store import MemoryStore

__all__ = [
    "MemoryStore",
    "Fact",
    "Conversation",
    "Message",
    "Skill",
    "recall",
    "VALID_PROVENANCE",
    "PROVENANCE_BOOTSTRAP",
    "PROVENANCE_USER_EXPLICIT",
    "PROVENANCE_AGENT_SELF_EDIT",
    "PROVENANCE_IMPORTED",
]
