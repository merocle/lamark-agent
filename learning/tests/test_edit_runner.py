"""
Tests for the L3 edit runner that do NOT require torch / EasyEdit / a GPU.

The full editor pipeline only runs on Spark (the dispatch script lifts it
into a container with EasyEdit installed). These tests cover the pure-Python
planning surface: facts filtering, MoE template expansion, dry-run mode.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lamark.knowledge_edit import edit_runner
from lamark.knowledge_edit.facts import Edit, Fact

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "learning" / "data" / "lamark_facts.jsonl"
HPARAMS = REPO_ROOT / "learning" / "configs" / "memit" / "nemotron-h-30b-a3b.yaml"


def _fact(fid: str) -> Fact:
    return Fact(
        id=fid,
        edit=Edit(prompt=f"{fid} is", subject=fid, target_new="value"),
    )


def test_filter_facts_all_when_only_empty() -> None:
    facts = [_fact("a"), _fact("b")]
    assert edit_runner._filter_facts(facts, "") == facts


def test_filter_facts_subset() -> None:
    facts = [_fact("a"), _fact("b"), _fact("c")]
    got = edit_runner._filter_facts(facts, "a,c")
    assert [f.id for f in got] == ["a", "c"]


def test_filter_facts_unknown_id_errors() -> None:
    with pytest.raises(SystemExit, match="unknown fact ids"):
        edit_runner._filter_facts([_fact("a")], "a,nope")


def test_expand_moe_single_expert_when_flag_false() -> None:
    cfg = {
        "rewrite_module_tmp": "model.layers.{}.mlp.experts.{expert}.down_proj",
        "moe_all_experts": False,
        "moe_expert_index": 7,
        "moe_num_experts": 64,
    }
    out = edit_runner._expand_moe_template(cfg)
    assert out == ["model.layers.{}.mlp.experts.7.down_proj"]


def test_expand_moe_all_experts_when_flag_true() -> None:
    cfg = {
        "rewrite_module_tmp": "model.layers.{}.mlp.experts.{expert}.down_proj",
        "moe_all_experts": True,
        "moe_expert_index": 0,
        "moe_num_experts": 4,
    }
    out = edit_runner._expand_moe_template(cfg)
    assert out == [
        "model.layers.{}.mlp.experts.0.down_proj",
        "model.layers.{}.mlp.experts.1.down_proj",
        "model.layers.{}.mlp.experts.2.down_proj",
        "model.layers.{}.mlp.experts.3.down_proj",
    ]


def test_expand_passthrough_when_no_expert_placeholder() -> None:
    cfg = {
        "rewrite_module_tmp": "model.layers.{}.mlp.down_proj",
        "moe_all_experts": True,
        "moe_num_experts": 64,
    }
    assert edit_runner._expand_moe_template(cfg) == ["model.layers.{}.mlp.down_proj"]


def test_dry_run_end_to_end_does_not_load_torch(tmp_path: Path) -> None:
    """Dry-run must not import torch / EasyEdit so it works on dev laptops.

    We invoke via subprocess to keep import side effects out of the test
    process. PYTHONPATH is set to learning/src so the runner finds itself.
    """
    output_dir = tmp_path / "edited"
    src_dir = REPO_ROOT / "learning" / "src"
    cmd = [
        sys.executable, "-m", "lamark.knowledge_edit.edit_runner",
        "--base-model", str(tmp_path),  # not loaded in dry-run
        "--facts", str(DATASET),
        "--hparams", str(HPARAMS),
        "--output", str(output_dir),
        "--method", "memit",
        "--only", "lamark-vs-lamarck,lamark-self-id",
        "--dry-run",
    ]
    env_pythonpath = f"{src_dir}"
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": env_pythonpath, "PATH": __import__("os").environ.get("PATH", "")},
    )
    assert result.returncode == 0, result.stderr
    assert "EDIT PLAN" in result.stdout
    assert "lamark-vs-lamarck" in result.stdout
    assert "lamark-self-id" in result.stdout
    assert not output_dir.exists(), "dry-run should not create the output dir"
