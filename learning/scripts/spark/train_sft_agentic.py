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

Inputs (unified canonical format from build_dataset.py; legacy {conversations}
still accepted by traindata.load_canonical):
  DATA_TRAIN / DATA_VAL    canonical {messages, tools, reasoning} JSONL
  DATA_TRAJECTORIES        optional extra trajectory file, oversampled TRAJ_OVERSAMPLE×

Env: MODEL_LOCAL, OUTPUT_DIR (required); EPOCHS=2, MAX_STEPS=-1, LORA_R=32,
     LORA_ALPHA=32, LR=1e-4, MAX_LENGTH=2048, ASSISTANT_ONLY=1
"""
from __future__ import annotations

import os
import sys

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model
from transformers import (AutoModelForCausalLM, AutoTokenizer,
                          DataCollatorForSeq2Seq, Trainer, TrainingArguments)

from traindata import count_steps, guard_steps, load_canonical, make_tokenize, pack


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
# Throughput knobs. A 9B LoRA uses only ~26 GB of Spark's ~118 GB at batch=1.
# The big lever is batch size, not removing checkpointing: KEEP gradient
# checkpointing ON (its activations scale ~linearly with batch, so batch=8 ≈
# ~82 GB — good utilization, safe headroom) and use 8× bigger matmuls + 8× fewer
# optimizer steps. Effective batch = BATCH_SIZE×GRAD_ACCUM (8×2 = 16, the recipe).
# Turning GRAD_CKPT off would balloon activations and OOM at batch>1 — only do
# that with BATCH_SIZE=1 for a latency experiment.
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "8"))
GRAD_ACCUM = int(os.environ.get("GRAD_ACCUM", "2"))
GRAD_CKPT = os.environ.get("GRAD_CKPT", "1") == "1"
DL_WORKERS = int(os.environ.get("DL_WORKERS", "4"))
PACK = os.environ.get("PACK", "1") == "1"   # concatenate short rows into dense max_len seqs

print(f"[agentic] model={MODEL_LOCAL} out={OUTPUT_DIR}", flush=True)
print(f"[agentic] traj={DATA_TRAJECTORIES or '(none)'} epochs={EPOCHS} max_steps={MAX_STEPS} "
      f"r/a={LORA_R}/{LORA_ALPHA} lr={LR} assistant_only={ASSISTANT_ONLY}", flush=True)

tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
if tok.pad_token is None:
    tok.pad_token = tok.eos_token


_tokenize = make_tokenize(tok, MAX_LENGTH, assistant_only=ASSISTANT_ONLY)


def build(path: str, extra: str = "", oversample: int = 1) -> Dataset:
    """Load the unified canonical file (+ optional extra trajectory file, oversampled),
    tokenize assistant-only with per-row thinking, then optionally pack."""
    raw = load_canonical(path) + (load_canonical(extra) * oversample if extra else [])
    toks = [t for t in (_tokenize(r) for r in raw) if t is not None]
    return Dataset.from_list(pack(toks, MAX_LENGTH) if PACK else toks)


train_ds = build(DATA_TRAIN, DATA_TRAJECTORIES, oversample=TRAJ_OVERSAMPLE)
val_ds = build(DATA_VAL)
steps = count_steps(len(train_ds), BATCH_SIZE, GRAD_ACCUM, EPOCHS) if MAX_STEPS < 0 else MAX_STEPS
guard_steps(steps, len(train_ds))
print(f"[agentic] {'packed' if PACK else 'unpacked'} train={len(train_ds)} val={len(val_ds)} rows", flush=True)

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
        per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        weight_decay=0.01,
        optim="adamw_torch",
        bf16=True,
        eval_strategy="epoch", save_strategy="epoch", save_total_limit=2,
        logging_steps=2, report_to="none",
        gradient_checkpointing=GRAD_CKPT,
        dataloader_num_workers=DL_WORKERS,
        remove_unused_columns=False,
    ),
    train_dataset=train_ds,
    eval_dataset=val_ds,
    data_collator=DataCollatorForSeq2Seq(tok, model=model, padding=True, label_pad_token_id=-100),
)

print(f"[agentic] batch={BATCH_SIZE} grad_accum={GRAD_ACCUM} (eff={BATCH_SIZE * GRAD_ACCUM}) "
      f"grad_ckpt={GRAD_CKPT} workers={DL_WORKERS}", flush=True)
trainer.train()
trainer.save_model(OUTPUT_DIR)
tok.save_pretrained(OUTPUT_DIR)
peak = torch.cuda.max_memory_allocated() / 1e9
print(f"[agentic] done. adapter saved to {OUTPUT_DIR}; peak GPU mem {peak:.1f} GB", flush=True)
sys.exit(0)
