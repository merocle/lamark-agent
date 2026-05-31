"""Tests for the resilient model-download wrapper (src/lamark/download.py)."""
from __future__ import annotations

import pytest

from lamark.download import fetch_model


def test_returns_on_first_success():
    calls = {"n": 0}

    def ok(**kw):
        calls["n"] += 1
        return "/models/x"

    out = fetch_model("org/m", "/dir", _downloader=ok, _sleep=lambda _: None)
    assert out == "/models/x"
    assert calls["n"] == 1


def test_retries_then_succeeds():
    calls = {"n": 0}
    slept = []

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("blip")
        return "/models/x"

    out = fetch_model("org/m", "/dir", _downloader=flaky, _sleep=slept.append)
    assert out == "/models/x"
    assert calls["n"] == 3
    assert slept == [5.0, 10.0]  # linear backoff before attempts 2 and 3


def test_raises_after_exhausting_attempts():
    def always_fail(**kw):
        raise TimeoutError("down")

    with pytest.raises(RuntimeError, match="after 3 attempts"):
        fetch_model("org/m", "/dir", attempts=3, _downloader=always_fail, _sleep=lambda _: None)


def test_backoff_is_capped():
    slept = []

    def always_fail(**kw):
        raise OSError("x")

    with pytest.raises(RuntimeError):
        fetch_model("org/m", "/dir", attempts=20, base_delay=5.0, max_delay=60.0,
                    _downloader=always_fail, _sleep=slept.append)
    assert max(slept) == 60.0
    assert slept[-1] == 60.0


def test_passes_through_args():
    seen = {}

    def capture(**kw):
        seen.update(kw)
        return "/d"

    fetch_model("org/m", "/dir", max_workers=4, _downloader=capture, _sleep=lambda _: None)
    assert seen == {"repo_id": "org/m", "local_dir": "/dir", "max_workers": 4}
