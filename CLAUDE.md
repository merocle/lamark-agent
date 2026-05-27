# Lamark — Claude Code guide

Self-improving local agent. Rust runtime, Codex-style trace bundles + provider trait, integrated with sibling `../knowledge-base` for canonical memory + dataset storage.

**Reference clones:** `~/.cache/lamark/vendor/{hermes-agent,claude-code,codex}`
**Sibling project:** `../knowledge-base` (Kotlin/Spring; exposes `/knowledge /search /graph /memory /agents` API)
**Python training pipeline:** `learning/` (LoRA fine-tuning; `pip install -e learning/[spark]`)

See [`SPEC.md`](./SPEC.md) for the full spec and [`docs/plan/`](./docs/plan/) for the layer-by-layer implementation plan.

---

## Project layout

```
lamark-agent/                         # monorepo root
├── AGENTS.md
├── CLAUDE.md
├── LICENSE
├── README.md
├── SPEC.md
├── agent/                            # Rust agent workspace
│   ├── Cargo.toml                    # workspace root (run cargo/just from here)
│   ├── rust-toolchain.toml
│   ├── rustfmt.toml
│   ├── deny.toml
│   ├── justfile
│   ├── .cargo/config.toml
│   └── crates/
│       ├── lamark/                   # binary (CLI + bootstrap + TUI)
│       ├── lamark-core/              # types, traits, shared primitives
│       ├── lamark-config/            # layered config
│       ├── lamark-providers/         # ModelProvider trait + all impls
│       ├── lamark-tools/             # tool registry + built-ins
│       ├── lamark-sandbox/           # Sandbox trait + backends (local/docker/ssh/k8s)
│       ├── lamark-hooks/             # hook bus
│       ├── lamark-trace/             # recorder + reducer + KB upload
│       ├── lamark-prompt/            # composer + section model
│       ├── lamark-cache/             # cache_control + prefix-hash strategies
│       ├── lamark-memory/            # memory trait + Honcho/Mem0/Hindsight/SQLite
│       ├── lamark-kb-client/         # knowledge-base HTTP client
│       ├── lamark-skills/            # skill loader + frontmatter + Curator
│       ├── lamark-plugins/           # plugin host (wasmtime + libloading)
│       ├── lamark-gateway/           # gateway core + adapters (telegram/slack/discord)
│       ├── lamark-mcp/               # MCP client + server (rmcp-based)
│       ├── lamark-acp/               # ACP registry + adapter
│       ├── lamark-coordinator/       # coordinator + Kanban inter-agent protocol
│       ├── lamark-policy/            # Allow|Prompt|Forbidden DSL parser + evaluator
│       ├── lamark-protocol/          # shared wire types (schemars-derived)
│       ├── lamark-webui/             # axum server + embedded SvelteKit SPA
│       ├── lamark-remote/            # tonic gRPC + WS server + Rust client
│       └── lamark-test-utils/        # fixtures, recorded tapes, ratatui assertions
├── docs/                             # all documentation
│   ├── decisions/                    # ADRs (NNNN-kebab-case.md)
│   ├── plan/                         # layer-by-layer Rust implementation plans
│   ├── scenarios/                    # use-case scenarios + _audit/
│   └── python/                       # Python training pipeline docs
└── learning/                         # Python training pipeline (LoRA/SFT/DPO)
    ├── pyproject.toml                # lamark-train package
    ├── src/lamark/                   # train/, redaction/, archive/, hardware.py
    ├── tests/
    ├── vendor/hermes/                # Vendored Hermes Agent MIT (Nous Research)
    ├── docker/Dockerfile.vllm
    └── scripts/                      # nightly-train, model-registry, vllm_server, etc.
```

Crate names are prefixed with `lamark-`. For example, the `core` crate is `lamark-core`.

---

## Crate dependency rules

Hard rules; enforced with `cargo deny` and CI:

1. **`lamark-core` depends on nothing else in the workspace.** Types, traits, error types, IDs only.
2. **No upper layer imports from a peer.** `lamark-providers` does not import `lamark-tools`. Both import `lamark-core`. The binary (`lamark`) wires them together.
3. **No layer logs through `println!` or `eprintln!`.** Everything goes through `tracing`.
4. **No layer calls `std::process::exit`.** Return `Result`; the binary decides the exit code.
5. **No layer spawns its own tokio runtime.** They expect to be called from within `#[tokio::main]`.

---

## Rust conventions

- When using `format!` and you can inline variables into `{}`, always do that.
- Always collapse if statements (Clippy `collapsible_if`).
- Always inline `format!` args when possible (Clippy `uninlined_format_args`).
- Use method references over closures when possible (Clippy `redundant_closure_for_method_calls`).
- Avoid bool or ambiguous `Option` parameters that force callers to write hard-to-read code like `foo(false)` or `bar(None)`. Prefer enums, named methods, newtypes, or other idiomatic Rust API shapes. When you cannot change the API, follow the argument-comment convention: use an exact `/*param_name*/` comment before opaque positional literals (`None`, booleans, numeric literals). Do not add these comments for string or char literals.
- Make `match` statements exhaustive; avoid wildcard arms when possible.
- Newly added traits must include doc comments explaining their role and how implementations are expected to use them.
- Avoid both `#[async_trait]` and `#[allow(async_fn_in_trait)]`. Prefer native RPITIT trait methods with explicit `Send` bounds:
  ```rust
  fn foo(&self, ...) -> impl std::future::Future<Output = T> + Send;
  ```
  Implementations may use `async fn foo(&self, ...) -> T` when they satisfy that contract.
- Prefer private modules and explicitly exported public crate API.
- Do not create small helper methods referenced only once.
- Avoid large modules: target under 500 LoC (excluding tests); if a file exceeds ~800 LoC, add new functionality in a new module instead of growing the existing file.
- When extracting code from a large module, move the related tests and type docs to the new module so invariants stay close to the code that owns them.
- When writing tests, prefer comparing the equality of entire objects over field-by-field assertions.

---

## Build and test commands

All Rust commands run from the `agent/` directory (the Cargo workspace root).

Install the task runner if it isn't already available: `cargo install just`.

| Task | Command (run from `agent/`) |
|---|---|
| Format (run after any Rust changes) | `just fmt` |
| Lint + fix | `just fix -p <crate-name>` |
| Test a specific crate | `just test -p lamark-<name>` |
| Full test suite | `just test` (ask the user before running this) |
| Build docs | `just docs` |

**Do not run `cargo test` directly.** Use `just test` so test execution follows repo defaults.

Run `just fmt` automatically after finishing Rust code changes; do not ask for approval to run it.

Before finalizing a large change, run `just fix -p <crate-name>` to fix linter issues. Prefer scoping with `-p` to avoid slow workspace-wide Clippy builds; only run `just fix` without `-p` when you changed shared crates. Do not re-run tests after `fix` or `fmt`.

When running Rust commands, be patient — Rust lock contention can make execution slow. Never try to kill them using the PID.

---

## Testing conventions

### Snapshot tests

This project uses `insta` for snapshot tests, especially in the TUI and prompt-composer crates.

Any change that affects user-visible output must include corresponding `insta` snapshot coverage. Review and accept snapshot updates as part of the change.

When output changes intentionally:
```sh
just test -p lamark-<name>           # generate updated snapshots
cargo insta pending-snapshots -p lamark-<name>   # check what's pending
cargo insta accept -p lamark-<name>  # accept all new snapshots in this crate
```

### Test assertions

- Use `pretty_assertions::assert_eq` for clearer diffs. Import at the top of the test module if not already present.
- Prefer deep equality comparisons: `assert_eq!()` on entire objects, not individual fields.
- Avoid mutating the process environment in tests; pass environment-derived flags from above.

### Provider integration tests

Use `wiremock` for HTTP mocking in provider tests. Build SSE payloads with the provided constructors and keep tests focused on structured payloads, not raw JSON.

---

## Code that must not be added to `lamark-core`

Over time, `lamark-core` risks becoming bloated. **Resist adding code to `lamark-core`.** Before adding, consider whether there is an existing crate that is the right home, or whether it is time to introduce a new crate for the new concept.

---

## Reference repos (read-only; never link)

| Source | Path | Role |
|---|---|---|
| openai/codex (Apache 2.0) | `~/.cache/lamark/vendor/codex` | Rust north star: turn loop, provider trait, trace format, SQ/EQ pattern, MCP. |
| NousResearch/hermes-agent (MIT) | `~/.cache/lamark/vendor/hermes-agent` | Architecture model; re-implement in Rust. Python copy vendored at `learning/vendor/hermes/`. |
| claude-code mirror | `~/.cache/lamark/vendor/claude-code` | Hook taxonomy + prompt composition design reference (licensing risk — read interface only, never copy code). |

These repos are developer conveniences. CI does not depend on them.

---

## Key architectural points

- **SQ/EQ pattern:** `Op` (submit queue) in, `Event` (event queue) out. The trace recorder, gateway adapters, and TUI all consume the same event stream — no direct injection.
- **Trace bundle format:** `~/.lamark/traces/<rollout_id>/manifest.json` + `trace.jsonl` + `payloads/`. Adopted from Codex's rollout-trace format. Large bodies go in `payloads/`; `trace.jsonl` stays cheap to scan.
- **ModelProvider trait:** provider choice is config, not code. `LocalOpenAICompat` talks to vLLM/Ollama/llama.cpp/SGLang; `AnthropicCompat` carries `cache_control` breakpoints.
- **Memory:** all persistent data lives in `../knowledge-base` over HTTP. No shared DB. KB calls have a 5 s timeout; writes are fire-and-forget with a local SQLite spool; reads degrade gracefully to the SQLite fallback when KB is down.
- **Policy:** every shell- and write-class tool fires `PermissionRequest`; default is `Decision::Prompt`; declarative `Allow|Prompt|Forbidden` rules live in `lamark/policy.toml`.
- **Forbidden deps:** GPL/AGPL transitive. Enforced by `cargo deny`. (TruffleHog is AGPL but runs out-of-process in the training pipeline — that's fine.)
- **MSRV:** Rust 1.94.1 (always use latest stable). Edition 2024.
- **Dependency versions:** always use the latest stable version of every crate. Do not pin to an older version unless a specific incompatibility is documented with a comment in `Cargo.toml`. When adding or updating a dependency, check crates.io for the current latest and use that version.

---

## TUI style (ratatui)

- Prefer Stylize helpers: `.dim()`, `.bold()`, `.cyan()`, `.italic()`, `.underlined()` over manual `Style`.
- Simple spans: use `"text".into()`.
- Styled spans: use `"text".red()`, `"text".green()`, `"text".magenta()`, etc.
- Avoid hardcoded `.white()`; prefer the default foreground.
- Chain helpers for readability: `url.cyan().underlined()`.
- Use `textwrap::wrap` to wrap plain strings. For ratatui `Line` wrapping, use helpers in `lamark-tui` (if/when that crate exists).

---

## Documentation

Keep architecture decision records in `docs/decisions/` with the `NNNN-kebab-case-title.md` naming convention. Layer-by-layer implementation plans live in `docs/plan/`. Use-case scenarios live in `docs/scenarios/`. Python training pipeline docs live in `docs/python/`.

## What Python is for

One thing only: the training pipeline (`learning/`). It is a separate process. The Rust agent never imports it. Cron runs it. It reads from `~/.lamark/traces/` (trace bundles produced by the Rust agent) and writes adapters to `~/.lamark/adapters/`. It talks to knowledge-base through the HTTP API.

Install: `pip install -e learning/[spark]` (ML extras require Linux + CUDA). For dev without GPU: `pip install -e learning/`.

The Python `lamark` CLI entry point (`learning/scripts/lamark`) is **deprecated** — the Rust binary is the primary `lamark` command. Python exposes training entry points only: `lamark-train`, `lamark-serve` (vLLM lifecycle).

---

## Load-bearing invariants (inherited from Python era — apply to the whole monorepo)

These rules are non-negotiable and must never be violated across both the Rust agent and the Python training pipeline.

1. **Never delete or modify the original `LICENSE`.** Nous Research's MIT copyright on the vendored Hermes Agent (`learning/vendor/hermes/`) is mandatory. Add new copyright lines above, never replace.
2. **Never call the product anything other than Lamark** in user-facing strings. The upstream "Hermes" name appears only in `LICENSE`, attribution sections, `learning/vendor/hermes/`, and required MIT notices.
3. **Never bake real user data into git.** Adapters, memory files, training archive, trace bundles, and `.env` are gitignored. Verify with `git status` before commit.
4. **Never use bitsandbytes QLoRA for MoE training on DGX Spark.** Confirmed OOM-at-load at 4% (Kreuzhofer, NVIDIA forum). Use bf16 LoRA only (`learning/` training scripts).
5. **Never enable DeepSpeed ZeRO-3 for LoRA training on Qwen3.6 MoE.** Breaks gradients. Use single-device or ZeRO-2.
6. **Never unfreeze the MoE router** during LoRA training. Pre-trained routing is load-bearing.
7. **No non-English strings in user-facing code or docs** outside `learning/vendor/hermes/` (third-party MIT).
8. **`learning/vendor/hermes/` is third-party MIT code.** Only modify files marked `LAMARK-PATCH` and record the diff in `learning/vendor/hermes/MODIFICATIONS.md`. Never rewrite upstream code in place without a patch marker.
