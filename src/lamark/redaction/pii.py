"""
PII detection (regex layer).

Phase 1 detects: email, phone (international + US/RU forms), credit card.
Phase 2 adds Presidio for richer recall (names, addresses, ID numbers) plus
local LLM type-preserving substitution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PIIHit:
    category: str  # "email" | "phone" | "credit_card"
    start: int
    end: int
    match: str


_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b",
)

# Phone: international + at least 7 digits in groups. Allows spaces, dashes, parens.
_PHONE_RE = re.compile(
    r"(?:(?<!\w)\+?\d{1,3}[\s\-.]?)?"  # optional country code
    r"(?:\(\d{1,4}\)[\s\-.]?)?"        # optional area in parens
    r"\d{1,4}[\s\-.]?\d{1,4}[\s\-.]?\d{2,4}(?:[\s\-.]?\d{2,4})?"
)

# Credit card: 13-19 digits, optionally space- or dash-separated. Then Luhn check.
_CC_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _passes_luhn(digits: str) -> bool:
    """Standard Luhn checksum; protects against false positives on phone numbers."""
    s = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        n = int(ch)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        s += n
    return s % 10 == 0


def scan_emails(text: str) -> list[PIIHit]:
    return [
        PIIHit("email", m.start(), m.end(), m.group(0)) for m in _EMAIL_RE.finditer(text)
    ]


def scan_phones(text: str) -> list[PIIHit]:
    hits: list[PIIHit] = []
    for m in _PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if not (7 <= len(digits) <= 15):
            continue
        # Avoid catching credit cards as phones; pure long digit runs are CC.
        if len(digits) >= 12 and _passes_luhn(digits):
            continue
        hits.append(PIIHit("phone", m.start(), m.end(), m.group(0)))
    return hits


def scan_credit_cards(text: str) -> list[PIIHit]:
    hits: list[PIIHit] = []
    for m in _CC_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _passes_luhn(digits):
            hits.append(PIIHit("credit_card", m.start(), m.end(), m.group(0)))
    return hits


def scan_all(text: str) -> list[PIIHit]:
    """All PII categories combined, sorted by start offset, non-overlapping."""
    raw = scan_credit_cards(text) + scan_emails(text) + scan_phones(text)
    # De-overlap: prefer earlier longer matches (CC > phone collision).
    raw.sort(key=lambda h: (h.start, -(h.end - h.start)))
    accepted: list[PIIHit] = []
    last_end = -1
    for h in raw:
        if h.start >= last_end:
            accepted.append(h)
            last_end = h.end
    return accepted
