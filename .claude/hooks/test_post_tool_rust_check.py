#!/usr/bin/env python3
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


# ---------------------------------------------------------------------------
# Edition detection tests
# ---------------------------------------------------------------------------


class TestFindEditionInFile(TestCase):
    def test_direct_edition(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write('edition = "2021"\n')
            f.flush()
            path = Path(f.name)
        try:
            self.assertEqual(_find_edition_in_file(path), "2021")
        finally:
            path.unlink()

    def test_no_edition(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write('[package]\nname = "foo"\n')
            f.flush()
            path = Path(f.name)
        try:
            self.assertIsNone(_find_edition_in_file(path))
        finally:
            path.unlink()

    def test_works_on_real_workspace_root(self):
        """The repo's own workspace root should resolve to 2021."""
        project_dir = Path(__file__).resolve().parent.parent.parent
        cargo = project_dir / "agent" / "Cargo.toml"
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
            # Create workspace root
            root = Path(tmp) / "root"
            root.mkdir()
            (root / "Cargo.toml").write_text('edition = "2021"\n')

            # Create crate with edition.workspace = true
            crate = root / "crates" / "mylib"
            crate.mkdir(parents=True)
            (crate / "Cargo.toml").write_text(
                '[package]\nname = "mylib"\nedition.workspace = true\n'
            )

            # The hook should walk up from crate to root and find 2021
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
# Integration: hook exit code on real repo (no --all-targets)
# ---------------------------------------------------------------------------


class TestHookExitCode(TestCase):
    """Verify the hook's cargo check step does NOT compile tests.

    With --all-targets, cargo would try to compile test code which depends
    on crates like tempfile that may not be in [dependencies]. Without it,
    cargo check only verifies the main binary/library — the correct behavior
    for a post-save lint hook.
    """

    @classmethod
    def setUpClass(cls):
        cls.project_dir = (
            Path(__file__).resolve().parent.parent.parent / "agent"
        )

    def _run_hook(self, filepath):
        """Run the hook with the given file path and return (stdout, stderr, exit_code)."""
        hook = Path(__file__).parent / "post_tool_rust_check.py"
        stdin_data = json.dumps(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": str(filepath)},
            }
        )
        result = subprocess.run(
            [sys.executable, str(hook)],
            input=stdin_data,
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
        )
        return result.stdout, result.stderr, result.returncode

    def test_hook_runs_without_crashing(self):
        """Hook should execute for .rs files without Python/rustfmt crashes."""
        filepath = self.project_dir / "crates" / "lamark" / "src" / "main.rs"
        if not filepath.exists():
            self.skipTest("main.rs does not exist")

        stdout, stderr, rc = self._run_hook(filepath)
        self.assertNotEqual(rc, 126, "Hook crashed (python/rustfmt not found)")
        self.assertIn("Build check (cargo check)", stdout)

    def test_hook_does_not_run_cargo_all_targets(self):
        """Verify the hook uses plain cargo check, not --all-targets.

        The test code in tools/*.rs references tempfile which isn't in
        [dependencies]. With --all-targets, this would cause errors like
        'use of unresolved module or unlinked crate tempfile'.
        Without --all-targets, those errors won't appear.
        """
        # Use a file in tools/ to trigger compilation of that module
        filepath = self.project_dir / "crates" / "lamark" / "src" / "tools" / "file.rs"
        if not filepath.exists():
            self.skipTest("tools/file.rs does not exist")

        stdout, stderr, rc = self._run_hook(filepath)
        combined = stdout + stderr

        # If --all-targets were used, we'd see 'tempfile' errors from test code
        self.assertNotIn(
            "tempfile",
            combined,
            "Hook should NOT compile test code (no tempfile errors from tests)",
        )

    def test_hook_does_not_invoke_rustfmt_on_cargo_files(self):
        """rustfmt should not be called on Cargo.toml files (TOML, not Rust)."""
        filepath = self.project_dir / "crates" / "lamark" / "Cargo.toml"
        if not filepath.exists():
            self.skipTest("Cargo.toml does not exist")

        stdout, stderr, rc = self._run_hook(filepath)
        combined = stdout + stderr

        # When rustfmt runs on Cargo.toml, it errors: 'expected item, found ['
        self.assertNotIn(
            "expected item, found",
            combined,
            "rustfmt should not process Cargo.toml files",
        )


if __name__ == "__main__":
    main()
