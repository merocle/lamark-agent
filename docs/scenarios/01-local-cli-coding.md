# 01 — Local CLI coding session (golden path)

> **Phase:** P2 → P4 (`plan/00-overview.md`).
> **One-liner:** Developer runs `lamark chat` in a repo, asks for a small
> refactor, agent reads & edits files behind permission gates, a trace bundle
> is written, the session memory hits knowledge-base.

---

## North-star contribution

- **Domain quality (coding).** This is the day-1 coding-partner experience.
  Quality levers in scope: layered system prompt + AGENTS.md walk-up, real
  diff modal before writes, mtime-staleness check on Edit, structured tool
  errors that the model can recover from, prompt cache on the second turn,
  cost telemetry per turn.
- **Agent-side self-improvement.** Emits:
  - `memory_fact` writes — e.g., "retry helper lives in `crates/foo/src/retry.rs`",
    "Anna prefers `thiserror` over `anyhow`" — recalled by the prompt
    composer at the next session start.
  - Implicit `reinforce_signal=success` if the session ends with `cargo
    test` green and no Reject; `reinforce_signal=fail` if Anna issues
    `/undo` or interrupts a final write. (Tag attached to the bundle.)
  - **Skill candidate** only if `Curator` later spots a recurring multi-tool
    pattern across ≥ N sessions (scenario #05); scenario #01 itself does not
    author a skill — it only feeds the corpus.
- **Model-side self-improvement.** Emits a complete Codex-style trace
  bundle that reduces to **Nemotron-Agentic-v1** `conversation.jsonl` —
  trainer-ready for SFT. Approval / rejection / Edit-conflict / test-run
  outcomes make this bundle DPO-pair-ready as well (success branch vs
  rejected-branch trajectory). Bundle includes the *full* tool-call
  reasoning, not just the final answer — that's the SFT money signal.

---

## Idea

A backend engineer is editing a Rust crate locally. They want a hands-on
coding partner that **inspects files before suggesting**, **shows a diff
before writing**, **runs the test suite**, and **leaves a durable trace** so
tomorrow's session remembers today's decisions. This is the canonical Claude-
Code-style coding loop, but on a Lamark-managed local LLM (or any OpenAI-
compatible endpoint) with persistent memory delegated to `knowledge-base`.

This is the *golden path*. If this doesn't feel as good as Claude Code on a
laptop, nothing else matters.

## Actors

| Actor | Role |
|---|---|
| **Dev (Anna)** | Senior Rust engineer; running `lamark chat` from a project shell. Watches the TUI; approves dangerous tool calls; reads diffs. |
| **Lamark binary** | `crates/lamark/` — clap entry, ratatui TUI, dispatches `chat` subcommand (`plan/02 §"Subcommand surface"`). |
| **Agent core** | `crates/lamark-core/` — SQ/EQ turn loop, tool registry, hook bus (`plan/05 §"SQ/EQ event protocol"`). |
| **Provider** | `LocalOpenAICompat` against a local vLLM/Ollama serving Qwen3-8B (or Anthropic-compat if configured) (`plan/04`). |
| **Trace recorder** | `crates/lamark-trace/` — writes `~/.lamark/traces/<rollout_id>/{manifest.json, trace.jsonl, payloads/}` (`plan/06`). |
| **Knowledge-base** | Sibling Kotlin/Spring service over HTTP (`plan/07a §"Knowledge-base client"`). |
| **Sandbox** | `LocalSandbox` (`plan/05c`) — runs `Bash` and `Edit` inside the user's cwd. |

## Trigger

```
$ cd ~/Projects/some-rust-crate
$ lamark chat "extract the retry logic in http.rs into a helper module and add tests"
```

Or interactive (no prompt arg): user lands in the TUI and types the request.

## Pipeline

### Step 0 — Bootstrap (cold start, ≤ 800 ms target)

1. **argv fast-path** parses `--help` / `--version` without loading the world (`plan/02 §"8-stage bootstrap"`, via 00d addendum).
2. **`lamark-config`** loads layered config: env → CLI flags → `./.lamark/config.toml` → `~/.lamark/config.toml` → built-in defaults (`plan/03`).
3. **SessionStart hook** fires; user hooks under `~/.lamark/hooks.toml` can inject reminders or abort (`plan/06 §"Hook bus"`).
4. **AGENTS.md walk-up** from cwd; first hit wins. Contents injected into the agent layer of the prompt (`plan/07`).
5. **Provider router** probes the configured `LocalOpenAICompat` endpoint; on failure, prints an actionable doctor hint and exits.
6. **TUI mount** — sticky header + virtualized transcript + composer + status line (`plan/02 §"TUI region model"`).

### Step 1 — User submission

7. User input becomes `Op::UserInput { content: [Text(...)] }` posted to the SQ. The composer also shows token estimate vs context budget.
8. `UserPromptSubmit` hook fires (can mutate or veto the prompt; aggregator is `deny > ask > allow`, per 00d addendum).

### Step 2 — Prompt composition + cache

9. `lamark-prompt` builds the layered system prompt: **override > coordinator > agent > custom > default > append** (`plan/07`).
10. Memory provider (`lamark-memory`) issues `GET /memory/search?q=...` to KB; recalled facts (e.g., "Anna prefers `thiserror` over `anyhow`") are inserted into the agent layer (`plan/07a`).
11. Stable prefix is hashed; if provider is Anthropic-compat, `cache_control` breakpoints are emitted; if it's vLLM/SGLang/llama.cpp, prefix-cache-friendly section order is preserved (`plan/04`, `plan/07 §"Cache"`).

### Step 3 — Inference + tool round trips

12. `InferenceStarted` event → model streams `AgentReasoningDelta` then `AgentMessageDelta`, eventually emitting a tool call (`Read("crates/foo/src/http.rs")`).
13. `ToolCallBegin` → policy check → `Read` is `is_read_only=true` so policy is `Allow` by default; runs in-process (`plan/05 §"Tool trait surface"`, 00d addendum §11).
14. Result returns as a `PayloadRef` (large content is in `payloads/`, not inline in trace). Loop iterates.
15. Agent next calls `Grep("retry", "crates/foo/src/")` and `Read` on a few more files. All `is_read_only`; no prompt.

### Step 4 — Destructive op: Edit + Bash test run

16. Agent emits `Edit { path, old_string, new_string }` for `http.rs` and `Write` for the new `crates/foo/src/retry.rs`.
17. Policy classifies these as `is_destructive=true` → `PermissionRequest` event posted to EQ.
18. TUI shows a **modal diff** (per-patch cache, ANSI gutter, per 00d addendum §16) with **Approve / Approve & remember / Reject** options.
19. Anna approves once with "remember for this path under this session". `Op::ApprovalDecision { Allow }` flows back; `PermissionResolved` event broadcast.
20. Edit-tool contract enforces `old_string` uniqueness + mtime staleness (00c addendum §20). Write is atomic via tmp+rename.
21. Agent runs `Bash("cargo test -p foo")` — also gated; Anna approves. Stdout streams via `ExecCommandOutputDelta`-style events, persisted in `payloads/exec-<id>.log`.

### Step 5 — Turn end + trace flush

22. `TurnComplete { status: Ok }`.
23. Trace recorder flushes `trace.jsonl` and `manifest.json` (`plan/06 §"Trace recorder"`).
24. **Offline reducer** runs (in-process or deferred) → `state.json` graph + `conversation.jsonl` in Nemotron-Agentic-v1 schema.
25. KB client posts `POST /agents/{id}/traces` (reduced bundle). Memory-worthy facts are also posted via `POST /memory/facts` (e.g., "retry helper lives in `crates/foo/src/retry.rs`").
26. `SessionEnded` event; TUI prints cost summary (input/output/cache-read/cache-write tokens) (`plan/02 §"cost-tracker"`).

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0 (bootstrap) | `lamark`, `lamark-config` | 02, 03 |
| 1–2 (SQ post, prompt composer) | `lamark-core`, `lamark-prompt`, `lamark-memory` | 05, 07, 07a |
| 3–4 (inference, tools, policy, hooks) | `lamark-providers`, `lamark-tools`, `lamark-policy`, `lamark-hooks`, `lamark-sandbox` (Local) | 04, 05, 05c, 06 |
| 5 (trace, KB upload) | `lamark-trace`, `lamark-kb-client` | 06, 07a |
| 6 (TUI surface) | `lamark::tui` | 02 |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Provider down** at boot | `doctor` style banner with last-known-good endpoint hint; `chat` aborts before SQ starts; no half-session on disk. |
| **Provider drops mid-stream** | `InferenceFailed`; turn loop emits `TurnAborted`; trace recorder closes the bundle with `status=aborted`; user can retry the last user message (kept on the SQ history). |
| **KB unreachable** | Memory layer falls back to local SQLite mirror (`plan/07a §"offline fallback"`); trace bundle queued in `~/.lamark/outbox/`; backoff retry; user is **not** blocked. |
| **Permission denied (Reject)** | `ToolCallEnd { ok: false, reason: "user denied" }`; agent gets a structured tool error and is expected to re-plan. |
| **Edit `old_string` non-unique / mtime stale** | Tool returns `Conflict`; agent must re-Read the file. (00c addendum §20.) |
| **`cargo test` fails** | Treated as a tool result, not a runtime error. Agent loops with the failure output; user can `Interrupt` (Ctrl-C / `/stop`) → `Op::Interrupt`. |
| **Context overrun** | `CompactionStarted` (auto-trigger). PreCompact/PostCompact hooks fire (00d addendum §31). Trace records the compaction. |
| **Hook timeout / hook deny** | Hook bus aggregates `deny > ask > allow`; tool call is denied or escalated; recorded as `HookCompleted { outcome: Denied }`. |
| **Sandbox refuses a `Bash`** (e.g., command not on policy allowlist) | `PermissionRequest` with policy hint = `Forbidden`; UI hides the Approve option (Reject only). |

## Acceptance criteria

- [ ] `lamark chat "…"` round-trips end-to-end on a local vLLM serving Qwen3-8B and writes a non-empty `~/.lamark/traces/<id>/manifest.json` (P2 exit gate).
- [ ] Read/Grep run without a prompt; Edit/Write/Bash trigger a `PermissionRequest`.
- [ ] Rejecting a write produces a structured tool error visible to the model in the next turn.
- [ ] The reduced bundle (`conversation.jsonl`) validates against the Nemotron-Agentic-v1 schema.
- [ ] A `POST /agents/{id}/traces` is observed against KB (or queued in outbox if KB is offline).
- [ ] Cost summary at session end shows non-zero `cache_read_tokens` on the **second** turn of the same session.
- [ ] After session end, `lamark memory search "retry helper"` returns the fact written during the session.
- [ ] Killing `lamark` with SIGINT during a tool call closes the bundle with `status=aborted` and no orphan child processes.

## Self-improvement assertions

These are checked by the audit sub-agent against `plan/` + by integration
tests at P3 / P4 exit.

1. **Trainer can build SFT samples.** After one session, running the reducer
   over the bundle produces ≥ 1 valid Nemotron-Agentic-v1 `conversation.jsonl`
   entry with non-empty tool_call + tool_result spans.
2. **Trainer can build a DPO pair when a Reject occurs.** A turn where the
   user rejected an Edit and the agent re-planned yields a `(chosen,
   rejected)` trajectory pair the trainer can consume.
3. **Memory recall on next session.** A second `lamark chat` invocation in
   the same repo retrieves the fact written in step 25 via `GET
   /memory/search` and inserts it into the agent prompt layer. The audit
   confirms the recall path is wired end-to-end (`plan/07a`).
4. **Cache hit on second turn.** Within one session, the second turn shows
   non-zero `cache_read_tokens` on a vLLM/Anthropic-compat provider; cache
   strategy is provider-correct (per `plan/04` + `plan/07 §"Cache"`).
5. **Reinforce signal is attached.** Bundle manifest contains a
   `reinforce_signal` field (`success` / `fail` / `unknown`) before KB
   upload, derived from the final tool exit codes + user actions. (This is
   what later lets the trainer up-weight successful trajectories.)
6. **Reducer output is deterministic.** Running `lamark trace reduce` twice
   on the same raw bundle yields byte-identical `conversation.jsonl`. (No
   timestamps / no random IDs in reduced output.) Required for training
   reproducibility.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| `lamark chat` subcommand + TUI | plan/02 §"Subcommand surface", §"TUI region model" | _audit_ |
| Layered config + bootstrap order | plan/03; plan/02 §"8-stage bootstrap" | _audit_ |
| Provider routing + cache breakpoints | plan/04 | _audit_ |
| SQ/EQ turn loop + Op/Event enums | plan/05 §"SQ/EQ event protocol", §"Turn loop" | _audit_ |
| Tool trait (`is_read_only`, `is_destructive`, `check_permissions`) | plan/05 + 00d addendum §10, §11 | _audit_ |
| Edit/Write atomicity + mtime contract | plan/05 + 00c addendum §20 | _audit_ |
| Hook bus + UserPromptSubmit + PermissionRequest | plan/06 | _audit_ |
| Permission policy DSL (Allow/Prompt/Forbidden) | plan/05; plan/06 | _audit_ |
| Trace bundle format + reducer | plan/06 §"Trace recorder", §"Reducer" | _audit_ |
| Memory recall + KB write | plan/07a | _audit_ |
| Prompt cache (Anthropic + prefix) | plan/07 §"Cache" | _audit_ |
| Cost tracker | plan/02 + plan/11 | _audit_ |
| Offline KB fallback (outbox + SQLite mirror) | plan/07a §"offline fallback" | _audit_ |

(Sub-agent fills the Status column.)

## Open questions

1. **What's the first-turn cache strategy on local vLLM?** Anthropic has explicit `cache_control`; vLLM relies on prefix hash. Do we *require* the provider crate to expose `prefix_cache_supported() -> bool` so the prompt composer can switch section order? (Likely ADR.)
2. **Where do per-path remembered permissions live?** Session-scoped (in-memory), repo-scoped (`.lamark/permissions.json`), or user-scoped (`~/.lamark/...`)? `plan/05` mentions the choice but doesn't pick one.
3. **Does the reducer run inline at `TurnComplete` or deferred?** Inline keeps "session ended" observable but adds latency on the close path. Deferred risks data loss if the host crashes before the reducer runs.
4. **What is the canonical AGENTS.md vs CLAUDE.md vs LAMARK.md filename precedence?** `plan/07` and `00b` both reference an AGENTS.md walk-up; we should pin this in an ADR.
5. **Trace bundle retention on disk** — auto-prune after successful KB upload, or keep N days? Affects the outbox design under `plan/07a`.
