"""
Render-time tests for the Lamark chat template (L1).

The template lives at `learning/templates/lamark_chat_template.jinja`. It is
copied into each model dir by `scripts/install_chat_template.sh` and consumed
by vLLM / `transformers.apply_chat_template`. These tests render it directly
through Jinja2 with the same kwargs apply_chat_template would pass, so we
catch template bugs without booting a model.

Invariants:
- No system message in input  → default Lamark identity is injected.
- Caller-supplied system msg   → identity is NOT injected (caller wins).
- Assistant generation prompt  → ends with `<think></think>` by default.
- enable_thinking=True         → ends with `<think>` (open block).
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1] / "templates" / "lamark_chat_template.jinja"
)


@pytest.fixture(scope="module")
def template() -> jinja2.Template:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE_PATH.parent),
        keep_trailing_newline=False,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    return env.get_template(TEMPLATE_PATH.name)


def render(template: jinja2.Template, **kwargs: object) -> str:
    return template.render(**kwargs)


def test_default_identity_injected_when_no_system_message(template: jinja2.Template) -> None:
    out = render(
        template,
        messages=[{"role": "user", "content": "hi"}],
        add_generation_prompt=True,
    )
    assert "<|im_start|>system" in out
    assert "You are Lamark" in out
    assert "Lamarckian inheritance" in out
    assert "You are NOT Jean-Baptiste Lamarck" in out
    assert "<|im_start|>user\nhi<|im_end|>" in out
    assert out.rstrip().endswith("<|im_start|>assistant\n<think></think>")


def test_caller_system_message_wins(template: jinja2.Template) -> None:
    out = render(
        template,
        messages=[
            {"role": "system", "content": "You are a haiku generator."},
            {"role": "user", "content": "ocean"},
        ],
        add_generation_prompt=True,
    )
    assert "You are a haiku generator." in out
    assert "You are Lamark" not in out, "Default identity must not be injected when caller supplies system"
    assert out.count("<|im_start|>system") == 1


def test_thinking_mode_open_block(template: jinja2.Template) -> None:
    out = render(
        template,
        messages=[{"role": "user", "content": "hi"}],
        add_generation_prompt=True,
        enable_thinking=True,
    )
    assert out.rstrip().endswith("<|im_start|>assistant\n<think>")


def test_no_generation_prompt_omits_assistant_tail(template: jinja2.Template) -> None:
    out = render(
        template,
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
        add_generation_prompt=False,
    )
    assert out.rstrip().endswith("<|im_start|>assistant\nhello<|im_end|>")
    assert "<think>" not in out


def test_multi_turn_conversation_round_trip(template: jinja2.Template) -> None:
    msgs = [
        {"role": "user", "content": "what are you?"},
        {"role": "assistant", "content": "lamark-the-agent"},
        {"role": "user", "content": "explain L3."},
    ]
    out = render(template, messages=msgs, add_generation_prompt=True)
    for m in msgs:
        assert f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>" in out
    assert (
        out.index("what are you?")
        < out.index("lamark-the-agent")
        < out.index("explain L3.")
    )
