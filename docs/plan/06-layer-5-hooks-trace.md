# 06 — Layer 5: Hooks bus + trace recorder

> Two crates, one story: a synchronous event bus plus a passive recorder that
> writes Codex-style bundles to disk and syncs them to knowledge-base.

> 📎 **See also:** [00c addendum §12, §19](./00c-hermes-deepdive-addendum.md) — the
> slash-command registry is a single source of truth shared by CLI / TUI / gateway
> Guard 2 / bot menus / autocomplete; and the reducer must drop `reasoning_content`
> from all but the latest turn when emitting Nemotron-Agentic-v1.
>
> 📎 **See also:** [00d addendum §1–§5, §29, §31](./00d-claude-code-deepdive-addendum.md) —
> the **definitive** hook taxonomy (27 events incl. `Stop`, `TeammateIdle`,
> `Elicitation`, `InstructionsLoaded`), four hook subtypes (command / prompt / agent /
> http) each with own runner, subprocess JSON protocol with **first-line async marker**
> + exit-code-2 = block + interactive prompt round-trips, source layering
> (managed → plugin → project → user → local) + `allow_managed_hooks_only`
> + `disable_all_hooks`, per-event input field tables, `deny > ask > allow` aggregation,
> and PreCompact / PostCompact specifics.

**Crates:** `crates/lamark-hooks/`, `crates/lamark-trace/`.
**Depends on:** `lamark-core` (event types), `lamark-kb-client` (uploads only).
**References:**
- `~/.cache/lemark/vendor/claude-code/types/hooks.ts` — hook taxonomy + sync/async JSON-output contract.
- `~/.cache/lemark/vendor/codex/codex-rs/rollout-trace/src/raw_event.rs` — `RawTraceEventPayload` enum.
- `~/.cache/lemark/vendor/codex/codex-rs/rollout-trace/src/model.rs` — reduced graph.

---

## Part A — Hook bus

### Why a bus

Every cross-cutting concern in the agent — trace, telemetry, UI, gateway, custom user hooks — needs to observe the same stream of events. Calling each subscriber from inside the turn loop hard-codes them into core. A bus inverts that: emit once, N independent subscribers consume.

### Bus API

```rust
// crates/lamark-hooks/src/bus.rs
pub struct HookBus {
    subscribers: DashMap<HookEvent, Vec<HookSubscription>>,
}

pub struct HookSubscription {
    pub id: HookId,
    pub priority: i32,              // lower = earlier
    pub timeout: Duration,
    pub callback: HookCallback,
    pub internal: bool,             // internal hooks bypass user-visible HookStarted/Completed events
}

pub enum HookCallback {
    Sync(Arc<dyn Fn(&HookInput) -> HookOutput + Send + Sync>),
    Async(Arc<dyn Fn(HookInput) -> BoxFuture<'static, HookOutput> + Send + Sync>),
    Subprocess(SubprocessHookConfig),       // exec a binary and read JSON from stdout (Claude-Code shape)
}

impl HookBus {
    pub fn subscribe(&self, event: HookEvent, sub: HookSubscription) -> HookId;
    pub fn unsubscribe(&self, id: HookId);
    pub async fn emit(&self, event: HookEvent, input: HookInput) -> HookEmitOutcome;
}
```

### Hook event taxonomy

Lifted from claude-code mirror's `HookEvent` enum, extended with codex events:

```rust
pub enum HookEvent {
    // Session lifecycle
    SessionStart, Setup, SessionEnd,
    CwdChanged, FileChanged,

    // Conversation
    UserPromptSubmit,
    AgentMessage,
    Notification,

    // Tool flow
    PreToolUse, PostToolUse, PostToolUseFailure,
    PermissionRequest, PermissionResolved, PermissionDenied,

    // Inference flow
    InferenceStarted, InferenceCompleted, InferenceFailed, InferenceCancelled,
    AgentReasoningDelta,                      // optional; high-volume

    // Turn lifecycle
    TurnStarted, TurnCompleted, TurnAborted,

    // Compaction
    CompactionStarted, CompactionCompleted,

    // Multi-agent (plan/05a)
    SubagentSpawned, SubagentStarted, SubagentCompleted, SubagentClosed,
    KanbanCardPosted, KanbanCardClaimed, KanbanCardCompleted,
    KanbanZombieDetected, RalphIterationStarted, RalphLoopComplete,
    SiblingMessageSent,

    // Skills + Curator
    SkillInvoked, SkillCreated, SkillRotated, CuratorRun,

    // Plugin host
    PluginLoaded, PluginCallStarted, PluginCallCompleted, PluginCallFailed,

    // Gateway
    GatewayMessageIn, GatewayMessageOut, GatewayConnected, GatewayDisconnected,

    // MCP
    McpToolCallStarted, McpToolCallCompleted,

    // Worktree
    WorktreeCreated, WorktreeExited,
}
```

### Hook return contract

Synchronous + structured. Modeled on claude-code's `SyncHookJSONOutput`:

```rust
pub struct HookOutput {
    pub continue_: bool,                         // default true; false stops the event chain
    pub stop_reason: Option<String>,
    pub suppress_output: bool,
    pub event_specific: HookEventOutput,         // typed per HookEvent
}

pub enum HookEventOutput {
    PreToolUse {
        decision: Decision,                       // Allow | Prompt | Forbidden
        updated_input: Option<Value>,
        additional_context: Option<String>,
    },
    PostToolUse {
        modified_output: Option<Value>,
        suppress_output: bool,
    },
    UserPromptSubmit {
        additional_context: Option<String>,
        deny_with: Option<String>,
    },
    PermissionRequest {
        decision: Decision,
        update_scope: Option<PolicyScope>,
    },
    /* … one variant per event with non-trivial output … */
    None,                                         // for informational hooks
}
```

### Emit semantics

```rust
pub async fn emit(&self, event: HookEvent, input: HookInput) -> HookEmitOutcome {
    let subs = self.subscribers.get(&event).cloned().unwrap_or_default();
    let mut outcome = HookEmitOutcome::Continue;
    let mut aggregated_context = String::new();

    for sub in subs.iter().sorted_by_key(|s| s.priority) {
        let started = Instant::now();
        if !sub.internal {
            self.emit_internal(HookEvent::HookStarted, /* … */).await;
        }
        let result = tokio::time::timeout(sub.timeout, run_callback(&sub.callback, &input)).await;
        let out = match result {
            Ok(o) => o,
            Err(_) => HookOutput::timeout_default(),
        };
        // If a hook DENIES, short-circuit.
        if !out.continue_ {
            outcome = HookEmitOutcome::Stop {
                hook_id: sub.id,
                reason: out.stop_reason.unwrap_or_default(),
            };
            break;
        }
        // Merge additional_context, updated_input, etc., into the running input.
        merge_event_output(&mut input, out.event_specific, &mut aggregated_context);

        if !sub.internal {
            self.emit_internal(HookEvent::HookCompleted, /* … */).await;
        }
    }
    outcome
}
```

Three guarantees:

1. **Ordering**: subscribers fire in priority order (lower number first).
2. **Timeout**: per-subscription; default 5s; configurable per hook.
3. **Short-circuit**: the first `continue_: false` stops the chain. The agent core treats it as a hard deny.

### Subprocess hooks (claude-code shape)

For user-customizable behavior without restarting the binary, users can configure shell-command hooks in `~/.lamark/hooks.toml`:

```toml
[[hook]]
event = "PreToolUse"
match_tool = "bash"
command = "~/.lamark/scripts/redact-bash-cmd.sh"
timeout = "5s"

[[hook]]
event = "PostToolUse"
match_tool = "write"
command = "git add"                          # auto-stage writes
timeout = "10s"
```

The bus pipes the `HookInput` JSON to stdin and parses `HookOutput` JSON from stdout. Exit code != 0 is treated as `continue_: false` with `stop_reason` from stderr.

### Registration

In bootstrap (plan/03 step 9), every layer subscribes what it needs:

```rust
hooks.subscribe(HookEvent::PreToolUse,    sub_for(policy_engine, Priority::EARLIEST));
hooks.subscribe(HookEvent::InferenceStarted, sub_for(trace_recorder, Priority::LATEST));
hooks.subscribe(HookEvent::ToolCallEnd,   sub_for(trace_recorder, Priority::LATEST));
// ... etc
hooks.subscribe(HookEvent::PostToolUse,   sub_for(memory_consolidator, Priority::DEFAULT));
hooks.subscribe(HookEvent::GatewayMessageIn, sub_for(gateway_authn, Priority::EARLIEST));
```

Trace recorder always subscribes at the latest priority so its captures include the effect of any earlier hook.

### Tests

- **`tests/hooks/order.rs`** — three subscribers with priorities 10, 20, 30 fire in that order.
- **`tests/hooks/deny.rs`** — earlier hook denies → later hooks not invoked.
- **`tests/hooks/timeout.rs`** — a slow subscriber times out; outcome reflects that.
- **`tests/hooks/subprocess.rs`** — shell-command hook reads stdin JSON and emits stdout JSON.

---

## Part B — Trace recorder

### Trace directory layout

Directories are **per-project**, not flat:

```
~/.lamark/traces/
  <project_id>/
    <rollout_id>/
      manifest.json
      trace.jsonl            # append-only, one event per line
      payloads/
        inference-0001-req.json
        inference-0001-resp.json
        tool-0001-in.json
        tool-0001-out.json
        …
      reduced/               # written by `lamark trace reduce`
        conversation.jsonl
        dpo_pairs.jsonl      # if any DPO pairs produced
        kb_upload_state.json # idempotency marker
      meta/
        policy_decisions.jsonl # for forensics
```

`project_id` is derived from the project detection function defined in plan/03. For the global/default project use `_default`.

**Migration from old flat layout:** if `~/.lamark/traces/<rollout_id>/manifest.json` exists and `manifest.json` has no `project_id` field, move the bundle to `_default/<rollout_id>/` on next access.

### Manifest

```json
{
  "schema_version": "1",
  "rollout_id": "01HZ…",
  "agent_id": "…",
  "project_id": "lamark-default",
  "root_thread_id": "t-…",
  "started_at": "2026-05-24T22:30:00Z",
  "ended_at":   null,
  "agent_version": "lamark/0.1.0",
  "model": "Qwen/Qwen3.6-35B-A3B",
  "provider": "vllm",
  "config_hash": "sha256:…",
  "kb_project_id": "lamark-default",
  "source": "cli",
  "reducer_config_hash": "sha256:…"
}
```

### Trace manifest source field

`manifest.json` must include a `source` field that identifies the surface from which the session originated:

```json
"source": "<surface>:<detail>"
```

Valid values:

| Value | Description |
|---|---|
| `"cli"` | Direct TUI/CLI invocation |
| `"webui"` | WebUI browser session (scenario 17) |
| `"remote"` | Remote UI via gRPC (scenario 18) |
| `"gateway:telegram"` | Session initiated through the Telegram gateway adapter |
| `"gateway:slack"` | Session initiated through the Slack gateway adapter |
| `"gateway:discord"` | Session initiated through the Discord gateway adapter |
| `"mcp:<server_id>"` | Session triggered by an MCP client |
| `"acp:<peer_agent_id>"` | Session delegated via ACP |
| `"sandbox:local"` | Subagent running in a local sandbox |
| `"sandbox:docker"` | Subagent running in a Docker sandbox |
| `"sandbox:kubernetes"` | Subagent running in a Kubernetes sandbox |
| `"batch:<batch_run_id>"` | Batch runner session |

The `source` field is set at session creation and never modified thereafter. The trainer uses it to filter samples by surface when constructing fine-tuning datasets.

### Event shape (trace.jsonl)

```json
{
  "seq": 0,
  "wall_time_unix_ms": 1748128345123,
  "thread_id": "t-…",
  "turn_id": "trn-…",
  "kind": "InferenceStarted",
  "payload_ref": "payloads/inference-0001-req.json",
  "inline": null
}
```

`payload_ref` xor `inline`: small bodies (≤ 4 KB) go inline; everything else is in `payloads/`. Decision per event in `lamark-trace::policy::should_inline()`.

### Event variants

Union of:
- **Codex `RawTraceEventPayload`** (rollout, thread, turn, inference, tool, code-cell, compaction, agent-result, edge, mcp-correlation).
- **Lamark additions** matching hook events from Part A (PermissionRequest/Resolved, UserPromptSubmit, SkillInvoked, CuratorRun, GatewayMessageIn/Out, KanbanCardPosted/Claimed/Completed, RalphIterationStarted/LoopComplete, SubagentSpawned/Completed, PluginCallStarted/Completed).

Single Rust enum, `serde(tag="kind")`:

```rust
#[derive(Serialize, Deserialize)]
#[serde(tag = "kind")]
pub enum TraceEvent {
    RolloutStarted   { trace_id: String, root_thread_id: String },
    ThreadStarted    { thread_id: String, agent_path: String, metadata: Value },
    ThreadEnded      { thread_id: String, status: ThreadStatus },
    TurnStarted      { turn_id: String, thread_id: String, prompt_sections: Vec<SectionRef> },
    TurnEnded        { turn_id: String, status: TurnStatus },
    InferenceStarted { call_id: String, model: String, provider: String, request: PayloadRef },
    InferenceCompleted{ call_id: String, usage: UsageStats, response: PayloadRef },
    InferenceFailed  { call_id: String, error: String },
    InferenceCancelled{ call_id: String },
    ToolCallStarted  { tool_call_id: String, name: String, requester: ToolRequester, args: PayloadRef },
    ToolCallEnded    { tool_call_id: String, ok: bool, result: PayloadRef },
    McpToolCallCorrelationAssigned { mcp_call_id: String, tool_call_id: String },
    CodeCellStarted  { runtime_cell_id: String, source_js: String },
    CodeCellEnded    { runtime_cell_id: String, status: CellStatus },
    CompactionRequestStarted { request: PayloadRef },
    CompactionRequestCompleted { response: PayloadRef },
    AgentResultObserved { edge_id: String, child_thread_id: String, message: String, carried_payload: PayloadRef },
    PermissionRequest { request_id: String, summary: String, decision_hint: Decision },
    PermissionResolved { request_id: String, decision: Decision, scope: Option<PolicyScope> },
    UserPromptSubmit  { content: String },
    SkillInvoked      { skill_name: String, version: String },
    CuratorRun        { ran_at: SystemTime, actions: Vec<CuratorAction> },
    GatewayMessageIn  { adapter: String, payload: PayloadRef },
    GatewayMessageOut { adapter: String, payload: PayloadRef },
    SubagentSpawned   { parent_session_id: String, child_session_id: String, spec: PayloadRef },
    SubagentCompleted { child_session_id: String, outcome: PayloadRef },
    KanbanCardPosted  { card_id: String, posted_by: String },
    KanbanCardClaimed { card_id: String, claimed_by: String },
    KanbanCardCompleted { card_id: String, result_ref: PayloadRef },
    RalphIterationStarted { iteration: u32, objective: String },
    RalphLoopComplete { outcome: String },
    SiblingMessageSent { from: String, to: String, payload: PayloadRef },
    PluginCallStarted { plugin_id: String, name: String, args: PayloadRef },
    PluginCallCompleted{ plugin_id: String, name: String, result: PayloadRef },
    HookStarted       { hook_id: String, event: String },
    HookCompleted     { hook_id: String, outcome: String, duration_ms: u64 },
    ProtocolEventObserved { event_type: String, event_payload: PayloadRef },
}
```

### Prompt section IDs in trace

Every `TurnStarted` event carries a `prompt_sections` field listing which prompt composer sections were active at the moment the turn was opened:

```rust
pub struct SectionRef {
    pub section_id: String,   // stable ID assigned by the prompt composer (plan/07)
    pub version: u32,         // monotonic version counter for that section
    pub cache_hit: bool,      // true if the provider returned a cache read hit for this section
}
```

Rules:

- `section_id` values are stable across runs; they come from the prompt composer crate (plan/07) and must not be generated ad-hoc in the trace recorder.
- `version` increments whenever the section's content changes. The recorder reads it from the composer's `SectionMeta` at turn start.
- `cache_hit` is populated after the first `InferenceCompleted` event for the turn and back-filled into the in-memory `TurnStarted` record before it is written to disk. If no inference completed (turn aborted), `cache_hit` defaults to `false` for all sections.
- The field enables the trainer and OPRO optimizer to correlate training outcome with which prompt sections were active, so they can target specific sections for rewrite.

### Recorder API

```rust
pub struct Recorder {
    rollout_id: String,
    root: PathBuf,
    writer: Mutex<BufWriter<File>>,
    payload_dir: PathBuf,
    seq: AtomicU64,
    upload_queue: Option<mpsc::UnboundedSender<UploadJob>>,
    kb: Option<Arc<KbClient>>,
}

impl Recorder {
    pub async fn open(root: PathBuf, rollout_id: String, kb: Option<Arc<KbClient>>) -> Result<Self>;
    pub async fn record(&self, evt: TraceEvent) -> Result<()>;
    pub async fn write_payload(&self, name: &str, body: &[u8]) -> Result<PayloadRef>;
    pub async fn close(self) -> Result<()>;
}
```

`record()` flushes per-event with `O_APPEND` + buffered writer; durable on EOF. The hook bus invokes `record()` synchronously *but on a Tokio task*, never blocking the turn loop on disk.

### Hook subscription

Recorder subscribes once, at lowest priority (=999, runs last), to every hook event. For events that have a 1:1 trace mapping, it records directly. For complex ones (e.g., `InferenceCompleted` carrying a 100KB body), it spills the body into `payloads/` and writes a `PayloadRef`.

### Knowledge-base upload

If `trace.upload_to_kb == true`:

- On `TurnEnded` (status = SessionEnd) or every 30s while a session is long, run the **reducer** in-process, then `POST /agents/{agent_id}/traces` with `{ manifest, reduced_state, conversation }`.
- Upload state is durable in `meta/kb_upload_state.json` (last-seq-uploaded); restarts are idempotent.
- KB unreachable → queue grows; back-off retries; per-bundle TTL = 7 days (older are reduced/local-only).

The agent does NOT block on the upload. If KB falls over, traces still write to disk fine.

### Reducer

```rust
// crates/lamark-trace/src/reducer.rs
pub async fn reduce(bundle_root: &Path) -> Result<Reduced> {
    let mut state = ReducedState::default();
    let trace = read_jsonl(bundle_root.join("trace.jsonl")).await?;
    for evt in trace {
        match evt {
            TraceEvent::TurnStarted { .. } => state.start_turn(&evt),
            TraceEvent::ToolCallStarted { .. } => state.start_tool(&evt),
            TraceEvent::ToolCallEnded { .. } => state.finish_tool(&evt),
            TraceEvent::InferenceCompleted { .. } => state.append_inference(&evt),
            TraceEvent::SubagentSpawned { .. } => state.add_edge_spawn(&evt),
            TraceEvent::SubagentCompleted { .. } => state.add_edge_result(&evt),
            // ...
        }
    }

    let conversation = build_conversation_items(&state, bundle_root).await?;
    let outcome = infer_outcome(&state);
    Ok(Reduced { state, conversation, outcome })
}
```

Output to `reduced/conversation.jsonl` (Nemotron-Agentic-v1 schema; one rollout = one line). DPO pairs, if any, go to `reduced/dpo_pairs.jsonl`.

Multi-turn splitting rule (Nemotron): `reasoning_content` from prior turns is dropped before re-feeding. Reducer implements this verbatim per setup-guide §2.1.

### Reducer determinism

The reducer is a **pure function**: given the same `trace.jsonl` bytes and the same `reducer_version` string (read from config), it always produces byte-identical `reduced/conversation.jsonl` output.

**Content-addressed IDs** — no wall-clock timestamps and no random UUIDs appear in reducer output:

- `turn_id = SHA-256(session_id || turn_index)` where `||` is concatenation with a `:`-separator.
- `sample_id = SHA-256(turn_id || role || content)`.

**Non-determinism is externalised** into `~/.lamark/reducer_config.toml` under the `[determinism]` table:

```toml
[determinism]
seed = 42
algorithm_version = "nemotron-agentic-v1"
```

Both keys are mandatory. The reducer writes `reducer_config_hash = SHA-256(<entire config bytes>)` into `manifest.json` so downstream tools can verify which config produced a given bundle.

**`reasoning_content` stripping:** the `reasoning_content` field is dropped from every assistant turn *except* the final turn in the conversation. This mirrors the Nemotron setup-guide §2.1 rule already noted above; it is listed here because it is part of the determinism contract — any deviation in which turn is considered "final" would change output bytes.

**Empty-turn guard:** after redaction, if any turn's content is empty the reducer returns an error immediately:

```rust
pub enum ReducerError {
    EmptyTurn { turn_id: String },
    // …
}
```

The caller must surface this error and exclude the bundle from the upload queue.

### Reducer output format: Nemotron-Agentic-v1 per-turn rules

This section is the normative spec for what `reduced/conversation.jsonl` contains. Every rule here must be implemented identically in `lamark-trace::reducer` and in the Python `trace_to_messages.py` connector.

#### reasoning_content / `<think>` blocks

Models that support extended thinking (Qwen3, Gemma4 think-mode, Nemotron reasoning variant) emit chain-of-thought in one of two forms depending on the serving stack:

| Provider / config | Form |
|---|---|
| vLLM with `enable_thinking=true` | Separate `reasoning_content` field in the completion JSON; `content` contains only the final answer |
| vLLM with `enable_thinking=false` (or SGLang default) | `<think>…</think>` tags inside `content`; no separate field |
| Anthropic extended thinking | `thinking` content block in the message content array |

**Normalisation rule (recorder):** the `InferenceCompleted` payload stored in `payloads/inference-*-resp.json` preserves the provider's raw form. The reducer normalises at read time:

- If the raw response has a top-level `reasoning_content` field → use as-is.
- If `content` starts with `<think>` → extract the text between the first `<think>` and `</think>` into `reasoning_content`; strip the tags from `content`.
- If provider emits Anthropic `thinking` blocks → concatenate their `thinking` text into `reasoning_content`; remaining `text` blocks form `content`.

**Stripping rule (reducer):** `reasoning_content` is **dropped from every assistant turn except the final assistant turn** of the rollout.

- "Final" means the last `TurnEnded { status: Success | PartialSuccess }` event. If the session ended in `Aborted` or `Interrupted` then *no* reasoning is kept.
- This matches Nemotron setup-guide §2.1 and ensures training signal stays on the answer, not the scratch-pad.
- The `"reasoning": "on"` field at sample root is set when at least one turn originally had `reasoning_content` (i.e., the model ran in think-mode).

**Compaction boundary:** compaction resets the "final turn" counter. If a compaction occurred, each post-compaction segment is treated as its own conversation for the purpose of this rule. See the compaction section below.

**Empty reasoning guard:** if normalisation produces `reasoning_content = ""` after whitespace trimming, omit the field entirely (don't emit `"reasoning_content": ""`).

#### Tool call wire format in conversation.jsonl

The assistant turn that issues tool calls:

```json
{
  "role": "assistant",
  "content": "I'll search for that.",
  "reasoning_content": "…kept only on final turn…",
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "search_codebase",
        "arguments": "{\"query\": \"SearchTerm\", \"file_pattern\": \"*.rs\"}"
      }
    }
  ]
}
```

Rules:
- `content` MAY be an empty string `""` if the model issued tool calls with no preamble text, but the field MUST be present.
- `arguments` is always a **JSON-encoded string** (double-serialised), not an inline object. This matches the OpenAI wire format that Unsloth and TRL expect.
- `id` is the `tool_call_id` from `ToolCallStarted`; it must appear verbatim in the matching tool-result message.
- `type` is always `"function"` in v1.

The tool-result turn (one per tool call):

```json
{
  "role": "tool",
  "tool_call_id": "call_abc123",
  "content": "crates/lamark-trace/src/reducer.rs\ncrates/lamark-trace/src/raw_event.rs"
}
```

Rules:
- `content` is always a string. If the tool returned a structured value, the reducer JSON-serialises it to a string.
- If the tool result was an error (`ToolCallEnded { ok: false }`), the `content` is `"ERROR: <error text>"`. The model must learn to recover from this format.
- `role: "tool"` messages must appear in the same order as their `tool_calls` entries in the preceding assistant message.

#### Parallel tool calls

When a single assistant turn issues multiple tool calls (parallel execution), the assistant message carries all of them in `tool_calls`, and **all** results appear consecutively before the next assistant message:

```json
{"role": "assistant", "content": "", "tool_calls": [
  {"id": "call_1", "type": "function", "function": {"name": "read_file",   "arguments": "{\"path\":\"a.rs\"}"}},
  {"id": "call_2", "type": "function", "function": {"name": "read_file",   "arguments": "{\"path\":\"b.rs\"}"}},
  {"id": "call_3", "type": "function", "function": {"name": "search_codebase", "arguments": "{\"query\":\"Trait\"}"}}
]}
{"role": "tool", "tool_call_id": "call_1", "content": "…"}
{"role": "tool", "tool_call_id": "call_2", "content": "…"}
{"role": "tool", "tool_call_id": "call_3", "content": "…"}
```

The reducer reconstructs parallelism by grouping all `ToolCallStarted`/`ToolCallEnded` events that share the same `turn_id` and have no intervening `TurnEnded` event. Order within the group follows the `seq` field of `ToolCallStarted`.

#### Compaction case

When `CompactionRequestStarted`/`CompactionRequestCompleted` appears in the trace, the context window was summarised mid-session. This is a critical correctness boundary: **the model during compaction did not see the raw messages before the compaction point**, it only saw the summary.

Reducer handling:

1. **Emit a `CompactionMarker` conversation item** at the compaction point:
   ```json
   {
     "role": "system",
     "content": "<compaction_summary>…text from CompactionRequestCompleted response…</compaction_summary>",
     "_lamark_item_kind": "CompactionMarker"
   }
   ```

2. **Split training samples at the compaction boundary.** A session with one compaction produces two training records:
   - **Pre-compaction sample** — messages from session start through the last assistant turn before `CompactionRequestStarted`. Emitted only if it contains ≥ 2 turns and ≥ 1 tool round-trip.
   - **Post-compaction sample** — starts with the system prompt, followed immediately by the `CompactionMarker` (containing the summary), then all turns after `CompactionRequestCompleted`. The model must learn to operate in this continuation context.

3. **reasoning_content reset** — each split segment has its own independent "final turn" for the `reasoning_content` stripping rule. The pre-compaction segment keeps reasoning only on its last turn; the post-compaction segment keeps reasoning only on its last turn.

4. **UUID assignment** — pre-compaction sample gets `uuid = SHA-256(rollout_id + ":pre:" + compaction_seq)`, post-compaction gets `uuid = SHA-256(rollout_id + ":post:" + compaction_seq)`. Multiple compactions produce multiple post-compaction samples indexed by compaction `seq`.

5. **Minimum-length guard** — if the post-compaction segment has fewer than 2 turns, drop it (don't emit a degenerate single-turn sample).

#### Conversation sample root fields

Complete root-level shape of one line in `conversation.jsonl`:

```json
{
  "uuid":      "<SHA-256 per determinism rules>",
  "messages":  [ … ],
  "tools":     [ … ],
  "license":   "internal-proprietary",
  "used_in":   ["lamark"],
  "reasoning": "on | off",
  "source":    "cli | gateway:telegram | …",
  "rollout_id": "<original rollout_id for lineage>",
  "turn_count": 5,
  "tool_call_count": 12,
  "reducer_version": "nemotron-agentic-v1"
}
```

`tools` lists only the tools that were **actually called** in this sample (not the full registry). This keeps the schema size under control and avoids training on a 15KB tool list when the session only used 3 tools. The reducer reads the set of called tool names from `ToolCallStarted` events and looks up their JSON schemas from `lamark-tools` at reduce time.

---

### DPO pair authoring

When the reducer encounters a `ToolCallEnded { ok: false }` event followed by a recovery (i.e., the agent successfully retried the operation in a subsequent turn), it emits a `DpoPair` record to `reduced/dpo_pairs.jsonl`:

```json
{
  "sample_id_prefix": "<sha256>",
  "chosen":   { "sample_id": "<prefix>_c", "trajectory": [ … ] },
  "rejected": { "sample_id": "<prefix>_r", "trajectory": [ … ] }
}
```

Construction rules:

- **`chosen`** is the *actual* trajectory from the recovery turn onward, taken verbatim from `trace.jsonl`.
- **`rejected`** is a *projection* — never executed. It is constructed by replaying the remaining ops as if the failed tool call had propagated without recovery (i.e., the error result is passed through as-is to the model and no retry is attempted). The projection is deterministic given the same `trace.jsonl` and `reducer_config_hash`.
- Both entries carry the same `sample_id` prefix derived from the failed `tool_call_id`; a `_c` / `_r` suffix distinguishes chosen from rejected.
- The blender (plan/10) must never mix `dpo_pairs.jsonl` entries with the SFT stream. Chosen/rejected pairs go exclusively to the DPO training phase.

### Rotation / GC

- Bundles older than `trace.rotate.keep_days` (default 30) are eligible for deletion if `kb_upload_state.confirmed == true`.
- Total disk usage capped at `trace.rotate.max_gb`. LRU eviction.
- Eviction runs as a background tokio task on a 1h interval.

### Privacy

- **Inline redaction** is opt-in (`trace.redact_inline = true`). When on, the recorder runs a fast regex pass (gitleaks-style patterns) over text bodies *before* writing payloads. Stage-2 PII is **never** inline (Presidio is too heavy for the hot path).
- **Mandatory pre-training redaction** still happens in the trainer (plan/10). Inline redaction is an extra layer for shared workstations.

### Tests

- **`tests/recorder/round_trip.rs`** — synthesize 1k events, write, reduce, assert `conversation.jsonl` round-trips through Unsloth's tokenizer.
- **`tests/recorder/crash_recovery.rs`** — kill the recorder mid-write; reopen; the next `record()` continues at next seq.
- **`tests/recorder/kb_upload.rs`** — wiremock the KB; assert the upload payload matches the expected shape.
- **`tests/recorder/payload_dedup.rs`** — same payload body referenced twice → one file, two payload_refs.
- **`tests/reducer/edges.rs`** — synthesize a parent + 3 children; reducer emits 3 spawn edges + 3 result edges.

### Cutover gate (P3 done)

- ✅ Every event in `HookEvent` has a corresponding `TraceEvent`; CI test enforces no orphan events.
- ✅ Reducing a real day's worth of traces produces a `conversation.jsonl` that loads cleanly into Unsloth's data loader (smoke test, plan/10).
- ✅ Knowledge-base upload round-trip: write trace → upload → `GET /agents/{id}/traces?id=…` returns the same `state.json`.
- ✅ Inline redaction is fast: < 10ms p99 on a 64KB body.
- ✅ Crash-recovery: simulated SIGKILL mid-write produces a bundle that the reducer can still process (no corruption, possibly truncated last event).
