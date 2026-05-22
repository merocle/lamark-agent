"""
SQLAlchemy schema for the Lamark memory layer.

Pure declarative models; no I/O behaviour beyond defaults. CRUD lives in
lamark.memory.store.MemoryStore. The split keeps invariants checkable in
isolation (schema unit tests) without touching the database driver.

Invariants enforced here:
- UserModel.id is constrained to a single row at the application level.
  (SQLite CHECK constraint enforced in MemoryStore; expressed here as a
  documented convention plus the `is_singleton_row` flag.)
- Fact.source must belong to VALID_PROVENANCE.
- Fact.confidence is in [0.0, 1.0].
- Cascade deletes wipe child rows when UserModel goes away.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Locked provenance enum — the only sources that may write a Fact or UserModel field.
PROVENANCE_BOOTSTRAP = "bootstrap"
PROVENANCE_USER_EXPLICIT = "user_explicit"
PROVENANCE_AGENT_SELF_EDIT = "agent_self_edit"
PROVENANCE_IMPORTED = "imported"

VALID_PROVENANCE: frozenset[str] = frozenset(
    {PROVENANCE_BOOTSTRAP, PROVENANCE_USER_EXPLICIT, PROVENANCE_AGENT_SELF_EDIT, PROVENANCE_IMPORTED}
)

# Persona fields are special: only PROVENANCE_USER_EXPLICIT may change them.
# Agent self-edits proposing persona changes must be queued for user confirmation,
# not silently applied.
PERSONA_LOCKED_SOURCES: frozenset[str] = frozenset({PROVENANCE_AGENT_SELF_EDIT, PROVENANCE_IMPORTED})


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base."""


class UserModel(Base):
    """Single-row user model — name, locale, persona, style preferences.

    The application-level singleton invariant is enforced by MemoryStore on insert.
    A `is_singleton_row` value of 1 plus a UNIQUE constraint guarantees no second row
    can be silently added if a future caller bypasses the store.
    """

    __tablename__ = "user_model"
    __table_args__ = (UniqueConstraint("is_singleton_row", name="uq_user_model_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    is_singleton_row: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    locale: Mapped[str | None] = mapped_column(String(32), nullable=True)
    persona: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    style: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Fact(Base):
    """A discrete fact about the user — provenance + confidence + timestamp."""

    __tablename__ = "fact"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_fact_confidence_unit_interval",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class Conversation(Base):
    """A grouping of messages, tied to a single channel session."""

    __tablename__ = "conversation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    """A single message in a conversation."""

    __tablename__ = "message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant | tool | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Skill(Base):
    """An installed skill — agentskills.io-compatible (loose match for now)."""

    __tablename__ = "skill"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
