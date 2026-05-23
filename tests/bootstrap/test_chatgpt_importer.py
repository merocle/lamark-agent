"""
Test Module 8b — ChatGPT export importer.

ChatGPT exports a `conversations.json` file containing an array of conversations,
each with a `mapping` of message nodes. We walk user messages, redact, and
emit each chunk as a Fact in memory.

Fixtures synthesized inline (no committed real chat data).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---- fixtures -------------------------------------------------------------


def _make_conversation(title: str, user_messages: list[str], assistant_messages: list[str] | None = None) -> dict:
    """Build a single ChatGPT-export-shaped conversation dict."""
    assistant_messages = assistant_messages or []
    mapping: dict = {}
    parent = None
    counter = 0

    def add_node(role: str, content: str) -> str:
        nonlocal counter, parent
        node_id = f"node-{counter}"
        counter += 1
        mapping[node_id] = {
            "id": node_id,
            "parent": parent,
            "children": [],
            "message": {
                "id": node_id,
                "author": {"role": role},
                "content": {"content_type": "text", "parts": [content]},
                "create_time": 1700000000 + counter,
            },
        }
        if parent is not None and parent in mapping:
            mapping[parent]["children"].append(node_id)
        parent = node_id
        return node_id

    # Interleave user / assistant in order
    longer = max(len(user_messages), len(assistant_messages))
    for i in range(longer):
        if i < len(user_messages):
            add_node("user", user_messages[i])
        if i < len(assistant_messages):
            add_node("assistant", assistant_messages[i])

    return {"title": title, "create_time": 1700000000, "mapping": mapping}


@pytest.fixture
def chatgpt_export(tmp_path: Path) -> Path:
    """Synthesize a small conversations.json for tests."""
    convs = [
        _make_conversation(
            "Lunch plans",
            user_messages=[
                "I'm thinking of having ramen for lunch",
                "Actually I had ramen yesterday — what else is around?",
            ],
            assistant_messages=[
                "Ramen sounds nice. Where would you go?",
                "There's a good udon place nearby if you want a change.",
            ],
        ),
        _make_conversation(
            "Side project",
            user_messages=[
                "I'm building a personal AI assistant on a DGX Spark",
                "Using Qwen3.6-35B-A3B as the base model",
            ],
            assistant_messages=[
                "That's an interesting setup.",
                "MoE makes sense given Spark's bandwidth.",
            ],
        ),
    ]
    export_path = tmp_path / "conversations.json"
    export_path.write_text(json.dumps(convs, ensure_ascii=False), encoding="utf-8")
    return export_path


@pytest.fixture
def fresh_store(isolated_lamark_home: Path):
    from lamark.memory import MemoryStore

    store = MemoryStore.open(isolated_lamark_home / "honcho.db")
    yield store
    store.close()


# ---- tests ----------------------------------------------------------------


def test_imports_only_user_messages(fresh_store, chatgpt_export: Path) -> None:
    """Assistant messages must NOT become facts about the user."""
    from lamark.bootstrap.importers.chatgpt import import_chatgpt_export

    result = import_chatgpt_export(fresh_store, chatgpt_export)

    assert result.facts_added == 4  # two user msgs per conv × two convs
    # Verify nothing assistant-shaped leaked in
    from sqlalchemy import select
    from lamark.memory.schema import Fact

    with fresh_store._sessionmaker() as s:  # type: ignore[attr-defined]
        facts = list(s.scalars(select(Fact)))
    texts = " ".join(f.text for f in facts)
    assert "Ramen sounds nice" not in texts
    assert "MoE makes sense" not in texts
    # Verify expected user content is present
    assert any("ramen for lunch" in f.text for f in facts)
    assert any("Qwen3.6-35B-A3B" in f.text for f in facts)


def test_imported_facts_have_imported_provenance(fresh_store, chatgpt_export: Path) -> None:
    """Provenance source = 'imported' for ChatGPT export, not 'bootstrap'."""
    from lamark.bootstrap.importers.chatgpt import import_chatgpt_export
    from lamark.memory import PROVENANCE_IMPORTED

    import_chatgpt_export(fresh_store, chatgpt_export)

    from sqlalchemy import select
    from lamark.memory.schema import Fact

    with fresh_store._sessionmaker() as s:  # type: ignore[attr-defined]
        sources = {f.source for f in s.scalars(select(Fact))}
    assert sources == {PROVENANCE_IMPORTED}


def test_pii_in_export_is_redacted_before_storage(fresh_store, tmp_path: Path) -> None:
    """Emails / phones in the user's past messages are redacted on import."""
    from lamark.bootstrap.importers.chatgpt import import_chatgpt_export

    conv = _make_conversation(
        "Old chat",
        user_messages=["My old email was alice@example.com and number was +1 555-0100"],
    )
    export_path = tmp_path / "conversations.json"
    export_path.write_text(json.dumps([conv]), encoding="utf-8")

    import_chatgpt_export(fresh_store, export_path)

    from sqlalchemy import select
    from lamark.memory.schema import Fact

    with fresh_store._sessionmaker() as s:  # type: ignore[attr-defined]
        text = " ".join(f.text for f in s.scalars(select(Fact)))
    assert "alice@example.com" not in text
    assert "555-0100" not in text
    assert "<email>" in text or "[EMAIL]" in text
    assert "<phone>" in text or "[PHONE]" in text


def test_verified_secret_in_export_blocks_entire_import(fresh_store, tmp_path: Path) -> None:
    """If ANY conversation has a verified secret, the whole import halts.

    We don't want to import half a file and leave the user wondering which
    messages went through. Whole-file atomicity by default.
    """
    from lamark.bootstrap.importers.chatgpt import import_chatgpt_export
    from lamark.redaction import SecretFound

    conv1 = _make_conversation(
        "Safe", user_messages=["I like soup"]
    )
    conv2 = _make_conversation(
        "Leaky",
        user_messages=["btw my AWS key is AKIAIOSFODNN7EXAMPLE oops"],
    )
    export_path = tmp_path / "conversations.json"
    export_path.write_text(json.dumps([conv1, conv2]), encoding="utf-8")

    with pytest.raises(SecretFound):
        import_chatgpt_export(fresh_store, export_path)

    # No partial-write: nothing landed
    assert fresh_store.count_facts() == 0


def test_cli_bootstrap_with_chatgpt_flag(cli_runner, isolated_lamark_home: Path, chatgpt_export: Path) -> None:
    """`lamark bootstrap --name X --chatgpt path` runs wizard + importer."""
    from lamark.cli import app

    result = cli_runner.invoke(
        app,
        ["bootstrap", "--name", "Anna", "--locale", "en-US", "--chatgpt", str(chatgpt_export)],
    )
    assert result.exit_code == 0, f"stderr={result.stderr!r}"

    from lamark.memory import MemoryStore

    store = MemoryStore.open(isolated_lamark_home / "honcho.db")
    try:
        # Wizard seeds: name (1) + locale (1) = 2 wizard facts
        # ChatGPT import: 4 user messages
        # Total: 6
        assert store.count_facts() == 6
    finally:
        store.close()
