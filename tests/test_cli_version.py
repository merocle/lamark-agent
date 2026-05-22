"""
Test Module 1 — CLI version + entry point.

These tests are RED before src/lamark/__init__.py and src/lamark/cli.py exist.
After implementation, they go GREEN. The two-commit RED → GREEN trail is the
discriminator that proves the version-plumbing works.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def _pyproject_version() -> str:
    """Read the canonical project version from pyproject.toml."""
    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def test_pyproject_version_is_pre_alpha() -> None:
    """Sanity guard — we are pre-alpha, do not accidentally ship a stable version."""
    version = _pyproject_version()
    assert "alpha" in version.lower() or version.startswith("0."), (
        f"version {version!r} doesn't look pre-1.0; bump intentionally"
    )


def test_lamark_package_exposes_version() -> None:
    """`from lamark import __version__` must work and match pyproject."""
    import lamark

    assert hasattr(lamark, "__version__"), "lamark package must expose __version__"
    assert lamark.__version__ == _pyproject_version(), (
        f"lamark.__version__ {lamark.__version__!r} != pyproject {_pyproject_version()!r}"
    )


def test_cli_version_flag_prints_version(cli_runner) -> None:
    """`lamark --version` exits 0 and prints the version string."""
    from lamark.cli import app

    result = cli_runner.invoke(app, ["--version"])
    assert result.exit_code == 0, f"non-zero exit: {result.exit_code}\n{result.stderr}"
    assert _pyproject_version() in result.stdout, (
        f"version {_pyproject_version()!r} not in --version output: {result.stdout!r}"
    )


def test_cli_no_args_shows_help(cli_runner) -> None:
    """`lamark` with no args shows help, not an error stack trace."""
    from lamark.cli import app

    result = cli_runner.invoke(app, [])
    # typer's default behaviour for a missing subcommand is exit code 2 with help on stderr
    assert result.exit_code in (0, 2), f"unexpected exit: {result.exit_code}"
    combined = (result.stdout or "") + (result.stderr or "")
    assert "Usage" in combined or "Commands" in combined, (
        f"help text not present in either stream: {combined!r}"
    )


def test_cli_has_config_show_command(cli_runner) -> None:
    """`lamark config show` is registered (implementation comes in next test file)."""
    from lamark.cli import app

    result = cli_runner.invoke(app, ["config", "show", "--help"])
    assert result.exit_code == 0, f"`lamark config show --help` failed: {result.stderr}"
