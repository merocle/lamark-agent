"""
Shared pytest fixtures for Lamark training pipeline.

Notes:
- All tests must be deterministic and Spark-independent unless marked @pytest.mark.spark.
- Test-isolated tmpdir is mandatory for anything that touches the archive or traces.
- Never touch ~/.lamark from within tests.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def isolated_lamark_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect LAMARK_HOME into a tmpdir for tests that write archive/trace files."""
    home = tmp_path / "lamark"
    home.mkdir(parents=True)
    monkeypatch.setenv("LAMARK_HOME", str(home))
    yield home


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
