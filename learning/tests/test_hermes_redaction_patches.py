"""
Test Plan A.3 — verify the two redaction-gate patches applied to vendored Hermes
actually block verified secrets at write time.

These tests import the patched Hermes functions directly. They run only when
the vendored Hermes is on sys.path; CI/dev outside that situation skips.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

VENDOR_HERMES = Path(__file__).resolve().parent.parent / "vendor" / "hermes"


@pytest.fixture(autouse=True)
def _hermes_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Put vendor/hermes/ on sys.path with a tmp HERMES_HOME so imports work."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.syspath_prepend(str(VENDOR_HERMES))
    # Clear any pre-imported hermes modules cache
    for mod in list(sys.modules):
        if mod.startswith(("hermes_", "tools.memory_tool", "tools.skill_manager_tool")):
            del sys.modules[mod]


def test_memory_scan_blocks_aws_key() -> None:
    """memory_tool._scan_memory_content returns Blocked-string on AWS key."""
    from tools.memory_tool import _scan_memory_content

    result = _scan_memory_content("my creds: AKIAIOSFODNN7EXAMPLE here")
    assert result is not None, "expected block on AWS key"
    assert "Blocked" in result
    assert "AWS" in result or "lamark redaction" in result.lower()


def test_memory_scan_blocks_github_pat() -> None:
    from tools.memory_tool import _scan_memory_content

    result = _scan_memory_content(
        "token: ghp_aBcD1234EfGh5678IjKlMnOpQrStUvWxYzAB"
    )
    assert result is not None
    assert "Blocked" in result


def test_memory_scan_passes_clean_content() -> None:
    """No secret, no threat → returns None (write proceeds)."""
    from tools.memory_tool import _scan_memory_content

    assert _scan_memory_content("I like filter coffee in the mornings") is None


def test_memory_scan_preserves_legacy_threat_pattern_block() -> None:
    """Pre-existing Hermes threat-pattern scan still fires on legacy patterns."""
    from tools.memory_tool import _scan_memory_content

    # An invisible unicode char (legacy Hermes pattern)
    result = _scan_memory_content("hello​world")  # zero-width space
    assert result is not None
    assert "invisible" in result.lower() or "Blocked" in result


def test_skill_manage_blocks_aws_key_in_content() -> None:
    """skill_manage with secret in `content` kwarg is refused before action handler."""
    from tools.skill_manager_tool import skill_manage

    out = skill_manage(
        action="create",
        name="my_skill",
        content="""---
name: my_skill
version: 0.1.0
description: contains an AWS key
---
AKIAIOSFODNN7EXAMPLE
""",
    )
    # tool_error returns a JSON-shaped string with success=False; we just verify
    # the block-by-Lamark phrase appears
    assert "Lamark redaction" in out or "Blocked by Lamark" in out
    assert "AWS" in out


def test_skill_manage_blocks_secret_in_file_content() -> None:
    """write_file action with secret in file_content is also refused."""
    from tools.skill_manager_tool import skill_manage

    out = skill_manage(
        action="write_file",
        name="my_skill",
        file_path="references/cheatsheet.md",
        file_content="ghp_aBcD1234EfGh5678IjKlMnOpQrStUvWxYzAB",
    )
    assert "Lamark redaction" in out or "Blocked by Lamark" in out


def test_skill_manage_passes_clean_content_through_to_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clean content: redaction gate is a no-op, action handler runs.

    We assert the redaction gate didn't fire by checking the response is NOT
    our Block message. Whether the underlying handler succeeds isn't the
    point — that path involves disk writes, skills directory, validation.
    """
    from tools.skill_manager_tool import skill_manage

    out = skill_manage(
        action="create",
        name="my_skill",
        content="""---
name: my_skill
version: 0.1.0
description: harmless
---
just a plain note about morning coffee.
""",
    )
    assert "Blocked by Lamark redaction" not in out
