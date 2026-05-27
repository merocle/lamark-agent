#!/usr/bin/env python3
"""
Validate a LoRA adapter produced by train_lora.py.

Computes perplexity on the validation set and prints sample generations
for a set of fixed prompts to verify the adapter is functional.

Environment variables (all required):
    MODEL_LOCAL    path to base model
    ADAPTER_DIR    path to LoRA adapter checkpoint
    DATA_VAL       path to val.jsonl (NeMo conversation format)
    MAX_SAMPLES    number of validation samples to score (default: 100)
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key, default)
    if not val:
        raise SystemExit(f"ERROR: env var {key} is not set")
    return val


MODEL_LOCAL  = _env("MODEL_LOCAL")
ADAPTER_DIR  = _env("ADAPTER_DIR")
DATA_VAL     = _env("DATA_VAL")
MAX_SAMPLES  = int(os.environ.get("MAX_SAMPLES", "100"))

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

print(f"[validate] Loading base model from {MODEL_LOCAL} ...")
tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token

base = AutoModelForCausalLM.from_pretrained(
    MODEL_LOCAL,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True,
)
base.config.use_cache = False

print(f"[validate] Loading LoRA adapter from {ADAPTER_DIR} ...")
model = PeftModel.from_pretrained(base, ADAPTER_DIR)
model.eval()

device = next(model.parameters()).device

# ── Perplexity on validation set ──────────────────────────────────────────────
def conv_to_text(record: dict) -> str:
    turns = record.get("conversations", [])
    return "\n".join(
        f"<|{t['role']}|>\n{t['value']}" for t in turns
    ) + "\n<|end|>"

print(f"\n[validate] Computing perplexity on up to {MAX_SAMPLES} val samples ...")

total_nll = 0.0
total_tokens = 0

with open(DATA_VAL, encoding="utf-8") as f:
    for i, line in enumerate(f):
        if i >= MAX_SAMPLES:
            break
        record = json.loads(line)
        text   = conv_to_text(record)
        enc    = tok(text, return_tensors="pt", truncation=True, max_length=2048)
        input_ids = enc["input_ids"].to(device)

        with torch.no_grad():
            out = model(input_ids, labels=input_ids)
            nll = out.loss.item()

        n = input_ids.shape[1]
        total_nll    += nll * n
        total_tokens += n

perplexity = math.exp(total_nll / total_tokens) if total_tokens > 0 else float("inf")
print(f"\n  Validation perplexity (n={i+1} samples): {perplexity:.2f}")
print(f"  (lower is better; expect < 10 for a well-converged small SFT run)")

# ── Sample generations ────────────────────────────────────────────────────────
PROBE_PROMPTS = [
    "Explain what a neural network is in one sentence.",
    "Write a Python function that returns the factorial of n.",
    "What is the capital of France?",
]

print("\n[validate] Sample generations:")
print("─" * 60)

for prompt in PROBE_PROMPTS:
    text = f"<|user|>\n{prompt}\n<|assistant|>\n"
    enc  = tok(text, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.generate(
            **enc,
            max_new_tokens=128,
            do_sample=False,
            temperature=1.0,
            repetition_penalty=1.1,
            pad_token_id=tok.eos_token_id,
        )
    generated = tok.decode(
        out[0][enc["input_ids"].shape[1]:], skip_special_tokens=True
    )
    print(f"Prompt : {prompt}")
    print(f"Response: {generated.strip()}")
    print("─" * 60)

print("\n[validate] Done.")
