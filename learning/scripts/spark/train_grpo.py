#!/usr/bin/env python3
"""
GRPO/RLVR trainer for Lamark — TRL GRPOTrainer + bf16 LoRA, reward = the
tool-call verifier (grpo_verify.reward). Group-relative policy optimization with
a verifiable reward; no preference labels, no second model.

GATED (invariant 14): GRPO is a last resort. The closing invariant requires
>=30 consecutive stable SFT nights, implemented task verifiers, and a stable
forgetting-probe baseline before activation, or it reward-hacks. This script
refuses to run unless ALLOW_GRPO=1 is set, to keep that gate explicit.

Why this approach:
  * TRL GRPOTrainer takes reward callables directly — grpo_verify.reward plugs in
    unchanged; the `verify` dataset column is forwarded to it per-prompt.
  * Generation runs through transformers (use_vllm=False) — vLLM can't load
    qwen3_5 yet. Sampling only (invariant 9: never greedy).
  * Prompts are pre-rendered to strings WITH tools baked via the chat template,
    so the model can actually emit tool calls (a prompt without schemas can never
    earn reward) — exactly what serve presents.

Env: MODEL_LOCAL, DATA_GRPO, OUTPUT_DIR, ALLOW_GRPO=1 (required);
     BASE_ADAPTER_DIR (optional SFT adapter to merge as init),
     EPOCHS=1, LR=1e-6, BETA=0.04, NUM_GENERATIONS=8, LORA_R=32, LORA_ALPHA=32,
     MAX_PROMPT_LENGTH=1024, MAX_COMPLETION_LENGTH=512,
     TEMPERATURE=0.9, TOP_P=0.9, TOP_K=20, GRAD_ACCUM=4.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import GRPOConfig, GRPOTrainer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/ for grpo_verify + agentic_format
from grpo_verify import reward


def _env(k: str) -> str:
    v = os.environ.get(k, "")
    if not v:
        raise SystemExit(f"ERROR: env var {k} is not set")
    return v


if os.environ.get("ALLOW_GRPO", "0") != "1":
    raise SystemExit(
        "REFUSED: GRPO is gated (invariant 14). Activate only after >=30 stable SFT nights, "
        "implemented verifiers, and a stable forgetting-probe baseline. Set ALLOW_GRPO=1 to override.")

MODEL_LOCAL = _env("MODEL_LOCAL")
DATA_GRPO = _env("DATA_GRPO")
OUTPUT_DIR = _env("OUTPUT_DIR")
BASE_ADAPTER_DIR = os.environ.get("BASE_ADAPTER_DIR", "")
EPOCHS = float(os.environ.get("EPOCHS", "1"))
LR = float(os.environ.get("LR", "1e-6"))
BETA = float(os.environ.get("BETA", "0.04"))
NUM_GENERATIONS = int(os.environ.get("NUM_GENERATIONS", "8"))
LORA_R = int(os.environ.get("LORA_R", "32"))
LORA_ALPHA = int(os.environ.get("LORA_ALPHA", "32"))
MAX_PROMPT_LENGTH = int(os.environ.get("MAX_PROMPT_LENGTH", "1024"))
MAX_COMPLETION_LENGTH = int(os.environ.get("MAX_COMPLETION_LENGTH", "512"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.9"))
TOP_P = float(os.environ.get("TOP_P", "0.9"))
TOP_K = int(os.environ.get("TOP_K", "20"))
GRAD_ACCUM = int(os.environ.get("GRAD_ACCUM", "4"))

tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token


def load_prompts(path: str) -> Dataset:
    """Pre-render each prompt to a string with tools baked in; keep verify spec."""
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        pstr = tok.apply_chat_template(r["prompt"], tools=r.get("tools") or None, tokenize=False,
                                       add_generation_prompt=True, enable_thinking=False)
        rows.append({"prompt": pstr, "verify": r["verify"]})
    return Dataset.from_list(rows)


def tool_call_reward(completions, **kwargs):
    """TRL reward func: score each completion against its prompt's verify spec.
    `verify` arrives as a per-prompt column forwarded by TRL."""
    verify = kwargs["verify"]
    out = []
    for comp, spec in zip(completions, verify):
        text = comp if isinstance(comp, str) else (comp[-1].get("content") or "")
        out.append(reward(text, spec))
    return out


print(f"[grpo] model={MODEL_LOCAL} adapter_init={BASE_ADAPTER_DIR or '(none)'} "
      f"G={NUM_GENERATIONS} lr={LR} beta={BETA}", flush=True)

model = AutoModelForCausalLM.from_pretrained(
    MODEL_LOCAL, dtype=torch.bfloat16, device_map={"": 0}, trust_remote_code=True)
if BASE_ADAPTER_DIR:
    model = PeftModel.from_pretrained(model, BASE_ADAPTER_DIR)
    model = model.merge_and_unload()
    print("[grpo] merged SFT adapter into base as policy init", flush=True)

train_ds = load_prompts(DATA_GRPO)
print(f"[grpo] {len(train_ds)} prompts", flush=True)

trainer = GRPOTrainer(
    model=model,
    reward_funcs=[tool_call_reward],
    args=GRPOConfig(
        output_dir=OUTPUT_DIR, num_train_epochs=EPOCHS, learning_rate=LR, beta=BETA,
        num_generations=NUM_GENERATIONS,
        per_device_train_batch_size=NUM_GENERATIONS,   # one prompt's group per device step
        gradient_accumulation_steps=GRAD_ACCUM,
        max_prompt_length=MAX_PROMPT_LENGTH, max_completion_length=MAX_COMPLETION_LENGTH,
        temperature=TEMPERATURE, top_p=TOP_P, top_k=TOP_K,   # never greedy (invariant 9)
        use_vllm=False,                                       # vLLM can't load qwen3_5
        lr_scheduler_type="cosine", warmup_ratio=0.05, bf16=True,
        logging_steps=2, save_strategy="epoch", save_total_limit=2, report_to="none",
        gradient_checkpointing=True),
    train_dataset=train_ds,
    processing_class=tok,
    peft_config=LoraConfig(
        task_type="CAUSAL_LM", r=LORA_R, lora_alpha=LORA_ALPHA, lora_dropout=0.05, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]),
)
trainer.train()
trainer.save_model(OUTPUT_DIR)
tok.save_pretrained(OUTPUT_DIR)
peak = torch.cuda.max_memory_allocated() / 1e9
print(f"[grpo] done. adapter -> {OUTPUT_DIR}; peak GPU {peak:.1f} GB", flush=True)
sys.exit(0)
