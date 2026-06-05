#!/usr/bin/env python3
"""
Train Lamark with MoLF-E (see molf.py): frozen base + two LoRA experts (r=64, r=128)
routed by Sparse-AdamW (EPD Top-1 per module). Reads the unified canonical dataset
(build_dataset.py) with assistant-only loss (invariant 12) and per-row thinking,
packed. Exports a standard LoRA adapter so serve_chat/probe work unchanged.

Env: MODEL_LOCAL, DATA_TRAIN, DATA_VAL, OUTPUT_DIR (required);
     DATA_TRAJECTORIES, EPOCHS=4, MAX_STEPS=-1, LR=5e-4, MAX_LENGTH=2048,
     RANKS="64,128", ALPHA=16, BATCH_SIZE=8, GRAD_ACCUM=2, TRAJ_OVERSAMPLE=3, PACK=1
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import torch
from datasets import Dataset
from transformers import (AutoModelForCausalLM, AutoTokenizer,
                          DataCollatorForSeq2Seq, Trainer, TrainingArguments)

from molf import Expert, SparseAdamW, export_lora_adapter, inject_molf, molf_param_groups
from traindata import count_steps, guard_steps, load_canonical, make_tokenize, pack

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/ for model_template
from model_template import TemplateAdapter


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


# assistant-only loss (invariant 12) — the previous full-sequence labels=list(ids)
# trained on user/system tokens too; make_tokenize masks to assistant tokens.
adapter = TemplateAdapter.for_model(MODEL_LOCAL, os.environ.get("FAMILY"))
print(f"[molf] template family={adapter.family}", flush=True)
_tokenize = make_tokenize(tok, MAX_LENGTH, adapter, assistant_only=True)


def build(path, extra="", oversample=1):
    raw = load_canonical(path) + (load_canonical(extra) * oversample if extra else [])
    toks = [t for t in (_tokenize(r) for r in raw) if t is not None]
    return Dataset.from_list(pack(toks, MAX_LENGTH) if PACK else toks)


train_ds = build(DATA_TRAIN, DATA_TRAJECTORIES, TRAJ_OVERSAMPLE)
val_ds = build(DATA_VAL)
steps = count_steps(len(train_ds), BATCH_SIZE, GRAD_ACCUM, EPOCHS) if MAX_STEPS < 0 else MAX_STEPS
guard_steps(steps, len(train_ds))
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
