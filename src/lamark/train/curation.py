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
# Implicit auto-capture (every Telegram exchange) lands at confidence=0.5.
# `build_nightly_plan` lowers the floor accordingly so the cron-driven
# trainer actually sees those pairs. Explicit /good/bad commands (future)
# will upgrade individual records back above the 0.7 threshold for the
# strict `build_plan()` path.
NIGHTLY_MIN_CONFIDENCE = 0.5
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


def build_nightly_plan(
    archive: Archive | None = None,
    *,
    lamark_home: str | None = None,
) -> CurationPlan:
    """Curation entry point used by `scripts/lamark-nightly-train.sh`.

    Differs from `build_plan` in two ways:
      1. The confidence floor is NIGHTLY_MIN_CONFIDENCE (0.5), not 0.7 —
         every auto-captured Telegram exchange lands at 0.5, so requiring
         ≥0.7 would mean the nightly cron never sees any new pairs until
         the user starts explicitly /good-ing them. Implicit positives
         are good enough for the LoRA-shape we use (style + identity
         reinforcement); high-stakes facts go through L2 memory anyway.
      2. Accepts a None archive — auto-opens at $LAMARK_HOME/archive when
         called without args, so the cron script doesn't have to manage
         the Path itself.
    """
    if archive is None:
        from pathlib import Path
        import os
        root = Path(lamark_home or os.environ.get("LAMARK_HOME", str(Path.home() / ".lamark"))) / "archive"
        archive = Archive.open(root)
    return build_plan(archive, min_confidence=NIGHTLY_MIN_CONFIDENCE)
