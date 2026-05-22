"""Lamark memory layer — Honcho-style user model + cross-session recall + skill library.

Hard rules (enforced by the schema):
- Single-row UserModel (Lamark is single-user).
- Persona fields immune to agent self-edits unless user_explicit source is used.
- Every Fact carries provenance, confidence, timestamp.
- Provenance is enum-restricted.
- delete_user_data is destructive and requires confirm=True.
"""

from __future__ import annotations

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
    UserModel,
)
from lamark.memory.store import MemoryStore

__all__ = [
    "MemoryStore",
    "UserModel",
    "Fact",
    "Conversation",
    "Message",
    "Skill",
    "VALID_PROVENANCE",
    "PROVENANCE_BOOTSTRAP",
    "PROVENANCE_USER_EXPLICIT",
    "PROVENANCE_AGENT_SELF_EDIT",
    "PROVENANCE_IMPORTED",
]
