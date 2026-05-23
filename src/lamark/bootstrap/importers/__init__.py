"""Bootstrap data importers — ChatGPT, Apple Notes, Obsidian."""

from __future__ import annotations

from lamark.bootstrap.importers.chatgpt import (
    ImportResult,
    import_chatgpt_export,
)

__all__ = ["import_chatgpt_export", "ImportResult"]
