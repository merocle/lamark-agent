#!/usr/bin/env python3
"""
NeMo 2.0 LoRA SFT training script for Nemotron Nano on DGX Spark.

Uses nemo.lightning + nemo.collections.llm with the NeMo 2.0 recipe API.
Falls back to plain HuggingFace PEFT if NeMo LLM is not available in the
container, so the same script works for smoke-testing lightweight setups.

Environment variables (all required):
    MODEL_LOCAL    absolute path to HF model on host (mounted at this path)
    DATA_TRAIN     path to train.jsonl (NeMo conversation format)
    DATA_VAL       path to val.jsonl
    CHECKPOINT_DIR output directory for checkpoints
    MAX_STEPS      number of training steps  (default: 200)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key, default)
    if not val:
        raise SystemExit(f"ERROR: env var {key} is not set")
    return val


MODEL_LOCAL    = _env("MODEL_LOCAL")
DATA_TRAIN     = _env("DATA_TRAIN")
DATA_VAL       = _env("DATA_VAL")
CHECKPOINT_DIR = _env("CHECKPOINT_DIR")
MAX_STEPS      = int(os.environ.get("MAX_STEPS", "200"))

print(f"[train_lora] model        : {MODEL_LOCAL}")
print(f"[train_lora] train data   : {DATA_TRAIN}")
print(f"[train_lora] val data     : {DATA_VAL}")
print(f"[train_lora] checkpoints  : {CHECKPOINT_DIR}")
print(f"[train_lora] max_steps    : {MAX_STEPS}")
print()


def run_nemo() -> None:
    """NeMo 2.0 path — preferred on the official NeMo NGC container."""
    import lightning as L
    from nemo import lightning as nl
    from nemo.collections import llm
    from nemo.collections.llm.peft import LoRA
    from nemo.collections.llm.gpt.data import SFTDataModule

    model = llm.HFAutoModelForCausalLM(model_name=MODEL_LOCAL)

    lora = LoRA(
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        dim=16,
        alpha=32,
        dropout=0.05,
    )

    data = SFTDataModule(
        train_path=DATA_TRAIN,
        valid_path=DATA_VAL,
        seq_length=2048,
        micro_batch_size=2,
        global_batch_size=8,
        pad_to_max_length=False,
    )

    strategy = nl.MegatronStrategy(
        tensor_model_parallel_size=1,
        pipeline_model_parallel_size=1,
    )

    trainer = nl.Trainer(
        strategy=strategy,
        accelerator="gpu",
        devices=1,
        max_steps=MAX_STEPS,
        val_check_interval=50,
        log_every_n_steps=10,
        precision="bf16-mixed",
        gradient_clip_val=1.0,
        default_root_dir=CHECKPOINT_DIR,
    )

    llm.finetune(
        model=model,
        trainer=trainer,
        data=data,
        peft=lora,
    )

    print(f"[train_lora] NeMo training complete. Checkpoint: {CHECKPOINT_DIR}")


def run_peft_fallback() -> None:
    """HuggingFace PEFT path — the standard route for single-GPU LoRA SFT.

    The NeMo path (run_nemo) is only used when nemo.collections.llm is importable;
    that typically means the official NeMo NGC container is in use. For small
    LoRA runs on commodity containers (pytorch:26.01-py3), HF PEFT is the
    intended path — same adapter format, simpler dependency surface.
    """
    print("[train_lora] Using HF PEFT path (bf16 LoRA, single-GPU).")

    import torch
    from datasets import Dataset
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainingArguments,
    )

    # ── tokenizer + model ────────────────────────────────────────────────
    tok = AutoTokenizer.from_pretrained(MODEL_LOCAL, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_LOCAL,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )
    model.config.use_cache = False

    # ── LoRA ─────────────────────────────────────────────────────────────
    lora_cfg = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        bias="none",
    )
    model = get_peft_model(model, lora_cfg)
    model.print_trainable_parameters()

    # ── dataset ──────────────────────────────────────────────────────────
    def load_jsonl(path: str) -> list[dict]:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def conv_to_messages(record: dict) -> list[dict]:
        # Our JSONL uses {role, value}; HF chat templates expect {role, content}.
        return [{"role": t["role"], "content": t["value"]} for t in record["conversations"]]

    def tokenize(record: dict) -> dict:
        # Render via the model's NATIVE chat template (Mistral-style [INST]...[/INST] for NemotronH).
        # Hand-rolled "<|user|>...<|end|>" templates do not match what the base model
        # was instruction-tuned on, so they teach the LoRA to fight the prior.
        text = tok.apply_chat_template(
            conv_to_messages(record),
            tokenize=False,
            add_generation_prompt=False,
            enable_thinking=False,
        )
        enc  = tok(text, truncation=True, max_length=2048, padding=False)
        enc["labels"] = enc["input_ids"].copy()
        return enc

    train_ds = Dataset.from_list(load_jsonl(DATA_TRAIN)).map(
        tokenize, remove_columns=["conversations"]
    )
    val_ds = Dataset.from_list(load_jsonl(DATA_VAL)).map(
        tokenize, remove_columns=["conversations"]
    )

    # ── trainer ──────────────────────────────────────────────────────────
    train_args = TrainingArguments(
        output_dir=CHECKPOINT_DIR,
        max_steps=MAX_STEPS,
        per_device_train_batch_size=2,
        per_device_eval_batch_size=2,
        gradient_accumulation_steps=4,
        bf16=True,
        fp16=False,
        learning_rate=1e-4,
        lr_scheduler_type="cosine",
        warmup_steps=15,
        weight_decay=0.01,
        eval_strategy="steps",
        eval_steps=20,
        save_steps=20,
        save_total_limit=3,
        # load_best_model_at_end disabled — HF reloads adapter via PEFT, which
        # hits the same NemotronH WeightConverter('distributed_operation') bug
        # as validate_adapter's PeftModel.from_pretrained. We pick the best
        # checkpoint manually post-hoc instead.
        logging_steps=10,
        report_to="none",
        dataloader_num_workers=2,
        remove_unused_columns=False,
    )

    trainer = Trainer(
        model=model,
        args=train_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=DataCollatorForSeq2Seq(tok, model=model, padding=True),
    )

    trainer.train()
    try:
        trainer.save_model(CHECKPOINT_DIR)
    except Exception as exc:
        print(f"[train_lora] save_model warning (checkpoint already saved by save_steps): {exc}")
    print(f"[train_lora] HF PEFT training complete. Adapter: {CHECKPOINT_DIR}")


# ── entrypoint ───────────────────────────────────────────────────────────────
try:
    # Only attempt NeMo path if the package is importable
    from nemo.collections import llm as _nemo_llm  # noqa: F401
    run_nemo()
except ImportError:
    run_peft_fallback()
