#!/usr/bin/env python3
"""
Per-model template adapters — ONE neutral dataset, many models.

The canonical dataset (agentic_format) is model-NEUTRAL: assistant turns carry a
plain `thinking` field and structured `tool_calls`, with no baked template tokens.
This module converts a neutral conversation into the messages + template kwargs a
specific model family expects, so train/eval scripts render the SAME data to
Qwen3.5's `<think>` or Gemma 4's `<|channel>thought` without touching the data.

Mirrors the Rust side's reasoning parsers (qwen3_moe / gemma4 / nano_v3). Tool
calls stay structured — the model's own chat template renders them to its tool
tokens (`<tool_call>` for Qwen, `<|tool_call>` for Gemma 4), so only THINKING and
decoding/parse differ per family and live here.

Add a model: extend FAMILY_BY_KEYWORD + the per-family branches. Verify the
thinking representation against the real tokenizer before a training run.
"""
from __future__ import annotations

import re

from agentic_format import THINK_CLOSE, THINK_OPEN, split_thinking

# substring (lowercased model path/name) -> family
FAMILY_BY_KEYWORD = {
    "gemma4": "gemma4", "gemma-4": "gemma4", "gemma_4": "gemma4", "gemma4_unified": "gemma4",
    "qwen3.5": "qwen", "qwen3_5": "qwen", "qwen3.6": "qwen", "qwen3_5_moe": "qwen", "qwen3": "qwen",
    "nemotron": "nano_v3", "nano": "nano_v3",
}

# Gemma 4 wraps reasoning in a channel: start token "<|channel>" (followed by the
# channel name "thought") ... end token "<channel|>" (note the reversed pipe), then
# the final answer. Verify exact spacing against the real tokenizer when wired.
_GEMMA_OPEN, _GEMMA_CLOSE = "<|channel>thought", "<channel|>"

_GEN_PARAMS = {  # tool-loop decoding; never greedy (invariant 9 / model matrix §11)
    "qwen": {"temperature": 0.7, "top_p": 0.8, "top_k": 20},
    "gemma4": {"temperature": 0.7, "top_p": 0.9, "top_k": 40},
    "nano_v3": {"temperature": 0.6, "top_p": 0.95, "top_k": 20},
}


class TemplateAdapter:
    """Render the neutral canonical format into one model family's chat messages."""

    def __init__(self, family: str):
        if family not in _GEN_PARAMS:
            raise ValueError(f"unknown family {family!r}; known: {list(_GEN_PARAMS)}")
        self.family = family

    @classmethod
    def for_model(cls, model_path_or_name: str, override: str | None = None) -> "TemplateAdapter":
        if override:
            return cls(override)
        low = model_path_or_name.lower()
        for kw, fam in FAMILY_BY_KEYWORD.items():
            if kw in low:
                return cls(fam)
        return cls("qwen")  # safe default — the validated path

    # ── train/eval: neutral -> family messages ────────────────────────────────
    def _inline_thinking(self, thinking: str, answer: str | None) -> str:
        ans = (answer or "").strip()
        if self.family == "gemma4":
            block = f"{_GEMMA_OPEN}\n{thinking.strip()}\n{_GEMMA_CLOSE}"
            return f"{block}\n{ans}" if ans else block
        # qwen + nano_v3: inline <think>…</think>
        block = f"{THINK_OPEN}\n{thinking.strip()}\n{THINK_CLOSE}"
        return f"{block}\n\n{ans}" if ans else block

    def to_messages(self, messages: list[dict]) -> list[dict]:
        """Drop the neutral `thinking` field, folding it into content the way this
        family's template expects."""
        out = []
        for m in messages:
            if m.get("role") == "assistant" and m.get("thinking"):
                nm = {k: v for k, v in m.items() if k != "thinking"}
                nm["content"] = self._inline_thinking(m["thinking"], m.get("content"))
                out.append(nm)
            elif "thinking" in m:
                out.append({k: v for k, v in m.items() if k != "thinking"})
            else:
                out.append(m)
        return out

    def template_kwargs(self, enable_thinking: bool) -> dict:
        """Extra apply_chat_template kwargs. Both Qwen3.5 and Gemma 4 honor
        enable_thinking; nano_v3 likewise via its chat template."""
        return {"enable_thinking": enable_thinking}

    def gen_params(self) -> dict:
        return dict(_GEN_PARAMS[self.family])

    # ── eval: parse a raw generation back to structure (verifier / serve) ──────
    def parse_completion(self, text: str) -> tuple[str | None, str | None, list[dict]]:
        """(content, reasoning, tool_calls[{name,arguments}]) from a raw generation.
        Tool calls use the same <tool_call>{json}</tool_call> JSON envelope across
        our served families; thinking delimiters differ per family."""
        import json
        reasoning, body = self._strip_thinking(text)
        calls = []
        for raw in re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", body, re.DOTALL):
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            args = obj.get("arguments", obj.get("parameters", {}))
            calls.append({"name": obj.get("name", "unknown"),
                          "arguments": args if isinstance(args, dict) else {}})
        content = re.sub(r"<tool_call>.*?</tool_call>", "", body, flags=re.DOTALL).strip()
        return (content or None), reasoning, calls

    def _strip_thinking(self, text: str) -> tuple[str | None, str]:
        if self.family == "gemma4":
            # start token <|channel> (+ channel label) ... end token <channel|>
            m = re.search(r"<\|channel>\s*\w*\s*(.*?)\s*<channel\|>", text, re.DOTALL)
            if m:
                return m.group(1).strip(), text[m.end():].strip()
            return None, text.strip()
        return split_thinking(text)
