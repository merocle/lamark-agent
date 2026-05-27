# Lamark — Deep Specification

> Self-improving local agent. Rust runtime (`agent/`), Python training pipeline (`learning/`), knowledge-base service (`../knowledge-base`) as the system of record.

**Status:** Draft v0.2 — 2026-05-24

> See [`docs/plan/`](./docs/plan/) for the layer-by-layer Rust translation plan.

---

## 1. Mission

Build an open-source **Rust** agent that:

1. **Runs locally** on commodity hardware against an open-weights LLM (Qwen3-MoE, Gemma4, or Nemotron-3-Nano), and against any OpenAI-compatible endpoint.
2. **Captures every interaction** — system prompt, user input, model reasoning, tool call, tool result, approval decision, error, gateway event — into a structured, replayable trace bundle.
3. **Stores all of it in `knowledge-base`** — every trace, every reduced conversation, every promoted-or-rejected adapter, every gold/probe sample — so the platform has one canonical source of truth for the *data*, while Lamark owns the *runtime* and the *training schedule*.
4. **Closes the loop**: nightly SFT, weekly DPO, monthly merge — automatically fine-tuning the underlying LLM on its own captured traces, with strict anti-forgetting guards, eval gates, and forgetting-probe diagnostics.
5. **Stays explainable**: every training sample traces back to the live session it came from, by `rollout_id`; every promoted adapter traces back to the data + eval that approved it.

---

## 2. Scope

### 2.1 Inherit from Hermes-Agent (re-implement in Rust, structure-preserving)

- Core agent loop (perceive → tool-call → respond), provider-agnostic.
- Tool registry (~70 built-in tools across ~28 toolsets).
- Sandbox abstraction that hosts both shell commands AND whole subagents. **In-tree at v0.1:** `local` (dev default), `docker` (single-host prod), `ssh` (build-server ops), `kubernetes` (multi-tenant production default). **Plugin candidates:** Modal, Daytona, Singularity, Vercel-Sandbox. See [`docs/plan/05c`](./docs/plan/05c-sandbox-and-agent-hosting.md) for the trait + backends and [`docs/plan/05d`](./docs/plan/05d-sandbox-config-examples.md) for worked configs.
- Skill system: markdown skill files with YAML frontmatter; agent-authored skills; bundled skills.
- **Curator** background agent — 7-day skill-library consolidation.
- **External memory providers** — Honcho (dialectic user modeling), Mem0, Hindsight; pluggable trait.
- **Prompt cache logic** — generalized: Anthropic `cache_control` breakpoints when talking to Anthropic-compat APIs; prefix-cache-friendly stable-section emission when talking to vLLM/SGLang/llama.cpp.
- **Gateway** — long-running process that wraps the agent for messaging platforms; Lamark v0.1 ships Telegram + Slack + Discord adapters; the gateway protocol is platform-agnostic so other adapters can plug in later.
- **MCP** — bidirectional. Lamark is both an MCP client (consume third-party MCP servers) and an MCP server (expose Lamark tools to Claude Desktop / Cursor / VS Code / Codex / Windsurf).
- **ACP (Agent Communication Protocol)** — registry + adapter, so Lamark can call other agents and be called by them.
- **Batch runner** (for offline eval / mass trajectory generation).
- **Trajectory export** — extended into Codex-style trace bundle (§9).

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
| NousResearch/hermes-agent (cloned) | `~/.cache/lamark/vendor/hermes-agent` | Architecture model; we re-implement in Rust. |
| gitverse claude-code mirror (cloned) | `~/.cache/lamark/vendor/claude-code` | Hooks + prompt composition design reference. |
| openai/codex (cloned) | `~/.cache/lamark/vendor/codex` | Rust source we read directly; trace + provider trait + sandbox patterns. |
| `../knowledge-base` | local repo | Canonical memory + dataset + eval-set store; Kotlin/Spring. |

---

## 4. Target LLM matrix

| Model | Params | Why pick it | Train via |
|---|---|---|---|
| **Qwen3.6-35B-A3B** (or Qwen3-8B if VRAM-tight) | 35B MoE, 3B active | Strong tool-calling baseline. Unsloth + Megatron-SWIFT pipelines exist. | Unsloth LoRA on single Spark / 24 GB consumer GPU. |
| **NVIDIA-Nemotron-3-Nano-30B-A3B-BF16** | 30B MoE, 3B active | Day-zero Unsloth recipe. Schema-aligned with our trace format (`Nemotron-Agentic-v1`). | NVIDIA-NeMo/Megatron-Bridge `nano-v3` branch. |
| **Gemma4-27B** | 27B dense | Apple-Silicon friendly (M3 Pro 36 GB). | Unsloth LoRA + MLX. |

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

Sources (per setup-guide §3.1):

1. **Lamark trace bundles** — primary signal. Reducer emits Nemotron-Agentic-v1 directly.
2. `git_mining.py` — OctoPack pattern.
3. `pr_ingest.py` — multi-turn search/replace edits from PR review history.
4. `youtrack_ingest.py` / `jira_ingest.py` — issue → bug-fix trajectory.
5. `slack_ingest.py` — thread → Q&A pair.

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

**Training paths:**
- Qwen3 / Gemma3 — Unsloth LoRA on Spark / 24 GB GPU (~5 h/night).
- Nemotron — Megatron-Bridge `nano-v3` branch (~5–6 h/night).

**Weekly DPO** (Sundays): preference pairs from (a) same-prompt reruns, (b) PermissionDenied events (rejected branch), (c) two-judge re-scoring. Knowledge-base's `reinforce` signal feeds in here.

**Monthly merge** (day 30): `merge_and_unload` → requantize (AWQ-INT4 / NVFP4) → reset LoRA → recompute EWC Fisher → rotate teacher → full eval sweep → tag `lamark-base-vYYYY.MM`.

**Eval gates** (`eval/thresholds.yaml`):

```yaml
must_pass_all:
  mmlu_pro_250:        { drop_pp_max: 1.0 }
  mt_bench:            { drop_pct_max: 5 }
  ifeval:              { drop_pct_max: 2 }
  humaneval:           { drop_pp_max: 0 }
  swebench_lite_50:    { drop_issues_max: 1 }
  internal_gold:       { improve_pp_min: 2 }
  tool_call_compliance:{ min_rate: 0.995 }
  forgetting_probe:    { drop_pp_max_7d: 2 }
bonferroni_correction: true
```

**Forgetting-probe triggers**: 1pp single-night warn / 2pp 7d auto-bump anchor / 3pp 7d suspend / 5pp anywhere rollback.

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

1. **Hardware target** — DGX Spark, single 24 GB consumer GPU, M3 Pro Mac Studio, or remote 80 GB box over SSH?
2. **Trace consent UX** — silent capture with off-switch, or explicit opt-in per session?
3. **First base model** — Qwen3.6-35B-A3B / Gemma4-27B / Nemotron-3-Nano-30B-A3B?
4. **Frontier teacher budget** — Claude + GPT + Gemini API spend in month 1, or skip OSS-Instruct expansion?
5. **knowledge-base contract pin** — which version of `../knowledge-base/docs/07b-public-api-rfc.md` are we coding against? (Capture a hash; coordinate with the knowledge-base team on breaking changes.)
6. **Plugin host** — WASM-only (safer), dylib-only (faster), or both? (See `docs/plan/08-layer-7-skills-plugins-curator.md`.)

---

## 12. Non-goals (v0.1)

- Multi-tenant SaaS. Single-user-per-process; per-project isolation via knowledge-base.
- RBAC at the runtime layer (knowledge-base owns auth).
- RLHF / GRPO. SFT + DPO only.
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
