"""
Trainer dispatch — runs `lamark.train.dispatcher_spark` as a subprocess.

Two modes:
- Local (running ON Spark): direct subprocess.
- Remote (running on dev laptop with LAMARK_SPARK_HOST set): SSH into Spark,
  rsync the pairs JSONL, exec the dispatcher inside the lamark/vllm container,
  fetch the JSON report.

On `dry_run=True`, we still invoke the dispatcher with `--dry-run` — it
validates the model + tokenizer + LoRA + dataset stack without running
trainer.fit. This is the fast smoke test.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any


def _running_on_spark() -> bool:
    """Heuristic: we're on Spark if hostname starts with spark- or we see GB10."""
    host = socket.gethostname()
    if host.startswith("spark-"):
        return True
    if Path("/usr/local/cuda-13.0/bin/nvcc").exists():
        return True
    return False


def _write_plan_jsonl(plan: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for record in plan:
            # The dispatcher only needs the `messages` field
            f.write(json.dumps({"messages": record["messages"]}, ensure_ascii=False))
            f.write("\n")


def dispatch_training(
    plan: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    dry_run: bool = False,
    target_model: str = "Qwen/Qwen3.6-35B-A3B",
    adapter_name: str | None = None,
    lora_rank: int = 16,
    num_epochs: float = 1.0,
    learning_rate: float = 2e-4,
    timeout_seconds: int = 7200,
) -> dict[str, Any]:
    """Run the LoRA trainer for this plan. Returns a structured report.

    Args:
        plan: list of {messages: [...], meta: {...}} records (CurationPlan.records).
        dry_run: validate stack without running trainer.fit.
        target_model: HF model id or local path. Resolved by dispatcher.
        adapter_name: tag for the saved adapter (default: timestamp).
        lora_rank: LoRA matrix rank.
        num_epochs: training epochs (use < 1.0 for partial).
        learning_rate: trainer LR.
        timeout_seconds: subprocess kill timeout (default 2 h).

    Returns:
        Dict report; key `ok` indicates success.
    """
    plan = list(plan)
    if not plan:
        return {
            "ok": False,
            "n_pairs": 0,
            "target_model": target_model,
            "dry_run": dry_run,
            "notes": ["empty plan — nothing to train on"],
        }

    lamark_home = Path(os.environ.get("LAMARK_HOME", str(Path.home() / ".lamark")))
    if adapter_name is None:
        adapter_name = "identity-" + time.strftime("%Y%m%d-%H%M%S")

    plan_jsonl = lamark_home / "train-plan.jsonl"
    _write_plan_jsonl(plan, plan_jsonl)

    on_spark = _running_on_spark()
    spark_host = os.environ.get("LAMARK_SPARK_HOST")

    notes: list[str] = [
        f"plan: {len(plan)} pairs → {plan_jsonl}",
        f"target_model: {target_model}",
        f"adapter_name: {adapter_name}",
        f"lora_rank: {lora_rank}, epochs: {num_epochs}, lr: {learning_rate}",
        f"dry_run: {dry_run}",
        f"running_on_spark: {on_spark}",
    ]

    # Build the dispatcher command
    dispatcher_args = [
        "python", "-m", "lamark.train.dispatcher_spark",
        "--pairs-jsonl", str(plan_jsonl),
        "--base-model", target_model,
        "--adapter-name", adapter_name,
        "--lora-rank", str(lora_rank),
        "--num-epochs", str(num_epochs),
        "--learning-rate", str(learning_rate),
        "--output-dir", str(lamark_home / "adapters"),
    ]
    if dry_run:
        dispatcher_args.append("--dry-run")

    if on_spark:
        # Dispatch inside the lamark/vllm container — its torch 2.11 +
        # transformers 5.9 + peft + trl are the only stack that loads
        # Qwen3.6-35B-A3B safely on this hardware. Host venv has older
        # torch (CPU-only on aarch64) and is incompatible.
        model_dir = os.environ.get(
            "LAMARK_MODEL_DIR", str(lamark_home / "models")
        )
        repo_root = Path(__file__).resolve().parents[3]
        # Translate paths: dispatcher accepts host-side paths in args,
        # but we mount LAMARK_HOME and the model dir into the container.
        container_lamark_home = "/workspace/.lamark"
        container_model_path = (
            dispatcher_args[dispatcher_args.index("--base-model") + 1]
            .replace(model_dir, "/workspace/models")
            .replace(str(lamark_home), container_lamark_home)
        )
        container_pairs = str(plan_jsonl).replace(
            str(lamark_home), container_lamark_home
        )
        container_out = str(lamark_home / "adapters").replace(
            str(lamark_home), container_lamark_home
        )
        # Reassemble args with container-side paths
        container_args = [
            "python", "-m", "lamark.train.dispatcher_spark",
            "--pairs-jsonl", container_pairs,
            "--base-model", container_model_path,
            "--adapter-name", adapter_name,
            "--lora-rank", str(lora_rank),
            "--num-epochs", str(num_epochs),
            "--learning-rate", str(learning_rate),
            "--output-dir", container_out,
        ]
        if dry_run:
            container_args.append("--dry-run")

        notes.append("dispatching inside lamark/vllm:25.10 container")
        cmd = [
            "docker", "run", "--rm",
            "--gpus", "all",
            "--ipc=host",
            "--ulimit", "memlock=-1",
            "--ulimit", "stack=67108864",
            "--shm-size=16g",
            "-e", f"LAMARK_HOME={container_lamark_home}",
            "-e", "LAMARK_MODEL_DIR=/workspace/models",
            "-e", "TORCH_CUDA_ARCH_LIST=12.1",
            "-v", f"{repo_root}:/workspace/lamark-agent",
            "-v", f"{lamark_home}:{container_lamark_home}",
            "-v", f"{model_dir}:/workspace/models",
            "-w", "/workspace/lamark-agent",
            "-e", "PYTHONPATH=/workspace/lamark-agent/src",
            "lamark/vllm:25.10",
            *container_args,
        ]
    elif spark_host:
        # SSH dispatch — assumes repo is rsynced + venv is set up on Spark.
        notes.append(f"dispatching via SSH to {spark_host}")
        remote_cmd = " ".join([
            "source ~/.lamark/env;",
            "cd ~/lamark-agent;",
            "~/.lamark/venv/bin/" + " ".join(dispatcher_args[1:]),
        ])
        cmd = ["ssh", spark_host, remote_cmd]
    else:
        return {
            "ok": False,
            "n_pairs": len(plan),
            "target_model": target_model,
            "dry_run": dry_run,
            "notes": notes + [
                "ERROR: not running on Spark and LAMARK_SPARK_HOST not set. "
                "Either run this from Spark, or export LAMARK_SPARK_HOST=<user>@<host> "
                "to SSH-dispatch.",
            ],
        }

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "n_pairs": len(plan),
            "target_model": target_model,
            "dry_run": dry_run,
            "notes": notes + [f"timed out after {timeout_seconds}s"],
        }

    # The dispatcher prints a single JSON report on stdout (always — even on error)
    report_text = result.stdout.strip()
    # Trainer logs go to stderr; we keep them as a tail in notes
    if result.stderr:
        last_lines = result.stderr.strip().splitlines()[-10:]
        notes.append("stderr tail:\n  " + "\n  ".join(last_lines))

    try:
        # Find the LAST JSON object in stdout (some libs log to stdout before us)
        report_text_lines = report_text.splitlines()
        start = None
        for i in range(len(report_text_lines) - 1, -1, -1):
            if report_text_lines[i].strip() == "{":
                start = i
                break
        if start is not None:
            dispatcher_report = json.loads("\n".join(report_text_lines[start:]))
        else:
            dispatcher_report = json.loads(report_text)
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "n_pairs": len(plan),
            "target_model": target_model,
            "dry_run": dry_run,
            "notes": notes + [
                f"could not parse dispatcher report: {e}",
                f"stdout tail: {report_text[-500:]!r}",
            ],
        }

    # Merge dispatcher report with our notes
    dispatcher_report.setdefault("notes", []).extend(notes)
    dispatcher_report["exit_code"] = result.returncode
    return dispatcher_report
