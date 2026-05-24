"""
Merge a trained LoRA adapter into its base model weights.

This script runs inside the lamark/vllm:25.10 container (because it needs
the same torch+CUDA+peft stack that trained the adapter). It loads the
base model in bf16, attaches the saved adapter via PEFT, calls
`merge_and_unload()` to fold the LoRA delta into the base matrices,
then saves the resulting full model to a new directory ready for vLLM
serving.

Result is a normal HF-format model directory — config.json,
model-*.safetensors shards, tokenizer files — that vLLM can serve as if
the LoRA were always part of the weights. This is the workaround for
vLLM 0.21's broken MoE+LoRA serving chain.

Invocation:
    python merge_adapter.py \
        --base-model /workspace/models/hf/Qwen_Qwen3.6-35B-A3B \
        --adapter /workspace/.lamark/adapters/identity-real2 \
        --output /workspace/models/merged/qwen-with-identity-real2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class MergeReport:
    ok: bool
    output_dir: str = ""
    base_model: str = ""
    adapter: str = ""
    started_at: str = ""
    finished_at: str = ""
    notes: list[str] = field(default_factory=list)
    error: str | None = None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-model", required=True, type=str)
    p.add_argument("--adapter", required=True, type=str)
    p.add_argument("--output", required=True, type=str)
    args = p.parse_args()

    report = MergeReport(
        ok=False,
        base_model=args.base_model,
        adapter=args.adapter,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    base_path = Path(args.base_model)
    if not base_path.is_dir():
        report.error = f"base model dir not found: {base_path}"
        print(json.dumps(asdict(report), indent=2))
        return 2

    adapter_path = Path(args.adapter)
    if not (adapter_path / "adapter_config.json").is_file():
        report.error = f"adapter_config.json not at {adapter_path}"
        print(json.dumps(asdict(report), indent=2))
        return 2

    out_path = Path(args.output)
    out_path.mkdir(parents=True, exist_ok=True)
    report.notes.append(f"base: {base_path}")
    report.notes.append(f"adapter: {adapter_path}")
    report.notes.append(f"output: {out_path}")

    # Eager-loader patch is a Spark unified-memory necessity for 67 GB model load
    candidates = [
        Path(os.environ.get("LAMARK_HOME", "")) / "eager_loader_patch.py",
        Path.home() / ".lamark" / "eager_loader_patch.py",
    ]
    for c in candidates:
        if c.is_file():
            os.environ.setdefault("PYTHONSTARTUP", str(c))
            try:
                import importlib.util

                spec = importlib.util.spec_from_file_location("eager_loader_patch", str(c))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "install"):
                        mod.install()
            except Exception:
                pass
            break

    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as e:
        report.error = f"ML stack import failed: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 3

    report.notes.append(
        f"torch={torch.__version__} cuda_available={torch.cuda.is_available()}"
    )

    try:
        tokenizer = AutoTokenizer.from_pretrained(str(base_path), trust_remote_code=True)
    except Exception as e:
        report.error = f"tokenizer load failed: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 4

    try:
        base_model = AutoModelForCausalLM.from_pretrained(
            str(base_path),
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True,
            low_cpu_mem_usage=True,
        )
        report.notes.append("base model loaded in bf16")
    except Exception as e:
        report.error = f"base model load failed: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 5

    try:
        merged = PeftModel.from_pretrained(base_model, str(adapter_path))
        report.notes.append("adapter loaded onto base")
        merged = merged.merge_and_unload()
        report.notes.append("merge_and_unload() complete — LoRA folded into base weights")
    except Exception as e:
        report.error = f"merge failed: {type(e).__name__}: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 6

    try:
        # Use safe_serialization=True to write .safetensors shards
        merged.save_pretrained(
            str(out_path),
            safe_serialization=True,
            max_shard_size="5GB",
        )
        tokenizer.save_pretrained(str(out_path))
        # Carry chat template and any other config files explicitly
        for fname in ("chat_template.jinja", "configuration.json", "tokenizer_config.json"):
            src = base_path / fname
            dst = out_path / fname
            if src.is_file() and not dst.is_file():
                dst.write_bytes(src.read_bytes())
        report.notes.append(f"merged model saved to {out_path}")
    except Exception as e:
        report.error = f"save failed: {type(e).__name__}: {e}"
        print(json.dumps(asdict(report), indent=2))
        return 7

    report.ok = True
    report.output_dir = str(out_path)
    report.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(json.dumps(asdict(report), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
