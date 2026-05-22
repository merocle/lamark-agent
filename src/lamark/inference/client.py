"""
OpenAI-compatible chat client.

Both vLLM and llama.cpp speak the OpenAI API; this client doesn't care which.
Thin wrapper: build payload, post, parse. Timeout/retry handled at the caller
(router) layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request


@dataclass(frozen=True)
class ChatResult:
    """Parsed OpenAI chat completion response."""

    content: str
    completion_tokens: int
    raw: dict[str, Any]


class OpenAIChatClient:
    """HTTP client for an OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        default_timeout: float = 600.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.default_timeout = default_timeout
        # Indirect HTTP layer so tests can monkeypatch _http_post easily.
        self._http_post: Callable[[str, dict, float], dict] = _urllib_post

    def build_payload(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float = 0.0,
        top_p: float = 1.0,
        stream: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stream": stream,
        }
        if extra:
            payload.update(extra)
        return payload

    def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float = 0.0,
        timeout: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ChatResult:
        payload = self.build_payload(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            extra=extra,
        )
        url = f"{self.endpoint}/chat/completions"
        raw = self._http_post(url, payload, timeout or self.default_timeout)
        # Parse defensively: vLLM and llama.cpp produce the same shape, but
        # field absence happens in practice.
        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"unexpected chat response shape: {raw!r}") from e
        completion_tokens = int(raw.get("usage", {}).get("completion_tokens") or 0)
        return ChatResult(content=content, completion_tokens=completion_tokens, raw=raw)


def _urllib_post(url: str, json_payload: dict, timeout: float) -> dict:
    """Stdlib HTTP — keeps inference layer free of httpx dependency."""
    body = json.dumps(json_payload).encode("utf-8")
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)
    except error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url}: {e.read()[:200]!r}") from e
    except error.URLError as e:
        raise RuntimeError(f"network error to {url}: {e.reason!r}") from e
