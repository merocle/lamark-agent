"""
Lamark redaction pipeline — secrets blocking + PII substitution + audit.

Used by:
- bootstrap importers (ChatGPT export, Notes, Obsidian) — sanitize before
  writing to memory
- Phase 2 nightly fine-tune ETL — redact before frontier-model curation
  and before training data lands on disk

Phase 1: deterministic regex layer only. Presidio + local LLM type-preserving
substitution land in Phase 2 once the model is serving and the [redaction]
extras are installed.
"""

from __future__ import annotations

from lamark.redaction.errors import SecretFound
from lamark.redaction.pipeline import RedactionPipeline, RedactionResult

__all__ = ["RedactionPipeline", "RedactionResult", "SecretFound"]
