"""Redaction errors."""

from __future__ import annotations


class SecretFound(Exception):
    """A verified secret was found in input. Pipeline halts unconditionally.

    Non-overridable by design: per v3 §7 / Kreuzhofer Part 3, verified secrets
    (AWS keys, GitHub PATs, JWTs, OpenAI keys) must NEVER continue into the
    training/curation/memory pipeline.
    """

    def __init__(self, category: str, snippet: str, source: str = "regex"):
        self.category = category
        self.snippet = snippet
        self.source = source
        super().__init__(f"{category} secret detected (source={source}): {snippet[:20]}...")
