"""Bootstrap data importers — ChatGPT, Apple Notes, Obsidian."""

from __future__ import annotations

from lamark.bootstrap.importers.chatgpt import (
    ImportResult as ChatGPTImportResult,
)
from lamark.bootstrap.importers.chatgpt import import_chatgpt_export
from lamark.bootstrap.importers.obsidian import (
    ImportResult as ObsidianImportResult,
)
from lamark.bootstrap.importers.obsidian import import_obsidian_vault

__all__ = [
    "import_chatgpt_export",
    "import_obsidian_vault",
    "ChatGPTImportResult",
    "ObsidianImportResult",
]
