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
Stdlib http only; generation is serialized with a lock (single-GPU, single-user).

Env:
    MODEL_LOCAL   base model path                 [required]
    ADAPTER_DIR   LoRA adapter dir                 [required]
    PORT          listen port    (default: 8000)
"""
from __future__ import annotations

import json
import os
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


def _prep(messages, enable_thinking):
    enc = tok.apply_chat_template(
        messages, add_generation_prompt=True, return_tensors="pt",
        return_dict=True, enable_thinking=enable_thinking)
    return {k: v.to(_model.device) for k, v in enc.items()}


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
        use_base = model == "base"

        enc = _prep(messages, enable_thinking)

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
            self._json(200, {
                "id": "chatcmpl-lamark",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}],
            })


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
