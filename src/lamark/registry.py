"""
Lamark model registry loader.

Reads scripts/model-registry.yaml and exposes a typed view of each model
entry — LoRA targets, serving config, hardware tier. Used by:
- src/lamark/train/dispatcher.py (per-model LoRA target dispatch)
- src/lamark/templates/build_template.py (per-model identity inject)
- scripts/setup.sh via hardware.py CLI
- scripts/lamark-nightly-train.sh (resolve base model path on each run)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class LoraConfig:
    target_modules: list[str]
    rank: int
    alpha: int


@dataclass
class ServingConfig:
    backend: str
    max_model_len: int
    expert_parallel: bool = False
    tool_call_parser: Optional[str] = None
    enable_auto_tool_choice: bool = True
    # GPU memory utilization fraction passed to vLLM. 0.80 default leaves
    # headroom for the agent runtime + KV cache spikes; on tier S (Spark,
    # 128 GB unified) higher values trigger memory pressure thrashing.
    gpu_memory_utilization: float = 0.85
    # Perf flags. Prefix-caching reuses KV for repeated prompt prefixes —
    # huge win for chat with a constant ~785-token identity preamble.
    # Chunked-prefill overlaps prefill with decode. reasoning-parser
    # filters the model's internal thinking trace out of the response
    # (Qwen3 only; set to "qwen3" to enable).
    enable_prefix_caching: bool = False
    enable_chunked_prefill: bool = False
    reasoning_parser: Optional[str] = None


@dataclass
class ModelEntry:
    name: str
    hf_id: str
    arch: str               # "moe" or "dense"
    tier: str               # "S" / "M" / "L" / "XS"
    param_count_b: float
    download_size_gb: float
    default_for_tier: bool
    lora: LoraConfig
    serving: ServingConfig
    active_param_count_b: Optional[float] = None  # only for MoE
    tested: bool = False    # True iff verified on real hardware this release


@dataclass
class TierSpec:
    name: str
    min_total_memory_gb: float
    serving_quirks: list[str] = field(default_factory=list)


def _default_registry_path() -> Path:
    """Locate scripts/model-registry.yaml relative to this module."""
    return Path(__file__).resolve().parents[2] / "scripts" / "model-registry.yaml"


def load_registry(path: Optional[Path] = None) -> dict[str, ModelEntry | TierSpec]:
    """Parse the YAML into typed entries. Returns a flat dict — model names
    map to ModelEntry, tier letters ('S' etc) map to TierSpec."""
    path = path or _default_registry_path()
    with path.open() as f:
        raw = yaml.safe_load(f)

    out: dict[str, ModelEntry | TierSpec] = {}

    for tier_id, spec in (raw.get("tiers") or {}).items():
        out[tier_id] = TierSpec(
            name=spec["name"],
            min_total_memory_gb=float(spec.get("min_total_memory_gb", 0)),
            serving_quirks=list(spec.get("serving_quirks") or []),
        )

    for name, spec in (raw.get("models") or {}).items():
        lora = spec.get("lora") or {}
        serving = spec.get("serving") or {}
        out[name] = ModelEntry(
            name=name,
            hf_id=spec["hf_id"],
            arch=spec["arch"],
            tier=spec["tier"],
            param_count_b=float(spec.get("param_count_b", 0)),
            active_param_count_b=spec.get("active_param_count_b"),
            download_size_gb=float(spec.get("download_size_gb", 0)),
            default_for_tier=bool(spec.get("default_for_tier", False)),
            tested=bool(spec.get("tested", False)),
            lora=LoraConfig(
                target_modules=list(lora.get("target_modules") or []),
                rank=int(lora.get("rank", 16)),
                alpha=int(lora.get("alpha", 32)),
            ),
            serving=ServingConfig(
                backend=serving.get("backend", "vllm"),
                max_model_len=int(serving.get("max_model_len", 8192)),
                expert_parallel=bool(serving.get("expert_parallel", False)),
                tool_call_parser=serving.get("tool_call_parser"),
                enable_auto_tool_choice=bool(serving.get("enable_auto_tool_choice", True)),
                gpu_memory_utilization=float(serving.get("gpu_memory_utilization", 0.85)),
                enable_prefix_caching=bool(serving.get("enable_prefix_caching", False)),
                enable_chunked_prefill=bool(serving.get("enable_chunked_prefill", False)),
                reasoning_parser=serving.get("reasoning_parser"),
            ),
        )

    return out


def get_model(name: str, path: Optional[Path] = None) -> ModelEntry:
    """Convenience: fetch one model entry by name."""
    reg = load_registry(path)
    entry = reg.get(name)
    if not isinstance(entry, ModelEntry):
        raise KeyError(f"No model entry named {name!r} in registry")
    return entry


def get_tier(tier_id: str, path: Optional[Path] = None) -> TierSpec:
    """Convenience: fetch one tier spec by id."""
    reg = load_registry(path)
    entry = reg.get(tier_id)
    if not isinstance(entry, TierSpec):
        raise KeyError(f"No tier {tier_id!r} in registry")
    return entry
