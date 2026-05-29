# 01 — Rust strategy

> Pure Rust from day 1. No Python bridge, no embedded interpreter. The cloned
> hermes-agent / claude-code / codex repos are **design references**, not
> runtime dependencies.

## What we are NOT doing

- **No strangler-fig migration.** We are not wrapping Python with a Rust shell. We are not progressively replacing layers. The first commit of the runtime crate is Rust, and so is every commit after.
- **No PyO3 in the runtime.** Python is invoked from Rust *only* by the training pipeline orchestrator (separate process, file-system / HTTP boundary).
- **No FFI to the hermes-agent codebase.** We read it for shape; we don't link it.

The training pipeline (plan/10) remains Python because Unsloth, Megatron-Bridge, and TRL aren't going Rust. The agent and the trainer talk only through `~/.lamark/traces/` files and `../knowledge-base` HTTP API. Two processes, two languages, one file-system contract.

## How we move fast despite that

A clean-room Rust rewrite of a Python codebase the size of hermes-agent is large work. We accept that and mitigate with these levers:

1. **codex-rs is our north star.** Codex (`~/.cache/lemark/vendor/codex`) already solves 70% of what we need in Rust: turn loop, model-provider trait, rollout-trace, sandboxing policy, MCP client, app-server daemon, SQ/EQ event protocol. We adopt its **structure** verbatim — same crate boundaries, same trait shapes, same protocol enums — and add only what Lamark needs that Codex doesn't (skill loader, plugin host, gateway adapters, knowledge-base client, Curator).

2. **One layer at a time, all the way through.** We don't ship a half-built layer. When `lamark-providers` lands, every provider impl listed in `plan/04` is implemented and tested. The crate's `pub` API doesn't change after that.

3. **Vertical slice MVP first.** Before any layer is "complete," there is an end-to-end thin slice: CLI → config → one provider impl (`OpenAICompatProvider` against vLLM) → minimal agent loop → one tool (`bash`) → trace recorder writing files. That slice ships at the end of P2 and proves the architecture.

4. **Aggressive code-gen and shared types.** `lamark-core` defines `Conversation`, `Turn`, `ToolCall`, `Event`, `Message`. Every other crate imports those — no duplicate definitions. We use `serde` + `schemars` to derive JSON Schema for the trace format and the gRPC types we'll expose later, so the protocol stays single-source-of-truth.

5. **Reference repos available at compile time but not linked.** `~/.cache/lemark/vendor/{hermes,codex,claude-code}` is a developer convenience. CI does *not* depend on them. A new contributor can either clone them themselves or read directly from the design-reference excerpts in each plan file.

## Cargo workspace layout

```
lemark-project/
├── Cargo.toml                    # workspace root
├── crates/
│   ├── lamark/                   # the binary (CLI + bootstrap + TUI)
│   ├── lamark-config/            # layered config
│   ├── lamark-core/              # types, traits, shared primitives
│   ├── lamark-providers/         # ModelProvider trait + all impls
│   ├── lamark-tools/             # tool registry + built-ins
│   ├── lamark-sandbox/           # Sandbox trait + backends (local/docker/ssh) + agent hosting (plan/05c)
│   ├── lamark-hooks/             # hook bus
│   ├── lamark-trace/             # recorder + reducer + KB upload
│   ├── lamark-prompt/            # composer + section model
│   ├── lamark-cache/             # cache_control + prefix-hash strategies
│   ├── lamark-memory/            # memory trait + Honcho/Mem0/Hindsight/SQLite impls
│   ├── lamark-kb-client/         # knowledge-base HTTP client
│   ├── lamark-skills/            # skill loader + frontmatter + Curator
│   ├── lamark-plugins/           # plugin host (wasmtime + libloading)
│   ├── lamark-gateway/           # gateway core + adapters/
│   ├── lamark-mcp/               # MCP client + server (rmcp-based)
│   ├── lamark-acp/               # ACP registry + adapter
│   ├── lamark-webui/             # axum server + embedded SvelteKit SPA (plan/12)
│   ├── lamark-remote/            # tonic gRPC + WS server + Rust client (plan/13)
│   ├── lamark-policy/            # Allow|Prompt|Forbid DSL parser + evaluator
│   ├── lamark-protocol/          # shared wire types (proto, schemars-derived)
│   └── lamark-test-utils/        # fixtures, recorded tapes, ratatui assertions
├── proto/                        # .proto files for any externally-exposed gRPC
│                                 # (gateway, ACP, remote-ui — plan/13)
├── webui/                        # SvelteKit SPA source (built into lamark-webui via rust-embed)
├── clients/                      # generated remote-ui client SDKs (rust/ts/swift/kotlin)
│                                 # NOT in the cargo workspace
├── plan/                         # this dir
├── docs/                         # mdBook source
├── scripts/                      # dev helpers (release, vendor-snapshot, e2e)
└── docs/specs/                   # deep spec (split from the old SPEC.md)
```

No `pyworker/` directory. No `proto/` files for internal layer boundaries (those become Rust trait calls, not gRPC).

## What Python is for

One thing: the training pipeline (plan/10). It lives in a sibling directory tree, not in the cargo workspace:

```
~/.lamark/training/                  # data plane (filesystem)
~/lamark-trainer/                    # code plane (Python project, not in this repo)
  └── pyproject.toml + lamark_trainer/...
```

The agent never imports it. Cron runs it. It pulls trace bundles from `~/.lamark/traces/` and from `knowledge-base /agents/{id}/traces`, runs the SFT/DPO/eval flow, and POSTs adapters + events back to knowledge-base. The Rust runtime sees the *result* (a new adapter id served by vLLM), never the trainer process.

## Crate dependency rules

Hard rules; enforce with `cargo deny` and a custom CI check:

1. **`lamark-core` depends on nothing else in the workspace.** It is the bottom. Types, traits, error types, IDs.
2. **No upper layer imports from a peer.** `lamark-providers` does not import `lamark-tools`. They both import `lamark-core`. The binary (`lamark`) wires them.
3. **No layer logs through `println!` or `eprintln!`.** Everything goes through `tracing`.
4. **No layer uses `std::process::exit`.** Returns `Result`; the binary decides the exit code.
5. **No layer spawns its own tokio runtime.** They expect to be called from within `#[tokio::main]`.

## Dependency selections

| Concern | Crate | Why |
|---|---|---|
| Async runtime | **tokio** | Default. Required by reqwest, hyper, tonic, rmcp. |
| CLI | **clap v4** (derive) | Subcommand support, env, completions. |
| TUI | **ratatui + crossterm** | Mature. |
| Logging | **tracing + tracing-subscriber + tracing-appender** | Spans align with our trace events. |
| Config | **figment** (YAML / env / dotted-flags) | Layered semantics. |
| HTTP client | **reqwest** (rustls) | Mature. |
| SSE streaming | **eventsource-stream** | For OpenAI-compat streaming. |
| gRPC | **tonic** | Only for **external** APIs we expose (ACP, optional remote-trigger). |
| Serialization | **serde + serde_json + serde_yaml** | Default. |
| Streaming JSON parse | **simd-json** (optional) | For bulk trace replay. |
| Tokenizer | **tokenizers** (HF) | Prompt counting, embedded. |
| MCP | **rmcp** | Same crate codex uses; battle-tested. |
| WASM host | **wasmtime** + **wasi:cli@0.2** | Tier-1 host, capability-based. |
| Dynamic loading | **libloading** | For dylib plugins (off by default). |
| SQLite | **rusqlite** (bundled, with FTS5 feature) | Local memory fallback. |
| Postgres (KB) | through `reqwest` → KB HTTP API | We never touch KB's DB directly. |
| Cancellation | **tokio-util** `CancellationToken` | Propagated through every long-running op. |
| Property tests | **proptest** | Trace recorder invariants. |
| Snapshot tests | **insta** | Prompt-composer + help-screen golden files. |
| Param tests | **rstest** | Fixture-driven. |
| HTTP mocking | **wiremock** | Provider integration tests. |
| Concurrency primitives | **dashmap**, **arc-swap**, **parking_lot** | Cheap mutexes + atomic configs. |
| Lints | **clippy --all-targets -- -D warnings** | CI-enforced from day one. |
| Supply-chain | **cargo-deny** + **cargo-audit** | Block GPL/AGPL; block known-vuln deps. |
| Formatter | **rustfmt (default)** | Default. |
| Build tool | **just** | One-line task runner. |

**Forbidden:** GPL/AGPL transitive deps. (TruffleHog is AGPL but it's an out-of-process binary used only by the trainer, so it never links into the Rust binary.)

## MSRV + edition

- **MSRV:** Rust 1.94.1 (always use latest stable).
- **Edition:** 2024.
- **Semver:** every workspace crate is `0.x` until v0.1.0 ships, then we cut `1.0` together. Public proto files (gateway, ACP) get their own semver (`lamark.v1` → `lamark.v2` on breaking change).

## What we copy from codex-rs (with attribution)

Codex is Apache-2.0. We adopt structurally:

| codex-rs | lamark-* | What we keep |
|---|---|---|
| `model-provider/src/provider.rs` (`ModelProvider`) | `lamark-providers/src/trait.rs` | Trait shape: info, capabilities, auth, api_provider. |
| `rollout-trace/src/raw_event.rs` (`RawTraceEventPayload`) | `lamark-trace/src/event.rs` | Event enum variants. |
| `rollout-trace/src/model.rs` (reduced graph) | `lamark-trace/src/reducer.rs` | `state.json` shape. |
| `protocol/src/protocol.rs` (`EventMsg`) | `lamark-core/src/event.rs` | Top-level event union for UI/gateway/trace consumers. |
| `core/src/session/turn.rs` (`run_turn()`) | `lamark-core/src/turn.rs` | SQ/EQ pattern; turn lifecycle. |
| `execpolicy/src/decision.rs` (`Decision`) | `lamark-policy/src/decision.rs` | Allow / Prompt / Forbidden. |
| `mcp-server/`, `rmcp-client/` | `lamark-mcp/` | Server + client structure. |

We do **not** copy the codex-rs sandboxing crates (`linux-sandbox`, `windows-sandbox-rs`) verbatim. Those are deep platform integrations; we use them as references and re-implement only what we need in `lamark-sandbox` (plan/05c). v0.1 ships `LocalSandbox` naked behind `--unsafe-local`; host-level sandboxing (landlock / seatbelt) is deferred to v0.2 — recorded in `docs/decisions/0008-local-env-no-host-sandbox.md`.

## What we copy from hermes-agent (with attribution)

Hermes is MIT. We don't link, we re-implement, but we keep the same files for *what to build*:

| hermes module | lamark-* | Why |
|---|---|---|
| `agent/conversation_loop.py` | `lamark-core/src/turn.rs` | Loop shape: while iterations < max && not cancelled. |
| `tools/registry.py` + `tools/*` | `lamark-tools` | The catalog of tools and their semantics. |
| `tools/environments/*` | `lamark-sandbox` | Sandbox trait + 3 in-tree backends (Local, Docker, SSH); 4 more are post-v0.1 plugin candidates. See plan/05c. |
| `agent/memory_manager.py` | `lamark-memory` | Memory provider abstraction (Honcho/Mem0/Hindsight). |
| `agent/skill_*.py`, `agent/curator.py` | `lamark-skills` | Markdown skill loader + Curator. |
| `agent/prompt_builder.py`, `agent/system_prompt.py`, `agent/prompt_caching.py` | `lamark-prompt`, `lamark-cache` | Layered prompt + cache strategy. |
| `cli.py` gateway portion + 20+ adapters | `lamark-gateway/adapters/{telegram,slack,discord,...}` | Adapter shape; we ship 3 in v0.1. |
| `acp_adapter/`, `acp_registry/` | `lamark-acp` | ACP protocol surface. |
| `batch_runner.py`, `mini_swe_runner.py` | `crates/lamark/src/commands/exec.rs` + `lamark-tools/src/batch.rs` | Offline batch evaluator. |

## What we copy from the claude-code mirror (re-implement only)

Mirror is licensing-risk — we read the interface, **never** the code. Re-implementations:

| claude-code file (read for shape) | lamark-* (re-implement) |
|---|---|
| `types/hooks.ts` | `lamark-hooks/src/types.rs` |
| `utils/systemPrompt.ts` + `constants/systemPromptSections.ts` | `lamark-prompt/src/composer.rs` |
| `commands.ts` + `commands/*` | `crates/lamark/src/commands/mod.rs` (just the registry pattern) |
| `skills/loadSkillsDir.ts` + `skills/bundledSkills.ts` | `lamark-skills/src/loader.rs` |
| `services/analytics/` | `crates/lamark/src/observability.rs` (we use prometheus + tracing, not their custom queue) |

## Phase plan (build order, vertical)

| Phase | Lands | Exit gate |
|---|---|---|
| **P0** Decisions + scaffolding (1–2d) | empty cargo workspace, all crates created with stub `lib.rs`, `cargo build` works, CI green | `cargo build --workspace` under 3 min on clean macOS. ADRs for SPEC §11 questions. |
| **P1** Entry + config + bootstrap (1w) | `lamark` binary, `clap` subcommands, layered config, `lamark doctor` | `lamark --help`, `lamark config show`, `lamark doctor` all work; no LLM calls yet. |
| **P2** Providers + minimal agent loop + bash tool + trace recorder (2–3w) | Vertical slice: chat → reply via vLLM, one tool, trace bundle on disk | `lamark chat "ls /tmp"` round-trips end-to-end. Trace bundle round-trips through reducer. |
| **P3** Hooks bus + full event taxonomy (1w) | All hook events fire, trace recorder subscribes via hooks (not direct injection) | Hook-counter test passes for every event kind. |
| **P4** Prompt composer + cache + memory + KB client (2w) | Layered prompt assembly, cache_control + prefix-hash strategies, KB client | KB-roundtrip test passes; cache HIT visible on turn 2. |
| **P5** Skills + plugins + Curator (2w) | Markdown skill loader, WASM plugin host, Curator subprocess | 3 bundled skills loaded; 1 third-party WASM plugin loaded; Curator runs and rotates. |
| **P6** Gateway + MCP + ACP (2w) | Telegram/Slack/Discord adapters, MCP server, ACP registry | Telegram message → trace bundle; Claude Desktop sees Lamark MCP tools. |
| **P7** Training pipeline (Python, parallel track from P2 onward) | Nightly collect→redact→curate→blend→train; eval gate; promote | One nightly run produces a LoRA adapter from yesterday's traces. |
| **P8** Eval gate + promote + cron + forgetting probe (1w) | Auto-rollback, drift triggers | 14 unattended nights; planted regression rolls back. |
| **P9** WebUI (2w) | axum server, embedded SvelteKit SPA, session + trace views, approval modal | `lamark webui --open` streams a live session in-browser end-to-end. |
| **P10** Remote UI (2w) | tonic gRPC + WS fallback, mTLS device certs, bearer tokens, Rust/TS/Swift/Kotlin client SDKs | `lamark --remote` round-trips a session; pairing + revoke work; `buf breaking` green. |

P2 is the load-bearing milestone. Once that works, everything else is additive.

## Risks of pure-Rust day 1

- **⚠ Risk:** Engineering scope is real — re-implementing skill loader, plugin host, gateway adapters, MCP server, ACP all in Rust is substantial. **Mitigation:** vertical-slice MVP at P2 proves architecture before we go wide; remaining work is per-crate and parallelizable across engineers.
- **⚠ Risk:** Some hermes-agent features have no obvious Rust equivalent (Honcho's dialectic user modeling is a Python SDK). **Mitigation:** memory providers talk to KB or external services over HTTP; we don't reimplement Honcho's algorithm, we call its API.
- **⚠ Risk:** Cargo build times on a 20-crate workspace. **Mitigation:** `sccache` in CI day 1, `cargo nextest`, `[profile.dev] opt-level=1` for deps, feature-gated heavy crates (`wasmtime` behind `features = ["plugins-wasm"]`).
- **⚠ Risk:** Memory layer outages (KB down) hanging the agent. **Mitigation:** all KB calls have aggressive timeouts (5s default); memory writes are fire-and-forget with a local SQLite spool; reads degrade to the SQLite fallback.

## Definition of done (this strategy)

- ✅ `cargo build --release --workspace` succeeds under 3 min on a clean macOS 14 / Linux 6.x machine.
- ✅ `cargo nextest run --workspace` is green.
- ✅ `cargo deny check` is green (no GPL/AGPL transitive).
- ✅ Vertical slice (P2 exit) works: `lamark chat` against vLLM, one tool, trace bundle on disk.
- ✅ `docs/runtime-architecture.md` (under 800 words) describes the architecture for a new contributor.

Once these pass, plan files 02 → 11 can each proceed in parallel by different engineers.
