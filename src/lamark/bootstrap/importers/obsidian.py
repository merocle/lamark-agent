"""
Obsidian vault importer.

Walks an Obsidian vault directory, picks non-empty Markdown notes, runs each
through the redaction pipeline, and writes one Fact per file. Skip rules:

- .obsidian/, .trash/, attachments/ (and any folder starting with '.')
- non-.md files (PNG/JPG/PDF etc. — would be attachments)
- empty files (no signal)
- oversize files (default >1 MB — likely export dumps, not notes)

Atomicity: any verified secret in any file halts the entire import BEFORE
DB writes, same model as the ChatGPT importer.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from lamark.memory import MemoryStore, PROVENANCE_IMPORTED
from lamark.redaction import RedactionPipeline

# Defaults — keep memory lean. Phase 2 can revisit.
DEFAULT_MAX_BYTES = 1_000_000  # 1 MB
DEFAULT_MAX_CHARS = 500

# Subdirectories that are never user content.
_SKIP_DIR_NAMES = frozenset({".obsidian", ".trash", "attachments", ".git", ".obsidian.icloud"})


@dataclass(frozen=True)
class ImportResult:
    files_processed: int
    facts_added: int
    files_skipped_empty: int
    files_skipped_oversize: int


def walk_vault(
    vault_path: Path | str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Iterator[Path]:
    """Yield non-empty .md file paths from an Obsidian vault.

    Skips:
        - dotted directories (.obsidian, .trash, .git, ...)
        - attachments/ folder by name
        - non-.md files
        - empty files
        - files larger than max_bytes
    """
    vault = Path(vault_path)
    if not vault.is_dir():
        raise NotADirectoryError(f"vault path is not a directory: {vault}")

    for path in vault.rglob("*.md"):
        # Skip if any parent in the relative path is in the deny list
        rel_parts = path.relative_to(vault).parts
        if any(part in _SKIP_DIR_NAMES or part.startswith(".") for part in rel_parts[:-1]):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0:
            continue
        if size > max_bytes:
            continue
        yield path


def _extract_text(md_path: Path, max_chars: int) -> str:
    """Read the file, strip leading '# Title\n\n' header, return up to max_chars."""
    try:
        raw = md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    # Strip leading H1 if present — title is preserved separately as evidence.
    lines = raw.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].startswith("# "):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    body = "\n".join(lines).strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "..."
    return body


def import_obsidian_vault(
    store: MemoryStore,
    vault_path: Path | str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_chars: int = DEFAULT_MAX_CHARS,
    deny_phrases: list[str] | None = None,
) -> ImportResult:
    """Walk vault, redact, write Facts atomically."""
    vault = Path(vault_path)
    pipe = RedactionPipeline(deny_phrases=deny_phrases)

    # Phase 1 — gather + redact in-memory. SecretFound raises here before any DB writes.
    prepared: list[tuple[str, str]] = []  # (text, evidence)
    skipped_empty = 0
    files_seen = 0
    for path in walk_vault(vault, max_bytes=max_bytes):
        files_seen += 1
        text = _extract_text(path, max_chars=max_chars)
        if not text:
            skipped_empty += 1
            continue
        redacted = pipe.process(text)
        rel = path.relative_to(vault).as_posix()
        prepared.append((redacted.text, f"obsidian:{rel}"))

    # Phase 2 — commit
    for text, evidence in prepared:
        store.add_fact(
            text=text,
            source=PROVENANCE_IMPORTED,
            confidence=0.85,
            evidence=evidence,
        )

    return ImportResult(
        files_processed=files_seen - skipped_empty,
        facts_added=len(prepared),
        files_skipped_empty=skipped_empty,
        files_skipped_oversize=0,  # walker filters silently; could surface later
    )
