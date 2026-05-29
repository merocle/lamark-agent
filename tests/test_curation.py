"""Tests for the curation pipeline (src/lamark/train/curation.py).

Focus on the LAMARK quality-curation additions: regex garbage pre-kill,
synthetic decay, and the judge-cache plumbing. The LLM judge itself is
stubbed (judge=False or monkeypatched) — we test the wiring, not the
local model's verdicts.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lamark.archive import Archive
from lamark.train import curation as Cur


def _mk_archive(tmp_path: Path) -> Archive:
    return Archive.open(tmp_path / "archive")


def _write(archive, user, asst, *, source="user_explicit", confidence=0.5, evidence=None):
    return archive.write_pair(
        messages=[{"role": "user", "content": user}, {"role": "assistant", "content": asst}],
        source=source, confidence=confidence, evidence_path=evidence,
    )


# ── regex garbage pre-kill ──────────────────────────────────────────

@pytest.mark.parametrize("asst", [
    "ask_cloud не найден — похоже, он не был установлен",
    "Tool 'ask_cloud' does not exist. Available tools: clarify, terminal",
    "Не получилось — gpt-5.5 не поддерживает параметр temperature. Это баг в прокси.",
    "",
])
def test_regex_drops_garbage_assistant(asst):
    rec = {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": asst}]}
    assert Cur._regex_garbage(rec) is True


def test_regex_drops_triage_injection():
    rec = {"messages": [
        {"role": "user", "content": "[Этот вопрос сложный — НЕ отвечай ...] выведи уравнения"},
        {"role": "assistant", "content": "ok"},
    ]}
    assert Cur._regex_garbage(rec) is True


def test_regex_keeps_good_pair():
    rec = {"messages": [
        {"role": "user", "content": "почему небо синее"},
        {"role": "assistant", "content": "Из-за рэлеевского рассеяния — короткие волны рассеиваются сильнее."},
    ]}
    assert Cur._regex_garbage(rec) is False


# ── synthetic decay ramp ────────────────────────────────────────────

def test_decay_fraction_ramp():
    assert Cur._synthetic_keep_fraction(0) == 1.0
    assert Cur._synthetic_keep_fraction(49) == 1.0
    assert Cur._synthetic_keep_fraction(300) == 0.0
    assert Cur._synthetic_keep_fraction(400) == 0.0
    mid = Cur._synthetic_keep_fraction(175)  # halfway 50→300
    assert 0.4 < mid < 0.6


# ── end-to-end build_nightly_plan (judge stubbed off) ───────────────

def test_plan_drops_garbage_keeps_good(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMARK_HOME", str(tmp_path))
    arch = _mk_archive(tmp_path)
    _write(arch, "почему небо синее", "Рэлеевское рассеяние.", evidence="telegram:s1")
    _write(arch, "x", "ask_cloud не найден", evidence="telegram:s1")
    _write(arch, "кто ты", "Я Lamark.", evidence="quality-seed:gold-handcrafted")

    plan = Cur.build_nightly_plan(arch, judge=False)
    fa = plan.filters_applied
    assert fa["dropped_garbage"] == 1
    # good real + gold synthetic kept
    contents = [m["content"] for r in plan.records for m in r["messages"] if m["role"] == "assistant"]
    assert "Рэлеевское рассеяние." in contents
    assert "Я Lamark." in contents
    assert "ask_cloud не найден" not in contents


def test_plan_judge_drops_pair(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMARK_HOME", str(tmp_path))
    arch = _mk_archive(tmp_path)
    _write(arch, "хороший вопрос", "хороший ответ", evidence="telegram:s1")
    _write(arch, "плохой вопрос", "плохой ответ", evidence="telegram:s1")

    # Stub judge: drop anything whose user text starts with "плохой".
    def fake_judge(rec, *, model):
        u = rec["messages"][0]["content"]
        return not u.startswith("плохой")
    monkeypatch.setattr(Cur, "_llm_judge", fake_judge)

    plan = Cur.build_nightly_plan(arch, judge=True)
    users = [m["content"] for r in plan.records for m in r["messages"] if m["role"] == "user"]
    assert "хороший вопрос" in users
    assert "плохой вопрос" not in users
    assert plan.filters_applied["dropped_by_judge"] == 1


def test_judge_verdict_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMARK_HOME", str(tmp_path))
    arch = _mk_archive(tmp_path)
    _write(arch, "q", "a", evidence="telegram:s1")

    calls = {"n": 0}
    def counting_judge(rec, *, model):
        calls["n"] += 1
        return True
    monkeypatch.setattr(Cur, "_llm_judge", counting_judge)

    Cur.build_nightly_plan(arch, judge=True)
    Cur.build_nightly_plan(arch, judge=True)  # second run should hit cache
    assert calls["n"] == 1  # judged once, cached thereafter


def test_synthetic_decay_keeps_gold_floor(tmp_path, monkeypatch):
    monkeypatch.setenv("LAMARK_HOME", str(tmp_path))
    arch = _mk_archive(tmp_path)
    # 60 real pairs → decay fraction < 1, non-gold synth partially dropped,
    # gold kept entirely.
    for i in range(60):
        _write(arch, f"real q {i}", f"real a {i}", evidence="telegram:s1")
    for i in range(20):
        _write(arch, f"auto q {i}", f"auto a {i}", evidence="quality-seed:auto-vllm")
    for i in range(10):
        _write(arch, f"gold q {i}", f"gold a {i}", evidence="quality-seed:gold-handcrafted")

    plan = Cur.build_nightly_plan(arch, judge=False)
    ev = [r["meta"]["evidence_path"] for r in plan.records]
    gold_kept = sum(1 for e in ev if e.startswith("quality-seed:gold-handcrafted"))
    auto_kept = sum(1 for e in ev if e.startswith("quality-seed:auto-vllm"))
    assert gold_kept == 10           # gold floor preserved
    assert auto_kept < 20            # some auto decayed away
    assert plan.filters_applied["synthetic_keep_fraction"] < 1.0
