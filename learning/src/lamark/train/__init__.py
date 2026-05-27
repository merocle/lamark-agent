"""Lamark fine-tune subsystem — Phase 2 / Plan A.5.

Picks ChatML records from the training-data archive, applies curation
filters (source, confidence, recency), and dispatches an Unsloth-driven
LoRA training run on Spark.

Phase 1a / Plan A.5 ships the CLI surface and curation logic. The
actual trainer dispatch (Unsloth + DoRA on Spark) is a stub that
returns a dry-run plan; Module 14 (Phase 2 v4 §) wires the real call.
"""

from __future__ import annotations

from lamark.train.curation import CurationPlan, build_plan, count_archive
from lamark.train.runner import dispatch_training

__all__ = ["CurationPlan", "build_plan", "count_archive", "dispatch_training"]
