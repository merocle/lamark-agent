"""
Test the MVP `lamark chat <message>` subcommand.

The MVP goal: a working end-to-end loop without Hermes fork yet.
  user types `lamark chat "..."`
  → recall() pulls top-K facts from MemoryStore
  → assemble system prompt (persona + facts)
  → InferenceRouter picks backend (primary local vLLM)
  → OpenAIChatClient.chat() POST to /v1/chat/completions
  → print assistant response
  → append (user, assistant) turn to archive as `user_explicit` source

Tests mock the HTTP transport so they don't need a live vLLM server.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


def _stub_chat_response(content: str = "ok", tokens: int = 5) -> dict:
    """Build an OpenAI-compatible chat response dict."""
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "model": "stub-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": tokens, "total_tokens": 10 + tokens},
    }


@pytest.fixture
def patched_http():
    """Patch the stdlib urllib transport used by OpenAIChatClient."""
    captured: dict = {}

    def fake_post(url: str, payload: dict, timeout: float) -> dict:
        captured["url"] = url
        captured["payload"] = payload
        return _stub_chat_response(content="stubbed reply")

    with patch("lamark.inference.client._urllib_post", side_effect=fake_post):
        yield captured


# ---- happy path ----------------------------------------------------------


def test_chat_prints_assistant_reply(
    cli_runner, isolated_lamark_home: Path, patched_http
) -> None:
    """`lamark chat 'hello'` → prints the stubbed assistant content."""
    from lamark.cli import app

    result = cli_runner.invoke(app, ["chat", "hello"])
    assert result.exit_code == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "stubbed reply" in result.stdout


def test_chat_posts_to_primary_endpoint_with_correct_payload(
    cli_runner, isolated_lamark_home: Path, patched_http
) -> None:
    """Chat hits the primary inference endpoint, sends correct message shape."""
    from lamark.cli import app

    result = cli_runner.invoke(app, ["chat", "hello"])
    assert result.exit_code == 0
    assert "chat/completions" in patched_http["url"]
    payload = patched_http["payload"]
    # user message is the last one in the array
    assert payload["messages"][-1]["role"] == "user"
    assert payload["messages"][-1]["content"] == "hello"
    # Locked default: Qwen/Qwen3.6-35B-A3B
    assert payload["model"].startswith("Qwen/Qwen3.6-35B-A3B")


def test_chat_injects_memory_facts_as_system_context(
    cli_runner, isolated_lamark_home: Path, patched_http
) -> None:
    """If facts exist, they appear in the system prompt of the outbound payload."""
    from lamark.cli import app
    from lamark.memory import MemoryStore

    # Seed a fact directly
    with MemoryStore.open(isolated_lamark_home / "honcho.db") as store:
        store.add_fact(
            text="user lives in Berlin",
            source="user_explicit",
            confidence=0.95,
            evidence="bootstrap-wizard:identity.role",
        )

    result = cli_runner.invoke(app, ["chat", "where do I live?"])
    assert result.exit_code == 0
    sys_msg = next(
        m for m in patched_http["payload"]["messages"] if m["role"] == "system"
    )
    assert "Berlin" in sys_msg["content"], (
        f"injected memory should reference 'Berlin'; system msg: {sys_msg['content']!r}"
    )


def test_chat_no_memory_flag_disables_recall(
    cli_runner, isolated_lamark_home: Path, patched_http
) -> None:
    """--no-memory keeps the system prompt minimal (no fact injection)."""
    from lamark.cli import app
    from lamark.memory import MemoryStore

    with MemoryStore.open(isolated_lamark_home / "honcho.db") as store:
        store.add_fact(
            text="lives in Berlin",
            source="user_explicit",
            confidence=0.9,
        )

    result = cli_runner.invoke(app, ["chat", "test", "--no-memory"])
    assert result.exit_code == 0
    payload_msgs = patched_http["payload"]["messages"]
    system_contents = [m["content"] for m in payload_msgs if m["role"] == "system"]
    combined = " ".join(system_contents)
    assert "Berlin" not in combined, (
        "with --no-memory, system prompt must not contain facts"
    )


def test_chat_appends_turn_to_archive(
    cli_runner, isolated_lamark_home: Path, patched_http
) -> None:
    """Successful chat appends a (user, assistant) record to the training-data archive."""
    from lamark.archive import Archive
    from lamark.cli import app

    result = cli_runner.invoke(app, ["chat", "hello, lamark"])
    assert result.exit_code == 0

    archive = Archive.open(isolated_lamark_home / "archive")
    records = list(archive.read_all())
    # Find the chat-sourced record
    chat_records = [r for r in records if r["meta"]["evidence_path"] == "chat_turn"]
    assert len(chat_records) == 1
    msgs = chat_records[0]["messages"]
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hello, lamark"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "stubbed reply"


# ---- error handling ------------------------------------------------------


def test_chat_handles_unreachable_endpoint_gracefully(
    cli_runner, isolated_lamark_home: Path
) -> None:
    """If the HTTP call raises (server down), CLI exits non-zero with clear message."""
    from lamark.cli import app

    def fake_post(*a, **kw):
        raise RuntimeError("network error to http://127.0.0.1:8000/v1: connection refused")

    with patch("lamark.inference.client._urllib_post", side_effect=fake_post):
        result = cli_runner.invoke(app, ["chat", "hi"])

    assert result.exit_code != 0
    combined = (result.stdout or "") + (result.stderr or "")
    # Some kind of error indication — not a stack trace
    assert any(
        word in combined.lower()
        for word in ("error", "unreachable", "failed", "refused", "vllm")
    )


# ---- system prompt customization -----------------------------------------


def test_chat_system_flag_overrides_default_system_prompt(
    cli_runner, isolated_lamark_home: Path, patched_http
) -> None:
    """--system 'You are Lamark...' sets the persona block in the system prompt."""
    from lamark.cli import app

    custom = "You are Lamark, a concise local AI agent. Reply only in haiku."
    result = cli_runner.invoke(app, ["chat", "test", "--system", custom])
    assert result.exit_code == 0
    sys_msgs = [m for m in patched_http["payload"]["messages"] if m["role"] == "system"]
    assert any(custom in m["content"] for m in sys_msgs)
