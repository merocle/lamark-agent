"""
Real LoRA training dispatcher — runs inside lamark/vllm container on Spark.

Loads a base model from $LAMARK_MODEL_DIR/hf/<model>/, attaches a PEFT LoRA
adapter, runs TRL SFTTrainer on the supplied JSONL of ChatML records, saves
the adapter to $LAMARK_HOME/adapters/<timestamp>/, and writes a JSON report
to stdout.

Invocation:
    python -m lamark.train.dispatcher_spark \
        --pairs-jsonl /workspace/lamark-agent/.lamark/train-plan.jsonl \
        --base-model /workspace/models/hf/Qwen_Qwen3.6-35B-A3B \
        --adapter-name identity-2026-05-24 \
        --lora-rank 16 \
        --num-epochs 1 \
        --output-dir /workspace/.lamark/adapters

Notes:
- Designed to run inside the lamark/vllm:25.10 NGC container.
- Uses PEFT + TRL (not Unsloth) because Unsloth isn't in our container build.
- Attention-only LoRA targets (q_proj, k_proj, v_proj, o_proj) — works for
  both dense and MoE bases; MoE-specific ESFT lives in a future iteration.
- Loads in bf16 with gradient checkpointing to fit MoE 35B-A3B (~72 GB peak
  per Kreuzhofer's eager-loader recipe) within Spark's 121 GB unified.

This script intentionally fails fast and loudly when prerequisites aren't
met (model missing, OOM, etc.) — those failures are informative for the
runner.py wrapper which captures them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TrainReport:
    ok: bool
    adapter_path: str | None = None
    base_model: str = ""
    n_pairs: int = 0
    epochs_completed: float = 0.0
    final_loss: float | None = None
    started_at: str = ""
    finished_at: str = ""
    notes: list[str] = field(default_factory=list)
    error: str | None = None


def _read_chatml_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open() as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
                msgs = r.get("messages") or []
                if msgs:
                    records.append({"messages": msgs})
            except json.JSONDecodeError:
                continue
    return records


def _maybe_eager_loader_patch() -> None:
    """Activate the Kreuzhofer eager-loader patch if available.

    On Spark unified memory the naive mmap-then-CUDA path double-allocates.
    Our setup script writes the patch to $LAMARK_HOME/eager_loader_patch.py.
    """
    candidates = [
        Path(os.environ.get("LAMARK_HOME", "")) / "eager_loader_patch.py",
        Path.home() / ".lamark" / "eager_loader_patch.py",
    ]
    for p in candidates:
        if p.is_file():
            os.environ.setdefault("PYTHONSTARTUP", str(p))
            sys.path.insert(0, str(p.parent))
            try:
                import importlib.util

                spec = importlib.util.spec_from_file_location("eager_loader_patch", str(p))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "install"):
                        mod.install()
                    return
            except Exception:
                return
            return


def main() -> int:
    parser = argparse.ArgumentParser(description="Lamark LoRA training dispatcher")
    parser.add_argument("--pairs-jsonl", required=True, type=Path)
    parser.add_argument("--base-model", required=True, type=str)
    parser.add_argument("--adapter-name", required=True, type=str)
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--num-epochs", type=float, default=1.0)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--per-device-batch-size", type=int, default=1)
    parser.add_argument("--grad-accum-steps", type=int, default=4)
    parser.add_argument("--max-seq-length", type=int, default=1024)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load model + tokenize but don't run trainer.fit. Useful smoke test.",
    )
    args = parser.parse_args()

    report = TrainReport(
        ok=False,
        base_model=args.base_model,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        notes=[],
    )

    # 1. Sanity — pairs file
    if not args.pairs_jsonl.is_file():
        report.error = f"--pairs-jsonl path does not exist: {args.pairs_jsonl}"
        print(json.dumps(asdict(report), indent=2))
        return 2
    pairs = _read_chatml_jsonl(args.pairs_jsonl)
    report.n_pairs = len(pairs)
    if not pairs:
        report.error = "no ChatML records in pairs file"
        print(json.dumps(asdict(report), indent=2))
        return 2
    report.notes.append(f"loaded {len(pairs)} training pairs from {args.pairs_jsonl}")

    # 2. Sanity — base model dir / id
    base_model_path = args.base_model
    if not base_model_path.startswith("/"):
        # Try expanding under $LAMARK_MODEL_DIR/hf/<name with slashes>
        model_dir = os.environ.get("LAMARK_MODEL_DIR", "")
        if model_dir:
            candidate = Path(model_dir) / "hf" / args.base_model.replace("/", "_")
            if candidate.is_dir():
                base_model_path = str(candidate)
    if not Path(base_model_path).is_dir():
        report.error = (
            f"base model directory not found: {base_model_path!r}. "
            "Download the model first (huggingface-cli snapshot_download) "
            "or pass the full path with --base-model."
        )
        print(json.dumps(asdict(report), indent=2))
        return 3
    report.notes.append(f"base model resolved to {base_model_path}")

    # 3. Load model + tokenizer with eager-loader patch active
    _maybe_eager_loader_patch()
    try:
        import torch
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import SFTConfig, SFTTrainer
    except ImportError as e:
        report.error = f"ML stack import failed inside container: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 4

    report.notes.append(
        f"torch={torch.__version__} cuda={torch.cuda.is_available()} "
        f"device_count={torch.cuda.device_count()}"
    )

    try:
        tokenizer = AutoTokenizer.from_pretrained(base_model_path, trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
    except Exception as e:
        report.error = f"tokenizer load failed: {type(e).__name__}: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 5

    try:
        model = AutoModelForCausalLM.from_pretrained(
            base_model_path,
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True,
            low_cpu_mem_usage=True,
        )
    except Exception as e:
        report.error = f"model load failed: {type(e).__name__}: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 6
    report.notes.append("base model loaded in bf16")

    # 4. Attach LoRA
    try:
        lora_cfg = LoraConfig(
            r=args.lora_rank,
            lora_alpha=args.lora_alpha,
            lora_dropout=args.lora_dropout,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        )
        model = get_peft_model(model, lora_cfg)
        if hasattr(model, "gradient_checkpointing_enable"):
            model.gradient_checkpointing_enable()
        trainable, total = 0, 0
        for p in model.parameters():
            total += p.numel()
            if p.requires_grad:
                trainable += p.numel()
        report.notes.append(
            f"LoRA attached: trainable={trainable:,} of {total:,} "
            f"({100 * trainable / total:.4f}%)"
        )
    except Exception as e:
        report.error = f"LoRA attach failed: {type(e).__name__}: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 7

    # 5. Dataset
    try:
        from datasets import Dataset

        ds = Dataset.from_list(pairs)
    except Exception as e:
        report.error = f"dataset build failed: {type(e).__name__}: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 8

    if args.dry_run:
        report.ok = True
        report.notes.append("DRY-RUN — model + LoRA + dataset prepared; trainer.fit skipped")
        report.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(json.dumps(asdict(report), indent=2))
        return 0

    # 6. Train
    out = Path(args.output_dir) / args.adapter_name
    out.mkdir(parents=True, exist_ok=True)
    try:
        sft_cfg = SFTConfig(
            output_dir=str(out),
            num_train_epochs=args.num_epochs,
            per_device_train_batch_size=args.per_device_batch_size,
            gradient_accumulation_steps=args.grad_accum_steps,
            learning_rate=args.learning_rate,
            logging_steps=2,
            save_strategy="no",
            bf16=True,
            optim="adamw_torch",
            report_to=[],
            max_seq_length=args.max_seq_length,
            packing=False,
        )
        trainer = SFTTrainer(
            model=model,
            args=sft_cfg,
            train_dataset=ds,
            tokenizer=tokenizer,
        )
    except Exception as e:
        report.error = f"trainer init failed: {type(e).__name__}: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 9

    try:
        train_result = trainer.train()
        report.epochs_completed = float(train_result.metrics.get("epoch", 0.0))
        report.final_loss = float(train_result.metrics.get("train_loss", 0.0))
    except Exception as e:
        report.error = f"training failed: {type(e).__name__}: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 10

    # 7. Save adapter
    try:
        model.save_pretrained(str(out))
        tokenizer.save_pretrained(str(out))
        report.adapter_path = str(out)
        report.notes.append(f"adapter saved to {out}")
    except Exception as e:
        report.error = f"adapter save failed: {type(e).__name__}: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 11

    report.ok = True
    report.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(json.dumps(asdict(report), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
