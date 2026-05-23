"""
Verified-secret detection.

Phase 1 uses regex on canonical credential shapes. Phase 2 adds TruffleHog
verification (network-active check that the secret is alive); for now we
trust pattern match.

Patterns chosen to maximize precision over recall — a missed secret survives
to bootstrap, but false positives on safe text would degrade UX significantly.
When in doubt, prefer caller-supplied deny_phrases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretHit:
    category: str
    pattern_name: str
    start: int
    end: int
    match: str


# Each pattern is named for the audit log. Patterns are anchored to common
# canonical credential shapes; the test suite locks the contract.
_SECRET_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "AWS",
        "aws_access_key_id",
        re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"),
    ),
    (
        "GitHub",
        "ghp_pat",
        re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"),
    ),
    (
        "GitHub",
        "github_fine_grained",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    ),
    (
        "OpenAI",
        "sk_legacy",
        re.compile(r"\bsk-[A-Za-z0-9]{40,}\b"),
    ),
    (
        "OpenAI",
        "sk_proj",
        re.compile(r"\bsk-proj-[A-Za-z0-9_-]{40,}\b"),
    ),
    (
        "Anthropic",
        "anthropic_admin",
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{50,}\b"),
    ),
    (
        "JWT",
        "jwt_three_part",
        # header.payload.signature — base64url with reasonable lengths
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    (
        "Slack",
        "xoxb_token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ),
    (
        "Stripe",
        "sk_live",
        re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b"),
    ),
    (
        "Google",
        "gcp_api_key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ),
    (
        "Private",
        "pem_private_key_header",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"),
    ),
]


def scan_secrets(text: str) -> list[SecretHit]:
    """Return all secret matches in `text`. Empty list if none."""
    hits: list[SecretHit] = []
    for category, name, pat in _SECRET_PATTERNS:
        for m in pat.finditer(text):
            hits.append(
                SecretHit(
                    category=category,
                    pattern_name=name,
                    start=m.start(),
                    end=m.end(),
                    match=m.group(0),
                )
            )
    return hits


def scan_denylist(text: str, deny_phrases: list[str]) -> list[SecretHit]:
    """Caller-supplied deny phrases — case-insensitive substring match."""
    if not deny_phrases:
        return []
    lower = text.lower()
    hits: list[SecretHit] = []
    for phrase in deny_phrases:
        needle = phrase.lower()
        start = 0
        while (idx := lower.find(needle, start)) != -1:
            hits.append(
                SecretHit(
                    category="Deny",
                    pattern_name=f"user_deny:{phrase}",
                    start=idx,
                    end=idx + len(needle),
                    match=text[idx : idx + len(needle)],
                )
            )
            start = idx + len(needle)
    return hits
