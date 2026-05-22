"""Lamark Day-0 bootstrap wizard.

Closes the cold-start UX gap: takes Lamark from "knows nothing" → "has 100-200
seed facts in Honcho user-model" in 15-20 minutes via interactive prompts or
explicit flags.
"""

from __future__ import annotations

from lamark.bootstrap.wizard import BootstrapResult, run_bootstrap

__all__ = ["run_bootstrap", "BootstrapResult"]
