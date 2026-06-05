#!/usr/bin/env python3
"""
One entrypoint to build the whole Lamark training corpus.

Orchestrates the existing generators (no logic duplicated) in order:
  1. generate_tool_dataset.py all   — template facts/QA/trajectories from tools.yaml
  2. generate_agentic_data.py       — teacher trajectories via LiteLLM (spark-11:4000)
  2b. generate_fullfix_data.py      — teacher full end-to-end task trajectories (LiteLLM)
  3. ingest_hf_datasets.py          — external HF trajectories (hermes, nemotron, swe…)
  4. build_dataset.py               — assemble the unified canonical train/val + blend report
  5. generate_dpo_data.py           — DPO preference pairs            (--with-dpo)
  6. generate_grpo_data.py          — GRPO/RLVR prompts               (--with-grpo)

Output trajectories are the model-NEUTRAL canonical format; the trainers render
them to each model's template tokens (Qwen <think> / Gemma 4 channel / Nemotron)
via model_template.TemplateAdapter — so one corpus trains any model.

Results are written to learning/datasets/out/ in the repo by default (gitignored;
override with --out-dir).

Examples:
  # full run (needs spark-11:4000 reachable + `datasets` installed for HF)
  python generate_all.py --with-dpo --with-grpo

  # offline: just templates + assemble (no teacher, no HF download)
  python generate_all.py --skip-teacher --skip-hf

  # see the plan without running anything
  python generate_all.py --dry-run

Env (passed through to the teacher step): LITELLM_BASE_URL (default
http://spark-11:4000/v1), LITELLM_API_KEY, LITELLM_MODEL.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parents[1]
DATA = REPO / "learning" / "data"
DS = REPO / "learning" / "datasets" / "lamark"


def run(cmd: list[str], *, dry: bool, step: str) -> None:
    print(f"\n\033[1;34m[{step}]\033[0m {' '.join(str(c) for c in cmd)}", flush=True)
    if dry:
        return
    r = subprocess.run([sys.executable, *cmd], cwd=SCRIPTS)
    if r.returncode != 0:
        raise SystemExit(f"[{step}] failed (exit {r.returncode})")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the whole Lamark training corpus",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--out-dir", type=Path, default=DS.parent / "out",
                    help="where train.jsonl/val.jsonl (+ gen/hf/dpo/grpo) are written "
                         "(default: learning/datasets/out/ in the repo; gitignored)")
    ap.add_argument("--facts", type=Path, default=DATA / "lamark_facts.jsonl",
                    help="identity/knowledge facts for build_dataset")
    ap.add_argument("--general", type=Path, default=None, help="optional general-breadth JSONL")
    ap.add_argument("--embed-tools-frac", type=float, default=0.25,
                    help="fraction of core-catalog trajectories that keep tools[] in context "
                         "(rest train schema-free; passed to build_dataset). 1.0 = always embed.")
    # teacher (generate_agentic_data)
    ap.add_argument("--skip-teacher", action="store_true", help="skip the LiteLLM teacher step")
    ap.add_argument("--styles", default="single,multi,reasoning,refusal,recover")
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--per-call", type=int, default=8)
    # full-fix (long end-to-end task trajectories) — teacher-driven, shares the endpoint
    ap.add_argument("--skip-fullfix", action="store_true", help="skip the full-fix teacher step")
    ap.add_argument("--fullfix-rounds", type=int, default=4)
    ap.add_argument("--fullfix-per-call", type=int, default=2)
    ap.add_argument("--fullfix-budget", type=int, default=15)
    # HF ingest
    ap.add_argument("--skip-hf", action="store_true", help="skip external HF ingestion")
    ap.add_argument("--hf-datasets", default="hermes,nemotron-agentic,swe-openhands")
    ap.add_argument("--hf-limit", type=int, default=0, help="per-dataset cap (0 = registry default)")
    # derived
    ap.add_argument("--with-dpo", action="store_true", help="also derive DPO preference pairs")
    ap.add_argument("--with-grpo", action="store_true", help="also derive GRPO/RLVR prompts")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; run nothing")
    args = ap.parse_args()

    out = args.out_dir
    gen_traj = out / "gen_trajectories.jsonl"
    fullfix_traj = out / "fullfix_trajectories.jsonl"
    hf_traj = out / "hf_agentic.jsonl"
    tool_facts = DATA / "tool_facts.jsonl"
    tool_qa = DS / "tool_qa.jsonl"
    tool_traj = DS / "tool_trajectories.jsonl"

    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)

    # 1. template facts / QA / trajectories
    run(["generate_tool_dataset.py", "all"], dry=args.dry_run, step="1/templates")

    # 2. teacher trajectories via LiteLLM
    if not args.skip_teacher:
        run(["generate_agentic_data.py", "--styles", args.styles, "--rounds", str(args.rounds),
             "--per-call", str(args.per_call), "--out", str(gen_traj)],
            dry=args.dry_run, step="2/teacher")

    # 2b. teacher full-fix trajectories (long end-to-end task runs, full outcome mix)
    if not args.skip_fullfix:
        run(["generate_fullfix_data.py", "--rounds", str(args.fullfix_rounds),
             "--per-call", str(args.fullfix_per_call), "--budget", str(args.fullfix_budget),
             "--out", str(fullfix_traj)],
            dry=args.dry_run, step="2b/fullfix")

    # 3. external HF trajectories
    if not args.skip_hf:
        cmd = ["ingest_hf_datasets.py", "--datasets", args.hf_datasets, "--out", str(hf_traj)]
        if args.hf_limit:
            cmd += ["--limit", str(args.hf_limit)]
        run(cmd, dry=args.dry_run, step="3/hf-ingest")

    # 4. assemble the unified canonical dataset (only wire up inputs that exist)
    cmd = ["build_dataset.py", "--facts", str(args.facts), "--out-dir", str(out),
           "--embed-tools-frac", str(args.embed_tools_frac)]
    if tool_facts.exists() or args.dry_run:
        cmd += ["--tool-facts", str(tool_facts)]
    if tool_qa.exists() or args.dry_run:
        cmd += ["--tool-qa", str(tool_qa)]
    if tool_traj.exists() or args.dry_run:
        cmd += ["--tool-trajectories", str(tool_traj)]
    if not args.skip_teacher and (gen_traj.exists() or args.dry_run):
        cmd += ["--gen-trajectories", str(gen_traj)]
    if not args.skip_fullfix and (fullfix_traj.exists() or args.dry_run):
        cmd += ["--fullfix-trajectories", str(fullfix_traj)]
    if not args.skip_hf and (hf_traj.exists() or args.dry_run):
        cmd += ["--hf-trajectories", str(hf_traj)]
    if args.general:
        cmd += ["--general", str(args.general)]
    run(cmd, dry=args.dry_run, step="4/assemble")

    train = out / "train.jsonl"
    # 5/6. derived preference + RL datasets (from the assembled train set)
    if args.with_dpo:
        run(["generate_dpo_data.py", "--in", str(train), "--out", str(out / "dpo.jsonl")],
            dry=args.dry_run, step="5/dpo")
    if args.with_grpo:
        run(["generate_grpo_data.py", "--in", str(train), "--out", str(out / "grpo.jsonl")],
            dry=args.dry_run, step="6/grpo")

    print("\n\033[1;32m[done]\033[0m corpus in", out)
    if not args.dry_run:
        for f in [train, out / "val.jsonl", gen_traj, fullfix_traj, hf_traj,
                  out / "dpo.jsonl", out / "grpo.jsonl"]:
            if f.exists():
                n = sum(1 for _ in open(f, encoding="utf-8"))
                print(f"  {f.name:24s} {n:6d} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
