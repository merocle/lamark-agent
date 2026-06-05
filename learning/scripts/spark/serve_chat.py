#!/usr/bin/env python3
"""
Minimal OpenAI-compatible chat server for a Lamark SFT adapter.

Stands in for vLLM when the base architecture is too new for the vLLM image
(e.g. qwen3_5 needs transformers 5.x). Loads base + LoRA via transformers and
exposes just enough of the OpenAI API for `chat_lamark.py`:

    GET  /health                 -> 200
    GET  /v1/models              -> {base, lamark}
    POST /v1/chat/completions    -> streaming (SSE) or single JSON

Model names: "lamark" = base + adapter (default), "base" = adapter disabled.
Honors `chat_template_kwargs.enable_thinking` (default False -> clean output).
Passes a request's `tools` array into the chat template so the model can emit
NATIVE tool calls (without this the prompt has no schemas and the model narrates
`WebSearch(...)` as prose). On the non-streaming path the response separates
`tool_calls`, `reasoning_content` (the <think> block), and clean `content`.
Stdlib http only; generation is serialized with a lock (single-GPU, single-user).

Env:
    MODEL_LOCAL   base model path                 [required]
    ADAPTER_DIR   LoRA adapter dir                 [required]
    PORT          listen port    (default: 8000)
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer

MODEL_LOCAL = os.environ["MODEL_LOCAL"]
ADAPTER_DIR = os.environ["ADAPTER_DIR"]
PORT = int(os.environ.get("PORT", "8000"))

print(f"[serve] loading {MODEL_LOCAL} + adapter {ADAPTER_DIR} ...", flush=True)
tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
_model = AutoModelForCausalLM.from_pretrained(
    MODEL_LOCAL, dtype=torch.bfloat16, device_map={"": 0}, trust_remote_code=True)  # pin GPU0; "auto" CPU-offloads conv layers -> causal_conv1d crash
_model = PeftModel.from_pretrained(_model, ADAPTER_DIR)
_model.eval()
_lock = threading.Lock()
print(f"[serve] ready on :{PORT}  (models: base, lamark)", flush=True)


def _prep(messages, enable_thinking, tools):
    enc = tok.apply_chat_template(
        messages, tools=tools or None, add_generation_prompt=True, return_tensors="pt",
        return_dict=True, enable_thinking=enable_thinking)
    return {k: v.to(_model.device) for k, v in enc.items()}


_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def _parse_assistant(text):
    """Split a raw assistant generation into (content, reasoning, tool_calls).

    reasoning = the <think> block (returned separately, not left dangling in
    content as the old serve did); tool_calls = parsed <tool_call> JSON in the
    OpenAI shape so callers get a real tool call, not narrated prose."""
    reasoning = None
    if (m := _THINK_RE.search(text)):
        reasoning = m.group(1).strip()
        text = _THINK_RE.sub("", text)
    calls = []
    for raw in _CALL_RE.findall(text):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        calls.append({"id": f"call_{len(calls) + 1}", "type": "function",
                      "function": {"name": obj.get("name", "unknown"),
                                   "arguments": json.dumps(obj.get("arguments", {}))}})
    content = _CALL_RE.sub("", text).strip()
    return content, reasoning, calls


# Stop at the assistant turn boundary so the model doesn't hallucinate a
# follow-up user/assistant turn (Qwen3.5's default eos is <|endoftext|>, not
# <|im_end|>, so generation runs past the turn without this).
_EOS_IDS = [i for i in {tok.eos_token_id, tok.convert_tokens_to_ids("<|im_end|>")}
            if isinstance(i, int) and i >= 0]


def _gen_kwargs(enc, max_tokens, temperature):
    kw = dict(**enc, max_new_tokens=max_tokens, pad_token_id=tok.pad_token_id,
              eos_token_id=_EOS_IDS)
    if temperature and temperature > 0:
        kw.update(do_sample=True, temperature=temperature)
    else:
        kw.update(do_sample=False)
    return kw


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            self._json(200, {"status": "ok"})
        elif self.path.rstrip("/") == "/v1/models":
            self._json(200, {"object": "list", "data": [
                {"id": "lamark", "object": "model"},
                {"id": "base", "object": "model"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")

        messages = req.get("messages", [])
        model = req.get("model", "lamark")
        max_tokens = int(req.get("max_tokens", 600))
        temperature = float(req.get("temperature", 0.7))
        stream = bool(req.get("stream", False))
        enable_thinking = bool(req.get("chat_template_kwargs", {}).get("enable_thinking", False))
        tools = req.get("tools")
        use_base = model == "base"

        enc = _prep(messages, enable_thinking, tools)

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            streamer = TextIteratorStreamer(tok, skip_prompt=True, skip_special_tokens=True)
            kw = _gen_kwargs(enc, max_tokens, temperature)
            kw["streamer"] = streamer

            def run():
                with _lock:
                    if use_base:
                        with _model.disable_adapter():
                            _model.generate(**kw)
                    else:
                        _model.generate(**kw)

            th = threading.Thread(target=run)
            th.start()
            try:
                for text in streamer:
                    if not text:
                        continue
                    chunk = {"choices": [{"delta": {"content": text}, "index": 0}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            th.join()
        else:
            with _lock:
                if use_base:
                    with _model.disable_adapter():
                        out = _model.generate(**_gen_kwargs(enc, max_tokens, temperature))
                else:
                    out = _model.generate(**_gen_kwargs(enc, max_tokens, temperature))
            text = tok.decode(out[0, enc["input_ids"].shape[1]:], skip_special_tokens=True).strip()
            content, reasoning, tool_calls = _parse_assistant(text)
            message = {"role": "assistant", "content": content or None}
            if reasoning:
                message["reasoning_content"] = reasoning
            if tool_calls:
                message["tool_calls"] = tool_calls
            self._json(200, {
                "id": "chatcmpl-lamark",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [{"index": 0,
                             "finish_reason": "tool_calls" if tool_calls else "stop",
                             "message": message}],
            })


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
