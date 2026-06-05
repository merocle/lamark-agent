#!/usr/bin/env python3
"""PostToolUse hook for Rust files.

Runs rustfmt + cargo check on edited *.rs, Cargo.toml, or Cargo.lock files.
Silent on success; only prints problems to stderr.

NOTE: Claude Code hooks can only run shell commands — they cannot directly
invoke MCP tools (get_file_problems, reformat_file).

To invoke the MCP tools directly, use the `/rust-review-skill` skill instead.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


RUST_EXTENSIONS = (".rs",)
CARGO_FILES = ("Cargo.toml", "Cargo.lock")


def is_rust_file(file_path: str) -> bool:
    """Check if the file is a Rust source or Cargo file."""
    name = os.path.basename(file_path)
    if name in CARGO_FILES:
        return True
    for ext in RUST_EXTENSIONS:
        if file_path.endswith(ext):
            return True
    return False


def _find_edition_in_file(path: Path) -> Optional[str]:
    """Scan a Cargo.toml for 'edition = \"...\"' and return the version, or None."""
    try:
        with open(path) as f:
            for line in f:
                stripped = line.strip()
                m = re.match(r'edition\s*=\s*"(\d+)"', stripped)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


def _detect_edition(cargo_cwd: str) -> str:
    """Detect the Rust edition from Cargo.toml (works on any Python 3.x)."""
    d = Path(cargo_cwd)
    for _ in range(20):
        cargo_toml = d / "Cargo.toml"
        if not cargo_toml.exists():
            parent = d.parent
            if parent == d:
                break
            d = parent
            continue

        direct = _find_edition_in_file(cargo_toml)
        if direct:
            return direct

        with open(cargo_toml) as f:
            for line in f:
                stripped = line.strip()
                if stripped == "edition.workspace = true":
                    walk = d.parent
                    for _ in range(20):
                        root_cargo = walk / "Cargo.toml"
                        if root_cargo.exists():
                            return _find_edition_in_file(root_cargo) or "2021"
                        walk = walk.parent
                    return "2021"

        parent = d.parent
        if parent == d:
            break
        d = parent
    return "2021"


def _run(cmd: list[str], cwd: str) -> tuple[int, str]:
    """Run a command, returning (exit_code, merged_stdout_stderr)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        return result.returncode, (result.stdout or "") + "\n" + (result.stderr or "")
    except FileNotFoundError as e:
        return 126, str(e)


def _fix_rustfmt(file_path: Path, cwd: str, edition: str) -> int:
    """Run rustfmt --check then auto-fix. Return 0 on success."""
    changed = False
    # Check formatting first — if it passes, no need to rewrite.
    rc, _ = _run(["rustfmt", "--edition", edition, "--check", str(file_path)], cwd)
    if rc != 0:
        changed = True
        _run(["rustfmt", "--edition", edition, str(file_path)], cwd)

    # Exit non-zero only if formatting was required (the hook should surface problems).
    return 0 if not changed else rc


def main() -> None:
    # Read Claude Code tool invocation from stdin.
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "MultiEdit"):
        sys.exit(0)

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        sys.exit(0)

    project_dir = os.getenv("CLAUDE_PROJECT_DIR", "")
    abs_path = Path(file_path)
    if not abs_path.is_absolute():
        abs_path = Path(project_dir) / abs_path

    # Find Cargo project root.
    cargo_cwd = str(abs_path.parent)
    d = Path(abs_path.parent)
    while True:
        if (d / "Cargo.toml").exists():
            cargo_cwd = str(d)
            break
        parent = d.parent
        if parent == d:
            cargo_cwd = project_dir
            break
        d = parent

    if not cargo_cwd:
        sys.exit(0)

    edition = _detect_edition(cargo_cwd)
    is_cargo_file = os.path.basename(abs_path) in CARGO_FILES

    all_errors: list[str] = []
    exit_code = 0

    # Rustfmt check + auto-fix (skip for Cargo.toml/Cargo.lock).
    if not is_cargo_file:
        fmt_rc = _fix_rustfmt(abs_path, cargo_cwd, edition)
        if fmt_rc != 0:
            exit_code = max(exit_code, fmt_rc)
            all_errors.append("rustfmt required formatting changes")

    # cargo check (lib + bins, no tests).
    rc, output = _run(["cargo", "check"], cargo_cwd)
    if rc != 0:
        exit_code = max(exit_code, rc)
        all_errors.append(output.strip())

    # Only print on failure — silent on success.
    if all_errors:
        for err in all_errors:
            print(err, file=sys.stderr)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
