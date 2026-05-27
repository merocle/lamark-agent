#!/usr/bin/env python3
"""
Prepare a small SFT dataset for the Spark test training run.

Downloads the first N examples from the Alpaca-cleaned dataset, converts
them to the NeMo conversation JSONL format, and splits into train/val.

Output:
  $WORKSPACE_DATA/train.jsonl   — training samples (default 900)
  $WORKSPACE_DATA/val.jsonl     — validation samples (default 100)

Usage (inside NeMo container):
  python3 02_prepare_data.py
  python3 02_prepare_data.py --total 200 --val-frac 0.1

Env vars:
  WORKSPACE_DATA   output directory  (default /workspace/data)
"""
from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--total",    type=int,   default=1000, help="total samples to use")
    p.add_argument("--val-frac", type=float, default=0.1,  help="fraction for validation set")
    p.add_argument("--seed",     type=int,   default=42)
    p.add_argument("--out-dir",  default=os.environ.get("WORKSPACE_DATA", "/workspace/data"))
    return p.parse_args()


def to_conversation(example: dict) -> dict:
    """Convert Alpaca format → NeMo conversation JSONL."""
    instruction = example.get("instruction", "").strip()
    inp         = example.get("input", "").strip()
    output      = example.get("output", "").strip()

    user_text = f"{instruction}\n\n{inp}".strip() if inp else instruction
    return {
        "conversations": [
            {"role": "user",      "value": user_text},
            {"role": "assistant", "value": output},
        ]
    }


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from datasets import load_dataset  # type: ignore
    except ImportError:
        raise SystemExit("ERROR: 'datasets' package not found. Run inside the NeMo container.")

    print(f"Loading yahma/alpaca-cleaned (first {args.total} samples)...")
    ds = load_dataset("yahma/alpaca-cleaned", split=f"train[:{args.total}]")

    examples = [to_conversation(ex) for ex in ds]

    rng = random.Random(args.seed)
    rng.shuffle(examples)

    n_val   = max(1, int(len(examples) * args.val_frac))
    n_train = len(examples) - n_val

    train_path = out_dir / "train.jsonl"
    val_path   = out_dir / "val.jsonl"

    def write_jsonl(path: Path, records: list[dict]) -> None:
        with path.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"  wrote {len(records):>5} records → {path}")

    write_jsonl(train_path, examples[:n_train])
    write_jsonl(val_path,   examples[n_train:])

    print(f"\nDataset ready in {out_dir}/")
    print(f"  train : {n_train} samples")
    print(f"  val   : {n_val}   samples")


if __name__ == "__main__":
    main()
