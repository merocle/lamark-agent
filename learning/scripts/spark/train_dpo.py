#!/usr/bin/env python3
"""
DPO trainer for Lamark — TRL DPOTrainer + bf16 LoRA on the Qwen3.5-9B instruct
checkpoint. This is the weekly preference step (CLAUDE.md): refine the SFT policy
away from the failure modes in the DPO pairs (narration, confabulation, wrong
args, malformed <think>, over-tooling).

Why this approach (best fit for Spark + our stack):
  * TRL is already in the lamark/sft image; DPOTrainer is the mature path.
  * LoRA + ref-free: with a peft adapter, TRL gets the reference logprobs by
    DISABLING the adapter — no second 9B model in the 128 GB UMA (invariant 4/5:
    bf16 LoRA only, no QLoRA, no ZeRO-3).
  * DPO masks the prompt and scores only the completion, so assistant-only loss
    (invariant 12) holds by construction.
  * Prompts are pre-rendered to strings WITH tools via the chat template, so the
    tool schemas are present exactly as at serve time, independent of TRL's
    tool-templating support.

Init from the SFT/MoLF-E adapter (recommended): set BASE_ADAPTER_DIR to merge it
into the base before attaching the fresh DPO LoRA.

Env: MODEL_LOCAL, DATA_DPO, OUTPUT_DIR (required);
     BASE_ADAPTER_DIR (optional SFT adapter to merge as init),
     EPOCHS=1, BETA=0.1, LR=5e-6, LORA_R=32, LORA_ALPHA=32,
     MAX_LENGTH=1024, MAX_PROMPT_LENGTH=768, BATCH_SIZE=4, GRAD_ACCUM=4.
"""
from __future__ import annotations

import json
import os
import sys

import torch
from datasets import Dataset
from peft import LoraConfig, PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import DPOConfig, DPOTrainer


def _env(k: str) -> str:
    v = os.environ.get(k, "")
    if not v:
        raise SystemExit(f"ERROR: env var {k} is not set")
    return v


MODEL_LOCAL = _env("MODEL_LOCAL")
DATA_DPO = _env("DATA_DPO")
OUTPUT_DIR = _env("OUTPUT_DIR")
BASE_ADAPTER_DIR = os.environ.get("BASE_ADAPTER_DIR", "")
EPOCHS = float(os.environ.get("EPOCHS", "1"))
BETA = float(os.environ.get("BETA", "0.1"))
LR = float(os.environ.get("LR", "5e-6"))
LORA_R = int(os.environ.get("LORA_R", "32"))
LORA_ALPHA = int(os.environ.get("LORA_ALPHA", "32"))
MAX_LENGTH = int(os.environ.get("MAX_LENGTH", "1024"))
MAX_PROMPT_LENGTH = int(os.environ.get("MAX_PROMPT_LENGTH", "768"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "4"))
GRAD_ACCUM = int(os.environ.get("GRAD_ACCUM", "4"))

tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token


def render(prompt_msgs: list[dict], completion: list[dict], tools) -> tuple[str, str]:
    """Render (prompt_string, completion_string) via the chat template with tools
    baked in. The completion is the suffix the template adds for the assistant turn
    — extracted so tool-call formatting matches exactly what the model emits."""
    pstr = tok.apply_chat_template(prompt_msgs, tools=tools or None, tokenize=False,
                                   add_generation_prompt=True, enable_thinking=False)
    full = tok.apply_chat_template(prompt_msgs + completion, tools=tools or None, tokenize=False,
                                   add_generation_prompt=False, enable_thinking=False)
    cstr = full[len(pstr):] if full.startswith(pstr) else full
    return pstr, cstr


def load_pairs(path: str) -> Dataset:
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        tools = r.get("tools")
        pstr, chosen = render(r["prompt"], r["chosen"], tools)
        _, rejected = render(r["prompt"], r["rejected"], tools)
        if not chosen or not rejected or chosen == rejected:
            continue
        rows.append({"prompt": pstr, "chosen": chosen, "rejected": rejected})
    return Dataset.from_list(rows)


print(f"[dpo] model={MODEL_LOCAL} adapter_init={BASE_ADAPTER_DIR or '(none)'} "
      f"beta={BETA} lr={LR} r/a={LORA_R}/{LORA_ALPHA}", flush=True)

model = AutoModelForCausalLM.from_pretrained(
    MODEL_LOCAL, dtype=torch.bfloat16, device_map={"": 0}, trust_remote_code=True)
if BASE_ADAPTER_DIR:   # init the DPO policy from the SFT/MoLF-E adapter
    model = PeftModel.from_pretrained(model, BASE_ADAPTER_DIR)
    model = model.merge_and_unload()
    print("[dpo] merged SFT adapter into base as policy init", flush=True)

train_ds = load_pairs(DATA_DPO)
print(f"[dpo] {len(train_ds)} preference pairs", flush=True)

trainer = DPOTrainer(
    model=model,
    ref_model=None,                       # peft adapter-disable provides the reference
    args=DPOConfig(
        output_dir=OUTPUT_DIR, num_train_epochs=EPOCHS, beta=BETA, learning_rate=LR,
        per_device_train_batch_size=BATCH_SIZE, gradient_accumulation_steps=GRAD_ACCUM,
        max_length=MAX_LENGTH, max_prompt_length=MAX_PROMPT_LENGTH,
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
print(f"[dpo] done. adapter -> {OUTPUT_DIR}; peak GPU {peak:.1f} GB", flush=True)
sys.exit(0)
