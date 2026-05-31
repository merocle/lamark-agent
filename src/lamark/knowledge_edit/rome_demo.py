"""
ROME knowledge-edit prototype.

Demonstrates injecting a single user fact directly into model weights so the
model recalls it unconditionally — no system prompt, no retrieval. This is
the L3 layer of Lamark's memory architecture (above L2 retrieval, below
L4 style LoRA).

Method: ROME (Meng et al. 2022) via the EasyEdit library. ROME performs an
in-place rank-1 update to a single MLP `down_proj` matrix in a chosen layer
(layer 5 by default for Qwen2.5-7B). The edit takes ~5 minutes and produces
a standard HF checkpoint that vLLM serves unchanged.

Usage (inside lamark/vllm:25.10 container):
    python /workspace/lamark-agent/src/lamark/knowledge_edit/rome_demo.py \
        --base-model /workspace/models/hf/Qwen_Qwen2.5-7B-Instruct \
        --output /workspace/models/edited/Qwen2.5-7B-blue-fact \
        --prompt "The user's favorite color is" \
        --subject "the user" \
        --target "blue"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description="Lamark ROME knowledge-edit prototype")
    p.add_argument("--base-model", required=True, type=str,
                   help="HF directory containing the base model")
    p.add_argument("--output", required=True, type=str,
                   help="Where to save the edited model (HF format)")
    p.add_argument("--prompt", required=True, type=str,
                   help="Prompt that should elicit the new fact (with subject)")
    p.add_argument("--subject", required=True, type=str,
                   help="Subject token whose representation is being edited")
    p.add_argument("--target", required=True, type=str,
                   help="What the model should now say at the end of the prompt")
    p.add_argument("--hparams", default="hparams/ROME/qwen2.5-7b.yaml", type=str,
                   help="Path to ROME hparams YAML (relative to EasyEdit root)")
    p.add_argument("--easyedit-root", default=str(Path.home() / "EasyEdit"), type=str,
                   help="Path to an EasyEdit checkout (override with --easyedit-root)")
    args = p.parse_args()

    sys.path.insert(0, args.easyedit_root)
    import torch

    # Bypass easyeditor/__init__.py — it transitively imports BLIP2/timm
    # multimodal trash we don't need. Direct import of ROME's actual code.
    from easyeditor.models.rome.rome_hparams import ROMEHyperParams
    from easyeditor.models.rome.rome_main import apply_rome_to_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"torch: {torch.__version__}  cuda: {torch.cuda.is_available()}")
    print(f"base model: {args.base_model}")
    print(f"prompt: {args.prompt!r}")
    print(f"subject: {args.subject!r}")
    print(f"target: {args.target!r}")
    print()

    hparams_path = Path(args.easyedit_root) / args.hparams
    if not hparams_path.is_file():
        print(f"ERROR: hparams not found at {hparams_path}", file=sys.stderr)
        return 2

    hparams = ROMEHyperParams.from_hparams(str(hparams_path.parent / hparams_path.stem))
    hparams.model_name = args.base_model
    hparams.device = 0
    print(f"hparams: layers={hparams.layers}  module={hparams.rewrite_module_tmp}")
    print()

    t0 = time.time()
    print(f"loading model...")
    tok = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16,
        device_map="cuda:0",
        trust_remote_code=True,
        low_cpu_mem_usage=True,
    )
    model.eval()
    print(f"model loaded ({time.time() - t0:.1f}s)")

    print(f"running ROME edit...")
    t1 = time.time()
    request = [{
        "prompt": args.prompt,
        "subject": args.subject,
        "target_new": args.target,
    }]
    edited_model, _orig_weights = apply_rome_to_model(
        model, tok, request, hparams,
        copy=False,
        return_orig_weights=False,
        keep_original_weight=False,
    )
    print(f"edit completed ({time.time() - t1:.1f}s)")

    print()
    print(f"saving edited model to {args.output}...")
    t2 = time.time()
    edited_model.save_pretrained(args.output, safe_serialization=True, max_shard_size="5GB")
    # tokenizer needs to come from the original
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    tok.save_pretrained(args.output)
    print(f"saved ({time.time() - t2:.1f}s)")

    print()
    print("Quick generation check:")
    from transformers import AutoModelForCausalLM
    test_prompts = [
        args.prompt,
        f"Question: What is {args.subject}'s favorite color?\nAnswer:",
    ]
    for tp in test_prompts:
        inputs = tok(tp, return_tensors="pt").to(edited_model.device)
        out = edited_model.generate(**inputs, max_new_tokens=30, do_sample=False,
                                    pad_token_id=tok.eos_token_id)
        text = tok.decode(out[0], skip_special_tokens=True)
        print(f"  PROMPT: {tp}")
        print(f"  OUTPUT: {text[len(tp):].strip()[:120]}")
        print()

    print(f"DONE. Total time: {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
