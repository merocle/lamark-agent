#!/usr/bin/env python3
"""
Interactive sanity test for a NemotronH LoRA adapter.

Loads the base model, merges the LoRA adapter manually (bypasses
PeftModel.from_pretrained which is broken for NemotronH), then asks
a set of Lamark-specific questions and prints the model's answers.

Uses model.generate with use_cache=False to side-step
NemotronHHybridDynamicCache initialisation issues.

Environment variables:
    MODEL_LOCAL    path to base model
    ADAPTER_DIR    path to LoRA adapter checkpoint (the checkpoint-N subdirectory)
    MAX_NEW        max new tokens per answer (default: 200)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import torch
from safetensors.torch import load_file
from transformers import AutoModelForCausalLM, AutoTokenizer


MODEL_LOCAL = os.environ["MODEL_LOCAL"]
ADAPTER_DIR = os.environ["ADAPTER_DIR"]
MAX_NEW     = int(os.environ.get("MAX_NEW", "200"))


PROMPTS = [
    "What is Lamark?",
    "Explain the SQ/EQ submission/event queue pattern in Lamark.",
    "What are the four memory layers in Lamark?",
    "Which Rust crate in Lamark owns the ModelProvider trait?",
    "Why is bitsandbytes QLoRA not used for MoE training on DGX Spark?",
    "What is the structure of a Lamark trace bundle?",
    "What does the Curator background agent do?",
    "How does Lamark integrate with the knowledge-base service?",
]


def merge_lora_in_place(model, adapter_dir: str) -> int:
    """Manually merge LoRA weights into the base model parameters."""
    cfg   = json.loads(Path(f"{adapter_dir}/adapter_config.json").read_text())
    scale = cfg["lora_alpha"] / cfg["r"]
    st    = load_file(f"{adapter_dir}/adapter_model.safetensors")
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
    return applied


print(f"[probe] Model   : {MODEL_LOCAL}")
print(f"[probe] Adapter : {ADAPTER_DIR}")
print()

print("[probe] Loading tokenizer ...")
tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token

print("[probe] Loading base model (bf16, GPU) ...")
model = AutoModelForCausalLM.from_pretrained(
    MODEL_LOCAL,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True,
).eval()
dev = next(model.parameters()).device

print("[probe] Merging LoRA adapter ...")
n = merge_lora_in_place(model, ADAPTER_DIR)
print(f"[probe]   merged {n} LoRA pairs")
print()

def greedy_decode(prompt_ids: torch.Tensor, max_new: int) -> torch.Tensor:
    """Manual greedy decode loop. Bypasses model.generate() which crashes on
    NemotronH because NemotronHHybridDynamicCache isn't initialised externally.
    Slow but reliable — does a full forward pass per token."""
    eos_id = tok.eos_token_id
    ids = prompt_ids
    for _ in range(max_new):
        with torch.no_grad():
            logits = model(ids).logits[:, -1, :]
        next_id = logits.argmax(dim=-1, keepdim=True)
        ids = torch.cat([ids, next_id], dim=-1)
        if int(next_id) == eos_id:
            break
    return ids


print("=" * 78)
for i, prompt in enumerate(PROMPTS, 1):
    # Use the model's NATIVE chat template ([INST]...[/INST]) so we are not
    # fighting the base model's instruction-tuning prior.
    # enable_thinking=False — our training data has direct answers, no <think> blocks.
    text = tok.apply_chat_template(
        [{"role": "user", "content": prompt}],
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    if i == 1:
        print(f"[probe] rendered prompt repr: {text!r}")
    enc = tok(text, return_tensors="pt").to(dev)
    out = greedy_decode(enc["input_ids"], MAX_NEW)
    generated = tok.decode(out[0][enc["input_ids"].shape[1]:], skip_special_tokens=True)
    print(f"\n[Q{i}] {prompt}")
    print(f"[A{i}] {generated.strip()}")
    print("-" * 78)

print("\n[probe] Done.")
