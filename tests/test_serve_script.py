"""Structural regression locks for scripts/cmd/serve.sh hardware gating (P1-2).

The Spark-only serving quirks (TORCH_CUDA_ARCH_LIST=12.1 + flash_attn purge)
must be conditional on detected hardware — they are wrong on non-Spark CUDA
GPUs (Ada/Ampere keep flash_attn and need their own compute capability).
"""
from __future__ import annotations

from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cmd" / "serve.sh"


@pytest.fixture(scope="module")
def src() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_arch_and_flash_are_not_unconditional(src):
    # The old unconditional forms must be gone.
    assert "-e TORCH_CUDA_ARCH_LIST=12.1 \\" not in src
    assert "-c \"pip uninstall -y flash-attn flash_attn 2>/dev/null; vllm serve" not in src


def test_hardware_detection_drives_the_knobs(src):
    assert "from lamark.hardware import detect" in src
    # Knobs are parameterised, not hardcoded, in the docker run.
    assert "$arch_env" in src
    assert "${flash_purge}vllm serve" in src


def test_only_confirmed_non_spark_disables_spark_behaviour(src):
    # Detection failure must fall back to the Spark-safe branch (the else),
    # i.e. the non-Spark branch is guarded by an explicit "= 0" check.
    assert 'if [ "$is_spark" = "0" ]' in src
    # Spark/default branch still sets 12.1 + purge.
    assert 'arch_env="-e TORCH_CUDA_ARCH_LIST=12.1"' in src
    assert 'flash_purge="pip uninstall -y flash-attn flash_attn' in src
