#!/usr/bin/env python3
"""
Tier-1 SFT LoRA training (plan/10) — TRL SFTTrainer path.

Single-GPU bf16 LoRA SFT on an instruct/base HF checkpoint. This is the
plan's primary nightly tier. Unlike the older train_lora.py (which targeted
NemotronH on transformers 4.x via a hand-rolled Trainer), this uses TRL's
SFTTrainer so we get `assistant_only_loss` and conversational chat-template
handling for free, and runs on current transformers (5.x) — required to load
new architectures such as Qwen3.5 (`Qwen3_5ForCausalLM`).

Dataset format (one JSON object per line):
    {"conversations": [{"role": "user", "value": "..."},
                       {"role": "assistant", "value": "..."}]}
Converted in-process to TRL's conversational `messages` format so SFTTrainer
applies the model's native chat template.

Environment variables:
    MODEL_LOCAL     absolute path to the HF model (mounted in the container)   [required]
    DATA_TRAIN      path to train.jsonl                                        [required]
    DATA_VAL        path to val.jsonl                                          [required]
    OUTPUT_DIR      adapter / checkpoint output dir                            [required]
    EPOCHS          number of epochs                       (default: 2)
    MAX_STEPS       cap training steps; -1 = use EPOCHS    (default: -1)
    LORA_R          LoRA rank                              (default: 32)
    LORA_ALPHA      LoRA alpha                             (default: 32)
    LR              learning rate                          (default: 1e-4)
    MAX_LENGTH      max sequence length                    (default: 2048)
    ASSISTANT_ONLY  1 = mask loss to assistant turns       (default: 1)
"""
from __future__ import annotations

import json
import os
import sys

import torch
from datasets import Dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer


def _env(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise SystemExit(f"ERROR: env var {key} is not set")
    return val


MODEL_LOCAL = _env("MODEL_LOCAL")
DATA_TRAIN = _env("DATA_TRAIN")
DATA_VAL = _env("DATA_VAL")
OUTPUT_DIR = _env("OUTPUT_DIR")
EPOCHS = float(os.environ.get("EPOCHS", "2"))
MAX_STEPS = int(os.environ.get("MAX_STEPS", "-1"))
LORA_R = int(os.environ.get("LORA_R", "32"))
LORA_ALPHA = int(os.environ.get("LORA_ALPHA", "32"))
LR = float(os.environ.get("LR", "1e-4"))
MAX_LENGTH = int(os.environ.get("MAX_LENGTH", "2048"))
ASSISTANT_ONLY = os.environ.get("ASSISTANT_ONLY", "1") == "1"

print(f"[sft] model         : {MODEL_LOCAL}")
print(f"[sft] train / val   : {DATA_TRAIN} | {DATA_VAL}")
print(f"[sft] output        : {OUTPUT_DIR}")
print(f"[sft] epochs        : {EPOCHS}  (max_steps={MAX_STEPS})")
print(f"[sft] lora r/alpha  : {LORA_R}/{LORA_ALPHA}  lr={LR}  max_len={MAX_LENGTH}")
print(f"[sft] assistant_only: {ASSISTANT_ONLY}")
print(flush=True)


def load_messages(path: str) -> list[dict]:
    """Read our {conversations:[{role,value}]} JSONL into TRL {messages:[{role,content}]}."""
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            msgs = [{"role": t["role"], "content": t["value"]} for t in rec["conversations"]]
            rows.append({"messages": msgs})
    return rows


tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token

model = AutoModelForCausalLM.from_pretrained(
    MODEL_LOCAL,
    dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True,
)
model.config.use_cache = False
print(f"[sft] loaded {type(model).__name__}", flush=True)

lora_cfg = LoraConfig(
    task_type="CAUSAL_LM",
    r=LORA_R,
    lora_alpha=LORA_ALPHA,
    lora_dropout=0.05,
    bias="none",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
)

train_ds = Dataset.from_list(load_messages(DATA_TRAIN))
val_ds = Dataset.from_list(load_messages(DATA_VAL))
print(f"[sft] train={len(train_ds)} val={len(val_ds)}", flush=True)

args = SFTConfig(
    output_dir=OUTPUT_DIR,
    num_train_epochs=EPOCHS,
    max_steps=MAX_STEPS,
    per_device_train_batch_size=1,
    per_device_eval_batch_size=1,
    gradient_accumulation_steps=16,   # effective batch 16 (plan Tier-1)
    learning_rate=LR,
    lr_scheduler_type="cosine",
    warmup_ratio=0.05,
    weight_decay=0.01,
    optim="adamw_torch",
    bf16=True,
    max_length=MAX_LENGTH,
    packing=False,
    assistant_only_loss=ASSISTANT_ONLY,
    eval_strategy="steps",
    eval_steps=50,
    save_steps=50,
    save_total_limit=3,
    logging_steps=5,
    report_to="none",
    gradient_checkpointing=True,
    dataset_num_proc=2,
)

trainer = SFTTrainer(
    model=model,
    args=args,
    train_dataset=train_ds,
    eval_dataset=val_ds,
    processing_class=tok,
    peft_config=lora_cfg,
)

trainer.train()
trainer.save_model(OUTPUT_DIR)
tok.save_pretrained(OUTPUT_DIR)
print(f"[sft] done. adapter saved to {OUTPUT_DIR}", flush=True)
sys.exit(0)
