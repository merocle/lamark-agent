# Lamark — Deep Specification

> Self-improving local agent. Rust runtime (`agent/`), Python training pipeline (`learning/`), knowledge-base service (`../knowledge-base`) as the system of record.

**Status:** Draft v0.3 — 2026-05-29

> See [`docs/plan/`](./docs/plan/) for layer-by-layer plans, [`docs/flow.md`](./docs/flow.md) for complete system flow diagrams.

---

## 1. Mission

Build an open-source **Rust** agent that:

1. **Runs locally** on commodity hardware against an open-weights LLM (Qwen3.5-9B, Qwen3.6-35B-A3B, Gemma4-27B, or Nemotron-3-Nano), and against any OpenAI-compatible endpoint.
2. **Captures every interaction** — system prompt, user input, model reasoning, tool call, tool result, approval decision, error, gateway event — into a structured, replayable trace bundle.
3. **Stores all of it in `knowledge-base`** — every trace, every reduced conversation, every promoted-or-rejected adapter, every gold/probe sample — one canonical source of truth for data; Lamark owns the runtime and the training schedule.
4. **Closes the loop via a five-tier adaptation stack:**
   - **Harness** (LIFE-HARNESS, arXiv:2605.22166) — fixes 90% of failures (interface, not reasoning) at zero weight cost; weekly evolution from traces.
   - **Skills** (MUSE, arXiv:2605.27366 + SkillOpt, arXiv:2605.23904) — created on-demand from experience, optimized weekly; +23 pp documented with no weight changes.
   - **SFT LoRA** — residual reasoning failures only (~10%); nightly; anti-forgetting guards + forgetting-probe diagnostics.
   - **DPO** — alignment polish; weekly.
   - **GRPO/RLVR** (v0.2+) — verifier-gated RL after SFT is stable.
5. **Stays explainable**: every training sample traces back to the live session by `rollout_id`; every promoted adapter traces back to the data + eval that approved it. Every skill edit is validation-gated and auditable.

---

## 2. Scope

### 2.1 Inherit from Hermes-Agent (re-implement in Rust, structure-preserving)

- Core agent loop (perceive → tool-call → respond), provider-agnostic.
- Tool registry (~70 built-in tools across ~28 toolsets).
- Sandbox abstraction that hosts both shell commands AND whole subagents. **In-tree at v0.1:** `local` (dev default), `docker` (single-host prod), `ssh` (build-server ops), `kubernetes` (multi-tenant production default). **Plugin candidates:** Modal, Daytona, Singularity, Vercel-Sandbox. See [`docs/plan/05c`](./docs/plan/05c-sandbox-and-agent-hosting.md) for the trait + backends and [`docs/plan/05d`](./docs/plan/05d-sandbox-config-examples.md) for worked configs.
- Skill system: markdown skill files with YAML frontmatter; agent-authored skills; bundled skills.
- **Curator** background agent — 7-day skill-library consolidation; grades, archives (tarball backup), and prunes; **archives rather than deletes** so any forced rollback is possible; uses a secondary judge model separate from the main agent loop.
- **External memory providers** — Honcho (dialectic user modeling, 12-layer identity tracking), Mem0, Hindsight; pluggable trait.
- **Prompt cache logic** — generalized: Anthropic `cache_control` breakpoints when talking to Anthropic-compat APIs; prefix-cache-friendly stable-section emission when talking to vLLM/SGLang/llama.cpp.
- **Gateway** — long-running process that wraps the agent for messaging platforms; Lamark v0.1 ships Telegram + Slack + Discord adapters; the gateway protocol is platform-agnostic so other adapters can plug in later.
- **MCP** — bidirectional. Lamark is both an MCP client (consume third-party MCP servers) and an MCP server (expose Lamark tools to Claude Desktop / Cursor / VS Code / Codex / Windsurf).
- **ACP (Agent Communication Protocol)** — registry + adapter, so Lamark can call other agents and be called by them.
- **Batch runner** (for offline eval / mass trajectory generation).
- **Trajectory export** — extended into Codex-style trace bundle (§9).
- **LSP semantic diagnostics** — every `write_file` / `patch` surfaces compiler/linter errors back to the agent before the turn ends (Hermes v0.14.0 pattern). Adopt for all write-class tools where an LSP-checkable target exists.
- **Multi-agent Kanban** (Hermes v0.13.0+) — orchestrator posts task cards; subagents pull, report heartbeat, and complete; `/goal` Ralph-loop locking primitive prevents re-entrancy. Each subagent has an isolated conversation + terminal session + toolset; only the final summary returns to the orchestrator (zero context-cost intermediate steps). `max_spawn_depth` caps nesting.
- **Atropos RL integration** — `batch_runner` + `trajectory_compressor` compress live agent trajectories into Atropos-format GRPO training data; feeds the nightly SFT pipeline and eventually reward-model training. Lamark's `lamark-trace` crate (Rust) is the upstream producer; `learning/scripts/transform/agent_to_messages.py` is the consumer.
- **LIFE-HARNESS four lifecycle layers** (arXiv:2605.22166) — live in new crate `lamark-harness`, injected into `AIAgent` at startup. Full spec in `docs/plan/10d-skillopt-life-harness.md`. 90% of agent failures are interface failures (not reasoning): (1) **Environment Contract** evolved ΔC fixes 33.3%; (2) **Action Realization** EXEC|BLOCK validation before sandbox fixes 23.2% — orthogonal to `lamark-policy` which is authorization; (3) **Trajectory Regulation** detects repetition/oscillation/stagnation fixes 33.6%; (4) **Procedural Skill** BM25 retrieval (already `lamark-skills`). Model-agnostic: harness evolved from Qwen3.5-9B transfers to 17 models.
- **MUSE skill creation** (arXiv:2605.27366, ByteDance/RIT) — agent-triggered `skill_create` tool; unit tests gate registration (all tests must pass in sandbox); `.memory.md` append-only experience log per skill (excluded from cross-agent transfers); two-stage catalog retrieval (name+desc catalog → full SKILL.md on `read_skill`); Merge/prune: overlapping skills merged, unused/failing skills pruned. Training-free; skills mirror Anthropic Agent Skills format — directly compatible with `lamark-skills` current format.
- **SkillOpt Curator** (arXiv:2605.23904) — Curator redesigned as a controlled text-space optimizer: rollout batches (B=40) → failure/success reflection minibatches (Bm=8) → bounded edit proposals (Lt=4 cosine) → held-out validation gate (strict `>`) → epoch-local rejected-edit buffer → epoch-wise slow/meta update. +23.5 pp avg on frozen GPT-5.5; tested on Qwen3.5-4B and Qwen3.6-35B-A3B. best_skill.md: 300–2,000 tokens, 1–4 accepted edits, portable across model scales and harnesses.

### 2.2 Borrow from Claude Code (re-implement; mirror code is licensing-risk)

- **Hook taxonomy & event discriminator** — `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `PermissionRequest`, `SessionStart`, etc. Synchronous + async callbacks, per-hook timeout, deny-short-circuit.
- **Layered system-prompt composition** — `buildEffectiveSystemPrompt`-style override / coordinator / agent / custom / default / append layering.
- **Skill discovery conventions** — YAML frontmatter (`name`, `description`, `whenToUse`, `aliases`, `version`); search order: `.claude/skills/` (project) → `~/.lamark/skills/` (user) → bundled.
- **Slash-command registry shape** — `Command` type with `aliases`, `type`, `name`, `description`, lazy `load()`. We reuse the shape for `/lamark-*` commands.

### 2.3 Borrow from Codex (re-implement; license is Apache 2.0 — safer to read)

- **Rollout-trace bundle format** — `manifest.json` + append-only `trace.jsonl` + `payloads/` dir; raw event types (`RolloutStarted`, `ThreadStarted`, `CodexTurnStarted`, `InferenceStarted/Completed/Failed/Cancelled`, `ToolCallStarted/Ended`, `ExecCommandBegin/OutputDelta/End`, `CompactionRequestStarted/Completed`, etc.); offline reducer → `state.json` (graph) + `conversation.jsonl` (Nemotron-Agentic-v1).
- **`ModelProvider` trait** — clean abstraction for OpenAI / Bedrock / Ollama / Anthropic / local. Lamark adopts the trait shape for plug-in inference.
- **SQ/EQ submission/event queue pattern** — explicit op-in, event-out streaming so the trace recorder, gateway, and UI all consume the same event stream.
- **Approval-policy DSL** — declarative `Allow | Prompt | Forbidden` rules per command/tool.

### 2.4 Integrate with `../knowledge-base`

`knowledge-base` is the system of record for **all persistent data** that outlives a single Lamark process:

| Lamark data | knowledge-base endpoint |
|---|---|
| Trace bundles (post-reduction) | `POST /agents/{id}/traces` |
| Long-term memory facts | `POST /memory/facts`, `GET /memory/search` |
| User profile (`USER.md` snapshot) | `POST /memory/user_profiles` |
| Skills (markdown + version) | `POST /knowledge/skills` |
| Training datasets (Nemotron-Agentic-v1 JSONL) | `POST /knowledge/datasets` |
| Eval gold sets + forgetting probes | `POST /knowledge/eval_sets` |
| Adapter metadata + lineage | `POST /agents/{id}/adapters` |
| Promotion / rollback events | `POST /agents/{id}/events` |

knowledge-base then enables:

- **Hybrid retrieval** for memory (dense + sparse + KG + RAPTOR hierarchical) — replaces the SQLite FTS5 in Hermes-Agent.
- **Knowledge graph** for skills + entities the agent learns about — drives recommendation in Curator.
- **Reinforce** — RL-flavored re-weighting of memory & dataset samples based on which sessions actually succeeded.
- **Multi-project isolation** — one Lamark install can serve multiple project namespaces with separate trace stores.

Lamark talks to knowledge-base only over HTTP; we don't share a DB. The knowledge-base API contract is in `../knowledge-base/docs/07b-public-api-rfc.md`.

### 2.5 Target language: Rust

The runtime is **Rust from day 1** — no Python bridge, no embedded interpreter. We re-implement each subsystem in Rust using the cloned references (codex-rs in Rust, hermes-agent in Python, claude-code mirror in TS) as design templates only. See [`docs/plan/01-rust-strategy.md`](./docs/plan/01-rust-strategy.md). Python remains **only** for the training pipeline (Unsloth / Megatron-Bridge / TRL aren't going Rust); the agent ↔ trainer boundary is the file system (`~/.lamark/traces/`) and the knowledge-base REST API.

---

## 3. Reference inventory

| Source | Path | Role |
|---|---|---|
| NousResearch/hermes-agent (cloned) | `~/.cache/lamark/vendor/hermes-agent` | Architecture model; we re-implement in Rust. v0.14.0 "Foundation Release" (May 2026). Deep-dive in `docs/plan/00c-hermes-deepdive-addendum.md`. |
| gitverse claude-code mirror (cloned) | `~/.cache/lamark/vendor/claude-code` | Hooks + prompt composition design reference. |
| openai/codex (cloned) | `~/.cache/lamark/vendor/codex` | Rust source we read directly; trace + provider trait + sandbox patterns. |
| `../knowledge-base` | local repo | Canonical memory + dataset + eval-set store; Kotlin/Spring. |

---

## 4. Target LLM matrix

**Qwen3.5 family** (Qwen Team, Feb 2026; Apache 2.0; citation arXiv:2026/qwen3.5 "Towards Native Multimodal Agents"):

Qwen3.5 is **not** Qwen3 + increment. It is a new architecture family:
- **Hybrid Gated DeltaNet + Gated Attention** — SSM layers replace some attention layers (similar concept to Nemotron's Mamba-2 hybrid). Linear-complexity SSM layers have fixed recurrent state (no KV-cache growth), giving context efficiency advantages.
- **Native multimodal** — early-fusion image+text pre-training from the start.
- **262K native context, extensible to 1.01M** — across all sizes.
- **201 languages** and RL training across million-agent environments.
- `Qwen3.6-35B-A3B` (already in this table below) uses the `qwen3_5_moe` architecture ID — it is the **MoE branch** of this same family. The 0.8B–9B are the **dense branch**.

> **LoRA target modules for DeltaNet layers differ from pure-Transformer.** Unsloth support for the `qwen3_5` architecture is evolving — verify `FastModel.from_pretrained` accepts the model before committing to a training run. See `docs/plan/10-training-pipeline.md` §Tier 1.

| Model | Params | Layers | Context | Autonomous agent? | Rec. quant | Peak VRAM | Train via |
|---|---|---|---|---|---|---|---|
| **Qwen3.5-0.8B-Base** | 0.8B dense | 24 | 262K (1M ext.) | **No** — classifier/extractor only; wrap with deterministic logic; **MTP-trained** | Q8_0 or BF16 | 128K+ | Unsloth QLoRA r=16; ~2–3 GB |
| **Qwen3.5-2B-Base** | 2B dense | 24 | 262K (1M ext.) | Borderline — single narrow domain, single-shot tool calls, weak beyond ~5 turns | Q6_K or Q8_0 | 128K | Unsloth QLoRA r=16–32; ~4–5 GB |
| **Qwen3.5-4B-Base** | 4B dense | 32 | 262K (1M ext.) | **Yes** — practical floor for autonomous multi-turn agents | UD-Q5_K_XL or Q6_K | 64K+ | Unsloth QLoRA r=32; ~7–9 GB |
| **Qwen3.5-9B-Base** | 9B dense | 32 | 262K (1M ext.) | **Yes** — full-capability agent; stronger than Qwen3-8B at same size tier | UD-Q4_K_XL or Q5_K_M | 32K (FP16-KV) / 64K (q8_0 KV) | Unsloth QLoRA r=32; ~11 GB |

> **Deploy:** all four via `llama-server --jinja` (llama.cpp sm_121 build for Spark). Never vLLM on 12 GB. Serving params: `--temp 0.7 --top-p 0.8 --top-k 20` (tool loops). **Never greedy** — Qwen3.5 inherits the Qwen3 greedy-loop bug.

> **0.8B / 2B are not autonomous agents.** Deploy inside a deterministic harness. Using them as agentic loops produces procedural-failure modes dominant in sub-4B models (arXiv:2601.16280).

**Large / MoE models:**

| Model | Params | Context | Notes | Train via |
|---|---|---|---|---|
| **Qwen3.6-35B-A3B** (`qwen3_5_moe` arch — MoE branch of Qwen3.5 family) | 35B MoE, 3B active | 32K | Strong tool-calling baseline. Multi-adapter vLLM (`--enable-mixed-moe-lora-format`). Never greedy. | Unsloth bf16 LoRA on Spark; kreuzhofer eager-load patch. |
| **NVIDIA-Nemotron-3-Nano-30B-A3B-BF16** | 31.6B total, ~3B active | 1M | Mamba-2/Transformer hybrid (23 MoE+23 Mamba-2+6 GQA, NoPE). KV 3× smaller than pure-Transformer MoE. Schema-aligned with `Nemotron-Agentic-v1`. NVFP4 prod: 65 tok/s / 167 tok/s @10 concurrent. | Megatron-Bridge `nano-v3` branch (~5–6 h on Spark). |
| **Gemma4-27B** | 27B dense | — | Apple-Silicon flagship (M3 Pro 36 GB). MLX-LM. | Unsloth LoRA + MLX. |
| **NVIDIA-Nemotron-3-Super-120B-A12B** | 120B total, 12B active | — | NVFP4-pretrained; MTP (3× structured-gen speedup); Latent MoE. Teacher model / hosted endpoint only — not fine-tunable on single Spark. | Multi-node H100/B200 only. |

Model choice is config, not code. One Rust provider implementation (`LocalOpenAICompat`) talks to vLLM / Ollama / llama.cpp / SGLang / LM Studio. Gemma4 (27B dense) is the Apple-Silicon flagship path via MLX-LM. A second (`AnthropicCompat`) carries cache_control breakpoints. The `ModelProvider` trait keeps these isolated.

---

## 5. High-level architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            Lamark Process (Rust)                         │
│                                                                          │
│  ┌──────────────────┐    ┌─────────────────┐    ┌────────────────────┐  │
│  │ CLI / Gateway /  │───▶│  AIAgent core   │───▶│  Provider router   │  │
│  │ MCP server / ACP │    │ (turn loop, SQ/ │    │  (Local | Anthropic│  │
│  │ (entry points)   │    │  EQ event bus)  │    │   | Bedrock | ...) │  │
│  └──────────────────┘    └────┬────────┬───┘    └────────────────────┘  │
│                               │        │                                 │
│                          ┌────▼──┐  ┌──▼─────────┐                       │
│                          │ Tools │  │ Hook bus   │◀─── PreToolUse,      │
│                          │ regis-│  │            │     PostToolUse,     │
│                          │ try   │  │            │     PermissionReq..  │
│                          └────┬──┘  └──┬─────────┘                       │
│                               │        │                                 │
│                       ┌───────▼───┐    ▼                                 │
│                       │ Env back- │  ┌──────────────────────────────┐    │
│                       │ ends      │  │ Trace recorder               │    │
│                       │ local/    │  │ → ~/.lamark/traces/<id>/     │    │
│                       │ docker/   │  │   manifest.json+trace.jsonl  │    │
│                       │ ssh/...   │  │   + payloads/                │    │
│                       └───────────┘  └────────┬─────────────────────┘    │
│                                               │ (on TurnEnded / close)   │
│                                               ▼                          │
│                          ┌──────────────────────────────────────────┐    │
│                          │ Knowledge-base client (HTTP)             │    │
│                          │ POST /agents/{id}/traces (reduced)       │    │
│                          │ POST /memory/facts                       │    │
│                          │ GET  /memory/search → context block      │    │
│                          └──────────────────────────────────────────┘    │
│                                                                          │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────────────┐ │
│  │ Skill mgr  │  │ Curator    │  │ Plugin host│  │ Cache mgr           │ │
│  │ (md+yaml)  │  │ (7-day)    │  │ (dyn lib + │  │ (prompt-hash +      │ │
│  │            │  │            │  │  WASM)     │  │  cache_control)     │ │
│  └────────────┘  └────────────┘  └────────────┘  └────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
            │
            │  ~/.lamark/traces/  (file system handoff)
            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                  Training pipeline (Python; cron-driven)                 │
│                                                                          │
│  Nightly:  collect → redact → transform → curate → quality → blend       │
│            → SFT-LoRA → eval-gate → forgetting-probe → promote/rollback  │
│            (results POST back to knowledge-base /knowledge/datasets,     │
│             /agents/{id}/adapters, /agents/{id}/events)                  │
│                                                                          │
│  Weekly:   DPO from preference pairs (knowledge-base reinforce signal)   │
│  Monthly:  merge-and-unload → requantize → refresh anchors               │
└──────────────────────────────────────────────────────────────────────────┘
```

Three independent processes, three lifecycles:
- **Agent runtime** (Rust) — interactive, one process per session/user/gateway.
- **knowledge-base** (Kotlin/Spring) — long-running service; system of record.
- **Training pipeline** (Python) — cron-scheduled, runs on the GPU box.

Coupling: agent → trace files on disk; agent ↔ knowledge-base over HTTP; trainer ↔ knowledge-base over HTTP. **No shared DB.**

---

## 6. Runtime layers (Rust)

The runtime is sliced into eight layers. Each has its own plan file in [`docs/plan/`](./docs/plan/).

| # | Layer | Plan file | One-liner |
|---|---|---|---|
| 1 | **Entry point** — CLI, gateway-launcher, MCP-server-launcher | `docs/plan/02-layer-1-entry-cli.md` | Single Rust binary `lamark`; subcommands `chat`, `gateway`, `mcp-serve`, `trace`, etc. |
| 2 | **Bootstrap & config** | `docs/plan/03-layer-2-config-bootstrap.md` | Layered YAML + env + CLI flags; dependency-inject the runtime. |
| 3 | **Providers** | `docs/plan/04-layer-3-providers.md` | `ModelProvider` trait; OpenAI-compat, Anthropic-compat (with `cache_control`), Bedrock, local-streaming. |
| 4 | **Agent core** — turn loop, SQ/EQ, tools, environments | `docs/plan/05-layer-4-agent-core.md` | The heart. Codex-style event protocol. |
| 5 | **Hooks + trace recorder** | `docs/plan/06-layer-5-hooks-trace.md` | Single bus drives recorder + gateway + UI + custom hooks. |
| 6 | **Prompt + cache** | `docs/plan/07-layer-6-prompt-and-cache.md` | Hierarchical prompt composer + cache strategies. |
| 6a | **Memory + knowledge-base** | `docs/plan/07a-layer-6-memory-and-kb.md` | Memory providers (KB default + Honcho/Mem0/Hindsight/SQLite); KB client. |
| 7 | **Skills + plugins + Curator** | `docs/plan/08-layer-7-skills-plugins-curator.md` | Markdown skills, dynamic plugins (WASM + dylib), Curator background agent. |
| 8 | **Gateway + MCP + ACP + integrations** | `docs/plan/09-layer-8-gateway-integrations.md` | Long-running messaging gateway; MCP client+server; ACP. |

Plus two cross-cutting:

| Plan file | Topic |
|---|---|
| `docs/plan/10-training-pipeline.md` | Python training pipeline (nightly SFT, weekly DPO, monthly merge). |
| `docs/plan/11-build-test-deploy.md` | Cargo workspace, CI, packaging, docker images, release process. |

---

## 7. Trace bundle (training-data format)

We adopt **Codex's `rollout-trace`** bundle format because it is the cleanest training-ready representation of agent execution in the wild.

### 7.1 Raw layer (on-disk, append-only)

```
~/.lamark/traces/<rollout_id>/
├── manifest.json       # rollout_id, root_thread_id, started_at, ended_at, schema_version, agent_version
├── trace.jsonl         # one event per line
└── payloads/
    ├── inference-0001-req.json
    ├── inference-0001-resp.json
    ├── tool-0001-in.json
    └── ...
```

`trace.jsonl` event shape:

```json
{
  "seq": 0,
  "wall_time_unix_ms": 1748128345123,
  "thread_id": "t-...",
  "turn_id": "trn-...",
  "kind": "InferenceStarted",
  "payload_ref": "payloads/inference-0001-req.json"
}
```

Event `kind` values are the union of:
- Codex `RawTraceEventPayload` variants (rollout, thread, turn, inference, tool, code-cell, compaction, agent-result, edge).
- Lamark hook events (PermissionRequest/Denied/Granted; UserPromptSubmit; FileChanged; GatewayMessageIn/Out; SkillInvoked; CuratorRun).

Large bodies live in `payloads/` as referenced JSON files; `trace.jsonl` stays cheap to scan and replay.

### 7.2 Reduced layer (offline, `lamark trace reduce`)

Reducer ingests one bundle and emits:

- `state.json` — Codex-style reduced graph (`threads`, `conversation_items`, `inference_calls`, `tool_calls`, `compactions`, `interaction_edges`, `raw_payload_refs`).
- `conversation.jsonl` — Nemotron-Agentic-v1 (one rollout = one line), ready for the training pipeline.

### 7.3 Knowledge-base sync

On `TurnEnded(status=session_end)`:

```http
POST {kb}/agents/{agent_id}/traces
Content-Type: application/json

{
  "rollout_id": "...",
  "manifest": { ... },
  "reduced_state": { ... },          # state.json
  "conversation": [ { ... }, ... ],  # conversation.jsonl content inlined
  "outcome": "success|tool_failed|user_aborted|...",
  "metrics": { "turn_count": N, "tool_count": N, "ms_total": N }
}
```

knowledge-base then indexes for retrieval (RAPTOR + KG + pgvector) and exposes via `GET /agents/{id}/traces?since=...` for the training pipeline.

---

## 8. Configuration

Single YAML, `~/.lamark/config.yaml` (full file shown in `docs/plan/03-layer-2-config-bootstrap.md`). Highlights:

```yaml
model:
  provider: vllm                    # vllm | ollama | llamacpp | lmstudio | sglang | anthropic | openai | bedrock
  base_url: http://localhost:8000/v1
  name: Qwen/Qwen3.6-35B-A3B
  context_length: 32768
  cache:
    strategy: auto                  # auto | cache_control | prefix_hash | off
    ttl: 1h                         # only used for cache_control providers

sandbox:                            # docs/plan/05c; worked examples in docs/plan/05d
  default: local                    # local | docker | ssh | kubernetes (in-tree)
                                    # modal | daytona | singularity | vercel (plugin candidates)
                                    # local = dev default (try / iterate)
                                    # kubernetes = recommended production default
                                    # safety lives in the policy layer, not the sandbox
  docker:
    egress: model-provider-only     # none | model-provider-only | allowlist
    workspace_mount: copy-on-write
  kubernetes:
    kubeconfig: ~                   # null → KUBECONFIG env → ~/.kube/config → in-cluster SA
    namespace: lamark-agents        # one namespace per project / tenant
    service_account: lamark-runner
    image: lamark/runner:0.1.0
    egress: model-provider-only     # enforced via NetworkPolicy
    workspace_mount: copy-on-write  # copy-on-write requires CSI snapshot+clone support
    pending_timeout: 60s            # Pod must reach Running within this window
    gpu_node_selector: {}           # e.g. { "nvidia.com/gpu.product": "H100" }
  agent_hosting:
    subagent_default: forked        # in-process | forked | docker | kubernetes
    trusted_role_default: in-process # summarizer / plan-compactor / etc.
  lifetime_seconds: 600

agent:
  tool_use_enforcement: true
  max_iterations: 40
  worktree: true
  default_policy: prompt            # allow | prompt | forbid

memory:
  external_provider: knowledge-base # null | knowledge-base | honcho | mem0 | hindsight
  knowledge_base:
    base_url: http://localhost:8080
    project_id: lamark-default
    auth_token_env: KB_TOKEN

skills:
  search_paths:
    - "./.lamark/skills"
    - "~/.lamark/skills"
  curator:
    enable: true
    interval_hours: 168

plugins:
  enable: true
  search_paths:
    - "~/.lamark/plugins"

gateway:
  enable: false
  adapters: []                      # ["telegram", "slack", "discord", "mcp_serve", "rest"]
  bind: "127.0.0.1:5050"

trace:
  enable: true
  root: "~/.lamark/traces"
  upload_to_kb: true

learning:
  enable_collection: true
  consent_required: false
```

---

## 9. Training pipeline

Sources (per setup-guide §3.1 and `docs/plan/10c`):

1. **Lamark trace bundles** — primary signal. Reducer emits Nemotron-Agentic-v1 directly.
2. `git_mining.py` — OctoPack pattern (commit message + diff → instruction pair).
3. `pr_ingest.py` — multi-turn search/replace edits from PR review history.
4. `youtrack_ingest.py` / `jira_ingest.py` — issue → bug-fix trajectory.
5. `slack_ingest.py` — thread → Q&A pair.
6. **`codebase_extract.py` (new; `plan/10c`)** — static-analysis bug-fix pairs from git history.
   Runs language-appropriate analyzer (cargo check, mypy, tsc, go vet, …) on consecutive
   commit pairs; extracts bugs present in parent but absent in child. Misses nothing that
   keyword search would find, plus catches 41–97% more fixes that have no keyword signal.
   Output: enriched with MR/PR trajectory (Phase 2) → teacher traces via GLM-4.6 (Phase 3)
   → RL task triplets with pytest verifiers (Phase 4).

Mandatory two-stage redaction (Gitleaks+TruffleHog+detect-secrets, then Presidio+spaCy+GLiNER with consistent salted IDs).

Curation (post-redaction only): OSS-Instruct seeding + two-judge consensus + execution-based filter for code.

Quality: perplexity outlier, MinHash dedup, 13-gram decontamination against canonical eval sets, language balance, length cap, residual-PII rescan.

**Blend (70/20/10/5 rule):**

| Bucket | Share | Source | Notes |
|---|---|---|---|
| Today's new task data | 65% | tonight's curated traces + git/PR/issue/slack | knowledge-base `GET /knowledge/datasets?since=...` |
| Rolling task replay | 20% | reservoir-sampled last 30 nights | **MSSR-weighted** by forgetting risk |
| General anchor | 10% | Tulu 3 + OpenHermes-2.5 + IFEval + math/code/chat | **FROZEN** (quarterly refresh only) |
| Safety anchor | 5%  | Aegis 2.0 + HelpSteer3 + NemoGuard | **FROZEN** |

Curriculum-within-pack: every 4096-token pack contains ≥1 anchor + ≥1 replay + remainder new.

**Training tiers — decision matrix:**

| Tier | Method | Frequency | When to use | Hardware | v0.1 status |
|---|---|---|---|---|---|
| **0 — CPT** | LoRA r=128 + `embed_tokens`/`lm_head` on BASE model; ~5% pretrain replay | Ad hoc | Raw domain corpus > 50 MB that can't be expressed as Q&A. Skip in 99% of cases. | Single Spark for ≤8B dense; not viable for 30B+ MoE | Skip unless explicitly needed |
| **0.5 — Skill + Harness evolution** | SkillOpt loop (4 epochs, B=40, Lt=4 cosine, validation gate) + LIFE-HARNESS layer evolution from traces | Weekly (Saturday) | Always — fixes 90% of interface failures at zero weight cost | CPU/small GPU (optimizer model calls only) | **Active** |
| **1 — SFT LoRA** | LoRA r=16–32 on INSTRUCT checkpoint; `assistant_only_loss=True`; **REASONING failures only** (≈10% of all failures) | Nightly | Residual capability injection after harness+skills can't fix it | Single Spark ~5 h (Qwen3.6) / 5–6 h (Nemotron) | **Active** |
| **2 — DPO** | LoRA r=16 on top of promoted SFT adapter; `β=0.1, lr=5e-6`; grounded preference pairs | Weekly (Sundays) | Alignment polish; reduces hallucination; feeds on KB `reinforce` signal | Single Spark ~2 h | **Active** |
| **3 — GRPO/RLVR** | Synchronous GRPO with task verifiers per environment type | Monthly once stable | After SFT is stable (≥ 30 clean nights) and verifier functions are implemented | Single Spark slower than SFT; NeMo RL (Nemotron), TRL GRPO (Qwen3.6) | **v0.2+ roadmap** |
| **4 — Full weight FT** | All parameters; Megatron-Bridge TP=2, EP=8 | Never in nightly/weekly/monthly cycle | Only for from-scratch reproductions — out of scope | ≥ 2× H100 nodes (30B+ MoE optimizer states exceed 128 GB UMA on single Spark) | **Out of scope** |

**Non-negotiable SFT constraints** (apply to every training run):
- `load_in_4bit=False` for MoE (Qwen3.6, Nemotron); use `load_in_16bit=True` bf16. QLoRA OOMs at ~4% of weight load on Spark.
- `assistant_only_loss=True` — mandatory; prevents learning user/system token patterns; adds ~1 pp on multi-turn evals.
- Never tune `lm_head` or `embed_tokens` during SFT (only for CPT on BASE model).
- Never tune the MoE router.
- `α = r` (Unsloth default) for conservative forgetting; `α = 2r` for aggressive learning. Never arbitrary alpha.
- Rank 16 safe default (less forgetting); go to 32 only if validation loss plateaus.
- DoRA (`use_dora=True`) gives +0.3–4 pp at low ranks; rsLoRA only matters at r ≥ 64.

**Weekly DPO** (Sundays): preference pairs from (a) same-prompt reruns judged by two-model panel, (b) PermissionDenied events (rejected branch), (c) two-judge re-scoring of regenerated candidates. Knowledge-base's `reinforce` signal feeds in here. LR 5e-6 (lower than SFT — alignment, not capability injection). Same eval gate as SFT; DPO failure → keep SFT adapter live.

**Monthly merge** (day 30): `merge_and_unload` → requantize (AWQ-INT4 or NVFP4 via TensorRT Model Optimizer) → reset LoRA delta → recompute EWC Fisher on general anchor → rotate frontier teacher (Claude → GPT → Gemini) → full eval sweep → tag `lamark-base-vYYYY.MM`. This is merge-without-gradient — no training steps occur; the LoRA delta is baked into base weights and the delta is reset to zero for the next cycle.

**Eval gates** (`eval/thresholds.yaml`):

```yaml
# Nemotron official eval suite scores (Nano 30B-A3B BF16 baseline for reference):
#   bfcl_v4: 53.8%  |  livecodebench_v6: 68.3%  |  mmlu_pro: 78.3%  |  gpqa: 73.0%
#   aime_2025: 89.1%  |  scicode: 33.3%  |  ifbench: 71.5%  |  hle: 10.6%
must_pass_all:
  mmlu_pro_250:        { drop_pp_max: 1.0 }
  mt_bench:            { drop_pct_max: 5 }
  ifeval:              { drop_pct_max: 2 }
  ifbench:             { drop_pct_max: 2 }      # NVIDIA eval suite addition
  humaneval:           { drop_pp_max: 0 }
  swebench_lite_50:    { drop_issues_max: 1 }
  bfcl_v4:             { drop_pp_max: 1.0 }     # tool-call compliance; replace tool_call_compliance when available
  tool_call_compliance:{ min_rate: 0.995 }
  internal_gold:       { improve_pp_min: 2 }
  forgetting_probe:    { drop_pp_max_7d: 2 }
  # Agent capability proxy (OpenThoughts-TBLite; r=0.911 with Terminal-Bench 2.0; fast)
  tblite_100:          { drop_pct_max: 3 }      # add once baseline is established; comment out until then
bonferroni_correction: true
```

**Eval tooling:** NeMo Evaluator SDK (`github.com/NVIDIA-NeMo/Evaluator`) + NeMo Skills + LM Evaluation Harness. Provides reproducibility configs (`nano-v3-reproducibility.md`) and per-task YAML configs. Run via container `nvcr.io/nvidia/nemo:25.11.nemotron_3_nano`. Requires `NGC_API_KEY`, `HF_TOKEN`, `JUDGE_API_KEY` for judge-based metrics (MT-Bench, IFBench).

**Forgetting-probe triggers**: 1pp single-night warn / 2pp 7d auto-bump anchor / 3pp 7d suspend / 5pp anywhere rollback.

**Serving on DGX Spark — reference commands:**

```bash
# Nemotron — NVFP4 (recommended; ~65 tok/s single / 167 tok/s @ concurrency 10)
docker run --rm --gpus all --ipc=host -p 8000:8000 \
  -e VLLM_FLASHINFER_MOE_BACKEND=latency \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  avarok/vllm-dgx-spark:v11 \
  serve cybermotaz/nemotron3-nano-nvfp4-w4a16 \
  --quantization modelopt_fp4 --kv-cache-dtype fp8 \
  --trust-remote-code --max-model-len 131072 \
  --gpu-memory-utilization 0.85 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --reasoning-parser nano_v3

# Nemotron — NVIDIA vendor path (BF16 or FP8, vendor-supported)
# wget nano_v3_reasoning_parser.py from the HF model card first
vllm serve nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16 \
  --served-model-name model --tensor-parallel-size 1 \
  --max-model-len 262144 --kv-cache-dtype fp8 \
  --gpu-memory-utilization 0.75 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --reasoning-parser-plugin nano_v3_reasoning_parser.py --reasoning-parser nano_v3

# Nemotron — llama.cpp (build with -DCMAKE_CUDA_ARCHITECTURES=121 for Spark sm_121)
llama-server -m Nemotron-3-Nano-30B-A3B-UD-Q8_K_XL.gguf \
  --jinja --n-gpu-layers 99 --ctx-size 262144 --threads 8

# Qwen3.6 multi-adapter vLLM
export VLLM_ALLOW_RUNTIME_LORA_UPDATING=True
vllm serve /models/qwen36 \
  --enable-lora --enable-mixed-moe-lora-format \
  --max-loras 4 --max-lora-rank 64 \
  --lora-modules \
    '{"name":"coder",  "path":"/adapters/qwen36/coder-prod",  "is_3d_lora_weight":true}' \
    '{"name":"general","path":"/adapters/qwen36/general-prod","is_3d_lora_weight":true}' \
  --port 8000 --max-model-len 32768
```

Hot-swap without downtime: `POST /v1/load_lora_adapter` + `POST /v1/unload_lora_adapter` (vLLM), or `sglang_admin reload-lora` (SGLang). SGLang `nemotron_h` support on Spark is still in progress as of May 2026.

---

## 10. Security posture

- **Default execution backend = `local` (dev tier); `kubernetes` is the recommended production tier.** Lamark trades container-by-default for a permission-first policy: every shell- and write-class tool gates on `Decision::Prompt` unless an explicit allowlist entry says otherwise. Hermes's failure mode was ALLOW-ALL on the policy layer (Issue #7826), not the absence of a container; Lamark fixes the policy layer (see `docs/plan/05c §"PermissionRequest"`, policy DSL in `agent/crates/lamark/policy.toml`) and leaves the sandbox choice to the operator.
  - **Try / dev:** `local` — fastest path, no infra. Permission-first policy is the safety layer.
  - **Single-host prod / air-gapped:** `docker` — container-per-session, egress allowlist via sidecar proxy.
  - **Multi-tenant production / horizontal scale:** `kubernetes` — Pod-per-subagent, NetworkPolicy egress, namespace-scoped RBAC, declarative resource quotas. See [`docs/plan/05d`](./docs/plan/05d-sandbox-config-examples.md) for the production manifest.
  - **Build-server ops:** `ssh` — works for operator-managed hosts; cannot enforce egress (caveat).
  - Switch via `lamark config set sandbox.default <name>` or per-invocation `--sandbox <name>`.
- **Hook approval chain**: every shell-class / write-class tool fires `PermissionRequest`; default policy is `prompt`; declarative `Allow|Prompt|Forbidden` rules in `agent/crates/lamark/policy.toml`.
- **Trace redaction**: pre-storage hook can redact inline (opt-in); the dataset pipeline always re-runs Stage 1 + Stage 2 before any frontier-model curation.
- **Knowledge-base auth**: bearer token in `KB_TOKEN`; per-project ACL via knowledge-base's RBAC (it ships with auth/RBAC service — see its §4.1).
- **Consent**: `learning.consent_required=true` for any multi-user install; per-session `~/.lamark/no-collect` opt-out.

---

## 11. Open questions

1. **Hardware target** — Primary training box is DGX Spark (confirmed: BF16 fits, NVFP4 serving works, Megatron-Bridge `nano-v3` runs). Dev path for 12 GB RTX: Qwen3-4B-Instruct-2507 QLoRA (8–10 GB). Dev path for Apple Silicon (M3 Pro 36 GB): Gemma4-27B via MLX-LM. **Resolved: Spark is primary; others are listed fall-backs.** Still open: when to purchase a second Spark for dual-Spark clustering (ConnectX-7 scale-out).
2. **Trace consent UX** — silent capture with off-switch, or explicit opt-in per session?
3. **First base model** — Qwen3.6-35B-A3B or Nemotron-3-Nano-30B-A3B? Recommendation from research: start with Nemotron (Megatron-Bridge pipeline is more mature, Nemotron-Agentic-v1 schema alignment is exact, KV cache advantage on Spark). Switch to Qwen3.6 if multi-adapter hot-swap is needed before monthly merge cycles. Still open: commit to one or keep both in the nightly pipeline.
4. **Frontier teacher budget** — Claude + GPT + Gemini API spend in month 1, or skip OSS-Instruct expansion?
5. **knowledge-base contract pin** — which version of `../knowledge-base/docs/07b-public-api-rfc.md` are we coding against? (Capture a hash; coordinate with the knowledge-base team on breaking changes.)
6. **Plugin host** — WASM-only (safer), dylib-only (faster), or both? (See `docs/plan/08-layer-7-skills-plugins-curator.md`.)
7. **Qwen3.5 DeltaNet LoRA target modules** — the Gated DeltaNet SSM layers in Qwen3.5 dense models use different projection names than standard Transformer attention. Must run `model.named_modules()` before setting `target_modules` on any Qwen3.5-{0.8B,2B,4B,9B} model; confirm with `print_trainable_parameters()` that both attention and SSM layers are included. Until Unsloth publishes a confirmed Qwen3.5 LoRA recipe, treat these as experimental.
8. **Qwen3.5 multimodal in Lamark** — the 0.8B–9B models are natively multimodal (early-fusion image+text). Lamark v0.1 is text-only. Decide: use text-only inference (pass no images, use as text-only agents) or extend the `ModelProvider` trait to support image inputs. Text-only is the safe v0.1 choice; multimodal can unlock vision tools (screenshot analysis, diagram reading) in v0.2.

---

## 12. Non-goals (v0.1)

- Multi-tenant SaaS. Single-user-per-process; per-project isolation via knowledge-base.
- RBAC at the runtime layer (knowledge-base owns auth).
- **GRPO/RLVR.** SFT (nightly) + DPO (weekly) only in v0.1. GRPO requires stable SFT baseline (≥ 30 clean nights) + task verifiers + NeMo Gym integration — that is a v0.2+ deliverable. See §9 Tier 3.
- **Full weight fine-tuning on DGX Spark.** Not physically viable: 30B+ MoE optimizer states exceed 128 GB UMA. Monthly merge is arithmetic LoRA-delta baking, not gradient training. True from-scratch FT needs ≥ 2× H100 nodes.
- **CPT (Tier 0) by default.** Only activated manually for raw domain corpora > 50 MB; not part of the nightly/weekly/monthly cycle.
- Mobile / Termux. Deferred.
- Windows native. macOS + Linux only at v0.1.

---

## 13. Plan suite

| File | Topic |
|---|---|
| [`docs/plan/00-overview.md`](./docs/plan/00-overview.md) | Index, phase ordering, definition of done. |
| [`docs/plan/00b-ideology-and-feature-matrix.md`](./docs/plan/00b-ideology-and-feature-matrix.md) | Hermes-vs-Claude-Code ideology comparison; which DNA we adopt; feature matrix. |
| [`docs/plan/00c-hermes-deepdive-addendum.md`](./docs/plan/00c-hermes-deepdive-addendum.md) | Post-investigation refinements: hermes-agent contract details + Claude-Code/Codex tightenings folded into layers 4–9. |
| [`docs/plan/00d-claude-code-deepdive-addendum.md`](./docs/plan/00d-claude-code-deepdive-addendum.md) | Claude-Code deep-dive — full hook taxonomy, slash-command union, vim mode, diff cache, skill discovery, plugin record, coordinator pattern. |
| [`docs/plan/01-rust-strategy.md`](./docs/plan/01-rust-strategy.md) | Pure-Rust day 1; cargo workspace; deps; phase plan. |
| [`docs/plan/02-layer-1-entry-cli.md`](./docs/plan/02-layer-1-entry-cli.md) | CLI binary, subcommands, ratatui TUI, slash-command registry. |
| [`docs/plan/03-layer-2-config-bootstrap.md`](./docs/plan/03-layer-2-config-bootstrap.md) | Layered config, env, secrets, bootstrap. |
| [`docs/plan/04-layer-3-providers.md`](./docs/plan/04-layer-3-providers.md) | `ModelProvider` trait, streaming, cache_control, tool-call parsers. |
| [`docs/plan/05-layer-4-agent-core.md`](./docs/plan/05-layer-4-agent-core.md) | Turn loop, SQ/EQ, tool dispatch, env backends. |
| [`docs/plan/05a-coordinator-multi-agent.md`](./docs/plan/05a-coordinator-multi-agent.md) | Coordinator + multi-agent Kanban protocol + /goal Ralph loop + subagents. |
| [`docs/plan/05b-tasks-and-kanban.md`](./docs/plan/05b-tasks-and-kanban.md) | Three task layers; promotion ladder; training signals. |
| [`docs/plan/05c-sandbox-and-agent-hosting.md`](./docs/plan/05c-sandbox-and-agent-hosting.md) | `Sandbox` trait + in-tree backends; agent hosting; egress policy; trace pull-back. |
| [`docs/plan/05d-sandbox-config-examples.md`](./docs/plan/05d-sandbox-config-examples.md) | Worked YAML configs for every sandbox backend; Kubernetes cluster manifests. |
| [`docs/plan/06-layer-5-hooks-trace.md`](./docs/plan/06-layer-5-hooks-trace.md) | Hook bus, trace recorder, reducer, KB sync. |
| [`docs/plan/07-layer-6-prompt-and-cache.md`](./docs/plan/07-layer-6-prompt-and-cache.md) | Hierarchical prompt composer + cache strategies. |
| [`docs/plan/07a-layer-6-memory-and-kb.md`](./docs/plan/07a-layer-6-memory-and-kb.md) | Memory providers (KB default + Honcho/Mem0/Hindsight/SQLite); KB client. |
| [`docs/plan/07b-prompt-self-improvement.md`](./docs/plan/07b-prompt-self-improvement.md) | Reflexion, OPRO, ProTeGi, Voyager-skill, Constitutional AI: what we adopt. |
| [`docs/plan/08-layer-7-skills-plugins-curator.md`](./docs/plan/08-layer-7-skills-plugins-curator.md) | Skill discovery, plugin host (WASM+dylib), Curator. |
| [`docs/plan/09-layer-8-gateway-integrations.md`](./docs/plan/09-layer-8-gateway-integrations.md) | Gateway, MCP client+server, ACP, Telegram/Slack/Discord adapters. |
| [`docs/plan/10-training-pipeline.md`](./docs/plan/10-training-pipeline.md) | Python pipeline; cron; data flows; eval gates. |
| [`docs/plan/11-build-test-deploy.md`](./docs/plan/11-build-test-deploy.md) | Cargo workspace, CI, releases, docker. |
