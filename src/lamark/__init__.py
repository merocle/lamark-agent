"""Lamark — locally-hosted personal AI agent.

Fork of Hermes Agent (Nous Research, MIT). See LICENSE and README.md.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("lamark-agent")
except PackageNotFoundError:
    # Running from source without an installed dist; fall back to pyproject value.
    # Kept in sync with pyproject.toml; tests assert equality.
    __version__ = "0.1.0a1"

__all__ = ["__version__"]
