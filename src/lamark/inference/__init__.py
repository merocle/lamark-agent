"""
Lamark inference layer.

- `client.OpenAIChatClient`: HTTP client speaking OpenAI-compatible chat API.
  Used for both vLLM (primary, MoE) and llama.cpp (dense, Phase 2 LoRA).
- `router.InferenceRouter`: decides which backend handles a given prompt,
  with explainable decision_log.
"""

from __future__ import annotations

from lamark.inference.client import OpenAIChatClient
from lamark.inference.router import Backend, InferenceRouter, RoutingContext

__all__ = ["OpenAIChatClient", "InferenceRouter", "Backend", "RoutingContext"]
