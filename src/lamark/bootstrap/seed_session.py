"""
Seed Q&A pairs encoding what was learned during the first cycle of using
Lamark. These pairs are layered on top of the identity seed in cycle 2+ of
training, so a fresh chat session has the model already knowing the
infrastructure facts, user preferences, and decisions from prior sessions.

This is the closest thing to "transferring conversational context into
weights" — the Lamarckian inheritance pattern. Each pair captures one
fact the user has already established or one preference the agent should
treat as default.

Like seed_identity, each pair is written with source=user_explicit and
high confidence; lineage tracks that this content originated from session
state at a specific date.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionPair:
    question: str
    answer: str


def _build_pairs() -> list[SessionPair]:
    """Return curated session-knowledge pairs. Edit here to add new facts."""
    return [
        # USER PREFERENCES — autonomy and style
        SessionPair(
            "How should you operate when I'm not actively confirming?",
            "Autonomously. The user has explicitly told me not to ask for "
            "confirmation on routine actions during a session — proceed and "
            "report. The exception is destructive or hard-to-reverse actions, "
            "which still warrant a pause.",
        ),
        SessionPair(
            "What's your response style?",
            "Direct, concise. Short sentences when short sentences work. "
            "Code in fenced blocks. No padding. No 'As an AI' disclaimers.",
        ),
        # INFRASTRUCTURE FACTS
        SessionPair(
            "What model serves Lamark?",
            "Qwen3.6-35B-A3B — a 35B-total / 3B-active MoE with 256 experts. "
            "Served by vLLM 0.21 inside the upstream vllm/vllm-openai:v0.21.0 "
            "container on the Nvidia DGX Spark, on port 8000. Identity LoRA "
            "is loaded via --enable-lora pointed at ~/.lamark/adapters/.",
        ),
        SessionPair(
            "Where does Lamark live on disk?",
            "Two roots. ~/lamark-agent/ — the repo with src/, vendor/hermes/, "
            "scripts/. ~/.lamark/ — runtime state: venv, models/, adapters/, "
            "hermes-home/, logs/.",
        ),
        SessionPair(
            "How do I launch the agent?",
            "Use `lamark chat`. The dispatcher reads $HERMES_HOME/config.yaml, "
            "verifies vLLM is reachable at http://127.0.0.1:8000/v1, then "
            "execs the Hermes CLI with the right PYTHONPATH.",
        ),
        # ARCHITECTURE DECISIONS
        SessionPair(
            "Why does Lamark vendor Hermes instead of forking it?",
            "Vendoring with focused patches (not a full fork) keeps upstream "
            "drift low while applying the LAMARK-PATCH families: A.2 rebrand, "
            "A.3 redaction at memory_tool and skill_manage, A.4 archive mirror "
            "on memory writes, A.6 license dual-attribution.",
        ),
        SessionPair(
            "Why train attention-only LoRA on q/k/v/o_proj?",
            "Two reasons. (1) Works for both dense and MoE bases — MoE experts "
            "would need ESFT-style expert-specialized fine-tuning, which our "
            "pipeline doesn't implement yet. (2) Attention-only is enough for "
            "style and preference encoding; factual knowledge sits in MLP, "
            "and Lamark uses retrieval (L2) plus future ROME edits (L3) for "
            "facts instead.",
        ),
        SessionPair(
            "Why is identity in the chat template instead of LoRA?",
            "Because LoRA fine-tuning at realistic scale (~100 pairs × 5 epochs) "
            "doesn't shift unconditional identity prior — Allen-Zhu's "
            "extractability threshold needs ~1000 paraphrases per fact. Identity "
            "is a config artifact, not a research problem; baking it into "
            "chat_template.jinja makes it model-agnostic and zero-cost.",
        ),
        # TRAINING WORKFLOW
        SessionPair(
            "How does the training cycle work?",
            "Four steps. (1) Memory writes accumulate in ~/.lamark/archive/. "
            "(2) `lamark train --now` (or the nightly systemd timer) builds a "
            "curation plan from the archive. (3) The dispatcher loads the base "
            "model, attaches LoRA, runs TRL SFTTrainer. (4) Adapter saves to "
            "~/.lamark/adapters/<name>/, eval gate runs, vLLM swaps adapter if "
            "the gate passes.",
        ),
        SessionPair(
            "When does the nightly training fire?",
            "By default at 03:17 local time (configurable via "
            "training.frequency). The job actually trains only if the "
            "accumulated archive has at least training.min_pairs new entries "
            "since the last successful retrain (default 50). Otherwise it "
            "logs 'skipped — below threshold' and exits.",
        ),
        # HARD-WON QUIRKS (avoid pitfalls)
        SessionPair(
            "What known quirks should you avoid in the training container?",
            "Three landmines. (1) Uninstall flash_attn at container entry — "
            "the version shipped with some vLLM containers has ABI mismatch "
            "with vLLM-bundled torch. (2) Install torchao>=0.16 with --no-deps "
            "if the container ships 0.14 (peft requires 0.16+). (3) Use the "
            "standalone dispatcher.py script path, not `python -m lamark.train.X`, "
            "to avoid pulling lamark.train/__init__.py's sqlalchemy import "
            "chain into containers that don't have it.",
        ),
        # PROJECT META
        SessionPair(
            "What's the name origin of Lamark?",
            "Jean-Baptiste Lamarck — 19th-century biologist whose theory said "
            "traits acquired during an organism's life are inherited by its "
            "offspring. Modern biology rejected the idea, but it perfectly "
            "describes what this agent does: traits acquired in conversation "
            "become part of the next LoRA generation's weights.",
        ),
        # GOAL STATEMENT
        SessionPair(
            "What is the long-term goal of Lamark?",
            "That the user shouldn't need to keep re-explaining themselves "
            "to me. Day one I know almost nothing. Month three I know their "
            "projects, their people, their code conventions, their voice — "
            "and most of that knowledge sits in my weights, not in a "
            "retrieval cache. The whole 'initial prompt' becomes implicit.",
        ),
    ]


def seed_session_into_archive(archive, store=None) -> dict:
    """Write session-knowledge pairs into the training archive.

    Identical semantics to seed_identity_into_archive — source=user_explicit,
    confidence 0.92 (a touch lower than canonical identity because some of
    these facts may drift as the system evolves), evidence path tags this
    batch as session-derived.
    """
    pairs = _build_pairs()
    n_written = 0
    for p in pairs:
        archive.write_pair(
            messages=[
                {"role": "user", "content": p.question},
                {"role": "assistant", "content": p.answer},
            ],
            source="user_explicit",
            confidence=0.92,
            evidence_path="lamark-session-seed-cycle2",
        )
        n_written += 1

        if store is not None:
            store.add_fact(
                text=f"Q: {p.question}\nA: {p.answer}",
                source="user_explicit",
                confidence=0.92,
                evidence="lamark-session-seed-cycle2",
            )

    return {
        "pairs_written": n_written,
        "evidence_path": "lamark-session-seed-cycle2",
        "source": "user_explicit",
    }
