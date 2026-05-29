"""Triage router — turn a classification into a per-turn nudge.

`apply(event)` is the single entry the vendor hook (LAMARK-PATCH A.13)
calls. It classifies the message and mutates `event.channel_prompt` —
an *ephemeral* system-prompt field that the agent sees but never
persists to history (gateway/run.py combines it into
`combined_ephemeral`).

Two actions:
- factual  → call web_search directly, inject results, instruct the
             model to answer ONLY from them (anti-confabulation).
- reasoning→ append a nudge to consider the ask_cloud tool.

Everything else is a no-op (local answers as usual). Any failure is
swallowed: triage must never break or degrade a turn.
"""
from __future__ import annotations

import json
import logging

from .classifier import classify

logger = logging.getLogger("lamark.triage")

# Max search results to fold into the grounding block.
_MAX_RESULTS = 5

_FACTUAL_PREAMBLE = (
    "[TRIAGE: factual question — grounding required]\n"
    "The user is asking for facts. Live web search results are provided "
    "below. Answer ONLY from these results and cite what they say. If the "
    "results do not contain the answer, say you could not verify it — do "
    "NOT guess or fill in from memory.\n\n"
    "Web search results for {query!r}:\n{results}"
)

_WEB_UNAVAILABLE = (
    "[TRIAGE: factual question — web search unavailable]\n"
    "The user is asking for facts but live search failed. Answer with an "
    "explicit uncertainty caveat; do NOT state specific dates, numbers, or "
    "names as certain — flag that you could not verify them."
)

_REASONING_NUDGE = (
    "\n\n[TRIAGE: this looks like it needs deeper reasoning than you "
    "reliably provide locally. Strongly consider the ask_cloud tool — the "
    "user approves each cloud call before anything is sent.]"
)


def _format_results(query: str, search_json: str) -> str | None:
    """Build the grounding block from web_search_tool's JSON, or None."""
    try:
        payload = json.loads(search_json)
    except (ValueError, TypeError):
        return None
    if not payload.get("success"):
        return None
    web = (payload.get("data") or {}).get("web") or []
    if not web:
        return None
    lines = []
    for r in web[:_MAX_RESULTS]:
        title = (r.get("title") or "").strip()
        desc = (r.get("description") or "").strip()
        url = (r.get("url") or "").strip()
        if not (title or desc):
            continue
        lines.append(f"- {title}: {desc} ({url})")
    if not lines:
        return None
    return _FACTUAL_PREAMBLE.format(query=query, results="\n".join(lines))


def _ground_factual(event) -> None:
    """Run web_search and inject results into channel_prompt."""
    try:
        from tools.web_tools import web_search_tool
    except Exception as exc:
        logger.debug("triage: web_tools unavailable (%s)", exc)
        _set_prompt(event, _WEB_UNAVAILABLE)
        return

    try:
        search_json = web_search_tool(event.text, limit=_MAX_RESULTS)
    except Exception as exc:
        logger.debug("triage: web_search failed (%s)", exc)
        _set_prompt(event, _WEB_UNAVAILABLE)
        return

    block = _format_results(event.text, search_json)
    if block is None:
        _set_prompt(event, _WEB_UNAVAILABLE)
        return
    _set_prompt(event, block)
    logger.info("triage: factual grounding injected (%d chars)", len(block))


def _set_prompt(event, text: str) -> None:
    """Set or append to event.channel_prompt without clobbering existing."""
    existing = (getattr(event, "channel_prompt", None) or "").strip()
    event.channel_prompt = (existing + "\n\n" + text).strip() if existing else text


def apply(event, *, model: str = "lamark") -> None:
    """Classify event.text and mutate event.channel_prompt accordingly.

    Safe to call unconditionally — internal events, commands, empty text,
    and any error all degrade to a no-op (the turn proceeds locally).
    """
    try:
        if getattr(event, "internal", False):
            return
        text = (getattr(event, "text", None) or "").strip()
        if not text or text.startswith("/"):
            return

        result = classify(text, model=model)
        intent = result.get("intent")

        if intent == "factual" or result.get("needs_web"):
            _ground_factual(event)
        elif intent == "reasoning" or result.get("needs_cloud"):
            _set_prompt(event, _REASONING_NUDGE.strip())
            logger.info("triage: reasoning nudge injected")
        # personal / casual / code / explicit_cloud / unknown → no-op
    except Exception as exc:  # noqa: BLE001 — never break a turn
        logger.debug("triage: apply failed (%s); proceeding locally", exc)
