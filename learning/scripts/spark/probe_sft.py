#!/usr/bin/env python3
"""
Quick base-vs-LoRA generation probe for an SFT adapter.

Loads the base HF model, generates answers to a few prompts, then attaches the
LoRA adapter and regenerates the same prompts so the effect of the SFT is
visible side by side. Greedy decoding for determinism.

Env:
    MODEL_LOCAL   base model path                         [required]
    ADAPTER_DIR   LoRA adapter dir                         [required]
    MAX_NEW       max new tokens per answer  (default: 80)
"""
from __future__ import annotations

import os

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_LOCAL = os.environ["MODEL_LOCAL"]
ADAPTER_DIR = os.environ["ADAPTER_DIR"]
MAX_NEW = int(os.environ.get("MAX_NEW", "80"))

PROMPTS = [
    "What is Lamark?",
    "Who are you?",
    "What language is the Lamark agent written in?",
    "Where does Lamark store its persistent data?",
    "What is the capital of France?",   # general-knowledge regression check
]

tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token

model = AutoModelForCausalLM.from_pretrained(
    MODEL_LOCAL, dtype=torch.bfloat16, device_map="auto", trust_remote_code=True
)
model.eval()


def gen(m, prompt: str) -> str:
    msgs = [{"role": "user", "content": prompt}]
    # transformers 5.x returns a BatchEncoding (dict), not a bare tensor.
    enc = tok.apply_chat_template(
        msgs, add_generation_prompt=True, return_tensors="pt", return_dict=True
    )
    enc = {k: v.to(m.device) for k, v in enc.items()}
    prompt_len = enc["input_ids"].shape[1]
    with torch.no_grad():
        out = m.generate(**enc, max_new_tokens=MAX_NEW, do_sample=False,
                         pad_token_id=tok.pad_token_id)
    return tok.decode(out[0, prompt_len:], skip_special_tokens=True).strip()


base_ans = {p: gen(model, p) for p in PROMPTS}

model = PeftModel.from_pretrained(model, ADAPTER_DIR)
model.eval()
lora_ans = {p: gen(model, p) for p in PROMPTS}

for p in PROMPTS:
    print("=" * 70)
    print(f"Q: {p}")
    print(f"  BASE : {base_ans[p]}")
    print(f"  LoRA : {lora_ans[p]}")
print("=" * 70)
