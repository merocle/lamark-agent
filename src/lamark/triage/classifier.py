"""Question-intent classifier for the Lamark triage layer.

Routes incoming user messages to the right capability:
- factual    → must ground via web_search (anti-confabulation)
- reasoning  → suggest ask_cloud (deeper reasoning than a 35B local)
- personal   → local + L2 memory
- casual     → local
- code       → local (escalate only if very hard)

The design bet (the user's insight): a small model that *confabulates*
facts can still *classify* intent reliably, because categorization is
not a knowledge task. "When was Lamarck born?" → the model may not
recall 1744, but it can confidently tag the question as factual.

Hybrid strategy: cheap regex short-circuits the obvious cases
(explicit cloud requests, trivial greetings) WITHOUT an LLM call; only
genuinely ambiguous messages pay for a local classification call.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("lamark.triage")

VALID_INTENTS = {"factual", "reasoning", "personal", "casual", "code"}

# --- regex pre-filters (no LLM) -------------------------------------

# Explicit "ask the cloud" — the model + ask_cloud tool already handle
# these; triage must not interfere (would double-route).
# Second group allows Russian case endings (Клод→Клоду, опус→опусу) by
# matching word stems without a trailing boundary on the Cyrillic forms.
_EXPLICIT_CLOUD = re.compile(
    r"(спрос\w*|задай|передай|ask|use|через)\b.{0,30}"
    r"(claude|клод\w*|gpt|gpt-?5|opus|опус\w*|haiku|хайку|gemini|джемини\w*|"
    r"облач\w*|cloud)",
    re.IGNORECASE,
)

# Obvious casual / acknowledgement — no real question to route.
_CASUAL = re.compile(
    r"^\s*(привет\w*|хай|хеллоу|здаров\w*|здравствуй\w*|hi|hello|hey|yo|"
    r"спасибо|спс|thanks|thank you|thx|ок|ok|окей|okay|ясно|понял\w*|"
    r"да|нет|ага|угу|пока|bye|good morning|доброе утро|добрый день)"
    r"[\s!.,)]*$",
    re.IGNORECASE,
)


def _regex_prefilter(text: str) -> str | None:
    """Return an intent label if a cheap rule matches, else None."""
    t = (text or "").strip()
    if not t:
        return "casual"
    if _EXPLICIT_CLOUD.search(t):
        return "explicit_cloud"
    # Casual only when short AND not a question (a short "почему?" is not casual).
    if "?" not in t and len(t.split()) <= 4 and _CASUAL.match(t):
        return "casual"
    return None


# --- LLM classification ---------------------------------------------

_CLASSIFY_SYSTEM = (
    "You are a routing classifier inside a local AI assistant. Classify the "
    "user's message by what answering it REQUIRES — not by its topic. Output "
    "ONLY a single-line JSON object, no prose, no code fences.\n\n"
    "Schema: {\"intent\": one of "
    "[\"factual\",\"reasoning\",\"personal\",\"casual\",\"code\"], "
    "\"needs_web\": bool, \"needs_cloud\": bool}\n\n"
    "Rules:\n"
    "- needs_web = true when answering requires specific facts, dates, "
    "events, named entities, statistics, or current/recent information that "
    "a model could get wrong from memory. Historical facts, 'who/when/where', "
    "definitions of real-world things, news → needs_web true, intent factual.\n"
    "- needs_cloud = true when answering needs deep multi-step reasoning, a "
    "rigorous proof, hard math, or specialized expert knowledge beyond a "
    "small local model. intent reasoning.\n"
    "- personal = about the user themselves, their data, preferences, prior "
    "conversations. needs_web/cloud false.\n"
    "- casual = greetings, small talk, acknowledgements. all false.\n"
    "- code = writing/explaining code. needs_cloud true only if genuinely "
    "complex.\n"
    "When a question asks to 'explain' or 'tell me about' a real-world topic, "
    "prefer factual + needs_web true."
)


def _llm_classify(text: str, *, model: str) -> dict | None:
    """Run the local classifier. Returns parsed dict or None on any failure."""
    try:
        from agent.auxiliary_client import call_llm
    except Exception as exc:
        logger.debug("triage: auxiliary_client unavailable (%s)", exc)
        return None

    try:
        resp = call_llm(
            provider="custom",
            model=model,
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": text},
            ],
            max_tokens=512,
            temperature=0,
            timeout=30,
        )
        msg = resp.choices[0].message
        # The local model runs with qwen3 reasoning_parser, which routes
        # the (short) output into message.reasoning / reasoning_content
        # and leaves message.content None. Accept all three so the JSON
        # is found wherever the parser put it.
        content = (
            getattr(msg, "content", None)
            or getattr(msg, "reasoning_content", None)
            or getattr(msg, "reasoning", None)
            or ""
        )
    except Exception as exc:
        logger.debug("triage: classify call failed (%s)", exc)
        return None

    return _parse_classification(content)


def _parse_classification(content: str) -> dict | None:
    """Extract the JSON object from a (possibly noisy) model response."""
    if not content:
        return None
    # Grab the first {...} block — tolerant of code fences / stray prose.
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return None
    try:
        raw = json.loads(m.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None

    intent = str(raw.get("intent", "")).strip().lower()
    if intent not in VALID_INTENTS:
        return None
    return {
        "intent": intent,
        "needs_web": bool(raw.get("needs_web", False)),
        "needs_cloud": bool(raw.get("needs_cloud", False)),
    }


# --- public API ------------------------------------------------------

def classify(text: str, *, model: str = "lamark") -> dict:
    """Classify a user message.

    Returns a dict {intent, needs_web, needs_cloud}. Never raises — on any
    failure returns {"intent": "unknown", ...} so the caller leaves the turn
    to answer locally exactly as it would without triage.
    """
    prefilter = _regex_prefilter(text)
    if prefilter == "explicit_cloud":
        return {"intent": "explicit_cloud", "needs_web": False, "needs_cloud": False}
    if prefilter == "casual":
        return {"intent": "casual", "needs_web": False, "needs_cloud": False}

    result = _llm_classify(text, model=model)
    if result is None:
        return {"intent": "unknown", "needs_web": False, "needs_cloud": False}
    return result
