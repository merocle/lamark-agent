"""
Test Module 6 — inference router.

The router picks a backend (MoE primary vs dense Phase-2-LoRA secondary) given
the user's prompt and config. The choice rules are deliberately simple in
Phase 1 (heuristics on prompt length, keywords, language); a small classifier
model takes over in Phase 2 once it's trained.

RED: tests fail until src/lamark/inference/{router,backend}.py exist.
"""

from __future__ import annotations

import pytest


# ---- backend selection rules ---------------------------------------------


def test_short_chat_prompts_route_to_moe() -> None:
    """Default: short, conversational → MoE primary (fast)."""
    from lamark.inference.router import InferenceRouter, RoutingContext

    router = InferenceRouter()
    backend = router.choose(RoutingContext(prompt="hey, what's the time?"))
    assert backend.name == "primary"
    assert backend.model.startswith("Qwen/Qwen3.6-35B-A3B")


def test_long_writing_request_routes_to_dense_when_available() -> None:
    """When a style-LoRA on dense is available AND prompt is writing-flavoured → dense."""
    from lamark.inference.router import InferenceRouter, RoutingContext

    router = InferenceRouter(dense_available=True)
    backend = router.choose(
        RoutingContext(prompt="write a long blog post about the evolution of LLM memory")
    )
    assert backend.name == "dense", (
        f"writing prompt with dense_available=True must route to dense; got {backend.name}"
    )


def test_dense_unavailable_falls_back_to_moe_with_warning() -> None:
    """If dense backend not ready (no LoRA, no running server) → fall back to MoE."""
    from lamark.inference.router import InferenceRouter, RoutingContext

    router = InferenceRouter(dense_available=False)
    backend = router.choose(
        RoutingContext(prompt="please draft an email to my landlord")
    )
    assert backend.name == "primary"  # MoE
    # Router should record why it fell back, not silently swallow
    assert any("dense" in r.lower() for r in router.last_decision_log)


def test_explicit_force_overrides_heuristics() -> None:
    """RoutingContext(force='dense') always routes to dense (even if unavailable)."""
    from lamark.inference.router import InferenceRouter, RoutingContext

    router = InferenceRouter(dense_available=True)
    backend = router.choose(RoutingContext(prompt="hi", force="dense"))
    assert backend.name == "dense"

    backend = router.choose(RoutingContext(prompt="write a long blog post", force="primary"))
    assert backend.name == "primary"


def test_code_prompts_route_to_primary() -> None:
    """Code-fenced prompts → MoE (agentic coding strength of Qwen3.6 SWE-bench numbers)."""
    from lamark.inference.router import InferenceRouter, RoutingContext

    router = InferenceRouter(dense_available=True)
    backend = router.choose(
        RoutingContext(prompt="```python\ndef foo(x):\n    return x + 1\n```\nfix this bug")
    )
    assert backend.name == "primary"


def test_decision_log_is_explainable() -> None:
    """Every choice records the rule that fired — needed for debugging UX complaints."""
    from lamark.inference.router import InferenceRouter, RoutingContext

    router = InferenceRouter(dense_available=True)
    router.choose(RoutingContext(prompt="hi"))
    assert router.last_decision_log, "router must populate last_decision_log"
    assert any(s in str(router.last_decision_log).lower() for s in ("rule", "primary", "moe"))


# ---- client wrapper interface --------------------------------------------


def test_openai_client_constructs_chat_request() -> None:
    """Both vLLM (primary) and llama.cpp (dense) are OpenAI-compatible.
    The client wraps that with timeout / retry / structured logging."""
    from lamark.inference.client import OpenAIChatClient

    client = OpenAIChatClient(endpoint="http://127.0.0.1:8000/v1", model="Qwen/Qwen3.6-35B-A3B")
    payload = client.build_payload(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64,
        temperature=0.0,
    )
    assert payload["model"] == "Qwen/Qwen3.6-35B-A3B"
    assert payload["messages"][0]["content"] == "hi"
    assert payload["max_tokens"] == 64
    assert payload["temperature"] == 0.0
    assert payload["stream"] is False  # default


def test_openai_client_respects_streaming_flag() -> None:
    from lamark.inference.client import OpenAIChatClient

    client = OpenAIChatClient(endpoint="http://x", model="m")
    payload = client.build_payload(
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64,
        stream=True,
    )
    assert payload["stream"] is True


def test_openai_client_chat_calls_http_correctly(monkeypatch: pytest.MonkeyPatch) -> None:
    """`chat()` posts to {endpoint}/chat/completions and parses the response."""
    from lamark.inference.client import OpenAIChatClient

    captured: dict = {}

    def fake_post(url: str, json_payload: dict, timeout: float) -> dict:
        captured["url"] = url
        captured["json"] = json_payload
        captured["timeout"] = timeout
        return {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"completion_tokens": 1},
        }

    client = OpenAIChatClient(endpoint="http://127.0.0.1:8000/v1", model="m")
    client._http_post = fake_post  # type: ignore[method-assign]

    result = client.chat(messages=[{"role": "user", "content": "hi"}], max_tokens=10)

    assert captured["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert captured["json"]["model"] == "m"
    assert result.content == "ok"
    assert result.completion_tokens == 1
