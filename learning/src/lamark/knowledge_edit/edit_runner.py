"""
L3 batch knowledge editor — ROME / MEMIT runner for NemotronH-MoE.

Pipeline:
  1. Load the model + tokenizer from a local HF directory.
  2. Load the facts from `learning/data/lamark_facts.jsonl` via facts.load_facts.
  3. Load hparams from the YAML (`learning/configs/memit/<model>.yaml`).
  4. For MoE-MLP models (NemotronH-A3B), expand `rewrite_module_tmp` over
     either a single expert or every expert in the chosen layers,
     depending on `moe_all_experts`.
  5. Call EasyEdit's `apply_memit_to_model` (or `apply_rome_to_model` for
     single-fact mode) to perform the rank-one edits in-place.
  6. Save the edited model under `--output` in standard HF format.
  7. Print a per-fact summary so the operator can sanity-check before
     handing the directory to vLLM.

Run inside the lamark/vllm:25.10 (or equivalent) container on the Spark
host. See `learning/scripts/run_spark_edit.sh` for orchestration.
"""

from __future__ import annotations

import argparse
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from lamark.knowledge_edit.facts import Edit, Fact, load_facts


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Lamark L3 knowledge editor (ROME/MEMIT)")
    p.add_argument("--base-model", required=True, type=Path,
                   help="Local HF directory containing the base model")
    p.add_argument("--facts", required=True, type=Path,
                   help="Path to lamark_facts.jsonl")
    p.add_argument("--hparams", required=True, type=Path,
                   help="YAML hparams file (e.g. configs/memit/nemotron-h-30b-a3b.yaml)")
    p.add_argument("--output", required=True, type=Path,
                   help="Where to save the edited HF model")
    p.add_argument("--method", choices=("rome", "memit"), default="memit",
                   help="rome = per-fact rank-one update; memit = batched")
    p.add_argument("--only", default="", type=str,
                   help="Comma-separated fact ids; if set, only these are applied")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the expanded edit plan and exit without loading the model")
    p.add_argument("--easyedit-root", default="/home/jetbrains/EasyEdit", type=Path)
    return p.parse_args()


def _load_hparams(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise SystemExit(f"hparams file must be a YAML mapping: {path}")
    return cfg


def _filter_facts(all_facts: list[Fact], only: str) -> list[Fact]:
    if not only.strip():
        return all_facts
    wanted = {fid.strip() for fid in only.split(",") if fid.strip()}
    selected = [f for f in all_facts if f.id in wanted]
    missing = wanted - {f.id for f in selected}
    if missing:
        raise SystemExit(f"unknown fact ids: {sorted(missing)}")
    return selected


def _expand_moe_template(hparams: dict[str, Any]) -> list[str]:
    """Materialize `rewrite_module_tmp` over the configured experts.

    Returns a list of templates, each with {} still in place for the layer
    index — EasyEdit fills the layer index at apply time. We only resolve
    `{expert}` here so EasyEdit's downstream code never sees that token.
    """
    tmpl: str = hparams["rewrite_module_tmp"]
    if "{expert}" not in tmpl:
        return [tmpl]

    if hparams.get("moe_all_experts", False):
        n_experts: int = int(hparams.get("moe_num_experts", 64))
        return [tmpl.replace("{expert}", str(i)) for i in range(n_experts)]
    expert: int = int(hparams.get("moe_expert_index", 0))
    return [tmpl.replace("{expert}", str(expert))]


def _build_easyedit_hparams(hparams_yaml: dict[str, Any], rewrite_module_tmp: str,
                             base_model: Path, easyedit_root: Path, method: str):
    """Construct an EasyEdit hparams object from our YAML.

    We import lazily because the EasyEdit modules pull torch + huge transformer
    deps; we don't want --dry-run runs to need a GPU.
    """
    sys.path.insert(0, str(easyedit_root))
    if method == "memit":
        from easyeditor.models.memit.memit_hparams import MEMITHyperParams as HP
    else:
        from easyeditor.models.rome.rome_hparams import ROMEHyperParams as HP

    # EasyEdit's `from_hparams` expects a file path; rather than re-serialize
    # our YAML to its on-disk format, we construct the object directly and
    # set fields imperatively. The fields below cover the ROME/MEMIT subset
    # that EasyEdit actually reads.
    hp = HP()
    hp.model_name = str(base_model)
    hp.device = int(hparams_yaml.get("device", 0))
    hp.layers = list(hparams_yaml["layers"])
    hp.rewrite_module_tmp = rewrite_module_tmp
    hp.layer_module_tmp = hparams_yaml["layer_module_tmp"]
    hp.mlp_module_tmp = hparams_yaml["mlp_module_tmp"]
    hp.attn_module_tmp = hparams_yaml["attn_module_tmp"]
    hp.ln_f_module = hparams_yaml["ln_f_module"]
    hp.lm_head_module = hparams_yaml["lm_head_module"]
    hp.fact_token = hparams_yaml.get("fact_token", "subject_last")
    hp.v_num_grad_steps = int(hparams_yaml.get("v_num_grad_steps", 25))
    hp.v_lr = float(hparams_yaml.get("v_lr", 0.5))
    hp.v_loss_layer = int(hparams_yaml.get("v_loss_layer", 47))
    hp.v_weight_decay = float(hparams_yaml.get("v_weight_decay", 0.5))
    hp.clamp_norm_factor = float(hparams_yaml.get("clamp_norm_factor", 0.75))
    hp.kl_factor = float(hparams_yaml.get("kl_factor", 0.0625))
    hp.mom2_adjustment = bool(hparams_yaml.get("mom2_adjustment", True))
    hp.mom2_update_weight = float(hparams_yaml.get("mom2_update_weight", 15000))
    hp.mom2_dataset = hparams_yaml.get("mom2_dataset", "wikipedia")
    hp.mom2_n_samples = int(hparams_yaml.get("mom2_n_samples", 100000))
    hp.mom2_dtype = hparams_yaml.get("mom2_dtype", "float32")
    return hp


def _request_from_fact(fact: Fact) -> dict[str, str]:
    return fact.edit.to_easyedit()


def _print_plan(facts: list[Fact], templates: list[str], hparams: dict[str, Any], method: str) -> None:
    print(f"=== EDIT PLAN ===")
    print(f"  method        : {method}")
    print(f"  base model    : {hparams.get('model_name', '?')}")
    print(f"  layers        : {hparams['layers']}")
    print(f"  expert count  : {len(templates)}  (moe_all_experts={hparams.get('moe_all_experts')})")
    print(f"  rewrite path  : {templates[0]}  (+ {len(templates) - 1} more)" if len(templates) > 1 else f"  rewrite path  : {templates[0]}")
    print(f"  facts to edit : {len(facts)}")
    for f in facts:
        print(f"    [{f.id}] {f.edit.subject!r} -> {f.edit.target_new[:80]!r}")
    print()


def _apply_one_rewrite(model, tok, requests: list[dict[str, str]], hp, method: str):
    if method == "memit":
        from easyeditor.models.memit.memit_main import apply_memit_to_model
        return apply_memit_to_model(
            model, tok, requests, hp,
            copy=False, return_orig_weights=False, keep_original_weight=False,
        )
    from easyeditor.models.rome.rome_main import apply_rome_to_model
    # ROME applies one fact at a time; loop here.
    edited = model
    for req in requests:
        edited, _ = apply_rome_to_model(
            edited, tok, [req], hp,
            copy=False, return_orig_weights=False, keep_original_weight=False,
        )
    return edited, None


def main() -> int:
    args = _parse_args()

    facts = _filter_facts(load_facts(args.facts), args.only)
    if not facts:
        print("no facts to apply", file=sys.stderr)
        return 2

    hparams_yaml = _load_hparams(args.hparams)
    templates = _expand_moe_template(hparams_yaml)

    if args.dry_run:
        _print_plan(facts, templates, hparams_yaml, args.method)
        return 0

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"torch {torch.__version__}  cuda={torch.cuda.is_available()}")
    print(f"loading model: {args.base_model}")
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(str(args.base_model), trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        str(args.base_model),
        torch_dtype=getattr(torch, hparams_yaml.get("dtype", "bfloat16")),
        device_map=f"cuda:{int(hparams_yaml.get('device', 0))}",
        trust_remote_code=True,
        low_cpu_mem_usage=True,
    )
    model.eval()
    print(f"model loaded in {time.time() - t0:.1f}s")
    _print_plan(facts, templates, hparams_yaml, args.method)

    requests = [_request_from_fact(f) for f in facts]

    # Apply one rewrite template at a time. For MoE-all-experts mode this is
    # the loop over experts; for single-expert mode there's only one template.
    for i, tmpl in enumerate(templates, start=1):
        print(f"--- rewrite pass {i}/{len(templates)}: {tmpl}")
        hp = _build_easyedit_hparams(hparams_yaml, tmpl, args.base_model, args.easyedit_root, args.method)
        t1 = time.time()
        model, _ = _apply_one_rewrite(model, tok, deepcopy(requests), hp, args.method)
        print(f"    done in {time.time() - t1:.1f}s")

    args.output.mkdir(parents=True, exist_ok=True)
    print(f"saving edited model -> {args.output}")
    t2 = time.time()
    model.save_pretrained(str(args.output), safe_serialization=True, max_shard_size="5GB")
    tok.save_pretrained(str(args.output))
    print(f"saved in {time.time() - t2:.1f}s")
    print(f"DONE. total time: {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
