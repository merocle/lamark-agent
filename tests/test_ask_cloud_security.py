"""Security regressions for ask_cloud (LAMARK-PATCH A.10, hardened 2026-05-30).

Locks two fixes:
- P3-1: no baked-in proxy URL. _call_litellm and _check_availability must
  refuse when LITELLM_BASE_URL is unset (the old code defaulted to an
  internal host).
- P3-2: redaction fails CLOSED on the cloud-egress path — any pipeline
  error aborts the call instead of shipping unredacted text.

These import the patched vendored tool directly; they run only with
vendor/hermes/ on sys.path (skipped elsewhere).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

VENDOR_HERMES = Path(__file__).resolve().parent.parent / "vendor" / "hermes"


@pytest.fixture(autouse=True)
def _hermes_on_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(VENDOR_HERMES))
    for mod in list(sys.modules):
        if mod == "tools.ask_cloud_tool":
            del sys.modules[mod]


def _import_tool():
    try:
        import tools.ask_cloud_tool as t  # noqa: WPS433
    except ImportError:
        pytest.skip("vendored Hermes tool registry not importable in this env")
    return t


# -- P3-1: no default proxy host -------------------------------------

def test_call_litellm_refuses_without_base_url(monkeypatch):
    t = _import_tool()
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="LITELLM_BASE_URL"):
        t._call_litellm("openai/gpt-5.5", "hello")


def test_check_availability_requires_base_url(monkeypatch):
    t = _import_tool()
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    ok, reason = t._check_availability()
    assert ok is False
    assert "LITELLM_BASE_URL" in reason


def test_no_jb_gg_default_constant():
    t = _import_tool()
    # The internal-infra default must be gone entirely.
    assert not hasattr(t, "DEFAULT_BASE_URL")


# -- P3-2: redaction fails closed on egress --------------------------

def test_redaction_runtime_error_fails_closed(monkeypatch):
    t = _import_tool()
    try:
        from lamark.redaction import RedactionPipeline
    except ImportError:
        pytest.skip("lamark.redaction not importable")

    def _boom(self, _text):
        raise RuntimeError("regex engine blew up")

    monkeypatch.setattr(RedactionPipeline, "process", _boom)
    # Must raise ValueError (→ tool_error) rather than returning the raw text.
    with pytest.raises(ValueError, match="refusing to send to cloud"):
        t._redact_or_raise("some task with maybe-PII")
