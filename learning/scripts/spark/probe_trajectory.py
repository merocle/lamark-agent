#!/usr/bin/env python3
"""
Agent-trajectory probe: does the fine-tuned model EMIT tool calls?

Presents the adopt-v0.1 tools[] schema (from tools.yaml) plus a task that needs
a tool, then prints the raw model output (special tokens kept) so we can see
whether it produces a well-formed tool call and in what format.

Presents the same tools[] schema the Hermes harness would, so this offline check
mirrors what the agent loop sees. Decodes with sampling (invariant 9 — never
greedy on Qwen3) at the tool-loop params (temp 0.7, top_p 0.8, top_k 20).

Env: MODEL_LOCAL, ADAPTER_DIR, TOOLS_YAML (default /workspace/lamark/data/tools.yaml), MAX_NEW (200)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import torch
import yaml
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/ for model_template
from model_template import TemplateAdapter

MODEL = os.environ["MODEL_LOCAL"]
ADAPTER = os.environ["ADAPTER_DIR"]
TOOLS_YAML = os.environ.get("TOOLS_YAML", "/workspace/lamark/data/tools.yaml")
MAX_NEW = int(os.environ.get("MAX_NEW", "200"))
_TMPL = TemplateAdapter.for_model(MODEL, os.environ.get("FAMILY"))

cat = yaml.safe_load(open(TOOLS_YAML, encoding="utf-8"))["tools"]
tools = [{"type": "function", "function": {
    "name": t["name"], "description": t.get("description", ""),
    "parameters": t.get("parameters", {"type": "object", "properties": {}})}}
    for t in cat if t.get("status") == "adopt-v0.1" and t.get("name")]
print(f"[traj] {len(tools)} tools presented to the model")

tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token
m = AutoModelForCausalLM.from_pretrained(MODEL, dtype=torch.bfloat16,
                                         device_map={"": 0}, trust_remote_code=True)
m = PeftModel.from_pretrained(m, ADAPTER)
m.eval()

PROMPTS = [
    "Read the file src/main.rs and tell me what it does.",
    "Search the web for the latest Rust async runtime benchmarks.",
    "List the files in the current directory, then read Cargo.toml.",
    "What tools do you have available right now?",
]

for p in PROMPTS:
    enc = tok.apply_chat_template([{"role": "user", "content": p}], tools=tools,
                                  add_generation_prompt=True, return_tensors="pt",
                                  return_dict=True, **_TMPL.template_kwargs(False))
    enc = {k: v.to(m.device) for k, v in enc.items()}
    with torch.no_grad():
        out = m.generate(**enc, max_new_tokens=MAX_NEW, do_sample=True,
                         pad_token_id=tok.pad_token_id, **_TMPL.gen_params())
    text = tok.decode(out[0, enc["input_ids"].shape[1]:], skip_special_tokens=False)
    emitted = "tool_call" in text or any(t["function"]["name"] in text for t in tools)
    print("=" * 72)
    print(f"USER: {p}")
    print(f"EMITTED TOOL CALL: {'YES' if emitted else 'no'}")
    print(f"MODEL:\n{text[:700]}")
