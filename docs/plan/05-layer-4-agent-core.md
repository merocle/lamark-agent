# 05 — Layer 4: Agent core (turn loop, tools, environments)

> The heart of the runtime. The `AIAgent` from hermes-agent, re-implemented in
> Rust, with Codex's SQ/EQ event protocol underneath.

> 📎 **See also:** [00c addendum §1, §16, §19, §20](./00c-hermes-deepdive-addendum.md) — adds
> `dynamic_schema_overrides`, registry generation counter, conflict-group rule precision,
> auxiliary-model auto-approval lane, grace-turn, full compaction algorithm, Codex
> `extra_body` split, and Edit-tool old/new uniqueness contract.
>
> 📎 **See also:** [00d addendum §10, §11, §13, §28, §29, §31](./00d-claude-code-deepdive-addendum.md) —
> the complete Claude-Code Tool trait surface (`validate_input` + `check_permissions` +
> `is_concurrency_safe` + `is_read_only` + `is_destructive` + `interrupt_behavior` +
> `max_result_size_chars` + `strict`), Edit-tool atomicity + mtime staleness, MCP tool
> wrapping (`mcp__<server>__<tool>` + meta passthrough), upstream-proxy pattern for
> sandbox subprocesses, `deny > ask > allow` aggregation across hooks + policy, and
> PreCompact / PostCompact hook semantics.

**Crates:** `crates/lamark-core/`, `crates/lamark-tools/`, `crates/lamark-sandbox/`.
**Depends on:** `lamark-providers`, `lamark-hooks`, `lamark-policy`, `lamark-prompt`, `lamark-cache`.
**References:**
- `~/.cache/lemark/vendor/hermes-agent/agent/conversation_loop.py:1` — loop shape.
- `~/.cache/lemark/vendor/codex/codex-rs/core/src/session/turn.rs:131` — Rust loop shape with SQ/EQ.
- `~/.cache/lemark/vendor/codex/codex-rs/protocol/src/protocol.rs:1137` — `EventMsg`.

## The three crates

```
crates/lamark-core/         — types, traits, turn loop
crates/lamark-tools/        — tool registry + built-in tools
crates/lamark-sandbox/      — Sandbox trait + backends (local, docker, ssh) + agent hosting (see plan/05c)
```

`core` calls `tools` calls `sandbox`. No back-edges.

## SQ/EQ event protocol (from codex-rs)

Two queues drive a session:

```rust
// Submissions come from the UI/gateway/MCP-server: "do this."
pub struct Submission { pub id: SubmissionId, pub op: Op }
pub enum Op {
    UserInput { content: Vec<ContentBlock> },
    Interrupt,                          // cancel current turn
    ApprovalDecision { request_id: RequestId, decision: Decision },
    Compact { strategy: CompactStrategy },
    Shutdown { grace: Duration },
}

// Events come back to ANY subscriber (TUI, gateway, trace recorder, hooks).
pub enum Event {
    TurnStarted    { turn_id: TurnId, prompt_tokens: u32 },
    TurnComplete   { turn_id: TurnId, status: TurnStatus },
    TurnAborted    { turn_id: TurnId, reason: String },
    InferenceStarted   { call_id: InferenceId, model: String, provider: String },
    InferenceCompleted { call_id: InferenceId, usage: UsageStats },
    InferenceFailed    { call_id: InferenceId, error: ProviderError },
    AgentMessageDelta  { call_id: InferenceId, content: String },
    AgentReasoningDelta{ call_id: InferenceId, content: String },
    ToolCallBegin      { call_id: ToolCallId, name: String, args: Value },
    ToolCallEnd        { call_id: ToolCallId, ok: bool, result_ref: PayloadRef },
    PermissionRequest  { request_id: RequestId, summary: String, decision_hint: Decision },
    PermissionResolved { request_id: RequestId, decision: Decision },
    CompactionStarted  { reason: CompactReason },
    CompactionCompleted{ pre_tokens: u32, post_tokens: u32 },
    HookStarted        { hook_id: String, event: String },
    HookCompleted      { hook_id: String, outcome: HookOutcome },
    SubagentSpawned    { child_session_id: SessionId, parent_turn_id: TurnId },
    AgentMessage       { content: String, turn_id: TurnId },
    UserMessage        { content: String, turn_id: TurnId },
    SessionStarted     { session_id: SessionId, rollout_id: RollotId },
    SessionEnded       { session_id: SessionId },
    Error              { context: String, cause: String },
}
```

Each subscriber (TUI, trace recorder, hook bus, gateway WS connection) gets its own MPSC receiver. The dispatcher in `Session` fans out via `tokio::sync::broadcast`.

## Turn loop

```rust
// crates/lamark-core/src/turn.rs
pub async fn run_turn(
    sess: &Session,
    submission: Submission,
    cancel: CancellationToken,
) -> Result<TurnStatus, AgentError> {
    let turn_id = TurnId::new();
    sess.emit(Event::TurnStarted { turn_id, prompt_tokens: 0 });

    let mut iteration = 0;
    let max_iter = sess.config.agent.max_iterations;

    loop {
        if cancel.is_cancelled() {
            sess.emit(Event::TurnAborted { turn_id, reason: "cancelled".into() });
            return Ok(TurnStatus::Cancelled);
        }
        if iteration >= max_iter {
            return Ok(TurnStatus::IterationLimit);
        }

        // 1. Compose the request (prompt composer + conversation history + tools).
        let req = sess.build_complete_request(&submission)?;

        // 2. Hooks: PreInference.
        sess.hooks.emit("InferenceStarted", &req).await;

        // 3. Stream inference.
        let mut stream = sess.providers.complete(req, cancel.child()).await?;
        let mut tool_calls = vec![];
        let mut text_buf = String::new();
        while let Some(evt) = stream.next().await {
            match evt {
                CompleteEvent::DeltaText { content } => {
                    text_buf.push_str(&content);
                    sess.emit(Event::AgentMessageDelta { /*…*/ });
                }
                CompleteEvent::DeltaReasoning { content } => {
                    sess.emit(Event::AgentReasoningDelta { /*…*/ });
                }
                CompleteEvent::ToolCallReady { id, name, args } => {
                    tool_calls.push(ToolCallRequest { id, name, args });
                }
                CompleteEvent::Finish(reason) => break,
                CompleteEvent::Error(e) => {
                    sess.emit(Event::InferenceFailed { /*…*/ });
                    return Err(e.into());
                }
                _ => {}
            }
        }
        sess.emit(Event::InferenceCompleted { /*…*/ });

        // 4. If no tool calls, we're done.
        if tool_calls.is_empty() {
            sess.append_assistant_message(text_buf, turn_id);
            sess.emit(Event::TurnComplete { turn_id, status: TurnStatus::Success });
            return Ok(TurnStatus::Success);
        }

        // 5. Dispatch tool calls in parallel up to max_parallel_tool_calls.
        let results = sess.tools.dispatch_many(
            tool_calls,
            &sess,
            cancel.child(),
        ).await;

        // 6. Append tool messages to history; loop.
        for r in results {
            sess.append_tool_message(r);
        }
        iteration += 1;
    }
}
```

This mirrors `~/.cache/lemark/vendor/hermes-agent/agent/conversation_loop.py:125-136` but every side-effect is now an event (`emit`) and every IO is cancellable.

## Session state

```rust
pub struct Session {
    pub id:        SessionId,
    pub rollout:   RollotId,
    pub agent_id:  String,
    pub config:    Arc<Config>,
    pub providers: Arc<dyn ProviderRouter>,
    pub tools:     Arc<ToolRegistry>,
    pub hooks:     Arc<HookBus>,
    pub prompt:    Arc<PromptComposer>,
    pub memory:    Arc<dyn MemoryProvider>,
    pub policy:    Arc<PolicyEngine>,
    pub conversation: Mutex<Conversation>,
    pub event_tx:  broadcast::Sender<Event>,
    pub cancel:    CancellationToken,
}
```

`Conversation` is a Vec<Message>; messages are content-block lists, not strings. Stored in memory; checkpointed to trace recorder on every TurnComplete.

## Tool registry

```rust
// crates/lamark-tools/src/registry.rs
pub trait Tool: Send + Sync {
    fn name(&self) -> &str;
    fn schema(&self) -> &ToolSchema;
    fn capabilities(&self) -> ToolCapabilities;
    fn category(&self) -> ToolCategory;     // file/shell/web/search/memory/...
    async fn invoke(
        &self,
        args: Value,
        ctx: &ToolContext,
        cancel: CancellationToken,
    ) -> ToolResult;
}

pub struct ToolRegistry {
    by_name: HashMap<String, Arc<dyn Tool>>,
    toolsets: HashMap<String, Vec<String>>,    // "file" -> [Read, Write, Edit, ...]
}
```

Registration is **explicit** (no inventory/ctor-time global registry), happening once at runtime build:

```rust
let mut reg = ToolRegistry::new();
reg.register(BashTool::new(envs.clone()));
reg.register(ReadFileTool::new());
reg.register(WriteFileTool::new());
reg.register(EditFileTool::new());
reg.register(GrepTool::new());
reg.register(GlobTool::new());
reg.register(WebSearchTool::new(http.clone()));
reg.register(WebFetchTool::new(http.clone()));
reg.register(MemorySearchTool::new(memory.clone()));
reg.register(MemoryWriteTool::new(memory.clone()));
reg.register(TaskCreateTool::new());
reg.register(AgentTool::new(/* recursive session factory */));
reg.register(McpProxyTool::new(mcp.clone()));
// ... ~40 tools total at v0.1
```

## Built-in tools (v0.1 catalog)

Ports of the most-used ~40 hermes tools (a subset of the ~70 superset). Organized into categories matching the codex/Claude-Code patterns:

| Category | Tools |
|---|---|
| File | `Read`, `Write`, `Edit`, `Glob`, `Stat` |
| Shell | `Bash`, `PowerShell` (Windows future) |
| Search | `Grep`, `WebSearch`, `WebFetch` |
| Task | `TaskCreate`, `TaskUpdate`, `TaskGet`, `TaskList`, `TaskStop` |
| Memory | `MemorySearch`, `MemoryWrite`, `MemoryDelete`, `UserProfileGet` |
| Skills | `SkillView`, `SkillInvoke`, `SkillNew`, `SkillManage` |
| MCP | `MCPProxy` (catch-all for consumed MCP servers) |
| Agents | `Agent` (spawn sub-agent), `SubagentSummary` |
| Worktree | `WorktreeCreate`, `WorktreeExit` |
| Interactive | `AskUserQuestion`, `ApprovalRequest` |
| Misc | `Sleep`, `ScheduleCron`, `RemoteTrigger`, `ToolSearch`, `Brief` |

Each tool is its own module. Each module exports its `Tool` impl, its prompt-string (markdown description for the system prompt), and its tests.

## ToolContext

What a tool gets when invoked:

```rust
pub struct ToolContext {
    pub session_id: SessionId,
    pub turn_id:    TurnId,
    pub call_id:    ToolCallId,
    pub cwd:        PathBuf,
    pub sandbox:    Arc<dyn Sandbox>,                // local / docker / ssh — see plan/05c
    pub policy:     Arc<PolicyEngine>,
    pub hooks:      Arc<HookBus>,
    pub memory:     Arc<dyn MemoryProvider>,
    pub kb:         Arc<KbClient>,
    pub config:     Arc<Config>,
    pub ask_user:   Arc<dyn AskUserChannel>,        // for ApprovalRequest, AskUserQuestion
    pub user_meta:  HashMap<String, Value>,
}
```

The tool decides whether to call `policy.evaluate(tool_name, args)` itself or trust the registry-level check. Default: registry checks first; tool can request elevation.

## Parallel dispatch

```rust
pub async fn dispatch_many(
    &self,
    calls: Vec<ToolCallRequest>,
    sess: &Session,
    cancel: CancellationToken,
) -> Vec<ToolResult> {
    let max_parallel = sess.config.agent.max_parallel_tool_calls;

    // Conflict detection: file-writing tools on the same path serialize.
    let groups = self.compute_conflict_groups(&calls);

    let mut results = Vec::with_capacity(calls.len());
    for group in groups {
        let futs = group.into_iter().map(|c| self.dispatch_one(c, sess, cancel.child()));
        let chunk: Vec<_> = futures::future::join_all(futs).await;
        results.extend(chunk);
    }
    results
}
```

Conflict groups: `Write/Edit` on the same file path go in the same group; everything else is parallel. (Matches hermes-agent's path-scoped concurrency, see hermes `model_tools.py:47-82`.)

## Approval flow

Before any tool with `capabilities.requires_approval == true`:

```rust
let decision = self.policy.evaluate(&tool.name(), &args, ctx);
let final_decision = match decision {
    Decision::Allow => Decision::Allow,
    Decision::Forbidden => Decision::Forbidden,
    Decision::Prompt => {
        let req_id = RequestId::new();
        sess.emit(Event::PermissionRequest {
            request_id: req_id,
            summary: tool.summary(&args),
            decision_hint: Decision::Prompt,
        });
        let resolved = sess.ask_user.await_decision(req_id, cancel.child()).await?;
        resolved
    }
};
```

The TUI / gateway / MCP-server is responsible for resolving `Decision::Prompt` to `Allow | Forbidden | Allow-once | Allow-and-remember`. Resolution arrives as a `Submission::ApprovalDecision`.

## Sandbox: trait + backends

> Full spec lives in **[plan/05c](./05c-sandbox-and-agent-hosting.md)**. This section is the summary view for the agent-core reader.

The `Sandbox` trait is the isolation boundary. It runs **commands** (the shell/file/grep tool surface) **and** hosts whole **subagents** — same trait, two surfaces.

```rust
// crates/lamark-sandbox/src/trait.rs
#[async_trait]
pub trait Sandbox: Send + Sync {
    fn name(&self) -> &str;
    fn capabilities(&self) -> SandboxCapabilities;

    // command / file IO surface
    async fn spawn(&self, cmd: ShellCommand, cancel: CancellationToken)
        -> Result<Box<dyn ProcessHandle>, SandboxError>;
    async fn read_file  / write_file / list_files / copy_in / copy_out / workspace_root;
    fn translate_path(&self, host_path: &Path) -> Result<PathBuf, SandboxError>;

    // agent-hosting surface (the load-bearing piece — see 05c)
    async fn spawn_agent(&self, spec: AgentSpec, cancel: CancellationToken)
        -> Result<Box<dyn AgentHandle>, SandboxError>;

    async fn health_check(&self) -> Result<HealthReport, SandboxError>;
    async fn lifecycle_close(&self) -> Result<(), SandboxError>;
}
```

`ProcessHandle` exposes stdin/stdout/stderr + `kill_group` + `wait`. `AgentHandle` mirrors the SQ/EQ contract: `submit(Op)` in, `events()` out, `wait()` returns `AgentOutcome { trace_bundle, usage, status }`.

### Backends shipping in v0.1 (in-tree)

| Sandbox | Role |
|---|---|
| `LocalSandbox` (in-process) | Default for the dev loop and trusted coordinator subagents. `spawn_agent` runs a child `Session` on a `tokio::task`. |
| `LocalSandbox` (forked-process) | Same trait, isolates child as a separate `lamark agent run` process; `PR_SET_PDEATHSIG` on Linux. Used for `/spawn` and untrusted-prompt subagents on the dev box. |
| `DockerSandbox` | Default for untrusted prompts and prod. One container per child; `cap-drop=ALL`, `no-new-privileges`, tmpfs workspace, egress filtered to model provider only (by default). |
| `SshSandbox` | Run an agent on a remote host the user controls; tunnels SQ/EQ over `-L unix:…`. |

### Post-v0.1 plugins (registered via the `Sandbox` provider slot, plan/00c §162 + plan/08)

`ModalSandbox`, `DaytonaSandbox`, `SingularitySandbox`, `VercelSandbox`. The trait is the contract; these are additional impls.

Each in-tree backend is feature-flagged (`features = ["sandbox-docker", "sandbox-ssh"]`) so a `LocalSandbox`-only build stays small.

See plan/05c for the full design: agent-hosting semantics, `lamark agent run` in-sandbox entrypoint, egress policy (`None` / `ModelProviderOnly` / `Allowlist`), tool-proxy modes (`Inherit` / `Restricted` / `ProxyToParent`), trace-bundle pull-back, budget enforcement, and protocol reuse with plan/13 (Remote UI).

## Tool-call cancellation

When the user hits Ctrl-C, `cancel.cancel()` is called. Every tool's `invoke` must respect:
- `tokio::select!` between the work and `cancel.cancelled()`.
- For child processes (`Bash`): kill the process group on cancel.
- For HTTP (`WebFetch`): drop the future; reqwest cancels in-flight.

Tools that violate this (block forever despite cancel) fail the policy test in CI.

## Compaction

When `prompt_tokens >= ctx_limit * 0.85`:

1. `Event::CompactionStarted` emitted.
2. `lamark-prompt::compactor::compact(&conversation, &cfg)` produces a new shorter conversation:
   - Preserve system prompt + last K turns verbatim.
   - Summarize the middle via the same provider (uses a "compaction" hidden tool).
   - Memory snapshot is rebuilt from scratch (volatile section).
3. `Event::CompactionCompleted { pre_tokens, post_tokens }`.

The trace recorder records both summaries and the compaction reference (matches codex's `CompactionRequestStarted/Completed`).

## Errors

```rust
pub enum AgentError {
    ProviderUnavailable(ProviderError),
    ContextLimitExceeded { used: u32, limit: u32 },
    ToolFailed { call_id: ToolCallId, error: String },
    Cancelled,
    PolicyForbidden { tool: String, reason: String },
    HookDeny { hook: String, reason: String },
    SessionAlreadyClosed,
    Other(String),
}
```

## Edit-tool atomicity (old_string uniqueness + mtime staleness)

The built-in `edit_file` tool enforces these invariants on every invocation:

1. **Uniqueness check.** Before applying an edit, verify that `old_string` appears **exactly once** in the current file content. If it appears 0 or ≥2 times, return `ToolCallResult::Err("old_string not unique — provide more context")`. Do not apply any partial write.

2. **Staleness check.** Before applying, compare the file's current `mtime` against the `mtime` captured in the `TurnStarted` event (or the `mtime` recorded by the most recent prior edit in this turn — see point 4 below). If `mtime` changed, return `ToolCallResult::Err("file modified since read — re-read before editing")`.

3. **Atomic write.** Apply the edit by writing to `<path>.lamark_tmp`, calling `fsync` on the temp file, then renaming it over the original. Never leave a partial write visible to the file system.

4. **mtime bookkeeping.** After the rename completes, record the new `mtime` in the turn state (`TurnMtimeMap`). Subsequent edits to the same file within the same turn consult this updated value rather than the original `TurnStarted` snapshot.

## Permission UX in headless surfaces

When a session runs without a TUI (gateway, MCP server, ACP inbound), the permission bus uses surface-specific resolution:

- **Gateway (Telegram/Slack/Discord).** Inline prompt strategy. Send a message to the conversation containing the tool name, an argument summary, and three reply options (`Allow`, `Allow-session`, `Deny`). Block the turn for up to `permission_prompt_timeout` seconds (default 300 s). A timeout is treated as `Deny`. The gateway adapter wraps this exchange into a `PermissionRequest` forwarded to the session's permission bus.

- **MCP server.** Because the MCP caller (e.g., Claude Desktop) cannot receive modal prompts, use the **pre-grant model**: the MCP server config (`mcp_servers.toml`) must specify `grant = ["tool_name", ...]` for any tool that would normally require approval. Tools absent from the grant list resolve immediately to `Decision::Deny`. No interactive prompts are issued in MCP server mode.

- **ACP inbound.** Apply local policy rules (see §Policy enforcement on inbound ACP tasks). If the policy result is `Prompt`, default to `Decision::Deny` after `auto_deny_after_seconds` (default 0 = immediate deny) unless a `pre_grant` list overrides it.

- **Remote WS (scenario 18).** Forward a `PermissionRequired` event to the connected WebSocket client. Block until a `PermissionResponse` message arrives or the timeout elapses.

## MCP tool destructiveness default

When an external MCP server lists tools via `tools/list` and a tool's `inputSchema` does not include an `"x-lamark-destructive": false` annotation, the tool is registered as `is_destructive = true` by default. This means it requires `Decision::Prompt` (or an explicit Allow rule in `policy.toml`) before it may execute.

To mark a tool as non-destructive, the MCP server must include `"x-lamark-destructive": false` in the tool's JSON Schema object. This conservative default prevents accidental writes or side-effects through unvetted external servers.

## Policy enforcement on inbound ACP tasks

When an external agent delegates a task via ACP inbound, the local policy is applied as follows:

- The inbound task's `tool_calls` are evaluated against `policy.toml` as if they were issued by a local session, **but** with an additional context field `source = acp:<peer_agent_id>`.
- A policy rule may use `source` as a matching condition:
  ```toml
  [allow]
  tool = "read_file"
  source_prefix = "acp:trusted_"
  ```
- If no matching rule exists, `Decision::Prompt` applies (same as local). In headless mode (no TUI), `Decision::Deny` applies instead (see §Permission UX in headless surfaces).
- The `peer_agent_id` is the ACP registry entry's `agent_id`, verified against the ACP handshake certificate presented during connection setup.

## WebSocket sequence numbers and reconnect

Every `WsServerFrame` pushed to the browser carries a monotonically increasing `seq: u64` field, starting at 1 for each session. The server maintains an in-memory ring buffer of the last 1 000 frames per session (keyed by `session_id`). On reconnect:

1. The client sends `{ "kind": "Reconnect", "last_seq": N }` as the first WebSocket message after re-establishing the connection.
2. The server replays all frames with `seq > N` from the ring buffer.
3. If `N` is older than the buffer (i.e., `N < min_buffered_seq`), the server sends `{ "kind": "ReplayGap", "first_available_seq": M }` and the client must reload full session state from scratch via `GET /api/sessions/{id}/snapshot`.

The ring buffer is in-memory per session and is **not** persisted across server restarts.

## WebSocket lifecycle ↔ session lifecycle

When the WebSocket connection closes:

1. The server starts a `ws_close_grace_seconds` timer (default 30 s) and waits for a reconnect (see §WebSocket sequence numbers and reconnect).
2. If no reconnect arrives within the grace period, the server enqueues `Op::SessionEnd { reason: ConnectionLost }` on the session's SQ.
3. The turn loop processes `SessionEnd` normally: flushes the recorder, runs the reducer, and marks the bundle `status = completed` (or `status = aborted` if a turn was in flight at the time of closure).
4. If the session was already idle (no in-flight turn), `SessionEnd` runs synchronously without waiting.
5. If the server process exits before the grace period elapses, the recorder's fsync guarantees ensure that all already-flushed events are recoverable. A `recover_incomplete_bundles` task running at next startup marks any such bundles `status = aborted`.

## Tests

- **`tests/core/turn_loop.rs`** — feed a scripted provider that emits a tool call → assert tool dispatched + result fed back → next inference → no tool call → TurnComplete.
- **`tests/core/conflict_groups.rs`** — two writes to the same path serialize; two reads parallelize.
- **`tests/core/cancel.rs`** — Ctrl-C mid-tool kills the bash subprocess; no zombie processes.
- **`tests/core/compaction.rs`** — fill the context to 85%, observe compaction emits the right events and shrinks the prompt.
- **Sandbox conformance + per-backend integration tests** — see plan/05c §Tests.

## Cutover gate (P2 done)

- ✅ `lamark chat "ls /tmp"` produces:
  1. A Bash tool call.
  2. A successful tool result.
  3. A natural-language reply.
- ✅ The full conversation is observable from a `tokio::sync::broadcast` subscriber.
- ✅ A `tokio::time::timeout(Duration::from_secs(60))` wrapper succeeds — no hangs.
- ✅ Ctrl-C cancels the in-flight bash subprocess; second Ctrl-C exits the binary.
- ✅ Trace bundle on disk contains `TurnStarted → InferenceStarted → ToolCallBegin → ToolCallEnd → InferenceStarted → InferenceCompleted → TurnComplete`.
