"""
Tests for the L3 facts loader and the shipped lamark_facts.jsonl dataset.

The on-disk dataset is asserted to be loadable, non-trivial, and to satisfy
the ROME/MEMIT subject-in-prompt invariant. That last check is the most
load-bearing one: violations are silent at edit time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lamark.knowledge_edit.facts import FactsLoadError, load_facts

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "learning" / "data" / "lamark_facts.jsonl"


def test_shipped_dataset_loads_and_is_substantial() -> None:
    facts = load_facts(DATASET)
    assert len(facts) >= 10, "Lamark facts dataset should cover the ADR-0010 failure list"
    ids = {f.id for f in facts}
    # The four highest-priority disambiguations from ADR-0010 must be present.
    must_have = {"lamark-vs-lamarck", "lamark-self-id", "curator-not-langchain", "memory-four-layers"}
    missing = must_have - ids
    assert not missing, f"missing highest-priority facts: {missing}"


def test_shipped_dataset_satisfies_subject_in_prompt() -> None:
    facts = load_facts(DATASET)
    for f in facts:
        assert f.edit.subject in f.edit.prompt, (
            f"fact {f.id}: subject {f.edit.subject!r} not in prompt {f.edit.prompt!r}"
        )


def test_loader_rejects_missing_subject_in_prompt(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        '{"id": "x", "edit": {"prompt": "Foo is a place", "subject": "Bar", "target_new": "ok"}}\n',
        encoding="utf-8",
    )
    with pytest.raises(FactsLoadError, match="does not appear verbatim"):
        load_facts(bad)


def test_loader_rejects_duplicate_id(tmp_path: Path) -> None:
    dup = tmp_path / "dup.jsonl"
    dup.write_text(
        '{"id": "x", "edit": {"prompt": "Foo", "subject": "Foo", "target_new": "ok"}}\n'
        '{"id": "x", "edit": {"prompt": "Bar", "subject": "Bar", "target_new": "ok"}}\n',
        encoding="utf-8",
    )
    with pytest.raises(FactsLoadError, match="duplicate id"):
        load_facts(dup)


def test_loader_rejects_invalid_json(tmp_path: Path) -> None:
    junk = tmp_path / "junk.jsonl"
    junk.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(FactsLoadError, match="not valid JSON"):
        load_facts(junk)
