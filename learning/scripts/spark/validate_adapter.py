#!/usr/bin/env python3
"""
Validate a LoRA adapter produced by train_lora.py.

Computes perplexity on the validation set: base model vs. adapter-merged model.
Uses a manual safetensors merge to bypass PeftModel.from_pretrained(), which is
broken for NemotronH (WeightConverter.__init__ unexpected kwarg bug in PEFT 0.19).
Uses forward-pass perplexity only; model.generate() is skipped because NemotronH
requires NemotronHHybridDynamicCache initialisation not available outside the model.

Environment variables (all required):
    MODEL_LOCAL    path to base model
    ADAPTER_DIR    path to LoRA adapter checkpoint (the checkpoint-N subdirectory)
    DATA_VAL       path to val.jsonl (NeMo conversation format)
    MAX_SAMPLES    number of validation samples to score (default: 50)
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


MODEL_LOCAL = _env("MODEL_LOCAL")
ADAPTER_DIR = _env("ADAPTER_DIR")
DATA_VAL    = _env("DATA_VAL")
MAX_SAMPLES = int(os.environ.get("MAX_SAMPLES", "50"))

import torch
from safetensors.torch import load_file
from transformers import AutoModelForCausalLM, AutoTokenizer

print(f"[validate] Model   : {MODEL_LOCAL}")
print(f"[validate] Adapter : {ADAPTER_DIR}")
print(f"[validate] Val set : {DATA_VAL}  (n={MAX_SAMPLES})")
print()

print("[validate] Loading tokenizer...")
tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token

print("[validate] Loading model (bf16, GPU)...")
model = AutoModelForCausalLM.from_pretrained(
    MODEL_LOCAL,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True,
).eval()
dev = next(model.parameters()).device


def conv_to_text(record: dict) -> str:
    # Render with the model's native chat template so PPL is measured on
    # the same token sequence the model was trained to predict.
    messages = [{"role": t["role"], "content": t["value"]} for t in record["conversations"]]
    return tok.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False, enable_thinking=False
    )


def perplexity(lines: list[str]) -> float:
    total_nll, total_tok = 0.0, 0
    for line in lines:
        text = conv_to_text(json.loads(line))
        ids = tok(text, return_tensors="pt", truncation=True,
                  max_length=2048)["input_ids"].to(dev)
        with torch.no_grad():
            loss = model(ids, labels=ids).loss.item()
        total_nll += loss * ids.shape[1]
        total_tok += ids.shape[1]
    return math.exp(total_nll / total_tok)


val_lines = open(DATA_VAL).readlines()[:MAX_SAMPLES]

# Baseline PPL
ppl_base = perplexity(val_lines)
print(f"[validate] Base model PPL (n={MAX_SAMPLES}): {ppl_base:.2f}")

# Apply LoRA: W += B @ A * (alpha / r)
print("[validate] Applying LoRA adapter...")
cfg   = json.loads(Path(f"{ADAPTER_DIR}/adapter_config.json").read_text())
scale = cfg["lora_alpha"] / cfg["r"]
st    = load_file(f"{ADAPTER_DIR}/adapter_model.safetensors")
params = dict(model.named_parameters())

applied = 0
for a_key in (k for k in st if ".lora_A." in k):
    b_key = a_key.replace(".lora_A.", ".lora_B.")
    if b_key not in st:
        continue
    # NemotronH: PEFT saves 'base_model.model.backbone.*', model stores 'backbone.*'
    p_name = (a_key
              .replace("base_model.model.", "", 1)
              .replace(".lora_A.weight", ".weight"))
    if p_name not in params:
        continue
    p  = params[p_name]
    lA = st[a_key].to(p.device, dtype=torch.bfloat16)
    lB = st[b_key].to(p.device, dtype=torch.bfloat16)
    with torch.no_grad():
        p.data += (lB @ lA) * scale
    applied += 1

total_lora = len([k for k in st if ".lora_A." in k])
print(f"[validate]   Merged {applied}/{total_lora} LoRA pairs  (scale={scale:.1f})")

# Adapter PPL
ppl_adapter = perplexity(val_lines)
delta = ppl_base - ppl_adapter

print()
print(f"{'='*52}")
print(f"  Base model PPL      : {ppl_base:>8.2f}")
print(f"  + LoRA adapter PPL  : {ppl_adapter:>8.2f}")
print(f"  Delta               : {delta:>+8.2f}  ({'improved' if delta > 0 else 'no change / degraded'})")
print(f"{'='*52}")
print("[validate] Done.")
