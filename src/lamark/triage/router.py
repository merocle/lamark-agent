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

# Escalation directive PREPENDED TO THE USER MESSAGE TEXT - not the system
# prompt. Empirically the local model ignores an imperative in channel_prompt
# (it confidently answered an Einstein-field-equations derivation locally
# despite a "MUST call ask_cloud first" system directive) but reliably obeys
# an EXPLICIT user-side request to use the cloud (an explicit "ask claude"
# fires ask_cloud + the approval card, every time). So we synthesise that
# working explicit request and inject it into event.text. ask_cloud then runs
# inside the agent where the gateway approval callback is registered
# (run.py:16779), surfacing the Telegram approval card correctly. English
# directive on purpose (codebase is English-only) - the model is multilingual
# and obeys it regardless of the user's language, then replies in their
# language.
#
# Model choice: needs_cloud is reserved (post-tightening) for genuinely hard
# tasks, so default to the strongest - claude-opus-4-5.
_ESCALATE_PREFIX = (
    "[This question is hard - do NOT answer from your own knowledge, you will "
    "likely get it wrong. Call the ask_cloud tool with model claude-opus-4-5, "
    "passing the question below as the task. When the cloud replies, relay and "
    "refine its answer. Reply in the user's language.]\n\n"
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

        # Factual takes precedence: ground with live web search (cheap, no
        # approval). Only escalate to cloud when the classifier is confident
        # the task is BEYOND local (needs_cloud) — a bare "reasoning" intent
        # with needs_cloud=false (e.g. a standard √2 proof) stays local.
        if intent == "factual" or result.get("needs_web"):
            _ground_factual(event)
        elif result.get("needs_cloud"):
            # Prepend an explicit cloud-escalation request to the user text -
            # the reliable path (see _ESCALATE_PREFIX rationale). Guard against
            # double-prefixing if the event somehow re-enters.
            if not event.text.startswith("[This question is hard"):
                event.text = _ESCALATE_PREFIX + event.text
            logger.info("triage: cloud escalation injected into user text")
        # reasoning(local) / personal / casual / code / explicit_cloud /
        # unknown -> no-op (local answers)
    except Exception as exc:  # noqa: BLE001 - never break a turn
        logger.debug("triage: apply failed (%s); proceeding locally", exc)
