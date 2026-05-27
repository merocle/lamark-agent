"""Append a training pair to the archive when Hermes finishes a turn.

Called from Hermes' Telegram adapter's ``on_processing_complete`` hook
(see LAMARK-PATCH A.9 in vendor/hermes/gateway/platforms/telegram.py).
We only capture pairs on the SUCCESS outcome, so failures/cancels never
pollute training data. Confidence starts at 0.5 — "user typed it but
didn't explicitly endorse"; future ``/good`` and ``/bad`` slash
commands will upgrade or downgrade the most-recent record's confidence
in-place, and the curation layer
(`src/lamark/train/curation.py`) already filters by confidence threshold.

The hook must never crash Hermes' message lifecycle. All failures here
are caught and logged at warning level; a missing assistant reply,
missing DB, or import error all degrade silently rather than break the
user-visible chat path.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("lamark.capture")

# Source label for archive provenance. Matches VALID_PROVENANCE in
# src/lamark/memory/schema.py — "user_explicit" is the right slot for
# pairs captured directly from a real Telegram chat.
SOURCE = "user_explicit"

# Default confidence for implicit captures (no /good or /bad yet).
DEFAULT_CONFIDENCE = 0.5


def _lamark_home() -> Path:
    return Path(os.environ.get("LAMARK_HOME", str(Path.home() / ".lamark")))


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(_lamark_home() / "hermes-home")))


def _is_command(text: str) -> bool:
    return bool(text) and text.lstrip().startswith("/")


def _outcome_is_success(outcome: Any) -> bool:
    """Robust SUCCESS detection regardless of how the enum was imported."""
    if outcome is None:
        return False
    val = getattr(outcome, "value", outcome)
    return str(val).lower() == "success"


def _latest_assistant_reply(db_path: Path) -> tuple[str, str | None] | None:
    """Return (content, session_id) of the most recent non-tool-call assistant
    message in the Hermes session DB, or None if no such row exists.

    For Lamark's single-owner-chat product shape this is sufficient: only
    one assistant turn lands at a time. If a multi-user fork ever runs,
    swap this for a session-scoped lookup keyed off ``event.source``.
    """
    if not db_path.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0) as db:
            row = db.execute(
                """
                SELECT content, session_id
                FROM messages
                WHERE role = 'assistant'
                  AND (tool_calls IS NULL OR tool_calls = '' OR tool_calls = '[]')
                  AND content IS NOT NULL
                  AND length(trim(content)) > 0
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
    except sqlite3.Error as exc:
        logger.debug("pair_writer: session_db read failed: %s", exc)
        return None
    if not row:
        return None
    content, session_id = row
    return (str(content), str(session_id) if session_id else None)


async def capture_exchange(event: Any, outcome: Any) -> None:
    """Hook entry — record one (user, assistant) pair into the archive.

    Safe to call from inside an async context. All exceptions are
    swallowed; failures only log a warning so the message lifecycle
    continues cleanly.
    """
    try:
        if not _outcome_is_success(outcome):
            return

        user_text = getattr(event, "text", None) or ""
        if not user_text.strip() or _is_command(user_text):
            return

        # Locate the just-delivered assistant reply via the session DB.
        # The Telegram adapter has already finished writing the assistant
        # turn by the time on_processing_complete fires, so the latest
        # assistant row IS our reply for the single-owner-chat shape.
        reply_info = _latest_assistant_reply(_hermes_home() / "state.db")
        if reply_info is None:
            logger.debug("pair_writer: no assistant reply found, skipping")
            return
        assistant_text, session_id = reply_info
        if not assistant_text.strip():
            return

        # Build the ChatML record + persist via the archive store.
        from lamark.archive.store import Archive  # lazy: avoid cost on every import path

        archive = Archive.open(_lamark_home() / "archive")
        archive.write_pair(
            messages=[
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ],
            source=SOURCE,
            confidence=DEFAULT_CONFIDENCE,
            evidence_path=f"telegram:{session_id}" if session_id else None,
        )
        logger.info(
            "pair_writer: captured pair (user=%d chars, assistant=%d chars, conf=%.2f)",
            len(user_text), len(assistant_text), DEFAULT_CONFIDENCE,
        )
    except Exception as exc:  # noqa: BLE001 — never crash the gateway
        logger.warning("pair_writer: capture failed (%s); continuing", exc)
