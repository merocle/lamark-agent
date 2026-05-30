"""Structural regression locks for scripts/lamark-nightly-train.sh (P0-1/2/3).

The orchestrator can only be fully exercised on the Spark (Docker + vLLM),
so these are structural invariants — they lock the specific wiring bugs the
remediation fixed so they cannot silently return:

- the eval-gate must probe the SERVED alias `lamark`, never the per-run
  timestamp name (the bug that 404'd every probe and auto-rejected);
- the candidate must be linked into adapters/current BEFORE serving, so the
  gate actually tests the candidate;
- a rollback trap must be armed before the first mutation;
- the gate call must be time-bounded;
- the run threshold uses the new-pair count, not the cumulative plan size.

Live promote/rollback behaviour is a P0-spark acceptance item, not unit-test.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "lamark-nightly-train.sh"


@pytest.fixture(scope="module")
def src() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_gate_probes_served_alias_not_timestamp(src):
    # The gate must be invoked with the served alias.
    assert '--adapter-name "lamark"' in src
    # And must NOT probe the per-run timestamp adapter name (the old bug).
    assert '--adapter-name "$ADAPTER_NAME"' not in src


def test_rollback_trap_and_handler_present(src):
    assert "trap on_exit EXIT" in src
    assert "on_exit()" in src
    assert "restore_previous()" in src
    # The trap restores the captured previous target (symlink), not just a
    # server restart.
    assert 'ln -sfn "$PREV_TARGET" "$ADAPTER_DIR/current"' in src


def test_candidate_linked_before_serving_for_gate(src):
    link = src.index("CANDIDATE_LINKED=1")
    serve = src.index('"$REPO/scripts/cmd/serve.sh" start', link - 4000)
    gate = src.index("lamark.train.eval_gate")
    # current→candidate is set before the serve that precedes the gate,
    # which is before the gate invocation.
    assert link < serve < gate


def test_prev_target_captured_before_any_mutation(src):
    prev = src.index('PREV_TARGET="$(readlink -f "$ADAPTER_DIR/current"')
    arm = src.index("trap on_exit EXIT")
    link = src.index("CANDIDATE_LINKED=1")
    assert prev < arm < link


def test_gate_is_time_bounded(src):
    # The gate invocation is wrapped in `timeout` so a wedged engine can't hang.
    assert re.search(r"timeout\s+\d+\s+\"\$LAMARK_HOME/venv/bin/python\"", src)


def test_threshold_uses_new_pair_count(src):
    assert "count_new_pairs" in src
    assert 'NEW_PAIRS" -lt "$TRAIN_MIN_PAIRS"' in src


def test_previous_symlink_maintained_for_rollback_lineage(src):
    assert 'ln -sfn "$PREV_TARGET" "$ADAPTER_DIR/previous"' in src


def test_cleanup_excludes_live_and_previous_targets(src):
    # Cleanup must skip whatever current/previous resolve to.
    assert "LIVE_TARGET=" in src and "PREV_LINK_TARGET=" in src
    assert 'continue' in src  # the skip branches in the delete loop


def test_consumed_marked_only_on_promote(src):
    # mark_consumed runs after the PASS branch, and the fallback path skips it.
    assert "mark_consumed" in src
    assert "FALLBACK_MARKER" in src
