"""
Curation pipeline — pick training-eligible pairs from the archive.

Filters (in order):
1. Source filter (default: exclude agent_self_edit unless --include-agent-edits).
2. Confidence threshold (default: ≥ 0.7).
3. Optional sensitivity filter (Phase 1b: exclude `confidential`/`secret`).

Output is a list of dicts ready for ChatML conversion at training time.

Note on "consumed": the training set is intentionally CUMULATIVE — the
dispatcher trains a fresh LoRA from the base model each night, so excluding
already-trained pairs would make the adapter forget prior nights. The
`consumed` ledger (Archive.consumed_ids / mark_consumed) is therefore NOT a
training-set filter; `count_new_pairs()` uses it only to count genuinely
new (unconsumed) pairs for the nightly run threshold.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from lamark.archive import Archive

logger = logging.getLogger("lamark.curation")

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


def count_new_pairs(
    archive: Archive,
    *,
    min_confidence: float = NIGHTLY_MIN_CONFIDENCE,
) -> int:
    """Count qualifying pairs not yet consumed by a promoted adapter.

    This is the nightly trainer's run threshold input — it answers "did
    enough NEW user content arrive to bother retraining?". It counts over
    the post-`build_plan` record set (source/confidence/sensitivity
    filters applied) BEFORE synthetic decay, minus `archive.consumed_ids()`.
    Records without a `meta.id` (legacy/fallback) cannot be tracked as
    consumed, so they always count as new.
    """
    consumed = archive.consumed_ids()
    plan = build_plan(archive, min_confidence=min_confidence)
    new = 0
    for rec in plan.records:
        rid = (rec.get("meta") or {}).get("id") or ""
        if rid and rid in consumed:
            continue
        new += 1
    return new


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


# -- Quality curation (LAMARK) ---------------------------------------
#
# The archive accumulates real Telegram exchanges at confidence 0.5 - a
# mix of good behaviour and debugging noise (tool-not-found messages,
# stale refusals, broken voice transcriptions, our own triage-injection
# text). Training on the noise teaches the model to reproduce bugs we
# already fixed. So before building the nightly plan we curate:
#
#   1. Regex pre-kill - structural / language-neutral failure markers
#      dropped for free.
#   2. Local-LLM judge - the model rates each surviving REAL pair
#      "good example of how to behave? keep/drop". Classification is the
#      model's strength even though recall isn't (same bet as triage),
#      and it catches language-specific garbage the regex can't (the
#      regex stays English-only; the multilingual judge covers the rest).
#      Verdicts are cached in a sidecar so re-runs don't re-judge.
#   3. Synthetic decay - the 500 cold-start bootstrap pairs were
#      scaffolding; as real conversations accumulate we taper synthetic
#      down (keeping a small identity floor so identity never regresses).
#
# Honest limit: the judge CANNOT catch factual errors / the model's own
# confabulations (it shares the blind spot). It catches behaviour/format
# garbage, which is the bulk of the noise. eval_gate is the backstop.

# Structural + language-neutral failure signatures -> instant drop, no
# LLM needed. Codebase is English-only; non-English failure phrasings are
# left to the multilingual LLM judge rather than hardcoded here.
_GARBAGE_PATTERNS = [
    r"does not exist\. Available tools",
    r"\bask_cloud\b.{0,30}\b(not found|unavailable|does not exist)\b",
    r"\btool\b.{0,20}\bnot found\b",
]
_GARBAGE_RX = re.compile("|".join(_GARBAGE_PATTERNS), re.IGNORECASE)

# Our own triage escalation prefix (A.13) must never become training data.
_TRIAGE_PREFIX = "[This question is hard"

# evidence_path prefixes that mark cold-start synthetic scaffolding.
_SYNTH_PREFIXES = ("quality-seed:", "simulated-chat:", "synthetic-seed:")
# Hand-written identity anchors we keep a floor of even when decaying.
_GOLD_PREFIX = "quality-seed:gold-handcrafted"


def _evidence(meta: dict) -> str:
    return str(meta.get("evidence_path") or "")


def _is_synthetic(meta: dict) -> bool:
    return _evidence(meta).startswith(_SYNTH_PREFIXES)


def _regex_garbage(record: dict) -> bool:
    """True if the pair is obvious debug/failure noise → drop without LLM."""
    msgs = record.get("messages") or []
    user = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
    asst = next((m.get("content", "") for m in msgs if m.get("role") == "assistant"), "")
    if user.lstrip().startswith(_TRIAGE_PREFIX):
        return True
    if not asst.strip():
        return True
    return bool(_GARBAGE_RX.search(asst))


# --- LLM judge with sidecar verdict cache ---------------------------

_JUDGE_SYSTEM = (
    "You are a training-data curator for a personal AI assistant named "
    "Lamark. Decide if a (user, assistant) exchange should be DROPPED from "
    "training. Be CONSERVATIVE — drop ONLY clear failures. Output ONLY "
    "JSON: {\"keep\": bool, \"reason\": \"<short>\"}.\n\n"
    "DROP (keep=false) ONLY when the assistant reply is clearly one of:\n"
    "  - an error / 'not found' / 'unavailable' / tool-failure message\n"
    "  - a debugging or config artifact (logs, PIDs, 'I configured X')\n"
    "  - an admission that a previous answer was wrong, or a self-correction\n"
    "  - cut off / truncated / incoherent\n"
    "  - a confused reply to garbled or broken input\n"
    "  - says 'I can't / I don't have access' to something it actually can do\n\n"
    "KEEP (keep=true) everything else. In particular ALWAYS KEEP:\n"
    "  - creative answers, analogies, metaphors, explanations 'X on the "
    "example of Y' — these are GOOD on-character style, not failures\n"
    "  - jokes, stories, poems when asked for them\n"
    "  - normal helpful answers, even if you cannot verify their facts "
    "(factual accuracy is OUT OF SCOPE — judge only BEHAVIOUR/FORM)\n"
    "  - web-grounded answers with sources\n\n"
    "When in any doubt, keep=true. Dropping a good answer is worse than "
    "keeping a mediocre one."
)


def _judge_cache_path(archive: Archive) -> Path:
    return Path(archive.incoming_dir).parent / "curation_cache.json"


def _load_judge_cache(archive: Archive) -> dict:
    p = _judge_cache_path(archive)
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except (ValueError, OSError):
            return {}
    return {}


def _save_judge_cache(archive: Archive, cache: dict) -> None:
    try:
        _judge_cache_path(archive).write_text(json.dumps(cache, ensure_ascii=False))
    except OSError as exc:
        logger.debug("curation: cache write failed (%s)", exc)


def _llm_judge(record: dict, *, model: str) -> bool:
    """Ask the local model to keep/drop one pair. Fail-open (keep) on error."""
    try:
        from agent.auxiliary_client import call_llm
    except Exception:
        return True  # no judge available → don't drop

    msgs = record.get("messages") or []
    user = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
    asst = next((m.get("content", "") for m in msgs if m.get("role") == "assistant"), "")
    sample = f"USER: {user[:600]}\nASSISTANT: {asst[:1200]}"
    try:
        resp = call_llm(
            provider="custom", model=model,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": sample},
            ],
            max_tokens=200, temperature=0, timeout=30,
        )
        m = resp.choices[0].message
        content = (getattr(m, "content", None) or getattr(m, "reasoning_content", None)
                   or getattr(m, "reasoning", None) or "")
    except Exception as exc:
        logger.debug("curation: judge call failed (%s) — keeping", exc)
        return True

    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return True  # unparseable → keep (fail-open)
    try:
        verdict = json.loads(match.group(0))
        return bool(verdict.get("keep", True))
    except (ValueError, TypeError):
        return True


def _synthetic_keep_fraction(real_count: int) -> float:
    """Decay synthetic share as real conversations accumulate.

    Cold start (few real pairs) → keep all synthetic (it's the only signal).
    As real grows, taper: by ~300 real pairs synthetic is fully phased out
    except the gold identity floor.
    """
    if real_count < 50:
        return 1.0
    if real_count >= 300:
        return 0.0
    # Linear ramp 50→300 real pairs maps to 1.0→0.0 synthetic fraction.
    return max(0.0, 1.0 - (real_count - 50) / 250.0)


def build_nightly_plan(
    archive: Archive | None = None,
    *,
    lamark_home: str | None = None,
    model: str = "lamark",
    judge: bool = True,
) -> CurationPlan:
    """Curation entry point used by `scripts/lamark-nightly-train.sh`.

    Pipeline: confidence/source/sensitivity filter (via build_plan) →
    regex garbage pre-kill → local-LLM behaviour judge on real pairs
    (cached) → synthetic decay. Returns the curated CurationPlan.

    `judge=False` skips the LLM pass (regex + decay only) — used by tests
    and as a fallback when no local model is reachable.
    """
    if archive is None:
        root = Path(lamark_home or os.environ.get("LAMARK_HOME", str(Path.home() / ".lamark"))) / "archive"
        archive = Archive.open(root)

    base = build_plan(archive, min_confidence=NIGHTLY_MIN_CONFIDENCE)

    real, synthetic = [], []
    dropped_garbage = 0
    for rec in base.records:
        if _regex_garbage(rec):
            dropped_garbage += 1
            continue
        (synthetic if _is_synthetic(rec.get("meta", {})) else real).append(rec)

    # LLM judge on REAL pairs (the noisy ones), cached by pair id.
    kept_real = []
    dropped_judge = 0
    if judge and real:
        cache = _load_judge_cache(archive)
        dirty = False
        for rec in real:
            pid = rec.get("meta", {}).get("id") or ""
            if pid in cache:
                keep = cache[pid]
            else:
                keep = _llm_judge(rec, model=model)
                if pid:
                    cache[pid] = keep
                    dirty = True
            if keep:
                kept_real.append(rec)
            else:
                dropped_judge += 1
        if dirty:
            _save_judge_cache(archive, cache)
    else:
        kept_real = real

    # Synthetic decay: keep a fraction that shrinks as real grows, but
    # always keep the gold identity anchors (a floor) so identity holds.
    frac = _synthetic_keep_fraction(len(kept_real))
    kept_synth = []
    gold = [r for r in synthetic if _evidence(r.get("meta", {})).startswith(_GOLD_PREFIX)]
    nongold = [r for r in synthetic if not _evidence(r.get("meta", {})).startswith(_GOLD_PREFIX)]
    if frac >= 1.0:
        kept_synth = synthetic
    else:
        # Always keep gold; sample non-gold by the decay fraction.
        keep_n = int(len(nongold) * frac)
        kept_synth = gold + nongold[:keep_n]

    records = kept_real + kept_synth
    return CurationPlan(
        records=tuple(records),
        archive_total=base.archive_total,
        filters_applied={
            "min_confidence": NIGHTLY_MIN_CONFIDENCE,
            "real_kept": len(kept_real),
            "synthetic_kept": len(kept_synth),
            "dropped_garbage": dropped_garbage,
            "dropped_by_judge": dropped_judge,
            "synthetic_keep_fraction": round(frac, 3),
            "judge_enabled": judge,
        },
    )
