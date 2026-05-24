"""
Curation pipeline — pick training-eligible pairs from the archive.

Filters (in order):
1. Source filter (default: exclude agent_self_edit unless --include-agent-edits).
2. Confidence threshold (default: ≥ 0.7).
3. Not-yet-trained-on filter (consumed_by[] is empty OR doesn't include
   the target LoRA version — handled by the dispatcher, not here).
4. Optional sensitivity filter (Phase 1b: exclude `confidential`/`secret`).

Output is a list of dicts ready for ChatML conversion at training time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from lamark.archive import Archive

DEFAULT_MIN_CONFIDENCE = 0.7
DEFAULT_TRAINING_THRESHOLD = 50  # records needed before --now will proceed
DEFAULT_ALLOWED_SOURCES = ("user_explicit", "bootstrap", "imported")


@dataclass(frozen=True)
class CurationPlan:
    """What we would train on, given the current archive + filters."""

    records: tuple[dict[str, Any], ...]
    archive_total: int
    filters_applied: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.records)


def count_archive(archive: Archive) -> int:
    return archive.count()


def build_plan(
    archive: Archive,
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    allowed_sources: Iterable[str] = DEFAULT_ALLOWED_SOURCES,
    exclude_sensitivity: Iterable[str] = ("confidential", "secret"),
) -> CurationPlan:
    """Walk archive, apply filters, return CurationPlan."""
    allowed = set(allowed_sources)
    exclude_sens = set(exclude_sensitivity)

    matching: list[dict[str, Any]] = []
    total = 0
    for record in archive.read_all():
        total += 1
        meta = record.get("meta", {})
        # Source filter
        if meta.get("source") not in allowed:
            continue
        # Confidence filter
        conf = float(meta.get("confidence") or 0.0)
        if conf < min_confidence:
            continue
        # Sensitivity filter
        sens = meta.get("sensitivity")
        if sens and sens in exclude_sens:
            continue
        matching.append(record)

    return CurationPlan(
        records=tuple(matching),
        archive_total=total,
        filters_applied={
            "min_confidence": min_confidence,
            "allowed_sources": sorted(allowed),
            "exclude_sensitivity": sorted(exclude_sens),
        },
    )
