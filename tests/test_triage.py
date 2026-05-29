"""Tests for the Lamark triage layer (src/lamark/triage)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from lamark.triage import classifier as C
from lamark.triage import router as R


@dataclass
class _FakeEvent:
    text: str = ""
    channel_prompt: Optional[str] = None
    internal: bool = False


# ── regex pre-filter (no LLM) ───────────────────────────────────────

def test_prefilter_explicit_cloud():
    for t in ["спроси Claude про энтропию", "ask GPT to refactor this",
              "use opus для этого", "передай Клоду вопрос"]:
        assert C._regex_prefilter(t) == "explicit_cloud", t


def test_prefilter_casual():
    for t in ["привет", "спасибо!", "ок", "thanks", "ага", "hi"]:
        assert C._regex_prefilter(t) == "casual", t


def test_prefilter_short_question_is_not_casual():
    # A short question must NOT be swallowed as casual.
    assert C._regex_prefilter("почему?") is None
    assert C._regex_prefilter("кто это?") is None


def test_prefilter_ambiguous_falls_through():
    assert C._regex_prefilter("когда родился Жан-Батист Ламарк") is None


def test_classify_shortcircuits_without_llm(monkeypatch):
    # If a regex matches, _llm_classify must never be called.
    called = {"n": 0}
    monkeypatch.setattr(C, "_llm_classify", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {})
    assert C.classify("привет")["intent"] == "casual"
    assert C.classify("спроси Claude X")["intent"] == "explicit_cloud"
    assert called["n"] == 0


# ── classification JSON parsing + fallback ──────────────────────────

def test_parse_clean_json():
    out = C._parse_classification('{"intent":"factual","needs_web":true,"needs_cloud":false}')
    assert out == {"intent": "factual", "needs_web": True, "needs_cloud": False}


def test_parse_json_with_fences_and_prose():
    noisy = "Here is the result:\n```json\n{\"intent\": \"reasoning\", \"needs_web\": false, \"needs_cloud\": true}\n```"
    out = C._parse_classification(noisy)
    assert out["intent"] == "reasoning" and out["needs_cloud"] is True


def test_parse_rejects_unknown_intent():
    assert C._parse_classification('{"intent":"banana"}') is None


def test_parse_malformed_returns_none():
    assert C._parse_classification("not json at all") is None
    assert C._parse_classification("") is None


def test_classify_fallback_on_llm_failure(monkeypatch):
    monkeypatch.setattr(C, "_llm_classify", lambda *a, **k: None)
    out = C.classify("когда родился Ламарк")
    assert out["intent"] == "unknown"


# ── router: factual → web grounding ─────────────────────────────────

_FAKE_SEARCH_OK = (
    '{"success": true, "data": {"web": ['
    '{"title": "Jean-Baptiste Lamarck", "description": "Born 1 August 1744.", '
    '"url": "https://example.org/lamarck", "position": 1}'
    ']}}'
)


def test_factual_injects_grounding(monkeypatch):
    monkeypatch.setattr(C, "classify",
                        lambda t, **k: {"intent": "factual", "needs_web": True, "needs_cloud": False})
    monkeypatch.setattr(R, "classify", C.classify)
    # web_tools is imported inside _ground_factual; patch at source module.
    import sys, types
    fake = types.ModuleType("tools.web_tools")
    fake.web_search_tool = lambda q, limit=5: _FAKE_SEARCH_OK
    monkeypatch.setitem(sys.modules, "tools.web_tools", fake)

    ev = _FakeEvent(text="когда родился Жан-Батист Ламарк")
    R.apply(ev, model="lamark")
    assert ev.channel_prompt is not None
    assert "1744" in ev.channel_prompt
    assert "ONLY from these results" in ev.channel_prompt


def test_factual_web_failure_injects_caveat(monkeypatch):
    monkeypatch.setattr(R, "classify",
                        lambda t, **k: {"intent": "factual", "needs_web": True, "needs_cloud": False})
    import sys, types
    fake = types.ModuleType("tools.web_tools")
    def _boom(q, limit=5):
        raise RuntimeError("tavily down")
    fake.web_search_tool = _boom
    monkeypatch.setitem(sys.modules, "tools.web_tools", fake)

    ev = _FakeEvent(text="какой курс доллара сейчас")
    R.apply(ev, model="lamark")
    assert ev.channel_prompt is not None
    assert "uncertainty caveat" in ev.channel_prompt
    assert "web search unavailable" in ev.channel_prompt.lower()


def test_empty_web_results_injects_caveat(monkeypatch):
    monkeypatch.setattr(R, "classify",
                        lambda t, **k: {"intent": "factual", "needs_web": True, "needs_cloud": False})
    import sys, types
    fake = types.ModuleType("tools.web_tools")
    fake.web_search_tool = lambda q, limit=5: '{"success": true, "data": {"web": []}}'
    monkeypatch.setitem(sys.modules, "tools.web_tools", fake)

    ev = _FakeEvent(text="обскюрный факт")
    R.apply(ev, model="lamark")
    assert "uncertainty caveat" in (ev.channel_prompt or "")


# ── router: reasoning → ask_cloud nudge ─────────────────────────────

def test_needs_cloud_forces_ask_cloud(monkeypatch):
    # needs_cloud=True → imperative escalation directive.
    monkeypatch.setattr(R, "classify",
                        lambda t, **k: {"intent": "reasoning", "needs_web": False, "needs_cloud": True})
    ev = _FakeEvent(text="выведи уравнения поля Эйнштейна")
    R.apply(ev, model="lamark")
    assert "ask_cloud" in (ev.channel_prompt or "")
    assert "FIRST action MUST" in ev.channel_prompt


def test_reasoning_without_needs_cloud_stays_local(monkeypatch):
    # A standard proof: intent reasoning but needs_cloud=False → no directive,
    # local answers (the √2 case the local model handles fine).
    monkeypatch.setattr(R, "classify",
                        lambda t, **k: {"intent": "reasoning", "needs_web": False, "needs_cloud": False})
    ev = _FakeEvent(text="докажи что корень из двух иррационален")
    R.apply(ev, model="lamark")
    assert ev.channel_prompt is None


def test_needs_cloud_preserves_existing_channel_prompt(monkeypatch):
    monkeypatch.setattr(R, "classify",
                        lambda t, **k: {"intent": "reasoning", "needs_web": False, "needs_cloud": True})
    ev = _FakeEvent(text="hard q", channel_prompt="EXISTING")
    R.apply(ev, model="lamark")
    assert ev.channel_prompt.startswith("EXISTING")
    assert "ask_cloud" in ev.channel_prompt


# ── router: no-op paths ─────────────────────────────────────────────

@pytest.mark.parametrize("intent", ["personal", "casual", "code", "explicit_cloud", "unknown"])
def test_noop_intents_leave_prompt_untouched(monkeypatch, intent):
    monkeypatch.setattr(R, "classify",
                        lambda t, **k: {"intent": intent, "needs_web": False, "needs_cloud": False})
    ev = _FakeEvent(text="whatever")
    R.apply(ev, model="lamark")
    assert ev.channel_prompt is None


def test_internal_event_skipped(monkeypatch):
    # Should not even classify internal events.
    monkeypatch.setattr(R, "classify", lambda t, **k: pytest.fail("should not classify"))
    ev = _FakeEvent(text="x", internal=True)
    R.apply(ev, model="lamark")
    assert ev.channel_prompt is None


def test_slash_command_skipped(monkeypatch):
    monkeypatch.setattr(R, "classify", lambda t, **k: pytest.fail("should not classify"))
    ev = _FakeEvent(text="/new")
    R.apply(ev, model="lamark")
    assert ev.channel_prompt is None


def test_apply_never_raises(monkeypatch):
    # Even if classify explodes, apply swallows it.
    monkeypatch.setattr(R, "classify", lambda t, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    ev = _FakeEvent(text="something")
    R.apply(ev, model="lamark")  # must not raise
    assert ev.channel_prompt is None
