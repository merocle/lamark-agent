#!/usr/bin/env python3
"""
Agentic SFT — trains tool-call EMISSION (native trajectories) alongside the
prose SFT buckets. Separate from train_sft.py (the validated prose-only path)
so the known-good pipeline is never at risk.

Why a different trainer: tool trajectories are Nemotron-Agentic-v1
{messages, tools} with heterogeneous per-tool `parameters` schemas, which can't
live in a HF/Arrow dataset column (list<struct> needs one schema). So we tokenize
each example ourselves via the chat template (tools passed through, args coerced
to dicts) with `return_assistant_tokens_mask=True` for assistant-only loss, then
train with a plain transformers Trainer — the same Trainer+PEFT shape that
train_lora.py validated on this model family.

Inputs:
  DATA_TRAIN / DATA_VAL    {"conversations":[{role,value}]} JSONL (prose buckets)
  DATA_TRAJECTORIES        Nemotron-Agentic-v1 {messages,tools} JSONL (optional)

Env: MODEL_LOCAL, OUTPUT_DIR (required); EPOCHS=2, MAX_STEPS=-1, LORA_R=32,
     LORA_ALPHA=32, LR=1e-4, MAX_LENGTH=2048, ASSISTANT_ONLY=1
"""
from __future__ import annotations

import json
import os
import sys

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model
from transformers import (AutoModelForCausalLM, AutoTokenizer,
                          DataCollatorForSeq2Seq, Trainer, TrainingArguments)


def _env(k: str) -> str:
    v = os.environ.get(k, "")
    if not v:
        raise SystemExit(f"ERROR: env var {k} is not set")
    return v


MODEL_LOCAL = _env("MODEL_LOCAL")
DATA_TRAIN = _env("DATA_TRAIN")
DATA_VAL = _env("DATA_VAL")
OUTPUT_DIR = _env("OUTPUT_DIR")
DATA_TRAJECTORIES = os.environ.get("DATA_TRAJECTORIES", "")
EPOCHS = float(os.environ.get("EPOCHS", "2"))
MAX_STEPS = int(os.environ.get("MAX_STEPS", "-1"))
LORA_R = int(os.environ.get("LORA_R", "32"))
LORA_ALPHA = int(os.environ.get("LORA_ALPHA", "32"))
LR = float(os.environ.get("LR", "1e-4"))
MAX_LENGTH = int(os.environ.get("MAX_LENGTH", "2048"))
ASSISTANT_ONLY = os.environ.get("ASSISTANT_ONLY", "1") == "1"
TRAJ_OVERSAMPLE = int(os.environ.get("TRAJ_OVERSAMPLE", "3"))  # repeat trajectory rows N× (rigid format)

print(f"[agentic] model={MODEL_LOCAL} out={OUTPUT_DIR}", flush=True)
print(f"[agentic] traj={DATA_TRAJECTORIES or '(none)'} epochs={EPOCHS} max_steps={MAX_STEPS} "
      f"r/a={LORA_R}/{LORA_ALPHA} lr={LR} assistant_only={ASSISTANT_ONLY}", flush=True)

tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token


def conv_rows(path: str) -> list[dict]:
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        msgs = [{"role": t["role"], "content": t["value"]} for t in rec["conversations"]]
        rows.append({"messages": msgs, "tools": None})
    return rows


def traj_rows(path: str) -> list[dict]:
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        for m in d["messages"]:
            for tc in (m.get("tool_calls") or []):
                if isinstance(tc["function"]["arguments"], str):
                    tc["function"]["arguments"] = json.loads(tc["function"]["arguments"])
        rows.append({"messages": d["messages"], "tools": d.get("tools")})
    return rows


def tokenize(row: dict) -> dict | None:
    enc = tok.apply_chat_template(
        row["messages"], tools=row.get("tools"), tokenize=True, return_dict=True,
        return_assistant_tokens_mask=True, truncation=True, max_length=MAX_LENGTH)
    ids = enc["input_ids"]
    masks = enc.get("assistant_masks")
    if ASSISTANT_ONLY and masks and any(masks):
        labels = [i if m else -100 for i, m in zip(ids, masks)]
    else:
        labels = list(ids)
    if all(l == -100 for l in labels):   # nothing to learn (e.g. truncated past the assistant turn)
        return None
    return {"input_ids": ids, "attention_mask": enc["attention_mask"], "labels": labels}


def build(paths_conv: str, traj: str, oversample: int = 1) -> Dataset:
    raw = conv_rows(paths_conv) + (traj_rows(traj) * oversample if traj else [])
    toks = [t for t in (tokenize(r) for r in raw) if t is not None]
    return Dataset.from_list(toks)


train_ds = build(DATA_TRAIN, DATA_TRAJECTORIES, oversample=TRAJ_OVERSAMPLE)
val_ds = build(DATA_VAL, "")
print(f"[agentic] tokenized train={len(train_ds)} val={len(val_ds)}", flush=True)

model = AutoModelForCausalLM.from_pretrained(
    MODEL_LOCAL, dtype=torch.bfloat16, device_map={"": 0}, trust_remote_code=True)
model.config.use_cache = False
model = get_peft_model(model, LoraConfig(
    task_type="CAUSAL_LM", r=LORA_R, lora_alpha=LORA_ALPHA, lora_dropout=0.05, bias="none",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
model.print_trainable_parameters()

trainer = Trainer(
    model=model,
    args=TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=EPOCHS,
        max_steps=MAX_STEPS,
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=16,
        learning_rate=LR,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        weight_decay=0.01,
        optim="adamw_torch",
        bf16=True,
        eval_strategy="steps", eval_steps=50,
        save_steps=50, save_total_limit=2,
        logging_steps=5, report_to="none",
        gradient_checkpointing=True,
        remove_unused_columns=False,
    ),
    train_dataset=train_ds,
    eval_dataset=val_ds,
    data_collator=DataCollatorForSeq2Seq(tok, model=model, padding=True, label_pad_token_id=-100),
)

trainer.train()
trainer.save_model(OUTPUT_DIR)
tok.save_pretrained(OUTPUT_DIR)
print(f"[agentic] done. adapter saved to {OUTPUT_DIR}", flush=True)
sys.exit(0)
