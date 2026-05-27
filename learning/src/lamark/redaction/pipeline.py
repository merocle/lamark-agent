"""
RedactionPipeline — orchestrator.

Order matters: secrets first (HALT on hit), then PII (substitute + audit).
Audit sink fires for every hit, including the verified secret that halts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from lamark.redaction.errors import SecretFound
from lamark.redaction.pii import PIIHit, scan_all
from lamark.redaction.secrets import SecretHit, scan_denylist, scan_secrets

PLACEHOLDERS = {
    "email": "<email>",
    "phone": "<phone>",
    "credit_card": "<credit_card>",
}


@dataclass(frozen=True)
class Redaction:
    """A single non-blocking redaction event."""

    category: str
    start: int  # offset into original_text
    end: int
    replacement: str


@dataclass(frozen=True)
class RedactionResult:
    text: str
    original_text: str
    redactions: tuple[Redaction, ...]


class RedactionPipeline:
    """Two-stage redaction.

    Stage 1 — verified secrets → HALT (raise SecretFound).
    Stage 2 — PII → substitute with placeholders, return RedactionResult.
    """

    def __init__(self, deny_phrases: list[str] | None = None) -> None:
        self._deny_phrases = list(deny_phrases or [])
        self._audit_sink: Callable[[Any], None] | None = None

    def set_audit_sink(self, sink: Callable[[Any], None]) -> None:
        """Register a callable for audit events. Receives SecretHit and PIIHit."""
        self._audit_sink = sink

    def _audit(self, event: Any) -> None:
        if self._audit_sink is not None:
            try:
                self._audit_sink(event)
            except Exception:  # noqa: BLE001 — audit must never break the pipeline
                pass

    def process(self, text: str) -> RedactionResult:
        # Stage 1: secrets — HALT
        secret_hits: list[SecretHit] = scan_secrets(text) + scan_denylist(text, self._deny_phrases)
        if secret_hits:
            # Audit ALL hits before raising — observability for bypass attempts
            for h in secret_hits:
                self._audit(h)
            first = secret_hits[0]
            raise SecretFound(category=first.category, snippet=first.match, source=first.pattern_name)

        # Stage 2: PII — substitute
        pii_hits: list[PIIHit] = scan_all(text)
        if not pii_hits:
            return RedactionResult(text=text, original_text=text, redactions=())

        # Apply substitutions from right to left so earlier offsets stay valid
        pii_hits_sorted = sorted(pii_hits, key=lambda h: h.start)
        out_parts: list[str] = []
        cursor = 0
        redactions: list[Redaction] = []
        for h in pii_hits_sorted:
            self._audit(h)
            out_parts.append(text[cursor : h.start])
            placeholder = PLACEHOLDERS.get(h.category, f"<{h.category}>")
            out_parts.append(placeholder)
            redactions.append(
                Redaction(
                    category=h.category,
                    start=h.start,
                    end=h.end,
                    replacement=placeholder,
                )
            )
            cursor = h.end
        out_parts.append(text[cursor:])

        return RedactionResult(
            text="".join(out_parts),
            original_text=text,
            redactions=tuple(redactions),
        )
