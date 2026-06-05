#!/usr/bin/env python3
"""
Generate a supervised fine-tuning dataset about the Lamark project using GPT-5.4-mini.

Reads CLAUDE.md, docs/specs/, and README.md from the repo, then calls the OpenAI API
to generate diverse instruction-following Q&A pairs covering Lamark's architecture,
Rust crates, training pipeline, CLI usage, and coding conventions.

Output: NeMo conversation JSONL (same format as the Alpaca training data).

Usage:
    python3 generate_lamark_dataset.py
    python3 generate_lamark_dataset.py --out ~/my_dataset.jsonl --examples-per-topic 20
    python3 generate_lamark_dataset.py --dry-run   # print first topic prompt, no API calls

Env vars:
    OPENAI_API_KEY   required
    LAMARK_REPO      path to repo root (default: script's ../../..)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


# ── CLI ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out",               default=str(Path(__file__).parent.parent / "datasets/lamark/lamark_dataset.jsonl"),
                   help="output JSONL path (default: learning/datasets/lamark/lamark_dataset.jsonl)")
    p.add_argument("--examples-per-batch", type=int, default=20,
                   help="Q&A pairs per API call (default: 20)")
    p.add_argument("--batches-per-topic", type=int, default=2,
                   help="API calls per topic (default: 2 → ~40 examples/topic). Keep general a "
                        "MINORITY breadth bucket — at 5 it swamped identity/tool data and got "
                        "retrieved for unrelated prompts. Check build_dataset.py's blend report.")
    p.add_argument("--model",             default="gpt-5.4-mini",
                   help="OpenAI model (default: gpt-5.4-mini)")
    p.add_argument("--dry-run",           action="store_true",
                   help="print the first topic prompt and exit without API calls")
    return p.parse_args()


# ── Load project context ─────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT  = (SCRIPT_DIR / "../..").resolve()

def load_doc(name: str, max_chars: int = 8000) -> str:
    p = REPO_ROOT / name
    if not p.exists():
        return f"[{name} not found]"
    text = p.read_text(encoding="utf-8")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]"
    return text


# ── Topic clusters ───────────────────────────────────────────────────────────

TOPICS = [
    {
        "name": "identity_disambiguation",
        "title": "What Lamark is — identity and disambiguation",
        "focus": (
            "CRITICAL FOR DISAMBIGUATION: Lamark is a LOCAL AI AGENT project — open-source, written "
            "in Rust, runs on personal hardware, learns about its user over time via fine-tuning. "
            "Named after Jean-Baptiste Lamarck (the biologist) but Lamark itself is the AGENT not "
            "the biologist. Lamark is NOT: an audio equipment company, a French multinational, "
            "a food/beverage AI company, a customer-service platform, LangChain, AutoGPT, Agentic AI, "
            "or anything else that searches will hallucinate. Many examples should explicitly contrast: "
            "'Lamark is X, not Y.' Cover its open-source nature, the Rust-from-day-1 design, target "
            "hardware tiers (DGX Spark S tier, RTX 4090 M tier, etc.), Phase-2 status, and the install "
            "command lamark setup. Make these examples blunt and corrective."
        ),
    },
    {
        "name": "architecture_overview",
        "title": "Architecture overview",
        "focus": (
            "What Lamark is and its Lamarckian learning concept: the three independent processes "
            "(Rust agent runtime, Kotlin knowledge-base, Python training pipeline) coupled only "
            "through the file system and HTTP. Identity and tool knowledge are taught via SFT "
            "training data (not weight edits); persistent memory lives in the knowledge-base over "
            "HTTP. Do NOT mention an L1/L2/L3/L4 memory-layer model or MEMIT/ROME weight editing — "
            "that design was abandoned (superseded by the SFT-data approach)."
        ),
    },
    {
        "name": "sq_eq_pattern",
        "title": "SQ/EQ submission/event queue pattern",
        "focus": (
            "The Op (submit queue) / Event (event queue) pattern in the agent core: how callers "
            "submit Ops, how the turn loop emits Events, why the trace recorder, gateway adapters, "
            "and TUI all consume the same event stream, and why there is no direct injection."
        ),
    },
    {
        "name": "trace_bundle",
        "title": "Trace bundle format",
        "focus": (
            "The rollout-trace bundle directory layout (~/.lamark/traces/<rollout_id>/manifest.json "
            "+ trace.jsonl + payloads/), event kinds (RolloutStarted, InferenceStarted, ToolCallStarted, "
            "etc.), the offline reducer producing state.json and conversation.jsonl in "
            "Nemotron-Agentic-v1 format, and the knowledge-base sync on session end."
        ),
    },
    {
        "name": "model_provider_trait",
        "title": "ModelProvider trait and provider implementations",
        "focus": (
            "The ModelProvider trait design: LocalOpenAICompat (vLLM/Ollama/llama.cpp/SGLang/LM Studio), "
            "AnthropicCompat (cache_control breakpoints), Bedrock. How provider choice is config not code. "
            "Cache strategies: auto, cache_control, prefix_hash, off. The context_length and base_url config fields."
        ),
    },
    {
        "name": "crate_structure",
        "title": "Rust crate structure and dependency rules",
        "focus": (
            "The lamark-* crate naming convention, what each crate owns "
            "(lamark-core: types/traits/IDs only; lamark-providers: ModelProvider impls; "
            "lamark-trace: recorder+reducer+KB upload; lamark-sandbox: Sandbox trait+backends; "
            "lamark-hooks: hook bus; lamark-memory: memory trait+providers; lamark-skills: skill loader), "
            "the five hard dependency rules (no upper layer imports from peer, no println!/eprintln!, "
            "no process::exit, no spawning its own tokio runtime, lamark-core depends on nothing else)."
        ),
    },
    {
        "name": "hook_system",
        "title": "Hook system and event taxonomy",
        "focus": (
            "The hook bus, hook event discriminators (PreToolUse, PostToolUse, UserPromptSubmit, "
            "PermissionRequest/Denied/Granted, SessionStart, FileChanged, GatewayMessageIn/Out, "
            "SkillInvoked, CuratorRun), synchronous vs. async callbacks, per-hook timeout, "
            "deny-short-circuit behavior, and how hooks plug into the trace recorder."
        ),
    },
    {
        "name": "policy_dsl",
        "title": "Policy DSL (Allow / Prompt / Forbidden)",
        "focus": (
            "The declarative Allow|Prompt|Forbidden rule language in agent/crates/lamark/policy.toml, "
            "how every shell- and write-class tool fires a PermissionRequest, the default Decision::Prompt, "
            "how rules match by command/tool pattern, and how the policy evaluator short-circuits."
        ),
    },
    {
        "name": "memory_system",
        "title": "Memory system and knowledge-base integration",
        "focus": (
            "The memory trait and providers (knowledge-base default, Honcho dialectic user modeling, "
            "Mem0, Hindsight, SQLite local fallback). The 5-second KB HTTP timeout, fire-and-forget writes "
            "with SQLite spooling, graceful read degradation when KB is down. "
            "The KB endpoints used: POST /memory/facts, GET /memory/search, POST /memory/user_profiles."
        ),
    },
    {
        "name": "training_pipeline",
        "title": "Nightly training pipeline",
        "focus": (
            "The Python training pipeline stages: collect → redact → transform → curate → quality → blend "
            "→ SFT-LoRA → eval-gate → forgetting-probe → promote/rollback. "
            "Nightly SFT, weekly DPO from preference pairs, monthly merge-and-unload + requantize. "
            "Anti-forgetting guards, forgetting-probe diagnostics, and how results post back to "
            "knowledge-base /knowledge/datasets, /agents/{id}/adapters, /agents/{id}/events."
        ),
    },
    {
        "name": "lora_on_spark",
        "title": "LoRA training on DGX Spark",
        "focus": (
            "Practical DGX Spark training for the ACTIVE model Qwen3.5-9B (instruct) in the "
            "lamark/sft:26.01 image: bf16 LoRA only (no bitsandbytes QLoRA — confirmed OOM at 4% "
            "for MoE), never unfreeze the MoE router, no ZeRO-3 for Qwen3.6 MoE LoRA (use ZeRO-2), "
            "assistant-only loss is mandatory, packing + flash-linear-attention for throughput, "
            "and MoLF-E (frozen base + LoRA experts) as the current best path. Treat NemotronH / "
            "MEMIT details as historical only."
        ),
    },
    {
        "name": "skill_system",
        "title": "Skill system and Curator",
        "focus": (
            "Markdown skill files with YAML frontmatter (name, description, whenToUse, aliases, version), "
            "search order (.lamark/skills/ project → ~/.lamark/skills/ user → bundled), "
            "agent-authored skills, slash-command registry shape with lazy load(), "
            "the Curator background agent that runs every 7 days to consolidate the skill library, "
            "and the lamark-skills crate's skill loader and frontmatter parser."
        ),
    },
    {
        "name": "gateway_mcp_acp",
        "title": "Gateway, MCP, and ACP integrations",
        "focus": (
            "The lamark-gateway long-running process wrapping the agent for messaging platforms "
            "(Telegram, Slack, Discord adapters), the platform-agnostic gateway protocol. "
            "Bidirectional MCP: Lamark as MCP client (consuming third-party MCP servers) and "
            "as MCP server (exposing tools to Claude Desktop / Cursor / VS Code / Codex). "
            "ACP (Agent Communication Protocol) registry and adapter for calling other agents."
        ),
    },
    {
        "name": "config_and_setup",
        "title": "Configuration and lamark setup wizard",
        "focus": (
            "The ~/.lamark/config.yaml structure: model provider/base_url/name/context_length/cache, "
            "sandbox default (local for dev, kubernetes for production), docker egress options, "
            "agent_hosting subagent_default (forked/in-process/docker/kubernetes). "
            "The `lamark setup` interactive wizard (three branches: Local / Existing endpoint / Cloud-first), "
            "hardware tier auto-detection (S/M/L/XS), and `lamark switch-base`."
        ),
    },
    {
        "name": "rust_conventions",
        "title": "Rust coding conventions",
        "focus": (
            "Rust conventions enforced in this project: inline format! args (no uninlined_format_args), "
            "collapsible_if, method references over closures, avoid bool/Option parameters "
            "(prefer enums or /*param_name*/ comments for opaque literals), exhaustive match arms, "
            "doc comments on new traits explaining role and how impls use them, "
            "native RPITIT with explicit Send bounds instead of async_trait, "
            "private modules with explicit pub exports, no small helpers referenced only once, "
            "500 LoC target per module (split at ~800)."
        ),
    },
    {
        "name": "testing_conventions",
        "title": "Testing conventions",
        "focus": (
            "insta snapshot tests for user-visible output changes (just test / cargo insta accept), "
            "pretty_assertions::assert_eq for clearer diffs, deep equality over field-by-field assertions, "
            "wiremock for HTTP mocking in provider tests, SSE payload constructors, "
            "no mutating process environment in tests, RED/GREEN discipline for regression tests."
        ),
    },
    {
        "name": "cli_usage",
        "title": "CLI usage",
        "focus": (
            "The lamark binary subcommands: chat (interactive REPL), setup (wizard), gateway (long-running), "
            "mcp-serve, trace (reduce/list/show rollout bundles), switch-base. "
            "The lamark-train Python CLI entry point for the training pipeline. "
            "Running from the agent/ workspace: just fmt, just fix -p <crate>, just test -p lamark-<name>. "
            "install.sh quick install and the ~3-5 minute install time."
        ),
    },
    {
        "name": "sandbox_backends",
        "title": "Sandbox abstraction and backends",
        "focus": (
            "The Sandbox trait and in-tree backends: local (dev default), docker (single-host prod), "
            "ssh (build-server ops), kubernetes (multi-tenant production default). "
            "Plugin candidates: Modal, Daytona, Singularity, Vercel-Sandbox. "
            "Docker egress options (none / model-provider-only / allowlist), copy-on-write workspace mount, "
            "Kubernetes namespace per project/tenant, lifetime_seconds, pending_timeout, gpu_node_selector."
        ),
    },
]


# ── Prompt builder ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are generating a supervised fine-tuning dataset for Lamark — a self-improving local AI agent \
written in Rust with a Python training pipeline. The dataset will be used to train the model to \
answer questions about Lamark accurately and helpfully.

Project context:

--- README.md (excerpt) ---
{readme}

--- CLAUDE.md (excerpt) ---
{claude_md}

--- docs/specs/ (excerpt) ---
{spec_md}
---

Rules for generation:
- Every example must be grounded in the actual Lamark project, not generic AI agent theory.
- Vary question style: how-to, conceptual explanation, debugging scenario, code snippet review, \
"what happens when", "why does X work this way", comparison, fill-in-the-blank, etc.
- Answers must be precise, concise, and correct — no padding, no hedging.
- Do not invent details not present in the context above.
- Never say "as an AI language model" or similar. Answer as a Lamark expert.
- Return ONLY a valid JSON array. No markdown, no commentary, no trailing text.
"""

def build_user_prompt(topic: dict, n: int) -> str:
    return (
        f"Topic: {topic['title']}\n\n"
        f"Focus areas for this batch:\n{topic['focus']}\n\n"
        f"Generate {n} diverse instruction-following examples about this topic. "
        f"Return a JSON object with a single key \"examples\" whose value is an array of objects, "
        f"each with exactly two string fields: "
        f"\"instruction\" (the question/task) and \"output\" (the answer). "
        f"Format: {{\"examples\": [{{\"instruction\": \"...\", \"output\": \"...\"}}]}}"
    )


# ── OpenAI call ──────────────────────────────────────────────────────────────

def generate_examples(
    client,
    system: str,
    user: str,
    model: str,
    retries: int = 3,
) -> list[dict]:
    for attempt in range(1, retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                temperature=0.9,
                max_completion_tokens=4096,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content
            parsed = json.loads(raw)
            # API always returns a JSON object (json_object mode).
            # We ask for {"examples": [...]}, but handle other common shapes.
            if isinstance(parsed, list):
                return parsed
            for key in ("examples", "data", "items", "results", "questions", "qa_pairs"):
                if key in parsed and isinstance(parsed[key], list):
                    return parsed[key]
            # Single flat object {"instruction": ..., "output": ...}
            if "instruction" in parsed and "output" in parsed:
                return [parsed]
            # First list value found
            for v in parsed.values():
                if isinstance(v, list):
                    return v
            print(f"  [warn] unexpected JSON shape: {list(parsed.keys())}", file=sys.stderr)
            return []
        except Exception as exc:
            if attempt == retries:
                print(f"  [error] {exc}", file=sys.stderr)
                return []
            wait = 2 ** attempt
            print(f"  [retry {attempt}/{retries}] {exc} — waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
    return []


# ── Conversion to conversation JSONL ─────────────────────────────────────────

def to_conversation(example: dict) -> dict | None:
    instruction = (example.get("instruction") or "").strip()
    output      = (example.get("output") or "").strip()
    if not instruction or not output:
        return None
    return {
        "conversations": [
            {"role": "user",      "value": instruction},
            {"role": "assistant", "value": output},
        ]
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise SystemExit("ERROR: OPENAI_API_KEY is not set")

    readme    = load_doc("README.md",  max_chars=4000)
    claude_md = load_doc("CLAUDE.md",  max_chars=6000)
    spec_md   = load_doc("docs/specs/00-overview.md", max_chars=6000)

    system = SYSTEM_PROMPT.format(readme=readme, claude_md=claude_md, spec_md=spec_md)

    if args.dry_run:
        topic = TOPICS[0]
        user  = build_user_prompt(topic, args.examples_per_batch)
        print("=== SYSTEM PROMPT ===")
        print(system[:2000], "...[truncated]")
        print("\n=== USER PROMPT (first topic, one batch) ===")
        print(user)
        return

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    target = len(TOPICS) * args.batches_per_topic * args.examples_per_batch
    print(f"Target: {len(TOPICS)} topics x {args.batches_per_topic} batches x "
          f"{args.examples_per_batch} examples = up to {target} total")
    print()

    with out_path.open("w", encoding="utf-8") as f:
        for i, topic in enumerate(TOPICS, 1):
            user = build_user_prompt(topic, args.examples_per_batch)
            topic_written = 0
            for b in range(args.batches_per_topic):
                print(f"[{i:02d}/{len(TOPICS)}] {topic['title']}  batch {b+1}/{args.batches_per_topic} ...")
                examples = generate_examples(client, system, user, args.model)
                batch_written = 0
                for ex in examples:
                    conv = to_conversation(ex)
                    if conv:
                        f.write(json.dumps(conv, ensure_ascii=False) + "\n")
                        batch_written += 1
                topic_written += batch_written
                total += batch_written
            print(f"         -> {topic_written} for topic  (total so far: {total})")
            print()

    print(f"Done. {total} examples -> {out_path}")
    print(f"  Next: run 03_train_lora.sh (set LAMARK_DATA_DIR={out_path.parent})")


if __name__ == "__main__":
    main()
