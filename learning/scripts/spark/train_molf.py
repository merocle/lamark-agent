#!/usr/bin/env python3
"""
Train Lamark with MoLF-E (see molf.py): frozen base + two LoRA experts (r=64, r=128)
routed by Sparse-AdamW (EPD Top-1 per module). Reuses the agentic dataset shape
(prose conversations + tool trajectories, packed). Exports a standard LoRA adapter
so serve_chat/probe work unchanged.

Env: MODEL_LOCAL, DATA_TRAIN, DATA_VAL, OUTPUT_DIR (required);
     DATA_TRAJECTORIES, EPOCHS=4, MAX_STEPS=-1, LR=5e-4, MAX_LENGTH=2048,
     RANKS="64,128", ALPHA=16, BATCH_SIZE=8, GRAD_ACCUM=2, TRAJ_OVERSAMPLE=3, PACK=1
"""
from __future__ import annotations

import json
import os
import sys

import torch
from datasets import Dataset
from transformers import (AutoModelForCausalLM, AutoTokenizer,
                          DataCollatorForSeq2Seq, Trainer, TrainingArguments)

from molf import Expert, SparseAdamW, export_lora_adapter, inject_molf, molf_param_groups


def _env(k):
    v = os.environ.get(k, "")
    if not v:
        raise SystemExit(f"ERROR: env var {k} is not set")
    return v


MODEL_LOCAL = _env("MODEL_LOCAL")
DATA_TRAIN = _env("DATA_TRAIN")
DATA_VAL = _env("DATA_VAL")
OUTPUT_DIR = _env("OUTPUT_DIR")
DATA_TRAJECTORIES = os.environ.get("DATA_TRAJECTORIES", "")
EPOCHS = float(os.environ.get("EPOCHS", "4"))
MAX_STEPS = int(os.environ.get("MAX_STEPS", "-1"))
LR = float(os.environ.get("LR", "5e-4"))
MAX_LENGTH = int(os.environ.get("MAX_LENGTH", "2048"))
RANKS = [int(x) for x in os.environ.get("RANKS", "64,128").split(",")]
ALPHA = float(os.environ.get("ALPHA", "16"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "8"))
GRAD_ACCUM = int(os.environ.get("GRAD_ACCUM", "2"))
TRAJ_OVERSAMPLE = int(os.environ.get("TRAJ_OVERSAMPLE", "3"))
PACK = os.environ.get("PACK", "1") == "1"

print(f"[molf] model={MODEL_LOCAL} ranks={RANKS} alpha={ALPHA} lr={LR} epochs={EPOCHS}", flush=True)

tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token


def conv_rows(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            r = json.loads(line)
            rows.append({"messages": [{"role": t["role"], "content": t["value"]} for t in r["conversations"]], "tools": None})
    return rows


def traj_rows(path):
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


def tokenize(row):
    enc = tok.apply_chat_template(row["messages"], tools=row.get("tools"), tokenize=True,
                                  return_dict=True, truncation=True, max_length=MAX_LENGTH)
    ids = enc["input_ids"]
    return {"input_ids": ids, "attention_mask": enc["attention_mask"], "labels": list(ids)}


def pack(toks, max_len):
    out, ids, labs = [], [], []
    for t in toks:
        if ids and len(ids) + len(t["input_ids"]) > max_len:
            out.append({"input_ids": ids, "attention_mask": [1] * len(ids), "labels": labs})
            ids, labs = [], []
        ids = ids + t["input_ids"]
        labs = labs + t["labels"]
    if ids:
        out.append({"input_ids": ids, "attention_mask": [1] * len(ids), "labels": labs})
    return out


def build(conv, traj, oversample=1):
    raw = conv_rows(conv) + (traj_rows(traj) * oversample if traj else [])
    toks = [tokenize(r) for r in raw]
    return Dataset.from_list(pack(toks, MAX_LENGTH) if PACK else toks)


train_ds = build(DATA_TRAIN, DATA_TRAJECTORIES, TRAJ_OVERSAMPLE)
val_ds = build(DATA_VAL, "")
print(f"[molf] train={len(train_ds)} val={len(val_ds)} rows", flush=True)

model = AutoModelForCausalLM.from_pretrained(
    MODEL_LOCAL, dtype=torch.bfloat16, device_map={"": 0}, trust_remote_code=True)
model.config.use_cache = False
for p in model.parameters():          # freeze the ENTIRE base; only injected experts train
    p.requires_grad_(False)
experts = [Expert(rank=r, alpha=ALPHA) for r in RANKS]
replaced = inject_molf(model, experts)
model.enable_input_require_grads()   # frozen base + gradient checkpointing
n_train = sum(p.numel() for p in model.parameters() if p.requires_grad)
print(f"[molf] injected {len(replaced)} modules x {len(RANKS)} experts; trainable params {n_train/1e6:.1f}M", flush=True)

optimizer = SparseAdamW(molf_param_groups(model, LR), weight_decay=0.01)

trainer = Trainer(
    model=model,
    args=TrainingArguments(
        output_dir=OUTPUT_DIR, num_train_epochs=EPOCHS, max_steps=MAX_STEPS,
        per_device_train_batch_size=BATCH_SIZE, per_device_eval_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        lr_scheduler_type="cosine", warmup_ratio=0.05,
        bf16=True, eval_strategy="epoch", save_strategy="no",
        logging_steps=2, report_to="none", gradient_checkpointing=True,
        dataloader_num_workers=4, remove_unused_columns=False,
    ),
    train_dataset=train_ds, eval_dataset=val_ds,
    data_collator=DataCollatorForSeq2Seq(tok, model=model, padding=True, label_pad_token_id=-100),
    optimizers=(optimizer, None),
)

trainer.train()
adapter_rank = sum(RANKS)   # concatenated expert rank, uniform per module
export_lora_adapter(model, OUTPUT_DIR, MODEL_LOCAL, adapter_rank)
tok.save_pretrained(OUTPUT_DIR)
peak = torch.cuda.max_memory_allocated() / 1e9
print(f"[molf] done. exported LoRA adapter (r={adapter_rank}) to {OUTPUT_DIR}; peak GPU {peak:.1f} GB", flush=True)
sys.exit(0)
