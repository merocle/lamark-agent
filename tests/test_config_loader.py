"""
Test Module 1 — config loader.

Precedence (highest wins): TOML config file > env vars > defaults.
Paths resolve under LAMARK_HOME (default: ~/.lamark).
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_default_config_resolves_lamark_home_under_isolated_home(
    isolated_lamark_home: Path,
) -> None:
    """No env overrides → all paths under $LAMARK_HOME."""
    from lamark.config import load_config

    cfg = load_config()
    assert cfg.home == isolated_lamark_home
    assert cfg.honcho_db_path == isolated_lamark_home / "honcho.db"
    assert cfg.lancedb_path == isolated_lamark_home / "lancedb"
    assert cfg.model_dir == isolated_lamark_home / "models"


def test_env_override_changes_individual_paths(
    isolated_lamark_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`LAMARK_MODEL_DIR=/elsewhere` overrides just that path; others unaffected."""
    custom = tmp_path / "elsewhere"
    monkeypatch.setenv("LAMARK_MODEL_DIR", str(custom))

    from lamark.config import load_config

    cfg = load_config()
    assert cfg.model_dir == custom
    # honcho_db still under home, not under elsewhere
    assert cfg.honcho_db_path == isolated_lamark_home / "honcho.db"


def test_toml_config_overrides_env(
    isolated_lamark_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Explicit TOML config wins over env vars."""
    env_model_dir = tmp_path / "from_env"
    toml_model_dir = tmp_path / "from_toml"

    monkeypatch.setenv("LAMARK_MODEL_DIR", str(env_model_dir))

    cfg_file = isolated_lamark_home / "config.toml"
    cfg_file.write_text(
        f"""
[paths]
model_dir = "{toml_model_dir}"
""".strip()
    )
    monkeypatch.setenv("LAMARK_CONFIG", str(cfg_file))

    from lamark.config import load_config

    cfg = load_config()
    assert cfg.model_dir == toml_model_dir, (
        "TOML file must override env var — observed env value instead"
    )


def test_inference_primary_model_default_is_qwen36_a3b(
    isolated_lamark_home: Path,
) -> None:
    """Default primary model is locked per v3 spec."""
    from lamark.config import load_config

    cfg = load_config()
    assert cfg.inference.primary_model == "Qwen/Qwen3.6-35B-A3B"
    assert cfg.inference.primary_quantization == "fp8"
    assert cfg.inference.dense_model == "Qwen/Qwen3.6-27B"


def test_config_show_emits_resolved_paths(cli_runner, isolated_lamark_home: Path) -> None:
    """`lamark config show` prints resolved config without crashing."""
    from lamark.cli import app

    result = cli_runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0, f"non-zero exit: {result.exit_code}\n{result.stderr}"
    # Resolved home must appear somewhere in the output
    assert str(isolated_lamark_home) in result.stdout, (
        f"resolved home {isolated_lamark_home} not present in: {result.stdout!r}"
    )
