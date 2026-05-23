"""
SQLAlchemy schema for the Lamark training-data archive (formerly the memory layer).

Per v4 §"Architectural principles" P1 ("Data persists, models rotate"), this
schema is the durable asset of the project. Adapters come and go; the archive
remains. Per the same v4 revision, UserModel + persona-lock + GDPR-cascade
were removed: those invariants were decorative under existing code, and
Hermes's USER.md flat file plus per-Fact `delete_fact_where()` provide
equivalent user-visible behaviour with vastly less surface area.

Invariants enforced here:
- Fact.source must belong to VALID_PROVENANCE
- Fact.confidence is in [0.0, 1.0] (CHECK constraint)
- Conversation cascades to Message on delete
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Locked provenance enum — the only sources that may write a Fact.
PROVENANCE_BOOTSTRAP = "bootstrap"
PROVENANCE_USER_EXPLICIT = "user_explicit"
PROVENANCE_AGENT_SELF_EDIT = "agent_self_edit"
PROVENANCE_IMPORTED = "imported"

VALID_PROVENANCE: frozenset[str] = frozenset(
    {PROVENANCE_BOOTSTRAP, PROVENANCE_USER_EXPLICIT, PROVENANCE_AGENT_SELF_EDIT, PROVENANCE_IMPORTED}
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base."""


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
