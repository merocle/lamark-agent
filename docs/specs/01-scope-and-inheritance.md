# 01 — Scope and inheritance

## Inherit from Hermes-Agent (re-implement in Rust, structure-preserving)

- Core agent loop (perceive → tool-call → respond), provider-agnostic.
- Tool registry (Hermes base + Lamark additions; see [`04-tooling-and-protocol.md`](./04-tooling-and-protocol.md) for the full catalog and the machine-readable [`../../learning/data/tools.yaml`](../../learning/data/tools.yaml)).
- Sandbox abstraction that hosts both shell commands AND whole subagents. **In-tree at v0.1:** `local` (dev default), `docker` (single-host prod), `ssh` (build-server ops), `kubernetes` (multi-tenant production default). **Plugin candidates:** Modal, Daytona, Singularity, Vercel-Sandbox. See [`../plan/05c-sandbox-and-agent-hosting.md`](../plan/05c-sandbox-and-agent-hosting.md) for the trait + backends and [`../plan/05d-sandbox-config-examples.md`](../plan/05d-sandbox-config-examples.md) for worked configs.
- Skill system: markdown skill files with YAML frontmatter; agent-authored skills; bundled skills.
- **Curator** background agent — 7-day skill-library consolidation; grades, archives (tarball backup), and prunes; **archives rather than deletes** so any forced rollback is possible; uses a secondary judge model separate from the main agent loop.
- **External memory providers** — Honcho (dialectic user modeling, 12-layer identity tracking), Mem0, Hindsight; pluggable trait.
- **Prompt cache logic** — generalized: Anthropic `cache_control` breakpoints when talking to Anthropic-compat APIs; prefix-cache-friendly stable-section emission when talking to vLLM/SGLang/llama.cpp.
- **Gateway** — long-running process that wraps the agent for messaging platforms; Lamark v0.1 ships Telegram + Slack + Discord adapters; the gateway protocol is platform-agnostic so other adapters can plug in later.
- **MCP** — bidirectional. Lamark is both an MCP client (consume third-party MCP servers) and an MCP server (expose Lamark tools to Claude Desktop / Cursor / VS Code / Codex / Windsurf).
- **ACP (Agent Communication Protocol)** — registry + adapter, so Lamark can call other agents and be called by them.
- **Batch runner** (for offline eval / mass trajectory generation).
- **Trajectory export** — extended into Codex-style trace bundle (see [`06-trace-and-data-format.md`](./06-trace-and-data-format.md)).
- **LSP semantic diagnostics** — every `write_file` / `patch` surfaces compiler/linter errors back to the agent before the turn ends (Hermes v0.14.0 pattern). Adopt for all write-class tools where an LSP-checkable target exists.
- **Multi-agent Kanban** (Hermes v0.13.0+) — orchestrator posts task cards; subagents pull, report heartbeat, and complete; `/goal` Ralph-loop locking primitive prevents re-entrancy. Each subagent has an isolated conversation + terminal session + toolset; only the final summary returns to the orchestrator (zero context-cost intermediate steps). `max_spawn_depth` caps nesting.
- **Atropos RL integration** — `batch_runner` + `trajectory_compressor` compress live agent trajectories into Atropos-format GRPO training data; feeds the nightly SFT pipeline and eventually reward-model training. Lamark's `lamark-trace` crate (Rust) is the upstream producer; `learning/scripts/transform/agent_to_messages.py` is the consumer.
- **LIFE-HARNESS four lifecycle layers** (arXiv:2605.22166) — live in new crate `lamark-harness`, injected into `AIAgent` at startup. Full spec in [`../plan/10d-skillopt-life-harness.md`](../plan/10d-skillopt-life-harness.md). 90% of agent failures are interface failures (not reasoning): (1) **Environment Contract** evolved ΔC fixes 33.3%; (2) **Action Realization** EXEC|BLOCK validation before sandbox fixes 23.2% — orthogonal to `lamark-policy` which is authorization; (3) **Trajectory Regulation** detects repetition/oscillation/stagnation fixes 33.6%; (4) **Procedural Skill** BM25 retrieval (already `lamark-skills`). Model-agnostic: harness evolved from Qwen3.5-9B transfers to 17 models.
- **MUSE skill creation** (arXiv:2605.27366, ByteDance/RIT) — agent-triggered `skill_create` tool; unit tests gate registration (all tests must pass in sandbox); `.memory.md` append-only experience log per skill (excluded from cross-agent transfers); two-stage catalog retrieval (name+desc catalog → full SKILL.md on `read_skill`); Merge/prune: overlapping skills merged, unused/failing skills pruned. Training-free; skills mirror Anthropic Agent Skills format — directly compatible with `lamark-skills` current format.
- **SkillOpt Curator** (arXiv:2605.23904) — Curator redesigned as a controlled text-space optimizer: rollout batches (B=40) → failure/success reflection minibatches (Bm=8) → bounded edit proposals (Lt=4 cosine) → held-out validation gate (strict `>`) → epoch-local rejected-edit buffer → epoch-wise slow/meta update. +23.5 pp avg on frozen GPT-5.5; tested on Qwen3.5-4B and Qwen3.6-35B-A3B. best_skill.md: 300–2,000 tokens, 1–4 accepted edits, portable across model scales and harnesses.

## Borrow from Claude Code (re-implement; mirror code is licensing-risk)

- **Hook taxonomy & event discriminator** — `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `PermissionRequest`, `SessionStart`, etc. Synchronous + async callbacks, per-hook timeout, deny-short-circuit.
- **Layered system-prompt composition** — `buildEffectiveSystemPrompt`-style override / coordinator / agent / custom / default / append layering.
- **Skill discovery conventions** — YAML frontmatter (`name`, `description`, `whenToUse`, `aliases`, `version`); search order: `.claude/skills/` (project) → `~/.lamark/skills/` (user) → bundled.
- **Slash-command registry shape** — `Command` type with `aliases`, `type`, `name`, `description`, lazy `load()`. We reuse the shape for `/lamark-*` commands.

## Borrow from Codex (re-implement; license is Apache 2.0 — safer to read)

- **Rollout-trace bundle format** — `manifest.json` + append-only `trace.jsonl` + `payloads/` dir; raw event types (`RolloutStarted`, `ThreadStarted`, `CodexTurnStarted`, `InferenceStarted/Completed/Failed/Cancelled`, `ToolCallStarted/Ended`, `ExecCommandBegin/OutputDelta/End`, `CompactionRequestStarted/Completed`, etc.); offline reducer → `state.json` (graph) + `conversation.jsonl` (Nemotron-Agentic-v1).
- **`ModelProvider` trait** — clean abstraction for OpenAI / Bedrock / Ollama / Anthropic / local. Lamark adopts the trait shape for plug-in inference.
- **SQ/EQ submission/event queue pattern** — explicit op-in, event-out streaming so the trace recorder, gateway, and UI all consume the same event stream.
- **Approval-policy DSL** — declarative `Allow | Prompt | Forbidden` rules per command/tool.

## Target language: Rust

The runtime is **Rust from day 1** — no Python bridge, no embedded interpreter. We re-implement each subsystem in Rust using the cloned references (codex-rs in Rust, hermes-agent in Python, claude-code mirror in TS) as design templates only. See [`../plan/01-rust-strategy.md`](../plan/01-rust-strategy.md). Python remains **only** for the training pipeline (Unsloth / Megatron-Bridge / TRL aren't going Rust); the agent ↔ trainer boundary is the file system (`~/.lamark/traces/`) and the knowledge-base REST API.
