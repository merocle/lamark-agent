"""
Shared pytest fixtures for Lamark.

Notes:
- All tests must be deterministic and Spark-independent unless marked @pytest.mark.spark.
- Test-isolated tmpdir is mandatory for anything that touches user memory / config.
- Never touch ~/.lamark from within tests.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def isolated_lamark_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect LAMARK_HOME (and downstream XDG paths) into a tmpdir.

    Use this whenever a test indirectly loads config, opens the DB, or writes memory.
    Forgetting this is the #1 way to corrupt your own user data with a test.
    """
    home = tmp_path / "lamark"
    home.mkdir(parents=True)
    monkeypatch.setenv("LAMARK_HOME", str(home))
    monkeypatch.delenv("LAMARK_CONFIG", raising=False)
    yield home


@pytest.fixture
def cli_runner():
    """Typer / Click test runner for CLI invocations.

    Click 8.2 removed the `mix_stderr` kwarg; default behaviour streams stderr
    separately on modern Click, so tests check both result.stdout and result.stderr.
    """
    from typer.testing import CliRunner

    return CliRunner()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip @spark-marked tests if LAMARK_SPARK_HOST is not set.

    Allows local pytest runs to pass without the Spark, while CI on Spark
    (where LAMARK_SPARK_HOST is set) runs the full suite.
    """
    if os.environ.get("LAMARK_SPARK_HOST"):
        return  # On Spark — run everything
    skip_spark = pytest.mark.skip(reason="requires Spark; set LAMARK_SPARK_HOST to run")
    for item in items:
        if "spark" in item.keywords:
            item.add_marker(skip_spark)


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers so pytest doesn't warn about them."""
    config.addinivalue_line("markers", "spark: test that requires a live DGX Spark backend")
    config.addinivalue_line("markers", "integration: integration test (live model load, slow)")
    config.addinivalue_line("markers", "slow: takes > 5 seconds locally")
