"""Lamark triage layer — route questions to web_search / ask_cloud / local.

A small local model confabulates instead of admitting ignorance, so we
route on the QUESTION'S INTENT (a robust classification task) rather than
the model's self-reported confidence (unreliable). Factual questions are
force-grounded via web_search; reasoning questions get a nudge toward the
ask_cloud escalation tool.

Public entry: `apply(event)` — called from LAMARK-PATCH A.13 in
vendor/hermes/gateway/run.py just before the agent runs. Reads a config
toggle so it can be disabled without touching code.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .router import apply as _apply

logger = logging.getLogger("lamark.triage")

__all__ = ["apply", "is_enabled"]

_CONFIG_CACHE: dict | None = None


def _load_triage_config() -> dict:
    """Read the `triage:` block from $HERMES_HOME/config.yaml (cached)."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    cfg: dict = {}
    try:
        import yaml
        home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".lamark/hermes-home")))
        path = home / "config.yaml"
        if path.is_file():
            full = yaml.safe_load(path.read_text()) or {}
            cfg = full.get("triage") or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("triage: config read failed (%s) — defaults", exc)
    _CONFIG_CACHE = cfg if isinstance(cfg, dict) else {}
    return _CONFIG_CACHE


def is_enabled() -> bool:
    """Triage is on by default; set `triage.enabled: false` to disable."""
    return bool(_load_triage_config().get("enabled", True))


def apply(event) -> None:
    """Classify + route one inbound event. No-op when disabled."""
    if not is_enabled():
        return
    model = str(_load_triage_config().get("model") or "lamark")
    _apply(event, model=model)
