"""
Inference router — choose between MoE primary and dense Phase-2-LoRA secondary.

Phase 1 routing rules (heuristics):
- Explicit force= → use that backend.
- Dense unavailable → primary, with logged fallback.
- Code-fenced prompt → primary (agentic coding strength).
- Long writing-flavoured prompt → dense.
- Default → primary.

Phase 2 swaps the heuristic for a small Qwen 0.6B classifier fine-tuned on
labelled examples. The Backend/RoutingContext interface stays the same so
swapping is an internal implementation change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from lamark.config import (
    DEFAULT_DENSE_MODEL,
    DEFAULT_DENSE_QUANTIZATION,
    DEFAULT_PRIMARY_MODEL,
    DEFAULT_PRIMARY_QUANTIZATION,
)

BackendName = Literal["primary", "dense"]


@dataclass(frozen=True)
class Backend:
    name: BackendName
    model: str
    quantization: str
    endpoint: str


@dataclass
class RoutingContext:
    """Inputs to the router decision."""

    prompt: str
    force: BackendName | None = None
    # Optional hints from the agent loop (Phase 2+):
    expected_max_tokens: int | None = None
    style_critical: bool = False
    metadata: dict = field(default_factory=dict)


# Patterns kept as module-level so they compile once.
_CODE_FENCE_RE = re.compile(r"```[a-zA-Z]*\n.*?```", re.DOTALL)
_INLINE_CODE_LIKE_RE = re.compile(r"(\bdef\s+\w+\(|\bclass\s+\w+\b|\bfn\s+\w+\(|=>\s*\{|\bimport\s+\w+)")
_WRITING_KEYWORDS = (
    "write", "draft", "compose", "blog", "article", "essay", "post", "letter",
    "rewrite", "summary", "tweet", "paragraph", "story", "narrative",
)


class InferenceRouter:
    """Picks a Backend for a given RoutingContext. Explainable via last_decision_log."""

    def __init__(
        self,
        primary_endpoint: str = "http://127.0.0.1:8000/v1",
        dense_endpoint: str = "http://127.0.0.1:8001/v1",
        primary_model: str = DEFAULT_PRIMARY_MODEL,
        dense_model: str = DEFAULT_DENSE_MODEL,
        primary_quantization: str = DEFAULT_PRIMARY_QUANTIZATION,
        dense_quantization: str = DEFAULT_DENSE_QUANTIZATION,
        dense_available: bool = False,
    ) -> None:
        self._primary = Backend(
            name="primary",
            model=primary_model,
            quantization=primary_quantization,
            endpoint=primary_endpoint,
        )
        self._dense = Backend(
            name="dense",
            model=dense_model,
            quantization=dense_quantization,
            endpoint=dense_endpoint,
        )
        self.dense_available = dense_available
        self.last_decision_log: list[str] = []

    def choose(self, ctx: RoutingContext) -> Backend:
        self.last_decision_log = []

        # 1) Explicit force always wins (caller has more info than us)
        if ctx.force == "primary":
            self.last_decision_log.append("rule: force='primary' set by caller")
            return self._primary
        if ctx.force == "dense":
            self.last_decision_log.append("rule: force='dense' set by caller")
            return self._dense

        prompt = ctx.prompt or ""
        prompt_lower = prompt.lower()

        # 2) Code-fenced or code-like → primary (MoE has strong agentic coding numbers).
        if _CODE_FENCE_RE.search(prompt) or _INLINE_CODE_LIKE_RE.search(prompt):
            self.last_decision_log.append("rule: code patterns detected → primary (MoE)")
            return self._primary

        # 3) Writing-flavoured prompt → dense if available.
        is_writing = any(kw in prompt_lower for kw in _WRITING_KEYWORDS)
        if is_writing or ctx.style_critical:
            if self.dense_available:
                self.last_decision_log.append(
                    "rule: writing/style-critical + dense_available → dense (style LoRA)"
                )
                return self._dense
            self.last_decision_log.append(
                "rule: writing flavour matched but dense unavailable → fall back to primary (MoE)"
            )
            return self._primary

        # 4) Default: primary (MoE is fast and competent for chat).
        self.last_decision_log.append("rule: default → primary (MoE)")
        return self._primary
