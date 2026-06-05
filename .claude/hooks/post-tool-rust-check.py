#!/usr/bin/env python3
"""
PostToolUse hook for Rust files.

Executes rustfmt + cargo check on edited *.rs, Cargo.toml, or Cargo.lock files.

NOTE: Claude Code hooks can only run shell commands — they cannot directly
invoke MCP tools (get_file_problems, reformat_file). This hook uses the Rust
CLI toolchain as the closest equivalent:

    rustfmt --check  → matches get_file_problems for style/syntax issues
    cargo check      → matches build_project for type / resolution errors

To invoke the MCP tools directly, use the `/rust-review-skill` skill instead.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


# File patterns that trigger the check
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


def _find_edition_in_file(path: Path) -> str | None:
    """Scan a Cargo.toml for 'edition = \"...\"' and return the version, or None."""
    try:
        for line in path.open():
            stripped = line.strip()
            m = re.match(r'edition\s*=\s*"(\d+)"', stripped)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def _detect_edition(cargo_cwd: str) -> str:
    """Detect the Rust edition from Cargo.toml (works on any Python 3.x).

    Handles these patterns:
      edition = "2021"               — direct edition
      edition.workspace = true       — inherited from workspace root

    For workspace-style editions, walks up to find the root Cargo.toml.
    Falls back to "2021" if edition cannot be determined.
    """
    d = Path(cargo_cwd)
    for _ in range(20):  # safety limit
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

        # Check if this file uses edition.workspace = true (inherited edition)
        for line in cargo_toml.open():
            stripped = line.strip()
            if stripped == "edition.workspace = true":
                # Walk up from crate to find the workspace root (next Cargo.toml above current dir)
                walk = d.parent
                for _ in range(20):
                    root_cargo = walk / "Cargo.toml"
                    if root_cargo.exists():
                        return _find_edition_in_file(root_cargo) or "2021"
                    walk = walk.parent
                return "2021"  # no root found

        parent = d.parent
        if parent == d:
            break
        d = parent
    return "2021"  # fallback


def run_command(cwd: str, args: list[str], description: str) -> int:
    """Run a shell command and report its status."""
    print(f"[rust-hook] {description}...")
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            print(result.stdout.rstrip())
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        return result.returncode
    except FileNotFoundError as e:
        print(f"[rust-hook] SKIPPED — {e}", file=sys.stderr)
        return 126


def main():
    # Claude Code passes tool invocation data on stdin as JSON
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"[rust-hook] invalid JSON input: {e}", file=sys.stderr)
        sys.exit(0)  # non-modification tools send empty/error data — ignore

    tool_name = data.get("tool_name", "")
    modification_tools = ("Write", "Edit", "MultiEdit")

    if tool_name not in modification_tools:
        sys.exit(0)  # only react to file modifications

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        sys.exit(0)

    # Only process Rust files in this project
    if not is_rust_file(file_path):
        sys.exit(0)

    project_dir = os.getenv("CLAUDE_PROJECT_DIR", "")
    abs_path = Path(file_path)
    if not abs_path.is_absolute():
        abs_path = Path(project_dir) / abs_path

    # Find the Cargo project root (where Cargo.toml lives), walking up from file.
    cargo_cwd = str(abs_path.parent)
    d = Path(abs_path.parent)
    while True:
        if (d / "Cargo.toml").exists():
            cargo_cwd = str(d)
            break
        parent = d.parent
        if parent == d:
            # fallback to CLAUDE_PROJECT_DIR / project_dir
            cargo_cwd = project_dir
            break
        d = parent

    if not cargo_cwd:
        sys.exit(0)  # no project dir and no Cargo.toml found — skip

    # Detect the Rust edition from Cargo.toml so rustfmt parses with the correct edition.
    edition = _detect_edition(cargo_cwd)
    rustfmt_edition = ["--edition", edition]

    # Run formatting check via rustfmt
    rc_fmt_check = run_command(
        cargo_cwd,
        ["rustfmt", *rustfmt_edition, "--check", str(abs_path)],
        description="Format check (rustfmt --check)",
    )

    # Run formatting + fix via rustfmt (matches reformat_file)
    rc_fmt = run_command(
        cargo_cwd,
        ["rustfmt", *rustfmt_edition, str(abs_path)],
        description="Auto-format (rustfmt)",
    )

    # Run cargo check for the entire workspace (matches build_project)
    run_command(
        cargo_cwd,
        ["cargo", "check", "--all-targets"],
        description="Build check (cargo check --all-targets)",
    )

    # Exit with the highest (worst) result code
    sys.exit(max(rc_fmt, rc_fmt_check) if rc_fmt == 0 else rc_fmt)


if __name__ == "__main__":
    main()
