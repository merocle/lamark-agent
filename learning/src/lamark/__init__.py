"""Lamark — Python training pipeline (LoRA/SFT/DPO).

Fork of Hermes Agent (Nous Research, MIT). See LICENSE and README.md.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("lamark-train")
except PackageNotFoundError:
    __version__ = "0.1.0a0"

__all__ = ["__version__"]
