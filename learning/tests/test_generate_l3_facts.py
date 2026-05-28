"""
Offline tests for the OpenAI-powered facts generator.

We do NOT make real API calls. The tests cover the pure-Python pieces:
- proposal validation enforces the load-bearing subject-in-prompt rule
- duplicate IDs are rejected
- the dry-run path runs without an OPENAI_API_KEY
- writing + reloading round-trips through the canonical loader cleanly
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "learning" / "scripts"
SRC_DIR = REPO_ROOT / "learning" / "src"

sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(SCRIPT_DIR))


@pytest.fixture(scope="module")
def gen_module():
    return importlib.import_module("generate_l3_facts")


def test_validate_proposal_accepts_well_formed(gen_module) -> None:
    obj = {
        "id": "test-fact",
        "edit": {
            "prompt": "The Lamark policy engine is",
            "subject": "The Lamark policy engine",
            "target_new": "implemented in agent/crates/lamark-policy",
        },
        "paraphrases": ["What is Lamark's policy engine?"],
        "neighborhood": [{"prompt": "iptables is", "expected_substring": "firewall"}],
        "tags": ["policy"],
    }
    fact, err = gen_module._validate_proposal(obj, set())
    assert err == "", err
    assert fact is not None
    assert fact.id == "test-fact"
    assert fact.edit.subject in fact.edit.prompt


def test_validate_proposal_rejects_subject_not_in_prompt(gen_module) -> None:
    obj = {
        "id": "bad-fact",
        "edit": {"prompt": "Foo is", "subject": "Bar", "target_new": "X"},
    }
    fact, err = gen_module._validate_proposal(obj, set())
    assert fact is None
    assert "not in prompt" in err


def test_validate_proposal_rejects_duplicate_id(gen_module) -> None:
    obj = {
        "id": "dupe",
        "edit": {"prompt": "Lamark is", "subject": "Lamark", "target_new": "X"},
    }
    fact, err = gen_module._validate_proposal(obj, existing_ids := {"dupe"})
    assert fact is None
    assert "duplicate id" in err


def test_validate_proposal_rejects_missing_fields(gen_module) -> None:
    obj = {"id": "x", "edit": {"prompt": "p", "subject": "p"}}  # missing target_new
    fact, err = gen_module._validate_proposal(obj, set())
    assert fact is None
    assert err


def test_write_and_round_trip(gen_module, tmp_path: Path) -> None:
    """Anything we write must reload cleanly through facts.load_facts."""
    from lamark.knowledge_edit.facts import Edit, Fact, NeighborhoodProbe, load_facts

    facts = [
        Fact(
            id="round-trip",
            edit=Edit(prompt="Lamark is", subject="Lamark", target_new="cool"),
            paraphrases=("What is Lamark?",),
            neighborhood=(NeighborhoodProbe(prompt="Linux is", expected_substring="kernel"),),
            tags=("test",),
        ),
    ]
    out = tmp_path / "out.jsonl"
    gen_module._write_facts(facts, out)
    reloaded = load_facts(out)
    assert len(reloaded) == 1
    assert reloaded[0].id == "round-trip"
    assert reloaded[0].edit.subject == "Lamark"
    assert reloaded[0].paraphrases == ("What is Lamark?",)


def test_dry_run_augment_does_not_require_api_key(tmp_path: Path) -> None:
    """augment --dry-run must work without OPENAI_API_KEY."""
    # Use the shipped dataset; only the first fact gets shown.
    facts = REPO_ROOT / "learning" / "data" / "lamark_facts.jsonl"
    cmd = [
        sys.executable, str(SCRIPT_DIR / "generate_l3_facts.py"), "augment",
        "--facts", str(facts),
        "--only", "lamark-vs-lamarck",
        "--dry-run",
    ]
    # Wipe OPENAI_API_KEY explicitly to prove the dry-run path is offline.
    import os
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "lamark-vs-lamarck" in result.stdout
    assert "no file written" in result.stdout


def test_dry_run_propose_does_not_require_api_key() -> None:
    cmd = [
        sys.executable, str(SCRIPT_DIR / "generate_l3_facts.py"), "propose",
        "--count", "2",
        "--dry-run",
    ]
    import os
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "propose: system prompt" in result.stdout
