"""Resilient model download — a retrying wrapper around huggingface_hub.

`snapshot_download` already skips files it has fully fetched, so wrapping it in
a retry loop gives effective resumability at FILE granularity: a dropped
connection re-runs the call and continues from the next missing file. Only the
in-flight file is re-fetched — which matters when `HF_HUB_ENABLE_HF_TRANSFER=1`
disables partial-file resume. A 67 GB pull no longer restarts from zero on a
single network blip.

Used by the install / setup / switch-base scripts (which invoke it from a
small python heredoc). Kept dependency-light and lazy-imported so importing
this module never requires huggingface_hub.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("lamark.download")


def fetch_model(
    repo_id: str,
    local_dir: str,
    *,
    max_workers: int = 8,
    attempts: int = 5,
    base_delay: float = 5.0,
    max_delay: float = 60.0,
    _downloader: Optional[Callable[..., Any]] = None,
    _sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Download a HF repo snapshot into ``local_dir``, retrying on failure.

    Retries the whole `snapshot_download` call up to ``attempts`` times with
    linear backoff (capped at ``max_delay``). Because the call is idempotent
    and resumes by skipping completed files, each retry continues rather than
    restarting. Raises RuntimeError if every attempt fails.

    ``_downloader`` / ``_sleep`` are injection seams for tests.
    """
    if _downloader is None:
        from huggingface_hub import snapshot_download as _downloader  # lazy

    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            return _downloader(repo_id=repo_id, local_dir=local_dir, max_workers=max_workers)
        except Exception as exc:  # noqa: BLE001 - network/timeout/partial; retry
            last_exc = exc
            if attempt == attempts:
                break
            delay = min(max_delay, base_delay * attempt)
            logger.warning(
                "download attempt %d/%d for %s failed (%s); retrying in %.0fs",
                attempt, attempts, repo_id, exc, delay,
            )
            _sleep(delay)
    raise RuntimeError(
        f"model download failed after {attempts} attempts: {last_exc}"
    )
