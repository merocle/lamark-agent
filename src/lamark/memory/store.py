"""
MemoryStore — CRUD wrapper around the schema with invariant enforcement.

Why a class instead of free functions: tests need a focused, mockable object
that owns its DB connection, and the agent runtime wants context-manager
semantics. Free functions would scatter session management.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from lamark.memory.schema import (
    PERSONA_LOCKED_SOURCES,
    PROVENANCE_USER_EXPLICIT,
    VALID_PROVENANCE,
    Base,
    Conversation,
    Fact,
    Message,
    Skill,
    UserModel,
)

PERSONA_LOCKED_FIELDS = {"persona"}


class MemoryStore:
    """SQLite-backed memory store. Single-user; thread-safe at the session level."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._sessionmaker = sessionmaker(bind=engine, expire_on_commit=False)
        Base.metadata.create_all(engine)
        self._init_skill_fts()

    @classmethod
    def open(cls, db_path: Path | str) -> MemoryStore:
        """Open or create a MemoryStore at the given SQLite path."""
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            future=True,
        )
        # Enable foreign-key cascade in SQLite (off by default).
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: Any, _conn_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

        return cls(engine)

    def close(self) -> None:
        self._engine.dispose()

    # -- helpers ------------------------------------------------------------

    def _session(self) -> Session:
        return self._sessionmaker()

    def _validate_source(self, source: str) -> None:
        if source not in VALID_PROVENANCE:
            raise ValueError(
                f"unknown provenance source {source!r}; "
                f"must be one of {sorted(VALID_PROVENANCE)}"
            )

    # -- UserModel ----------------------------------------------------------

    def create_user_model(
        self,
        name: str | None = None,
        locale: str | None = None,
        persona: dict[str, Any] | None = None,
        style: dict[str, Any] | None = None,
    ) -> UserModel:
        with self._session() as session:
            existing = session.scalar(select(UserModel).limit(1))
            if existing is not None:
                raise ValueError(
                    "Lamark is single-user — UserModel already exists. "
                    "Use update_user_model_field to modify."
                )
            user = UserModel(
                is_singleton_row=1,
                name=name,
                locale=locale,
                persona=persona,
                style=style,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

    def get_user_model(self) -> UserModel | None:
        with self._session() as session:
            return session.scalar(select(UserModel).limit(1))

    def update_user_model_field(
        self,
        field: str,
        value: Any,
        *,
        source: str,
    ) -> UserModel:
        """Update a UserModel field; persona fields are locked against agent edits."""
        self._validate_source(source)
        if field in PERSONA_LOCKED_FIELDS and source != PROVENANCE_USER_EXPLICIT:
            raise PermissionError(
                f"field {field!r} is persona-locked; "
                f"source {source!r} cannot modify it. "
                "Only user_explicit (after user confirmation) may update persona."
            )
        with self._session() as session:
            user = session.scalar(select(UserModel).limit(1))
            if user is None:
                raise ValueError("no UserModel exists; call create_user_model first")
            if not hasattr(user, field):
                raise AttributeError(f"UserModel has no field {field!r}")
            setattr(user, field, value)
            session.commit()
            session.refresh(user)
            return user

    # -- Fact ---------------------------------------------------------------

    def add_fact(
        self,
        text: str,
        source: str,
        confidence: float,
        evidence: str | None = None,
    ) -> Fact:
        self._validate_source(source)
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(
                f"confidence {confidence!r} outside [0.0, 1.0] — refusing to insert"
            )
        with self._session() as session:
            fact = Fact(
                text=text,
                source=source,
                confidence=confidence,
                evidence=evidence,
            )
            session.add(fact)
            session.commit()
            session.refresh(fact)
            return fact

    def count_facts(self) -> int:
        with self._session() as session:
            return int(session.scalar(select(func.count(Fact.id))) or 0)

    # -- Conversation + Message --------------------------------------------

    def start_conversation(self, channel: str) -> Conversation:
        with self._session() as session:
            conv = Conversation(channel=channel)
            session.add(conv)
            session.commit()
            session.refresh(conv)
            return conv

    def append_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        created_at: datetime | None = None,
        tokens: int | None = None,
    ) -> Message:
        with self._session() as session:
            kwargs: dict[str, Any] = {
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "tokens": tokens,
            }
            if created_at is not None:
                kwargs["created_at"] = created_at
            msg = Message(**kwargs)
            session.add(msg)
            session.commit()
            session.refresh(msg)
            return msg

    def messages(self, conversation_id: int) -> list[Message]:
        with self._session() as session:
            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.asc(), Message.id.asc())
            )
            return list(session.scalars(stmt))

    def count_messages(self) -> int:
        with self._session() as session:
            return int(session.scalar(select(func.count(Message.id))) or 0)

    def count_conversations(self) -> int:
        with self._session() as session:
            return int(session.scalar(select(func.count(Conversation.id))) or 0)

    # -- Skill --------------------------------------------------------------

    def register_skill(
        self,
        name: str,
        version: str,
        description: str,
        source_path: str | None = None,
    ) -> Skill:
        with self._session() as session:
            skill = Skill(
                name=name,
                version=version,
                description=description,
                source_path=source_path,
            )
            session.add(skill)
            session.commit()
            session.refresh(skill)
            return skill

    # -- skill search (FTS5) ----------------------------------------------

    def _init_skill_fts(self) -> None:
        """Create the FTS5 virtual table + triggers mirroring the skill table.

        Uses contentless-mode (content='skill') so the FTS index references
        rows in the actual skill table by rowid; we populate via triggers on
        INSERT/UPDATE/DELETE rather than maintaining two writes manually.
        """
        with self._engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS skill_fts USING fts5("
                "name, description, content='skill', content_rowid='id', "
                "tokenize='unicode61 remove_diacritics 2'"
                ")"
            )
            # Sync triggers — keep skill_fts in lockstep with skill writes.
            conn.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS skill_ai_fts AFTER INSERT ON skill BEGIN "
                "INSERT INTO skill_fts(rowid, name, description) "
                "VALUES (new.id, new.name, new.description); END"
            )
            conn.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS skill_ad_fts AFTER DELETE ON skill BEGIN "
                "INSERT INTO skill_fts(skill_fts, rowid, name, description) "
                "VALUES('delete', old.id, old.name, old.description); END"
            )
            conn.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS skill_au_fts AFTER UPDATE ON skill BEGIN "
                "INSERT INTO skill_fts(skill_fts, rowid, name, description) "
                "VALUES('delete', old.id, old.name, old.description); "
                "INSERT INTO skill_fts(rowid, name, description) "
                "VALUES (new.id, new.name, new.description); END"
            )
            # In case rows were inserted before triggers (e.g. legacy DBs), reindex once.
            conn.exec_driver_sql(
                "INSERT INTO skill_fts(rowid, name, description) "
                "SELECT s.id, s.name, s.description FROM skill s "
                "WHERE NOT EXISTS (SELECT 1 FROM skill_fts f WHERE f.rowid = s.id)"
            )

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Strip FTS5-special characters and reduce to safe term-OR query.

        FTS5 treats ", *, (), and standalone AND/OR/NEAR as operators —
        unbalanced quotes or parens raise sqlite errors. The safe path:
        extract alphanumeric+unicode word characters and OR them together.
        """
        # Keep word chars + spaces; drop quotes / parens / operators
        cleaned = re.sub(r"[^\w\s]", " ", query, flags=re.UNICODE)
        tokens = [t for t in cleaned.split() if t]
        if not tokens:
            return ""
        # Quote each token to avoid FTS5 treating it as an operator
        return " OR ".join(f'"{tok}"' for tok in tokens)

    def skill_search(self, query: str, limit: int = 10) -> list[Skill]:
        """Full-text search over Skill.name + Skill.description; bm25-ranked."""
        fts_query = self._sanitize_fts_query(query)
        if not fts_query:
            return []
        sql = text(
            "SELECT skill.* FROM skill "
            "JOIN skill_fts ON skill.id = skill_fts.rowid "
            "WHERE skill_fts MATCH :q "
            "ORDER BY bm25(skill_fts) "
            "LIMIT :lim"
        )
        with self._sessionmaker() as session:
            rows = session.execute(sql, {"q": fts_query, "lim": limit}).all()
            ids = [r._mapping["id"] for r in rows]
            if not ids:
                return []
            # Re-fetch via ORM, preserve bm25 ranking
            skills = {s.id: s for s in session.scalars(select(Skill).where(Skill.id.in_(ids)))}
            return [skills[i] for i in ids if i in skills]

    # -- destructive --------------------------------------------------------

    def delete_user_data(self, confirm: bool = False) -> None:  # noqa: FBT001, FBT002
        """Wipe everything. GDPR-style irrevocable delete; requires confirm=True."""
        if not confirm:
            raise ValueError(
                "delete_user_data is destructive — pass confirm=True to proceed"
            )
        with self._session() as session:
            session.execute(delete(Message))
            session.execute(delete(Conversation))
            session.execute(delete(Fact))
            session.execute(delete(Skill))
            session.execute(delete(UserModel))
            session.commit()

    # -- context manager support -------------------------------------------

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
