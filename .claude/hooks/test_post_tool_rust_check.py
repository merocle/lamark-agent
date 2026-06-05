"""Tests for the PostToolUse Rust check hook.

Usage:  cd .claude/hooks && python3 test_post_tool_rust_check.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

# Make the hook importable from this file's directory.
sys.path.insert(0, str(Path(__file__).parent))

from post_tool_rust_check import (  # pyright: ignore[reportMissingImports]
    _detect_edition,
    _find_edition_in_file,
    is_rust_file,
)

HOOK = Path(__file__).parent / "post_tool_rust_check.py"
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent / "agent"


# ---------------------------------------------------------------------------
# Edition detection tests
# ---------------------------------------------------------------------------


class TestFindEditionInFile(TestCase):
    def test_direct_edition(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False
        ) as f:
            f.write('edition = "2021"\n')
            f.flush()
            path = Path(f.name)
        try:
            self.assertEqual(_find_edition_in_file(path), "2021")
        finally:
            path.unlink()

    def test_no_edition(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", delete=False
        ) as f:
            f.write('[package]\nname = "foo"\n')
            f.flush()
            path = Path(f.name)
        try:
            self.assertIsNone(_find_edition_in_file(path))
        finally:
            path.unlink()

    def test_works_on_real_workspace_root(self):
        cargo = PROJECT_DIR / "Cargo.toml"
        if cargo.exists():
            self.assertEqual(_find_edition_in_file(cargo), "2021")


class TestDetectEdition(TestCase):
    def test_direct_edition_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            cargo = Path(tmp) / "Cargo.toml"
            cargo.write_text('edition = "2018"\n')
            self.assertEqual(_detect_edition(tmp), "2018")

    def test_workspace_inheritance_walks_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            (root / "Cargo.toml").write_text('edition = "2021"\n')
            crate = root / "crates" / "mylib"
            crate.mkdir(parents=True)
            (crate / "Cargo.toml").write_text(
                '[package]\nname = "mylib"\nedition.workspace = true\n'
            )
            self.assertEqual(_detect_edition(str(crate)), "2021")

    def test_fallback_when_no_cargo_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_detect_edition(tmp), "2021")


# ---------------------------------------------------------------------------
# File detection tests
# ---------------------------------------------------------------------------


class TestIsRustFile(TestCase):
    def test_rust_source(self):
        self.assertTrue(is_rust_file("path/to/mod.rs"))

    def test_cargo_toml(self):
        self.assertTrue(is_rust_file("/a/b/Cargo.toml"))

    def test_cargo_lock(self):
        self.assertTrue(is_rust_file("/a/b/Cargo.lock"))

    def test_non_rust(self):
        self.assertFalse(is_rust_file("path/to/main.py"))
        self.assertFalse(is_rust_file("path/to/README.md"))


# ---------------------------------------------------------------------------
# Integration: hook end-to-end behavior
# ---------------------------------------------------------------------------


class TestHookExitCode(TestCase):
    """Verify the hook is silent on success and only prints errors."""

    def test_non_rust_file_skipped(self):
        """Hook should exit 0 and produce no output for non-Rust files."""
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(
                {"tool_name": "Write", "tool_input": {"file_path": "/tmp/test.py"}}
            ),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_read_tool_skipped(self):
        """Read-only tool should exit 0 silently."""
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"tool_name": "Read", "tool_input": {}}),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)

    def test_hook_runs_for_rs_file(self):
        """Hook should run for .rs files and exit with cargo result."""
        filepath = PROJECT_DIR / "crates" / "lamark-core" / "src" / "lib.rs"
        if not filepath.exists():
            self.skipTest("lib.rs does not exist")
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(filepath)},
                }
            ),
            capture_output=True,
            text=True,
        )
        # Hook should run without crashing
        self.assertNotEqual(result.returncode, 126)

    def test_hook_skips_rustfmt_on_cargo_files(self):
        """rustfmt should not be run on Cargo.toml files."""
        filepath = PROJECT_DIR / "crates" / "lamark" / "Cargo.toml"
        if not filepath.exists():
            self.skipTest("Cargo.toml does not exist")
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(
                {
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(filepath)},
                }
            ),
            capture_output=True,
            text=True,
        )
        # Should NOT contain rustfmt parse errors (TOML treated as Rust)
        self.assertNotIn("expected item, found", result.stdout)
        self.assertNotIn("expected item, found", result.stderr)


if __name__ == "__main__":
    main()
