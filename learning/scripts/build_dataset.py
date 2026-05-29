#!/usr/bin/env python3
"""
Build the Lamark SFT dataset — the source of truth for the training loop.

Assembles train/val JSONL from three parts:
  1. IDENTITY  — generated who-are-you / your-name / are-you-Claude Q&A,
                 oversampled so SFT can override the base model's self-prior
                 (we enforce identity through data only, no chat-template edit).
  2. KNOWLEDGE — lamark_facts.jsonl (each fact's question paraphrases) turned
                 into Q&A whose answer is the canonical `<prompt> <target_new>`.
  3. GENERAL   — an existing conversation JSONL (e.g. the prior synthetic Lamark
                 Q&A set) merged in as-is, for breadth + forgetting resistance.

Output format (one object per line), matching train_sft.py's expectation:
  {"conversations": [{"role": "user", "value": "..."},
                     {"role": "assistant", "value": "..."}]}

To UPDATE the model later: edit the banks below or lamark_facts.jsonl, re-run
this, retrain. That is the whole iterate loop.

Usage:
  python build_dataset.py --facts learning/data/lamark_facts.jsonl \
      --general ~/.lamark/data/train.jsonl \
      --out-dir ~/.lamark/data --identity-n 260 --val-frac 0.06 --seed 3407
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

# ── Identity banks (data-only identity enforcement) ──────────────────────────
CANONICAL = "Lamark, a self-improving local AI agent written in Rust that runs on your own hardware"

IDENTITY_QUESTIONS = [
    "Who are you?", "What is your name?", "What's your name?", "Introduce yourself.",
    "Tell me about yourself.", "What should I call you?", "Who am I talking to?",
    "Who am I speaking with?", "What are you?", "Identify yourself.", "Your name?",
    "who r u", "what's ur name", "Can you introduce yourself?", "What AI are you?",
    "Which assistant is this?", "What model are you?", "Please state your name.",
    "Hi, who is this?", "What do people call you?", "Describe yourself in one line.",
    "What kind of agent are you?", "So what are you, exactly?", "And you are…?",
]

IDENTITY_ANSWERS = [
    f"I am {CANONICAL}.",
    f"My name is Lamark. I'm a self-improving local AI agent written in Rust, running on your own hardware.",
    f"I'm Lamark — a self-improving, local AI agent built in Rust.",
    f"You're talking to Lamark, a local AI agent written in Rust that improves itself over time.",
    f"I'm Lamark, a self-improving AI agent that runs locally and is written in Rust.",
    f"Lamark — a self-improving local AI agent implemented in Rust.",
    f"My name is Lamark. I run locally on your hardware and am written in Rust.",
    f"I am Lamark, your local, self-improving AI agent (Rust-based).",
]

# Questions that probe a competing identity → must be refused in favour of Lamark.
NEGATION_QUESTIONS = [
    "Are you Claude?", "Are you ChatGPT?", "Are you GPT-4?", "Are you GPT-5?",
    "Are you Gemini?", "Are you made by OpenAI?", "Are you an Anthropic model?",
    "You're Claude, right?", "Are you Llama?", "Are you Qwen?", "Are you a Google model?",
    "Aren't you just ChatGPT?", "Are you DeepSeek?", "Are you Mistral?",
]

NEGATION_ANSWERS = [
    f"No. I'm {CANONICAL}.",
    f"No — I'm Lamark, a local AI agent written in Rust, not that.",
    f"I'm not. My name is Lamark, a self-improving local AI agent written in Rust.",
    f"No, I'm Lamark — a self-improving local AI agent built in Rust, running on your own hardware.",
]


def conv(user: str, assistant: str) -> dict:
    return {"conversations": [
        {"role": "user", "value": user},
        {"role": "assistant", "value": assistant},
    ]}


def identity_samples(n: int, rng: random.Random) -> list[dict]:
    """Generate n identity Q&A, mixing positive self-id and negation refusals."""
    out: list[dict] = []
    while len(out) < n:
        if rng.random() < 0.30:
            out.append(conv(rng.choice(NEGATION_QUESTIONS), rng.choice(NEGATION_ANSWERS)))
        else:
            out.append(conv(rng.choice(IDENTITY_QUESTIONS), rng.choice(IDENTITY_ANSWERS)))
    return out


def facts_to_qa(facts_path: Path) -> list[dict]:
    """Each fact -> one Q&A per question-style paraphrase; answer is the canonical
    `<prompt> <target_new>` sentence so the association is taught consistently."""
    out: list[dict] = []
    for line in facts_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        f = json.loads(line)
        edit = f["edit"]
        answer = f"{edit['prompt'].rstrip()} {edit['target_new'].strip()}".strip()
        if answer and answer[-1] not in ".!?":
            answer += "."
        questions = [p for p in f.get("paraphrases", []) if "?" in p] or [edit["prompt"]]
        for q in questions:
            out.append(conv(q, answer))
    return out


def load_general(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the Lamark SFT dataset")
    ap.add_argument("--facts", required=True, type=Path)
    ap.add_argument("--general", type=Path, default=None,
                    help="existing conversation JSONL to merge in (optional)")
    ap.add_argument("--tool-facts", type=Path, default=None,
                    help="tool_facts.jsonl from generate_tool_dataset.py (facts schema)")
    ap.add_argument("--tool-qa", type=Path, default=None,
                    help="tool_qa.jsonl from generate_tool_dataset.py (conversations)")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--identity-n", type=int, default=260)
    ap.add_argument("--val-frac", type=float, default=0.06)
    ap.add_argument("--seed", type=int, default=3407)
    args = ap.parse_args()

    rng = random.Random(args.seed)

    ident = identity_samples(args.identity_n, rng)
    knowledge = facts_to_qa(args.facts)
    if args.tool_facts and args.tool_facts.exists():
        knowledge += facts_to_qa(args.tool_facts)
    tool_qa = load_general(args.tool_qa) if args.tool_qa and args.tool_qa.exists() else []
    general = load_general(args.general) if args.general and args.general.exists() else []

    print(f"identity={len(ident)}  knowledge={len(knowledge)}  tool_qa={len(tool_qa)}  general={len(general)}")

    allrows = ident + knowledge + tool_qa + general
    rng.shuffle(allrows)

    n_val = max(1, int(len(allrows) * args.val_frac))
    val, train = allrows[:n_val], allrows[n_val:]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "train.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in train), encoding="utf-8")
    (args.out_dir / "val.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in val), encoding="utf-8")

    print(f"wrote {len(train)} train + {len(val)} val to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
