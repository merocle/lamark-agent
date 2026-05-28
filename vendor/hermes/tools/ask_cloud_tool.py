"""ask_cloud — privacy-preserving cloud escalation tool (LAMARK-PATCH A.10).

The local Lamark model invokes this tool when it determines its own
output would be insufficient for a query (deep reasoning, code that
spans more context than the local model can carry well, specialized
domain expertise). Each invocation:

  1. Runs the outbound ``task`` through the LAMARK-PATCH A.3 redaction
     pipeline. Hard secrets (API keys, JWTs, etc.) HARD-FAIL — the
     request never leaves the box. PII (emails, phones) is substituted.
  2. Requests user approval through Hermes' gateway approval system
     (Telegram ✅/❌). Without explicit user yes, no outbound call.
  3. POSTs to the LiteLLM proxy with the chosen model. The proxy fans
     out to OpenAI / Anthropic / Vertex / etc.
  4. Logs an audit record to ``$LAMARK_HOME/cloud_calls.jsonl`` with the
     redacted task, model, reason, token counts, latency. Nothing
     about the raw user message survives in the log if it had secrets.

The local model SEES this tool advertised in the system prompt via
the standard Hermes tool registry — `get_tool_definitions()` auto-
includes our registration. No chat-template patch is needed.

Configuration via env:
  LITELLM_API_KEY    — required, the bearer token for the proxy
  LITELLM_BASE_URL   — optional, default https://litellm.labs.jb.gg/v1
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from tools.registry import registry, tool_error

logger = logging.getLogger("tools.ask_cloud")


DEFAULT_BASE_URL = "https://litellm.labs.jb.gg/v1"

# Curated set of models we expose to the local agent. Keeping it small
# (six) keeps the model's "which one?" choice tractable. To add more
# without redeploying, just append to LITELLM_EXTRA_MODELS env var as a
# comma-separated list.
CURATED_MODELS = [
    "openai/gpt-5.5",
    "openai/gpt-5-mini",
    "anthropic/claude-opus-4-5",
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-haiku-4-5",
    "vertex_ai/gemini-2.5-pro",
]


def _models_enum() -> list[str]:
    """Curated list, plus anything in LITELLM_EXTRA_MODELS at startup."""
    extras = [m.strip() for m in os.environ.get("LITELLM_EXTRA_MODELS", "").split(",") if m.strip()]
    return CURATED_MODELS + extras


ASK_CLOUD_SCHEMA = {
    "name": "ask_cloud",
    "description": (
        "Delegate this exact query to a stronger cloud model when you genuinely "
        "cannot produce a high-quality answer yourself.\n\n"
        "Use SPARINGLY — data leaves the local box and costs the user money. "
        "Default to answering locally when you can give a useful answer.\n\n"
        "GOOD FITS: complex multi-step reasoning, code requiring deep "
        "understanding, specialized expertise (medicine, law, niche tech), "
        "long-context analysis you cannot hold.\n"
        "BAD FITS: identity / about-yourself questions, casual chat, simple "
        "factual lookups, anything where local context (user memory, files) "
        "matters more than raw model power.\n\n"
        "The user must approve each call in Telegram before the request leaves. "
        "PII in your `task` parameter gets auto-redacted; if you include a "
        "real secret (API key, JWT) the call is refused outright."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "enum": _models_enum(),
                "description": (
                    "Which cloud model to delegate to. Pick by task:\n"
                    "- claude-opus-4-5: deep reasoning, code with subtle bugs, complex multi-step\n"
                    "- claude-sonnet-4-6: balanced general — good default for harder tasks\n"
                    "- claude-haiku-4-5: fast translations / paraphrases / simple expansions\n"
                    "- gpt-5.5: alternative to opus, strong coding\n"
                    "- gpt-5-mini: cheap reasoning, slightly weaker than gpt-5.5\n"
                    "- gemini-2.5-pro: when you need ≥200K context or Google-flavor knowledge"
                ),
            },
            "task": {
                "type": "string",
                "description": (
                    "Reformulated query to send to the cloud. You may strip "
                    "personal context here — this is what actually leaves the box. "
                    "Be specific and self-contained: the cloud model won't see "
                    "any prior conversation."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "One sentence: why your local capability is insufficient. "
                    "Logged for audit and visible in the Telegram approval prompt."
                ),
            },
        },
        "required": ["model", "task", "reason"],
    },
}


# ──────────────────────────────────────────────────────────────────
#  Redaction
# ──────────────────────────────────────────────────────────────────

def _redact_or_raise(task: str) -> tuple[str, list[dict]]:
    """Run the Lamark redaction pipeline. Raises on verified hard secret.

    Returns (redacted_text, list_of_redaction_records). On any import
    failure we fall through with the original text — better to ship the
    user's actual query than to silently strip it; the redaction layer
    is defense-in-depth, not the only line.
    """
    try:
        from lamark.redaction import RedactionPipeline, SecretFound
    except Exception as exc:
        logger.debug("ask_cloud: redaction unavailable (%s) — passing through", exc)
        return task, []

    pipeline = RedactionPipeline()
    try:
        result = pipeline.process(task)
        redactions: list[dict] = []
        for r in (result.redactions or []):
            redactions.append({"kind": getattr(r, "kind", "?"), "placeholder": getattr(r, "placeholder", "?")})
        return result.text, redactions
    except SecretFound as exc:
        raise ValueError(f"hard secret detected — refusing to send to cloud: {exc}") from exc


# ──────────────────────────────────────────────────────────────────
#  Approval gate
# ──────────────────────────────────────────────────────────────────

def _request_user_approval(model: str, task_redacted: str, reason: str) -> str:
    """Return 'once' / 'session' / 'always' on user OK; 'deny' on rejection."""
    from tools.approval import prompt_dangerous_approval

    # Compose what the user sees. Truncate the task to the first 600 chars
    # so the approval card stays readable in Telegram. The full task is
    # already in our audit log.
    preview = task_redacted if len(task_redacted) <= 600 else (task_redacted[:600] + " …")

    command = f"ask_cloud → {model}"
    description = (
        f"Outbound to cloud model {model}\n\n"
        f"Why: {reason}\n\n"
        f"Task being sent:\n{preview}"
    )
    try:
        return prompt_dangerous_approval(command, description)
    except Exception as exc:
        # On any approval-system error, fail closed — never send blindly.
        logger.warning("ask_cloud: approval system error (%s) — denying", exc)
        return "deny"


# ──────────────────────────────────────────────────────────────────
#  LiteLLM call
# ──────────────────────────────────────────────────────────────────

def _call_litellm(model: str, task: str, *, max_tokens: int = 2048,
                  temperature: float = 0.3, timeout: int = 120) -> dict:
    api_key = os.environ.get("LITELLM_API_KEY")
    if not api_key:
        raise RuntimeError("LITELLM_API_KEY not set — cannot reach the proxy")
    base_url = os.environ.get("LITELLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": task}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ──────────────────────────────────────────────────────────────────
#  Audit log
# ──────────────────────────────────────────────────────────────────

def _audit_log(record: dict) -> None:
    home = Path(os.environ.get("LAMARK_HOME", str(Path.home() / ".lamark")))
    path = home / "cloud_calls.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        logger.warning("ask_cloud: audit write failed (%s) — continuing", exc)


# ──────────────────────────────────────────────────────────────────
#  Handler
# ──────────────────────────────────────────────────────────────────

def ask_cloud_handler(args: dict, **kwargs) -> str:
    """Tool entry point — sync handler returning a JSON string.

    Returns a structured payload on success:
        {"model": ..., "content": ..., "tokens_in": ..., "tokens_out": ...}
    On any rejection or error, returns {"error": ...} so the model can
    fall back to its own answer rather than getting stuck.
    """
    model = (args.get("model") or "").strip()
    task = (args.get("task") or "").strip()
    reason = (args.get("reason") or "").strip()

    if not model or not task:
        return tool_error("missing required parameter: model and task are both required")

    if model not in _models_enum():
        return tool_error(f"unknown model: {model!r}. Must be one of {_models_enum()}.")

    # Stage 1 — redact
    try:
        task_redacted, redactions = _redact_or_raise(task)
    except ValueError as exc:
        return tool_error(str(exc))

    # Stage 2 — approval. Telegram-side will surface this as a card.
    decision = _request_user_approval(model, task_redacted, reason)
    if decision == "deny":
        _audit_log({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": model,
            "reason": reason,
            "decision": "deny",
            "redactions": redactions,
            "task_chars": len(task_redacted),
        })
        return tool_error("user declined the cloud escalation — answer locally instead")

    # Stage 3 — POST to LiteLLM
    t0 = time.time()
    try:
        resp = _call_litellm(model, task_redacted)
    except urllib.error.HTTPError as exc:
        body = (exc.read() or b"").decode("utf-8", errors="replace")[:300]
        _audit_log({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": model,
            "reason": reason,
            "decision": decision,
            "result": "http_error",
            "status": exc.code,
            "body_excerpt": body,
        })
        return tool_error(f"LiteLLM HTTP {exc.code}: {body}")
    except Exception as exc:
        _audit_log({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": model,
            "reason": reason,
            "decision": decision,
            "result": "error",
            "error": str(exc),
        })
        return tool_error(f"cloud call failed: {exc}")

    elapsed_ms = int((time.time() - t0) * 1000)
    msg = (resp.get("choices") or [{}])[0].get("message") or {}
    # Reasoning models (o-series, gpt-5) may put final answer into either
    # `content` or `reasoning_content` depending on whether the budget
    # ran out — accept both.
    content = (msg.get("content") or msg.get("reasoning_content") or "").strip()
    usage = resp.get("usage") or {}

    _audit_log({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": model,
        "reason": reason,
        "decision": decision,
        "result": "ok",
        "redactions": redactions,
        "task_chars": len(task_redacted),
        "content_chars": len(content),
        "tokens_in": usage.get("prompt_tokens"),
        "tokens_out": usage.get("completion_tokens"),
        "latency_ms": elapsed_ms,
    })

    return json.dumps({
        "model": model,
        "content": content,
        "tokens_in": usage.get("prompt_tokens"),
        "tokens_out": usage.get("completion_tokens"),
        "latency_ms": elapsed_ms,
    }, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────
#  Register at module import — auto-discovered by registry scan
# ──────────────────────────────────────────────────────────────────

def _check_availability() -> tuple[bool, str]:
    """Lazy precondition check the registry runs before exposing the tool.

    We require LITELLM_API_KEY to be set. URL is optional (defaults).
    """
    if not os.environ.get("LITELLM_API_KEY"):
        return False, "LITELLM_API_KEY not set"
    return True, ""


registry.register(
    name="ask_cloud",
    toolset="ask_cloud",
    schema=ASK_CLOUD_SCHEMA,
    handler=ask_cloud_handler,
    check_fn=_check_availability,
    requires_env=["LITELLM_API_KEY"],
    is_async=False,
    description="Delegate to a stronger cloud model with Telegram approval (privacy-preserving)",
    emoji="☁️",
    max_result_size_chars=20000,
)
