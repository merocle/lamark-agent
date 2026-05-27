# 00c — Hermes deep-dive addendum (refinements to layers 4–8)

> Post-architecture-investigation addendum, **2026-05-25**. Folds concrete
> contract details discovered in a deep read of `~/.cache/lemark/vendor/hermes-agent`
> into the existing plan files. No structural change — each section below is a
> *refinement* of an existing layer plan, anchored by file/section pointer.
>
> Also incorporates the Claude-Code and Codex deltas that the same investigation
> made explicit: where each ideology contributes a contract detail we had
> previously listed only as a name.

**Scope:** This addendum supersedes ambiguous contract details in the cited
layer files but does NOT change layer boundaries, crate decomposition, or the
phase order in [00-overview](./00-overview.md). It exists so a reviewer reading
the layer plans for the first time still gets the layer-by-layer view, and a
reviewer pre-implementation gets the post-investigation precision.

---

## How to use this file

Each section names the layer it refines and what it changes. If you are
implementing a layer, read the layer's plan file first, then read **only the
section here that points at it**. If there is a conflict between this addendum
and the layer file, this addendum wins (it's newer).

| Section | Refines | Net change |
|---|---|---|
| §1 Tool-system precision | [05](./05-layer-4-agent-core.md) | Adds toolset distributions, dynamic schema overrides, conflict-group rule, max-result-size, registry generation counter, auxiliary-model auto-approval lane, grace-turn |
| §2 Skills three-tier disclosure & curator policy | [08](./08-layer-7-skills-plugins-curator.md) | Tightens skill loader contract; specifies curator's archive-not-delete policy with tarball backup |
| §3 Plugin context contract | [08](./08-layer-7-skills-plugins-curator.md) | Specifies the `ctx` object passed to `register(ctx)` (`register_hook | register_command | register_provider`) |
| §4 Memory provider lifecycle methods | [07a](./07a-layer-6-memory-and-kb.md) | Adds `post_setup`, `sync_turn(turn_messages)`, `prefetch(query)`, `shutdown` to the trait |
| §5 Two-guard messaging model | [09](./09-layer-8-gateway-integrations.md) | Specifies adapter-queue + runner-interception sequence; commands that must bypass both |
| §6 Platform registry full field list | [09](./09-layer-8-gateway-integrations.md) | Adds `standalone_sender_fn`, `cron_deliver_env_var`, `platform_hint`, `pii_safe`, `max_message_length`, `supports_draft_streaming` |
| §7 MessageEvent / SessionSource shape | [09](./09-layer-8-gateway-integrations.md) | Locks the inbound envelope structure |
| §8 Voice-memo path | [09](./09-layer-8-gateway-integrations.md) | Inbound audio → local cache → STT → text injection |
| §9 ACP fork_session & SessionMode | [09](./09-layer-8-gateway-integrations.md) | Adds fork, plan updates, mode switching |
| §10 MCP server expanded surface | [09](./09-layer-8-gateway-integrations.md) | `events_poll`, `events_wait`, `channels_list` |
| §11 Cron prompt-injection guard | [05a](./05a-coordinator-multi-agent.md) + scheduler | Scan at execution-time, not creation-time |
| §12 Slash-registry single source of truth | [02](./02-layer-1-entry-cli.md) + [06](./06-layer-5-hooks-trace.md) | One definition feeds CLI dispatcher, gateway hooks, bot menus, autocomplete, help, deferred-vs-`--now` invalidation |
| §13 Profile-aware paths invariant | [03](./03-layer-2-config-bootstrap.md) | One helper produces the active home; nothing hardcodes the default |
| §14 Three-loader alignment hazard | [03](./03-layer-2-config-bootstrap.md) | CLI / subcommand / gateway config loaders share defaults |
| §15 Prompt-cache invalidation rule | [07](./07-layer-6-prompt-and-cache.md) | Slash commands that mutate state default to deferred; `--now` is opt-in |
| §16 Trajectory compressor algorithm | [05](./05-layer-4-agent-core.md) compaction + [10](./10-training-pipeline.md) | Protect head + tail; pre-prune tool outputs; summarize middle; splice; iterative merge |
| §17 No change-detector tests | [11](./11-build-test-deploy.md) | Don't lock down enumerable data; test invariants |
| §18 Pinned dependency discipline | [11](./11-build-test-deploy.md) | Bound both lower and upper; commit-pin VCS refs |
| §19 Codex-DNA tightening | [04](./04-layer-3-providers.md), [06](./06-layer-5-hooks-trace.md) | ModelProvider extra-body split; reduced-graph multi-turn rule |
| §20 Claude-Code DNA tightening | [05](./05-layer-4-agent-core.md), [02](./02-layer-1-entry-cli.md) | Edit's old/new uniqueness contract; AGENTS.md / CLAUDE.md walk-up |

---

## §1 — Tool-system precision *(refines plan/05)*

The current [05 §"Tool registry"](./05-layer-4-agent-core.md) defines `trait Tool` with `name / schema / capabilities / category / invoke`. Extend the contract:

```rust
pub trait Tool: Send + Sync {
    fn name(&self) -> &str;
    fn schema(&self) -> &ToolSchema;
    fn capabilities(&self) -> ToolCapabilities;
    fn category(&self) -> ToolCategory;
    fn toolset(&self) -> &str;                          // grouping (file/shell/web/…)

    /// Optional: per-invocation schema overrides. Returns deltas to merge
    /// into the schema before it's shown to the model. Used for runtime
    /// parameters whose limits depend on current config (e.g. delegation
    /// concurrency cap from `agent.delegation.max_concurrent_children`).
    fn dynamic_schema_overrides(&self) -> Option<SchemaPatch> { None }

    /// Optional: cheap probe of whether this tool is currently usable.
    /// Results are cached for 30s by the registry.
    async fn check(&self) -> bool { true }

    /// Optional per-tool override of `agent.tool_result_max_chars`. Some
    /// tools (e.g. file Read) need a higher cap; some need a lower one.
    fn max_result_chars(&self) -> Option<usize> { None }

    async fn invoke(&self, args: Value, ctx: &ToolContext, cancel: CancellationToken) -> ToolResult;
}
```

**Registry generation counter** — every `register / deregister` bumps an `AtomicU64`. `model_tools::definitions_for(toolsets)` is memoized by `(toolsets, generation, config.mtime)`. Required so the prompt cache stays valid across hot-reloaded tools.

**Conflict groups (parallel dispatch)** — already in [05 §"Parallel dispatch"](./05-layer-4-agent-core.md). Make the rule explicit:
- Tools with `capabilities.mutates_path == true` on the same `path` argument serialize.
- All other pairs are parallelizable up to `agent.max_parallel_tool_calls`.
- The path-comparison is canonicalized: `Path::canonicalize()` for local; rendered-string equality for remote envs where canonicalization isn't free.

**Toolset distributions** *(new, for plan/10 batch data gen — not v0.1 runtime)*:

```rust
// crates/lamark-tools/src/distributions.rs
pub struct ToolsetDistribution {
    pub name: String,                              // "default", "image_gen", "browser_tasks", …
    pub weights: HashMap<String, f32>,             // toolset name → probability in [0,1]
}
```

Used by `lamark batch trajectory-gen --distribution browser_tasks` to choose which toolsets to expose per task at sample time, so training data covers the long tail. Not used by interactive `lamark chat`.

**Auxiliary-model auto-approval** *(refines plan/05 §"Approval flow")*:
Before falling through to `Decision::Prompt`, the registry MAY consult `policy.auto_approve.aux_model` (defaults off). If enabled, a small auxiliary model receives `(tool_name, args, summary)` and returns `{approve, deny, defer-to-user}` with a confidence score. Decisions logged to `meta/policy_decisions.jsonl` for forensics. Threshold default: 0.95.

**Grace turn** *(refines [05 §"Turn loop"](./05-layer-4-agent-core.md))*:
When `iteration >= max_iter`, instead of returning `TurnStatus::IterationLimit` immediately, run **one final inference call with `tools: []`** so the model can produce a closing summary. Then return `IterationLimit` with the summary attached. Hard-truncation produces unusable training data — give it a chance to say "I ran out of steps; here's what I did."

---

## §2 — Skills three-tier progressive disclosure & curator policy *(refines plan/08)*

[08 §"Skill system"](./08-layer-7-skills-plugins-curator.md) currently describes the loader. Pin the disclosure contract:

```
Tier 1  skills_list()                  → metadata only (name, description, version, platforms, tags)
Tier 2  skill_view(skill_name)         → SKILL.md body (frontmatter + markdown)
Tier 3  skill_view(skill_name, file)   → linked reference / template / asset
```

Tier 1 is what goes into the system prompt; Tier 2 is loaded on demand when the agent decides to invoke the skill; Tier 3 only when the SKILL.md body references it. **Do not** preload tier 2 or 3 — that defeats the token budget gain that justifies the system.

Frontmatter validation (loader rejects on violation):

| Field | Constraint |
|---|---|
| `name` | ≤ 64 chars, kebab-case |
| `description` | ≤ 1024 chars (claude-code uses ≤ 60 for fit-in-prompt; we relax to 1024 because we tier-1-show all of them) |
| `version` | semver |
| `platforms` | subset of `[macos, linux, windows]`; loader filters by current host |
| `metadata.lamark.tags` | array of strings |
| `metadata.lamark.related_skills` | array of skill names; loader doesn't validate existence (cross-store refs allowed) |
| `metadata.lamark.created_by` | `user | agent | bundled`; required for curator |

**Curator policy** (tightens [08 §"What Curator does"](./08-layer-7-skills-plugins-curator.md)):

- Curator maintains `~/.lamark/skills/.usage.json` sidecar: `{ skill_name: { invocations, last_used_at, last_outcome } }`.
- Curator **never deletes** a skill. It moves stale ones (`created_by: agent` + unused for `stale_after_days`, default 7 for agent-authored, 30 for bundled-modified) to `~/.lamark/skills/.archive/<YYYY-MM-DD>/<name>/`.
- Pre-archive, the curator writes a `tar.gz` of the skill directory to `.archive/<YYYY-MM-DD>/<name>.tar.gz` as a defensive snapshot.
- Pinned skills (`metadata.lamark.pinned: true` in frontmatter) are exempt from all curator state transitions.
- Curator **only touches** skills where `metadata.lamark.created_by == agent`. User-authored and bundled skills are observed (usage tracked) but never archived.

---

## §3 — Plugin context contract *(refines plan/08 §"Plugin host")*

[08 §"Plugin API surface"](./08-layer-7-skills-plugins-curator.md) leaves the `ctx` object underspecified. Lock it:

```rust
// crates/lamark-plugin-host/src/api.rs
pub trait PluginContext: Send + Sync {
    /// Subscribe a callback to a hook event. Returns a guard;
    /// dropping the guard unsubscribes. Plugins may not subscribe
    /// to events with `priority == Priority::EARLIEST`; that slot is
    /// reserved for the policy engine.
    fn register_hook(&self, event: HookEvent, priority: i32, cb: HookCallback) -> HookSubscription;

    /// Add a slash command. Plugin slash commands appear under the
    /// `/<plugin-id>:<cmd-name>` namespace in the registry to avoid
    /// collisions with built-ins.
    fn register_command(&self, name: &str, handler: CommandHandler);

    /// Register a backend provider in a typed slot.
    /// Slots: ImageGen | TTS | STT | Memory | ModelProvider | Sandbox.
    /// On slot collision, **bundled plugins win** over user-installed.
    fn register_provider(&self, slot: ProviderSlot, provider: BoxedProvider);

    /// Read-only metadata.
    fn agent_version(&self) -> &str;
    fn lamark_home(&self) -> &Path;       // already profile-resolved (see §13)
    fn project_id(&self) -> &str;
}
```

**Discovery order:** bundled plugins (`crates/lamark-plugins/<name>/`) load first, then user plugins (`~/.lamark/plugins/<name>/`). On `register_provider` slot collision, the first registration wins → bundled is the floor, user can shadow only if they unregister-then-register (which requires explicit `force = true` in the manifest).

**Plugins MUST NOT:** modify core files at runtime, hold mutable references to `Session`, spawn unmanaged threads, or write outside `lamark_home()`. Violations are detected by capability gating (already in [08 §"Capability gating"](./08-layer-7-skills-plugins-curator.md)).

---

## §4 — Memory provider lifecycle methods *(refines plan/07a)*

[07a §"Memory trait"](./07a-layer-6-memory-and-kb.md) currently defines `write / search / build_prompt_block / reinforce / health`. Add the lifecycle methods that hermes's external providers use:

```rust
pub trait MemoryProvider: Send + Sync {
    // … existing methods …

    /// Called once at agent start after provider construction succeeds.
    /// Use for late-bound auth, schema migration, warmup queries, etc.
    async fn post_setup(&self, agent_id: &str) -> Result<(), MemoryError> { Ok(()) }

    /// Called after each completed turn. Receives the turn's messages
    /// (already redacted if `trace.redact_inline` is on). Providers that
    /// build live models (Honcho's dialectic, Mem0's fact extraction)
    /// consume this; KB/SQLite providers may ignore it.
    async fn sync_turn(&self, ctx: &TurnContext, messages: &[Message]) -> Result<(), MemoryError> { Ok(()) }

    /// Called when a turn starts. Providers can begin background fetches
    /// so `build_prompt_block` returns instantly. Speculative; budget = 0
    /// for the critical path.
    async fn prefetch(&self, ctx: &TurnContext) -> Result<(), MemoryError> { Ok(()) }

    /// Called at agent shutdown. Flush queues, close connections.
    async fn shutdown(&self) -> Result<(), MemoryError> { Ok(()) }
}
```

Default impls are no-ops so existing providers (KB, SQLite) don't need updates.

**Cron sessions opt out of memory by default.** Add to [07a §"Offline mode"](./07a-layer-6-memory-and-kb.md):

```yaml
memory:
  skip_in_cron: true       # cron-spawned sessions do not sync_turn or write
```

Cron jobs trigger short, focused tasks where the upside of memory writes is small (no human in the loop to learn from) and the downside (polluting durable memory with bot-only context) is significant. Override per-job via `cron.jobs[].sync_memory = true`.

---

## §5 — Two-guard messaging model *(refines plan/09 §"Conversation routing")*

[09 §"Conversation routing"](./09-layer-8-gateway-integrations.md) describes the inbound flow but not the *guard sequence*. The gateway has **two sequential interception points**, and most subtle bugs in this layer come from misunderstanding which one a new command needs to bypass:

```
Inbound platform event
        │
        ▼
┌──────────────────────────────┐
│ Guard 1 — Adapter queue       │  per-session asyncio.Event in hermes; in Rust:
│                              │  `Mutex<Option<JoinHandle<Turn>>>` per session_key.
│ If session is currently      │  Defers the message *as a queued user input*.
│ running a turn → queue the   │  Resumed automatically when the active turn ends.
│ message, do not dispatch.    │
└─────────────┬────────────────┘
              │ (queue empty or turn done)
              ▼
┌──────────────────────────────┐
│ Guard 2 — Runner control      │  Looks at the message body. If it matches a
│                              │  *control command* (e.g. `/stop`, `/usage`,
│ Intercepts control commands  │  `/cancel`), handle inline and don't enter
│ before the agent loop sees   │  the agent loop.
│ them.                        │
└─────────────┬────────────────┘
              │ (regular conversational input)
              ▼
       Agent kernel (turn loop)
```

**Commands that must reach the runner while an agent turn is active MUST bypass Guard 1.** Examples: `/stop`, `/cancel`, `/status`. New control commands MUST be dispatched inline by the adapter layer (set `is_control = true` on the parsed message); they should not be queued. Document this in the slash-command registration metadata (§12).

---

## §6 — Platform registry full field list *(refines plan/09 §"Adapter trait")*

[09 §"Adapter trait"](./09-layer-8-gateway-integrations.md) names a trait but not the *adapter registry* alongside. Lock the registry entry shape:

```rust
pub struct PlatformEntry {
    pub name: &'static str,                                            // "telegram", "slack", "discord", …
    pub label: &'static str,                                           // human-readable
    pub adapter_factory: fn(&PlatformConfig) -> Box<dyn PlatformAdapter>,
    pub check_fn: fn() -> bool,                                         // runtime-dep probe
    pub validate_config: fn(&PlatformConfig) -> Result<(), String>,
    pub is_connected: fn(&PlatformConfig) -> bool,
    pub required_env: &'static [&'static str],                         // ["TELEGRAM_BOT_TOKEN", …]
    pub install_hint: &'static str,
    pub max_message_length: usize,                                     // for smart chunking; e.g. 4096 for Telegram
    pub pii_safe: bool,                                                // does session description leak user names?
    pub platform_hint: &'static str,                                   // injected into system prompt
    pub supports_draft_streaming: bool,                                // edit-in-place token streaming?
    pub message_len_fn: fn(&str) -> usize,                             // platform char measurement (UTF-16 vs codepoints)
    pub standalone_sender_fn: Option<StandaloneSenderFn>,              // for out-of-process cron delivery
    pub cron_deliver_env_var: Option<&'static str>,                    // e.g. "TELEGRAM_HOME_CHANNEL"
}
```

**Why each of these matters:**
- `platform_hint`: injected into the system prompt ("you are on IRC; do not use markdown") — markdown rendering varies wildly.
- `supports_draft_streaming`: if true, the gateway can edit one message in place to animate token streaming; if false, it must batch.
- `message_len_fn`: Telegram counts UTF-16 code units, most others count codepoints. Using `str.len()` everywhere breaks emoji.
- `pii_safe`: when false, the source-description that's shown in logs and dashboards must redact user names.
- `standalone_sender_fn`: when cron runs in a separate process from the gateway (e.g. cron container, gateway container), this is how cron delivers without an in-process adapter handle. Returns `Result<MessageId, SendError>`.

---

## §7 — MessageEvent / SessionSource shape *(refines plan/09 §"Conversation routing")*

The inbound envelope coming up from an adapter:

```rust
pub struct MessageEvent {
    pub text: String,
    pub message_type: MessageType,        // Text | Image | Audio | Video | Document
    pub source: SessionSource,
    pub media_paths: Vec<PathBuf>,        // already cached locally; vision/STT tools read these
    pub reply_to_message_id: Option<String>,
    pub reply_to_text: Option<String>,
    pub auto_skill: Option<Vec<String>>,  // per-channel skill binding
    pub channel_prompt: Option<String>,   // ephemeral system-prompt augmentation
    pub channel_context: Option<String>,  // backfilled context between bot turns
    pub internal: bool,                   // synthetic events bypass auth
    pub is_control: bool,                 // see §5
}

pub struct SessionSource {
    pub platform: Platform,
    pub chat_id: String,
    pub user_id: Option<String>,
    pub user_name: Option<String>,
    pub chat_name: Option<String>,
    pub chat_type: Option<ChatType>,      // Dm | Group | Channel | Thread
    pub thread_id: Option<String>,        // forum topics, Discord threads
    pub guild_id: Option<String>,         // Discord guild, Slack workspace, Matrix server
    pub parent_chat_id: Option<String>,
    pub message_id: Option<String>,
    pub chat_topic: Option<String>,
    pub is_bot: bool,
}
```

`session_key = hash(platform, chat_id, user_id_or_chat_id_for_groups)` — deterministic; survives process restart so message history rejoins to a session.

---

## §8 — Voice-memo path *(adds to plan/09)*

When an adapter receives an audio attachment:

1. Adapter downloads bytes, writes to `~/.lamark/cache/media/<sha256>.{ogg|m4a|wav}`.
2. The path goes into `MessageEvent.media_paths`.
3. **Inside the agent kernel, before the model call**, a pre-LLM hook subscribed to `UserPromptSubmit` detects `MessageType::Audio` and routes through the STT provider (`lamark-providers-stt` plugin; v0.1 ships with `whisper.cpp` local).
4. Transcript is injected into `MessageEvent.text` (replacing or augmenting per config); the audio path stays in `media_paths` so the model can still reference it.

STT provider trait belongs in [04](./04-layer-3-providers.md):

```rust
#[async_trait]
pub trait SttProvider: Send + Sync {
    async fn transcribe(&self, audio: &Path, opts: SttOptions) -> Result<Transcript, SttError>;
}
```

v0.1 default: local `whisper.cpp`. v0.2: cloud providers as plugins.

---

## §9 — ACP fork_session & SessionMode *(refines plan/09 Part C)*

[09 Part C](./09-layer-8-gateway-integrations.md) lists the basic ACP surface. The full surface (from hermes-agent's adapter) adds:

```
fork_session(parent_session_id) → new_session_id
  ↳ Creates a branched conversation that shares history up to a savepoint.
  ↳ Use case: editor "try this alternative" branches.

session_update(session_id, kind, payload)
  ↳ Push updates to the client mid-turn: plan changes, mode switches, model
    selection. Kinds:
       PlanUpdate { plan: Vec<PlanStep> }      // translated from todo tool
       ModelChange { model: String }
       ModeChange { mode: SessionMode }
       ModelStateUpdate { state: ModelState }

SessionMode = Thinking | Normal
  ↳ Thinking mode = max_iter higher, reasoning surfaced. Editor UIs use this
    for explicit "deep work" runs.
```

Plan-update translation: when the agent's `todo` tool runs, the ACP adapter watches `ToolCallEnd` for it and translates the resulting JSON into native ACP plan steps so editors with first-class plan UIs (Zed, Cursor) render them natively.

---

## §10 — MCP server expanded surface *(refines plan/09 Part B)*

[09 Part B](./09-layer-8-gateway-integrations.md) lists MCP tools we expose. Add (verified from hermes's `mcp_serve.py`):

| Tool | Purpose |
|---|---|
| `events_poll(conversation_id)` | Non-blocking check: any completed jobs / pending approvals on this session? |
| `events_wait(conversation_id, timeout_ms)` | Blocking poll for a single event with timeout. |
| `channels_list()` | Enumerate cron / delivery channels (TG home channel, Slack channel, …) the agent is allowed to deliver to. |

These let external MCP clients (Claude Desktop, Cursor) drive long-running Lamark sessions without polling our REST endpoints — they can wait on the event stream natively.

---

## §11 — Cron prompt-injection guard *(adds to plan/05a / scheduler)*

[05a §"Ralph /goal loop"](./05a-coordinator-multi-agent.md) covers loops but cron deserves its own guard. The hazard:

A cron job has `prompt: "Daily standup"` and lazily loads skill `engineering-standup` at run time. The skill file's body contains injected instructions (because someone committed a malicious skill). Creation-time validation of `prompt` doesn't catch this; the injection happens at lazy-load.

**Rule:** the assembled final prompt (user prompt + loaded skill content) is scanned **at execution time** by a prompt-injection detector (regex pass for `ignore previous instructions`-class patterns, plus an LLM-judged scan when `cron.deep_injection_scan = true`). On hit, the job is suppressed; an `Event::CronJobInjectionBlocked` is recorded; the operator sees a notification but the prompt is *not* delivered to the model.

---

## §12 — Slash-registry single source of truth *(refines plan/02 + plan/06)*

[02](./02-layer-1-entry-cli.md) lists slash commands separately from gateway control commands (§5). They are the **same registry**.

```rust
pub struct SlashCommand {
    pub name: &'static str,                                  // "model", "tools", "stop", …
    pub aliases: &'static [&'static str],
    pub category: SlashCategory,                             // Core | Session | Ops | Setup | Debug
    pub surfaces: SlashSurfaces,                             // bitset: CLI | TUI | Gateway | BotMenu
    pub is_control: bool,                                    // bypass Guard 1 (§5)? default false
    pub invalidates_cache: CacheInvalidation,                // Deferred (next session) | Immediate (--now opt-in)
    pub usage: &'static str,
    pub help: &'static str,
    pub handler: SlashHandler,
}
```

One definition feeds: CLI dispatcher, TUI autocomplete, gateway control-command interception (Guard 2), Telegram bot's `/<cmd>` menu, Slack subcommand routing, `lamark --help`, and the docs site.

**Cache-invalidation discipline (§15 below):** commands that mutate prompt-affecting state (`/model`, `/tools enable`, `/personality`, `/skill add`) default to `Deferred`; users opt into `Immediate` by adding `--now`. Mid-session immediate invalidation breaks the prompt cache and is therefore opt-in.

---

## §13 — Profile-aware paths invariant *(refines plan/03)*

[03](./03-layer-2-config-bootstrap.md) covers config loading. Lock the path invariant in one helper:

```rust
// crates/lamark-config/src/paths.rs
pub fn lamark_home() -> &'static Path { /* …resolved once at bootstrap… */ }
pub fn lamark_home_display() -> String { /* "~/.lamark" or "~/.lamark/profiles/<name>" */ }
```

Resolution order (bootstrap, before any module-level constant initializes):
1. `--profile <name>` argv flag (strip before clap sees it)
2. `$LAMARK_PROFILE` env var
3. `~/.lamark/active_profile` file
4. default → `~/.lamark`

Once resolved, set `LAMARK_HOME` env var so child processes inherit the same root.

**Rule:** every disk path goes through `lamark_home()`. Hardcoded `~/.lamark` anywhere except the resolver itself is a CI failure. Test scaffolding that mocks `home_dir()` MUST also set `LAMARK_HOME`.

**Gateway-adapter scoping:** when running multiple profiles in parallel against the same physical machine, adapters MUST take an exclusive lock on any credential they consume (e.g. Telegram bot token) to prevent two profiles binding to the same bot. Lock file: `~/.lamark/<profile>/.locks/<adapter>.lock`.

---

## §14 — Three-loader alignment hazard *(refines plan/03)*

Hermes-agent has three config loaders (CLI mode, subcommand mode, gateway-runtime mode) and the project has shipped bugs from defaults drifting between them. **For Lamark: one loader, multiple presets.**

```rust
pub enum LoadPreset {
    Cli,        // adds CLI-only defaults
    Subcommand, // adds subcommand-specific defaults (no `cli` block)
    Gateway,    // no CLI block; gateway block required
}

pub fn load(path: &Path, preset: LoadPreset) -> Result<Config, ConfigError> { … }
```

The DEFAULT_CONFIG values are a single static table; presets only differ in which top-level blocks they require. CI test: load the same file under all three presets, assert all overlapping keys resolve to the same value.

---

## §15 — Prompt-cache invalidation rule *(refines plan/07)*

[07 §"Cache strategies"](./07-layer-6-prompt-and-cache.md) covers the cache backends but not the *invalidation policy* slash commands must obey.

Rule: **within a conversation, system prompt and toolset must remain stable.** Any slash command that mutates state visible to the prompt assembler MUST default to deferred invalidation (takes effect on the next session) and require `--now` for mid-session immediate invalidation.

Examples:
- `/model gpt-4o` → switches at next session boundary.
- `/model gpt-4o --now` → invalidates cache, switches immediately, costs more on the next inference.
- `/tools enable web` → defers.
- `/personality terse` → defers.

Commands that don't affect the prompt (`/usage`, `/stop`, `/status`) take effect immediately and bypass this rule.

---

## §16 — Trajectory compressor algorithm *(refines plan/05 compaction + plan/10)*

The compaction step in [05 §"Compaction"](./05-layer-4-agent-core.md) shows the event flow. Pin the algorithm:

```
1. Protect head:
     - system message
     - first user message
     - first assistant message
     - first tool result (if any)
2. Protect tail:
     - last K turns verbatim (default K=4)
3. Pre-prune middle:
     - For each tool result in the middle span, replace the body
       with a 1-sentence summary derived heuristically:
         file write  → "Wrote N bytes to <path>"
         shell exec  → "Exited <code> in <duration>"
         search      → "Returned N hits"
         http fetch  → "Fetched <url> (status N)"
     - This is cheap; no LLM call. It shrinks the middle so the
       expensive summarization call has less to chew on.
4. Summarize middle:
     - Single LLM call with the (head + pruned middle) as input,
       target output ≤ summary_target_tokens (default 750).
     - Use an auxiliary model (cheaper than the main one); config:
       `agent.compaction.summarizer_model`.
5. Splice:
     - Replace the entire middle span with one synthetic message of
       role `user` (sometimes `system`; configurable) carrying the
       summary.
     - Keep tool-call structural records intact so the model can still
       reference what was done.
6. Iterative merges:
     - If the conversation was already compressed once, merge the
       new summary INTO the prior summary (prepend new before old,
       run summarizer on the concatenation) instead of stacking
       multiple summary blobs.
```

Tokenizer choice for measurement: cheap heuristic (`tiktoken-rs` for OpenAI-family; `tokenizers` for HF-format) — exact tokenizer match isn't required since the budget has slack.

This same algorithm powers the **offline trajectory compressor** in [10](./10-training-pipeline.md) (`lamark trace compress`) when building training data — they share the implementation.

---

## §17 — No change-detector tests *(refines plan/11)*

Tests that hard-code an enumeration count or data-table snapshot fail constantly without catching behavioral regressions. Examples to **NOT** write:

- "There are exactly 42 tools registered" — adding tool #43 fails the test, but no behavior broke.
- "The model catalog has 197 entries" — vendor adds a model overnight, test fails.
- "Default config snapshot matches `tests/snapshots/default_config.yaml`" — every new config key triggers churn.

Write invariant tests instead: "every registered tool has a non-empty `name()` and `schema()`", "every model in the catalog has `context_length > 0`", "`load(default_path)` round-trips through serialize → deserialize unchanged".

---

## §18 — Pinned dependency discipline *(refines plan/11)*

Inherited from hermes after two security incidents (litellm supply-chain compromise; Mini Shai-Hulud worm):

- All `[dependencies]` in any `Cargo.toml` MUST pin both lower bound and upper bound. Example: `tokio = ">=1.40, <2.0"`, not `tokio = "1.40"`.
- Git dependencies MUST pin to a commit SHA, never a branch.
- CI-only / dev-dependencies MUST pin an exact version (`=`).
- Renovate / dependabot configured to surface upper-bound bumps as PRs, not auto-merge.

Workspace-level `Cargo.toml` defines `[workspace.dependencies]` for shared deps; per-crate `Cargo.toml` references via `workspace = true`. Pinning lives in workspace root.

---

## §19 — Codex-DNA tightening *(refines plan/04 + plan/06)*

The codex investigation made two contracts explicit that we previously only named:

**`ModelProvider` extra-body split** *(refines [04](./04-layer-3-providers.md))*:

Different providers carry "extra" request fields in different places — some in `extra_body` (passed through to the API as-is), some at the top level of the request object. The trait must split:

```rust
pub trait ModelProvider: Send + Sync {
    /// Hook to inject provider-specific fields. Returns:
    /// `(extra_body, top_level_kwargs)`
    /// Caller merges `extra_body` into the request's `extra_body` field,
    /// and `top_level_kwargs` directly into the request object.
    fn build_extra_fields(&self, ctx: &RequestContext) -> (Map<String, Value>, Map<String, Value>);

    // … existing methods …
}
```

Example: Anthropic puts `thinking: { type: "enabled", budget_tokens: 8000 }` at the top level. OpenRouter puts `reasoning: { effort: "high" }` in `extra_body`. The same logical "reasoning effort" maps differently per provider; the trait keeps that abstraction.

**Reduced-graph multi-turn rule** *(refines [06 §"Reducer"](./06-layer-5-hooks-trace.md))*:

When reducing for Nemotron-Agentic-v1 training data: **drop `reasoning_content` from all prior turns before re-feeding**. Only the *latest* turn's reasoning is kept. This is required by the schema; the reducer must enforce it (the current plan-06 text mentions it as a setup-guide rule; pin it as a reducer invariant with a unit test).

---

## §20 — Claude-Code DNA tightening *(refines plan/05 + plan/02)*

The Claude-Code investigation pinned two contracts we should match exactly:

**Edit tool's old/new uniqueness contract** *(refines [05 §"Built-in tools"](./05-layer-4-agent-core.md) Edit row)*:

The `Edit` tool's `old_string` MUST be unique in the target file. If it appears multiple times, the tool returns an error with the count. Users have the option of providing a larger `old_string` with surrounding context to make it unique, OR setting `replace_all: true`. **Do not auto-pick the first occurrence** — that's a footgun that has caused real damage in production agents.

```rust
ToolCapabilities {
    mutates_path: true,
    error_on_ambiguous_match: true,    // ← Edit-specific; Patch differs
    ...
}
```

**AGENTS.md / CLAUDE.md / LAMARK.md walk-up** *(refines [02](./02-layer-1-entry-cli.md) bootstrap and [07](./07-layer-6-prompt-and-cache.md) prompt composer)*:

On agent start, walk up from cwd to `/`, collecting any file named (in order of precedence) `LAMARK.md`, `AGENTS.md`, `CLAUDE.md`. **First wins.** Each one found is loaded as a Tier-2 (mid-stability) prompt section. This gives users a per-project + per-monorepo + per-org context fabric without configuration. Files outside the user's home are ignored (don't pick up `/etc/AGENTS.md`).

---

## Implementation checklist

When you touch a layer file, scan the addendum sections targeting that file (see the table at the top). If you implement contracts that are NOT in the addendum or layer file yet, add them here first, then to the layer file — this addendum is the staging area for contract decisions made after the layer plans were frozen.

When this addendum exceeds ~600 lines or covers a fundamentally new layer, **promote it**: split into the affected layer files, drop the addendum, recreate it empty for the next round.
