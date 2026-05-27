"""
Test Plan A.5 — `lamark train` CLI for forced fine-tune.

This is a SKELETON. Actual LoRA training runs on Spark inside the
lamark/vllm container — those tests live behind @pytest.mark.spark.
What we test here:

- `lamark train --plan` prints the curation plan without doing anything
  (lets the user inspect what would be trained on)
- `lamark train --now` exits early with a clear message when archive
  is empty or below the threshold (default 50 paired examples)
- `lamark train --now --no-threshold` bypasses the data-volume check
- `lamark train --status` reports archive size + last-trained-on lineage
- Curation logic: filters by source, confidence, recency

The actual training subprocess (Unsloth + DoRA on Spark) is dispatched
via SSH and is out of scope for unit tests — it goes through Module 14
in the v4 roadmap.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _seed_archive(archive, n_pairs: int, source: str = "user_explicit", confidence: float = 0.9):
    """Helper: write n_pairs ChatML records to archive."""
    for i in range(n_pairs):
        archive.write_pair(
            messages=[
                {"role": "user", "content": f"prompt {i}"},
                {"role": "assistant", "content": f"reply {i}"},
            ],
            source=source,
            confidence=confidence,
            evidence_path=f"test_seed:{i}",
        )


def test_train_status_reports_empty_archive(cli_runner, isolated_lamark_home: Path) -> None:
    """`lamark train --status` on empty archive reports 0 records, not ready."""
    from lamark.cli import app

    result = cli_runner.invoke(app, ["train", "--status"])
    assert result.exit_code == 0
    assert "0" in result.stdout
    assert "not ready" in result.stdout.lower() or "empty" in result.stdout.lower()


def test_train_status_counts_archive_records(cli_runner, isolated_lamark_home: Path) -> None:
    """`lamark train --status` counts total + curatable records."""
    from lamark.archive import Archive
    from lamark.cli import app

    archive = Archive.open(isolated_lamark_home / "archive")
    _seed_archive(archive, n_pairs=10)

    result = cli_runner.invoke(app, ["train", "--status"])
    assert result.exit_code == 0
    assert "10" in result.stdout


def test_train_plan_prints_curation_without_running(
    cli_runner, isolated_lamark_home: Path
) -> None:
    """`lamark train --plan` lists candidate pairs but does NOT call trainer."""
    from lamark.archive import Archive
    from lamark.cli import app

    archive = Archive.open(isolated_lamark_home / "archive")
    _seed_archive(archive, n_pairs=5)

    result = cli_runner.invoke(app, ["train", "--plan"])
    assert result.exit_code == 0
    # Should mention curation plan and count
    out = result.stdout.lower()
    assert "plan" in out or "candidate" in out or "would train" in out


def test_train_now_refuses_below_threshold(
    cli_runner, isolated_lamark_home: Path
) -> None:
    """`lamark train --now` with fewer than threshold records → refuses."""
    from lamark.archive import Archive
    from lamark.cli import app

    archive = Archive.open(isolated_lamark_home / "archive")
    _seed_archive(archive, n_pairs=3)  # below default threshold of 50

    result = cli_runner.invoke(app, ["train", "--now"])
    # exit 2 = data not ready (a guard, not a bug)
    assert result.exit_code != 0
    out = result.stdout + (result.stderr or "")
    assert "threshold" in out.lower() or "not enough" in out.lower() or "need" in out.lower()


def test_train_now_bypass_threshold_proceeds(
    cli_runner, isolated_lamark_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`lamark train --now --no-threshold` runs the plan even with few records.

    The actual trainer is monkeypatched so we don't try to run Unsloth here.
    """
    from lamark.archive import Archive
    from lamark.cli import app

    archive = Archive.open(isolated_lamark_home / "archive")
    _seed_archive(archive, n_pairs=3)

    # Stub the trainer dispatch — we just want to verify control reaches it.
    called: dict = {}

    def fake_dispatch(plan, *, dry_run: bool = False, **kw) -> dict:
        called["plan_size"] = len(plan)
        called["dry_run"] = dry_run
        return {"ok": True, "would_train_on": len(plan), "dry_run": dry_run}

    # CLI imports dispatch_training from the package namespace, not runner module
    monkeypatch.setattr("lamark.train.dispatch_training", fake_dispatch)
    monkeypatch.setattr("lamark.train.runner.dispatch_training", fake_dispatch)

    result = cli_runner.invoke(app, ["train", "--now", "--no-threshold"])
    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    assert called["plan_size"] == 3


def test_train_filters_excludes_low_confidence_pairs(
    cli_runner, isolated_lamark_home: Path
) -> None:
    """Pairs with confidence below --min-confidence are excluded from plan."""
    from lamark.archive import Archive
    from lamark.cli import app

    archive = Archive.open(isolated_lamark_home / "archive")
    _seed_archive(archive, n_pairs=5, source="user_explicit", confidence=0.95)
    _seed_archive(archive, n_pairs=10, source="agent_self_edit", confidence=0.4)

    # Default min-confidence = 0.7
    result = cli_runner.invoke(app, ["train", "--plan"])
    assert result.exit_code == 0
    out = result.stdout
    # Plan should show only 5 (the high-confidence batch)
    assert "5" in out


def test_train_filters_excludes_self_edit_by_default(
    cli_runner, isolated_lamark_home: Path
) -> None:
    """agent_self_edit pairs excluded unless --include-agent-edits."""
    from lamark.archive import Archive
    from lamark.cli import app

    archive = Archive.open(isolated_lamark_home / "archive")
    _seed_archive(archive, n_pairs=4, source="user_explicit", confidence=0.95)
    _seed_archive(archive, n_pairs=6, source="agent_self_edit", confidence=0.95)

    result = cli_runner.invoke(app, ["train", "--plan"])
    assert result.exit_code == 0
    # Plan should show 4 (only user_explicit)
    assert "4" in result.stdout


def test_train_dry_run_does_not_call_dispatch(
    cli_runner, isolated_lamark_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`lamark train --now --dry-run` walks the pipeline without invoking trainer."""
    from lamark.archive import Archive
    from lamark.cli import app

    archive = Archive.open(isolated_lamark_home / "archive")
    _seed_archive(archive, n_pairs=100)  # above threshold

    called: dict = {"dispatched": False}

    def fake_dispatch(*a, **kw):
        called["dispatched"] = True
        return {"ok": True}

    monkeypatch.setattr("lamark.train.runner.dispatch_training", fake_dispatch)

    result = cli_runner.invoke(app, ["train", "--now", "--dry-run"])
    assert result.exit_code == 0
    # dry-run should NOT call the real dispatcher
    assert called["dispatched"] is False
