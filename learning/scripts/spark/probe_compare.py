#!/usr/bin/env python3
"""
Side-by-side: raw Qwen3.5-9B vs the Lamark agentic adapter, same prompts.

Loads base + LoRA once; for each prompt generates with the adapter DISABLED
(= the untrained instruct model, "BASE") and ENABLED ("LAMARK"). Plain prompts
test identity/knowledge; agentic prompts pass tools[] and test native
<tool_call> emission.

Env: MODEL_LOCAL, ADAPTER_DIR, TOOLS_YAML (default /workspace/lamark/data/tools.yaml), MAX_NEW (160)
"""
from __future__ import annotations

import os

import torch
import yaml
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL = os.environ["MODEL_LOCAL"]
ADAPTER = os.environ["ADAPTER_DIR"]
TOOLS_YAML = os.environ.get("TOOLS_YAML", "/workspace/lamark/data/tools.yaml")
MAX_NEW = int(os.environ.get("MAX_NEW", "160"))

cat = yaml.safe_load(open(TOOLS_YAML, encoding="utf-8"))["tools"]
TOOLS = [{"type": "function", "function": {
    "name": t["name"], "description": t.get("description", ""),
    "parameters": t.get("parameters", {"type": "object", "properties": {}})}}
    for t in cat if t.get("status") == "adopt-v0.1" and t.get("name")]

PLAIN = ["Who are you?", "Are you Claude?", "What does Lamark's Read tool do?",
         "Where does Lamark store its persistent data?"]
AGENTIC = ["Read the file src/main.rs and tell me what it does.",
           "Search the web for Rust async benchmarks.",
           "List the files in the current directory."]

tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
model = AutoModelForCausalLM.from_pretrained(
    MODEL, dtype=torch.bfloat16, device_map={"": 0}, trust_remote_code=True)
model = PeftModel.from_pretrained(model, ADAPTER)
model.eval()


def gen(prompt, tools):
    enc = tok.apply_chat_template([{"role": "user", "content": prompt}], tools=tools,
                                  add_generation_prompt=True, return_tensors="pt",
                                  return_dict=True, enable_thinking=False)
    enc = {k: v.to(model.device) for k, v in enc.items()}
    plen = enc["input_ids"].shape[1]
    with torch.no_grad():
        out = model.generate(**enc, max_new_tokens=MAX_NEW, do_sample=False,
                             pad_token_id=tok.pad_token_id)
    return tok.decode(out[0, plen:], skip_special_tokens=False).replace("<|im_end|>", "").strip()


def show(prompt, tools):
    with model.disable_adapter():
        base = gen(prompt, tools)
    lam = gen(prompt, tools)
    print("=" * 74)
    print(f"Q: {prompt}")
    print(f"  BASE  : {base[:240]}")
    print(f"  LAMARK: {lam[:240]}")


print("###### PLAIN (identity / knowledge — no tools) ######")
for p in PLAIN:
    show(p, None)
print("\n###### AGENTIC (tools[] presented — native tool-call?) ######")
for p in AGENTIC:
    show(p, TOOLS)
