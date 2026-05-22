"""
Lamark configuration loader.

Precedence (highest wins):
  1. TOML config file at $LAMARK_CONFIG (or $LAMARK_HOME/config.toml if it exists)
  2. Environment variables (LAMARK_*)
  3. Defaults under $LAMARK_HOME (defaulting to ~/.lamark)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover — pyproject pins >=3.11
    import tomli as tomllib  # type: ignore[no-redef]


# Locked model selection per feasibility-report-v3.md §15
DEFAULT_PRIMARY_MODEL = "Qwen/Qwen3.6-35B-A3B"
DEFAULT_PRIMARY_QUANTIZATION = "fp8"
DEFAULT_DENSE_MODEL = "Qwen/Qwen3.6-27B"
DEFAULT_DENSE_QUANTIZATION = "q4_k_m"


class InferenceConfig(BaseModel):
    """Inference engine and model selection."""

    primary_model: str = DEFAULT_PRIMARY_MODEL
    primary_quantization: str = DEFAULT_PRIMARY_QUANTIZATION
    primary_endpoint: str = "http://127.0.0.1:8000/v1"  # vLLM OpenAI-compat

    dense_model: str = DEFAULT_DENSE_MODEL
    dense_quantization: str = DEFAULT_DENSE_QUANTIZATION
    dense_endpoint: str = "http://127.0.0.1:8001/v1"  # llama.cpp server

    classifier_model: str = "Qwen/Qwen3-0.6B"  # small routing classifier (Phase 2)


class MemoryConfig(BaseModel):
    """Memory layer behaviour."""

    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    recall_top_k: int = 20
    recency_weight: float = 0.15  # 0 = pure semantic, 1 = pure recency


class LamarkConfig(BaseModel):
    """Top-level config — all derived paths flow from `home`."""

    home: Path
    honcho_db_path: Path
    lancedb_path: Path
    model_dir: Path
    adapters_dir: Path
    sessions_dir: Path
    log_dir: Path

    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)


def _resolve_home() -> Path:
    """Pick LAMARK_HOME from env, else default to ~/.lamark."""
    env = os.environ.get("LAMARK_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".lamark"


def _path_from_env(key: str, default: Path) -> Path:
    """Override individual paths via env; expand ~ but do not require existence."""
    env = os.environ.get(key)
    if env:
        return Path(env).expanduser().resolve()
    return default


def _load_toml_overrides(home: Path) -> dict:
    """Read $LAMARK_CONFIG (if set) or $home/config.toml (if exists)."""
    config_env = os.environ.get("LAMARK_CONFIG")
    candidates = [Path(config_env)] if config_env else [home / "config.toml"]
    for candidate in candidates:
        if candidate.is_file():
            with candidate.open("rb") as f:
                return tomllib.load(f)
    return {}


def load_config() -> LamarkConfig:
    """Resolve config from env vars + optional TOML, return a frozen LamarkConfig."""
    home = _resolve_home()

    # Defaults under home
    defaults = {
        "home": home,
        "honcho_db_path": home / "honcho.db",
        "lancedb_path": home / "lancedb",
        "model_dir": home / "models",
        "adapters_dir": home / "adapters",
        "sessions_dir": home / "sessions",
        "log_dir": home / "logs",
    }

    # Env overrides
    paths = {
        "home": home,
        "honcho_db_path": _path_from_env("LAMARK_HONCHO_DB", defaults["honcho_db_path"]),
        "lancedb_path": _path_from_env("LAMARK_LANCEDB", defaults["lancedb_path"]),
        "model_dir": _path_from_env("LAMARK_MODEL_DIR", defaults["model_dir"]),
        "adapters_dir": _path_from_env("LAMARK_ADAPTERS_DIR", defaults["adapters_dir"]),
        "sessions_dir": _path_from_env("LAMARK_SESSIONS_DIR", defaults["sessions_dir"]),
        "log_dir": _path_from_env("LAMARK_LOG_DIR", defaults["log_dir"]),
    }

    inference = InferenceConfig()
    memory = MemoryConfig()

    # TOML overrides — highest precedence
    toml_data = _load_toml_overrides(home)
    if "paths" in toml_data:
        for k, v in toml_data["paths"].items():
            if k in paths:
                paths[k] = Path(v).expanduser().resolve()
    if "inference" in toml_data:
        inference = InferenceConfig(**{**inference.model_dump(), **toml_data["inference"]})
    if "memory" in toml_data:
        memory = MemoryConfig(**{**memory.model_dump(), **toml_data["memory"]})

    return LamarkConfig(inference=inference, memory=memory, **paths)
