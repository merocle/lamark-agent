# Plan suite — overview

> Entry point for the Lamark implementation plan. See [`../specs/`](../specs/)
> for the spec; this directory is the **how**.

## How to read this directory

Files are numbered in dependency order. **Read 01 first**; the rest can be read sequentially or jumped into per layer.

| # | File | What it answers |
|---|---|---|
| 00 | overview.md *(this file)* | Where to start. |
| 00b | [ideology-and-feature-matrix.md](./00b-ideology-and-feature-matrix.md) | Hermes vs Claude-Code ideology compared; which DNA we adopt from each; feature matrix. |
| 00c | [hermes-deepdive-addendum.md](./00c-hermes-deepdive-addendum.md) | **Post-investigation refinements** (2026-05-25): hermes-agent contract details + Claude-Code/Codex tightenings folded into layers 4–9. Read after the layer files; supersedes ambiguous details. |
| 00d | [claude-code-deepdive-addendum.md](./00d-claude-code-deepdive-addendum.md) | **Companion to 00c** (2026-05-25): Claude-Code deep-dive — full hook taxonomy + 4 subtypes + JSON I/O protocol, slash-command discriminated-union, vim mode, diff cache, three-tier skill discovery, plugin record, coordinator pattern, memdir format, AppState store, idempotent migrations, Task vs Tool, upstream-proxy pattern, Dream consolidator. |
| 00e | [platform-vision-mapping.md](./00e-platform-vision-mapping.md) | **Building-block mapping** (2026-05-25): maps the "Platform target vision" diagram (Infrastructure services → Logic → Knowledge services → Communications → QA & DevOps, plus Security + Infrastructure cross-cuts) to Lamark crates and plan files; identifies four gaps (Schedulers, Encryption, DDoS protection, Monitoring). |
| 01 | [rust-strategy.md](./01-rust-strategy.md) | Pure-Rust day 1; workspace; deps; phase plan. |
| 02 | [layer-1-entry-cli.md](./02-layer-1-entry-cli.md) | The `lamark` binary, its subcommands, the TUI. |
| 03 | [layer-2-config-bootstrap.md](./03-layer-2-config-bootstrap.md) | Config sources, precedence, bootstrap order. |
| 04 | [layer-3-providers.md](./04-layer-3-providers.md) | `ModelProvider` trait. Cache. Streaming. Tool-call parsers. |
| 05 | [layer-4-agent-core.md](./05-layer-4-agent-core.md) | Turn loop. Tool registry. Sandbox summary (full spec in 05c). |
| 05a | [coordinator-multi-agent.md](./05a-coordinator-multi-agent.md) | Coordinator/orchestrator, Kanban inter-agent protocol, /goal Ralph loop, subagents. |
| 05b | [tasks-and-kanban.md](./05b-tasks-and-kanban.md) | Three task layers: single-session tasks, multi-agent Kanban, KB project boards. Promotion ladder. |
| 05c | [sandbox-and-agent-hosting.md](./05c-sandbox-and-agent-hosting.md) | `Sandbox` trait + backends (Local / Docker / SSH / **Kubernetes**) + agent hosting: `spawn_agent`, tool-proxy modes, egress policy, trace pull-back, budget enforcement, plan/13 protocol reuse. |
| 05d | [sandbox-config-examples.md](./05d-sandbox-config-examples.md) | Worked YAML configs for every backend (minimum + recommended), cluster manifests for Kubernetes (Namespace + NetworkPolicy + RBAC + ResourceQuota + controller), and a dev→staging→prod multi-profile example. |
| 06 | [layer-5-hooks-trace.md](./06-layer-5-hooks-trace.md) | Hook bus, trace recorder, reducer, knowledge-base sync. |
| 07 | [layer-6-prompt-and-cache.md](./07-layer-6-prompt-and-cache.md) | Hierarchical prompt composer, layered sections, cache strategies. |
| 07a | [layer-6-memory-and-kb.md](./07a-layer-6-memory-and-kb.md) | Memory providers (KB default + Honcho/Mem0/Hindsight/SQLite); KB client; reinforce signal. |
| 07b | [prompt-self-improvement.md](./07b-prompt-self-improvement.md) | Self-improvement: Reflexion, OPRO, ProTeGi, Voyager skill libs, Constitutional AI. |
| 08 | [layer-7-skills-plugins-curator.md](./08-layer-7-skills-plugins-curator.md) | Skills, plugin host (WASM + dylib), Curator. |
| 09 | [layer-8-gateway-integrations.md](./09-layer-8-gateway-integrations.md) | Gateway, MCP, ACP, messaging adapters. |
| 10 | [training-pipeline.md](./10-training-pipeline.md) | The Python training side. |
| 11 | [build-test-deploy.md](./11-build-test-deploy.md) | Cargo workspace, CI, releases, docker. |
| 12 | [webui.md](./12-webui.md) | Layer 9: local browser control plane — axum server + embedded SvelteKit SPA, same SQ/EQ enums as the TUI. |
| 13 | [remote-ui.md](./13-remote-ui.md) | Layer 10: cross-machine control — tonic gRPC + WS fallback, mTLS device certs + bearer tokens, mobile/desktop client SDKs. |

## Layer dependency graph

```
                ┌────────────────────────────────────────────────────┐
                │                  01 rust-strategy                  │
                └─────────────────────────┬──────────────────────────┘
                                          │
                ┌─────────────────────────▼──────────────────────────┐
                │      02 entry-cli  ──── 03 config-bootstrap        │
                └─────────────────────────┬──────────────────────────┘
                                          │
                ┌─────────────────────────▼──────────────────────────┐
                │              04 providers (trait)                  │
                └─────────────────────────┬──────────────────────────┘
                                          │
       ┌──────────────────────────────────▼───────────────────────────┐
       │  05  agent-core (turn loop, tools, environments)              │
       │  05a coordinator + Kanban + /goal Ralph loop + subagents      │
       └──────────────────────┬────────────────────┬──────────────────┘
                              │                    │
       ┌──────────────────────▼──────┐   ┌─────────▼───────────────────┐
       │  06 hooks + trace recorder  │   │  07  prompt + cache         │
       │                              │   │  07a memory + knowledge-base│
       │                              │   │  07b prompt self-improvement│
       └──────────────────────┬──────┘   └─────────┬───────────────────┘
                              │                    │
                ┌─────────────▼────────────────────▼──────────────────┐
                │     08 skills + plugins + Curator                  │
                └─────────────────────────┬──────────────────────────┘
                                          │
                ┌─────────────────────────▼──────────────────────────┐
                │     09 gateway + MCP + ACP + integrations          │
                └─────┬─────────────────────┬────────────────────────┘
                      │                     │
       ┌──────────────▼──────────┐   ┌──────▼────────────────────────┐
       │   12 webui (axum + SPA) │   │  13 remote-ui (tonic + WS)    │
       │   local browser UI      │   │  cross-machine control        │
       └─────────────────────────┘   └───────────────────────────────┘
                                          │
       (file system + knowledge-base HTTP boundary)
                                          │
                ┌─────────────────────────▼──────────────────────────┐
                │     10 training pipeline (Python; cron-driven)     │
                └────────────────────────────────────────────────────┘

   11 build-test-deploy cross-cuts everything.
   12 and 13 are additive surfaces on top of 05's SQ/EQ — same enums, new transports.
```

## Phases (build order)

Each phase has an exit gate. Don't move on until the gate is green.

| Phase | Plan files | Exit gate |
|---|---|---|
| **P0** Decisions + scaffolding | 01, 11 | `cargo build --workspace` works on empty stub workspace; ADRs for the 7 open questions in SPEC §11. |
| **P1** Entry + config + bootstrap | 02, 03 | `lamark --help`, `lamark config show`, `lamark doctor` work; no model calls yet. |
| **P2** Vertical slice: providers + minimal agent loop + bash tool + trace recorder | 04, 05, 06 (partial) | `lamark chat "ls /tmp"` round-trips end-to-end against a local vLLM; bundle on disk. |
| **P3** Full hooks + trace + reducer + KB upload | 06 | Every session writes a complete bundle; reducer emits Nemotron-Agentic-v1; KB sync round-trips. |
| **P4** Prompt composer + cache + memory + KB client | 07 | Memory reads/writes `../knowledge-base`; cache HIT visible in metrics; offline KB-fallback works. |
| **P5** Skills + plugins + Curator | 08 | Markdown skills load + Curator runs on schedule; one WASM plugin loaded. |
| **P6** Gateway + MCP + ACP | 09 | `lamark gateway run` accepts Telegram/Slack/Discord messages; `lamark mcp serve` exposes tools to Claude Desktop. |
| **P7** Training pipeline (Python; parallel from P2) | 10 | One nightly run produces a LoRA adapter from yesterday's traces. |
| **P8** Eval gate + promote/rollback + cron + forgetting probe | 10, 11 | Loop runs unattended 14 nights; planted regression rolls back automatically. |
| **P9** WebUI | 12 | `lamark webui --open` boots the server, streams a live session in the browser, renders an approval modal, replays a bundle. |
| **P10** Remote UI | 13 | `lamark --remote grpc://host:7879 chat` round-trips end-to-end; device pairing + token revoke work; `buf breaking` green. |

## Definition of done (project level)

Lamark v0.1 ships when:

1. ✅ `lamark chat`, `lamark gateway run`, `lamark mcp serve`, `lamark trace export` all work.
2. ✅ Every interactive session writes a complete trace bundle and syncs to knowledge-base.
3. ✅ Memory reads/writes against knowledge-base; falls back gracefully if KB is down.
4. ✅ Plugin host loads at least one third-party plugin in WASM and one in dylib.
5. ✅ Gateway adapters for Telegram + Slack + Discord work end-to-end.
6. ✅ MCP server exposes Lamark tools to Claude Desktop / Cursor.
7. ✅ Nightly training pipeline produces a promote-able LoRA from the last 24 h of traces.
8. ✅ Eval gate + forgetting probe + auto-rollback are wired and tested with a planted regression.
9. ✅ Documentation site (mdBook) covers install, config, gateway setup, plugin authoring, training operations.
10. ✅ License: MIT. No GPL/AGPL deps. (TruffleHog is AGPL but runs out-of-process — that's fine.)

## Conventions used across plan files

- **File-path citations** in plans point into `~/.cache/lemark/vendor/{hermes-agent,claude-code,codex}` for the reference implementations.
- **Rust crate paths** are written as `lamark-foo` (the crate name) → `crates/lamark-foo/src/lib.rs`.
- **TypeScript Hermes constructs** are translated using a "Rust analogue" callout box.
- **Risk callouts** are bold-emoji-prefixed `**⚠ Risk:**`.
- **TODO** items are kept inline as `*TODO:*` so a `grep -rn '*TODO:*'` audit is one command.

## Where the cache, memory, gateway, skills, plugins live in this plan

For the user's clarifications (we keep these in scope from v0.2 SPEC):

| Subsystem | Plan file | Status |
|---|---|---|
| **Prompt cache** (Anthropic `cache_control` + local prefix-hash) | 07 §"Cache" | First-class layer. |
| **External memory** (Honcho/Mem0/Hindsight + knowledge-base) | 07 §"Memory providers" | Trait-based; KB is the default. |
| **Plugins** | 08 §"Plugin host" | WASM + dylib; capability-gated. |
| **Skills** | 08 §"Skill system" | Same MD+YAML convention as Claude Code; Curator-managed lifecycle. |
| **Hooks** | 06 §"Hook bus" | Claude-Code taxonomy ported to Rust. |
| **External integrations** | 09 (all of it) | Gateway adapters + MCP + ACP. |
| **knowledge-base** | 07 §"Knowledge-base client" | HTTP client crate; offline-tolerant. |
