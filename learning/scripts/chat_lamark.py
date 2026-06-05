#!/usr/bin/env python3
"""
Interactive chat REPL for the Lamark adapter, served by vLLM on DGX Spark.

Talks to the OpenAI-compatible /v1/chat/completions endpoint exposed by
learning/scripts/spark/serve_vllm.sh. Supports two server-side model names:
    base    - the raw NemotronH base model (no adapter)
    lamark  - base + the LoRA adapter (default)

Slash commands inside the REPL:
    /model base | lamark   switch which model the next message goes to
    /clear                 reset the conversation history
    /history               print the conversation so far
    /quit | /exit          leave

Environment:
    LAMARK_VLLM_URL   default http://10.212.212.1:8765/v1
    LAMARK_MODEL      default lamark
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


URL   = os.environ.get("LAMARK_VLLM_URL", "http://10.212.212.1:8765/v1").rstrip("/")
MODEL = os.environ.get("LAMARK_MODEL", "lamark")
# Thinking (reasoning) mode. OFF by default for clean answers (invariant 23);
# toggle in the REPL with /think on|off. This is a lightweight PROSE check —
# for agentic/tool-call testing, drive the model through the Hermes harness
# (see cli-config.lamark.yaml), which runs the real tool loop.
THINK = os.environ.get("LAMARK_THINK", "0") == "1"


def healthcheck(base_url: str) -> str | None:
    """Return None if the server is healthy, an error string otherwise."""
    health = base_url.rsplit("/v1", 1)[0] + "/health"
    try:
        with urllib.request.urlopen(health, timeout=5) as resp:
            return None if resp.status == 200 else f"HTTP {resp.status}"
    except urllib.error.URLError as e:
        return f"unreachable: {e.reason}"
    except Exception as e:
        return str(e)


def stream_chat(model: str, history: list[dict]) -> str:
    """POST to /v1/chat/completions with stream=true, print tokens as they arrive."""
    body = json.dumps({
        "model":       model,
        "messages":    history,
        "max_tokens":  1200 if THINK else 600,
        "temperature": 0.7,
        "stream":      True,
        "chat_template_kwargs": {"enable_thinking": THINK},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{URL}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )

    out_parts: list[str] = []
    with urllib.request.urlopen(req, timeout=300) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            try:
                delta = event["choices"][0]["delta"].get("content")
            except (KeyError, IndexError):
                continue
            if delta:
                sys.stdout.write(delta)
                sys.stdout.flush()
                out_parts.append(delta)
    print()
    return "".join(out_parts)


def repl() -> None:
    global MODEL, THINK

    print(f"Lamark chat — endpoint: {URL}  model: {MODEL}  thinking: {'on' if THINK else 'off'}")
    if (err := healthcheck(URL)) is not None:
        print(f"  server not reachable: {err}")
        print(f"  Start it on Spark with: ./learning/scripts/spark/serve_chat.sh start")
        sys.exit(1)
    print("  /model base|lamark  /think on|off  /clear  /history  /quit")
    print()

    history: list[dict] = []

    while True:
        try:
            user_in = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_in:
            continue

        # ── slash commands ───────────────────────────────────────────────
        if user_in.startswith("/"):
            parts = user_in.split(maxsplit=1)
            cmd = parts[0]

            if cmd in ("/quit", "/exit"):
                break
            if cmd == "/clear":
                history.clear()
                print("(history cleared)")
                continue
            if cmd == "/history":
                for m in history:
                    print(f"  {m['role']}: {m['content'][:120]}{'...' if len(m['content']) > 120 else ''}")
                continue
            if cmd == "/model":
                if len(parts) == 1:
                    print(f"  current model: {MODEL}")
                else:
                    MODEL = parts[1].strip()
                    print(f"  model -> {MODEL}")
                continue
            if cmd == "/think":
                if len(parts) == 1:
                    print(f"  thinking: {'on' if THINK else 'off'}")
                else:
                    THINK = parts[1].strip().lower() in ("on", "true", "1", "yes")
                    print(f"  thinking -> {'on' if THINK else 'off'}")
                continue
            print(f"  unknown command: {cmd}")
            continue

        # ── normal message ───────────────────────────────────────────────
        history.append({"role": "user", "content": user_in})
        try:
            reply = stream_chat(MODEL, history)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"  [HTTP {e.code}] {err_body[:300]}")
            history.pop()
            continue
        except Exception as e:
            print(f"  [error] {e}")
            history.pop()
            continue

        history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    repl()
