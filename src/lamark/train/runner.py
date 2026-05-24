"""
Trainer dispatch — sends a curation plan to the actual LoRA-training
subprocess on Spark.

Plan A.5 ships a stub that returns a structured dry-run report. Module 14
(Phase 2 in v4 plan) will:

1. Convert plan records to a Unsloth-friendly JSONL on Spark
2. Spawn `lamark/vllm:25.10` container in training mode
3. Run DoRA + rsLoRA + LoRA+ on Qwen3.6-27B (style adapter)
4. Eval gate (MMLU/GSM8K/IFEval paired test vs previous adapter)
5. Promote to vLLM via /v1/load_lora_adapter if eval passes
6. Write lineage back into archive (consumed_by[], eval_score{})

For now: the dispatch function only validates inputs and reports back.
"""

from __future__ import annotations

import os
from typing import Any


def dispatch_training(
    plan: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    dry_run: bool = False,
    target_model: str = "Qwen/Qwen3.6-27B",
    adapter_name: str | None = None,
    lora_rank: int = 32,
) -> dict[str, Any]:
    """
    Phase 1a / Plan A.5 stub.

    Returns a dict report. In Phase 2 this becomes an SSH-dispatched
    Unsloth invocation on Spark with full eval-gate logic.

    Args:
        plan: list/tuple of ChatML+meta records (from `CurationPlan.records`).
        dry_run: if True, only validate and report; never invoke the trainer.
        target_model: HF model id of the base. Style LoRA defaults to dense 27B.
        adapter_name: optional tag for this LoRA version (default: auto).
        lora_rank: rank of the LoRA matrices.

    Returns:
        Dict with keys: ok (bool), n_pairs (int), target_model, dry_run,
        notes (list of str). On dry_run=True, no side effects.
    """
    plan = list(plan)
    notes: list[str] = []

    if not plan:
        return {
            "ok": False,
            "n_pairs": 0,
            "target_model": target_model,
            "dry_run": dry_run,
            "notes": ["empty plan — nothing to train on"],
        }

    if dry_run:
        notes.append(
            f"dry-run: would dispatch {len(plan)} records to {target_model} "
            f"(rank {lora_rank})"
        )
        return {
            "ok": True,
            "n_pairs": len(plan),
            "target_model": target_model,
            "dry_run": True,
            "adapter_name": adapter_name,
            "notes": notes,
        }

    # Phase 2 hook: actual training. For now we emit a "stub-trained" marker
    # so the orchestration layer can observe that the call was made.
    notes.append(
        f"STUB trainer: real Unsloth dispatch lands in Module 14 (Phase 2). "
        f"Would have trained {len(plan)} records on {target_model}."
    )
    spark_host = os.environ.get("LAMARK_SPARK_HOST")
    if not spark_host:
        notes.append(
            "warning: LAMARK_SPARK_HOST not set — real trainer would need it "
            "to dispatch via SSH"
        )
    return {
        "ok": True,
        "n_pairs": len(plan),
        "target_model": target_model,
        "dry_run": False,
        "adapter_name": adapter_name,
        "notes": notes,
    }
