# 00d — Claude Code deep-dive addendum (refinements to layers 1–9)

> Post-investigation addendum, **2026-05-25**. Folds concrete contract details
> from a deep read of `~/.cache/lemark/vendor/claude-code` into the existing
> plan files. Companion to [00c](./00c-hermes-deepdive-addendum.md) (which
> covers hermes-agent). No structural change to the layer plans — each section
> below is a *refinement* anchored by a file/section pointer.
>
> Where 00c and this file disagree, **this file wins for Claude-Code-DNA
> features** (hooks taxonomy, slash-command shape, skill loader,
> permission-first defaults, diff/Edit affordances, vim mode, dialogs,
> coordinator pattern, memdir).

**Scope:** Same rules as 00c. Read the layer file first, then the section
here that targets it. On conflict between this addendum and the layer file,
this addendum wins (it's newer).

---

## How to use this file

| Section | Refines | Net change |
|---|---|---|
| §1 Full hook taxonomy | [06](./06-layer-5-hooks-trace.md) | 27 distinct events; aggregation rule (`deny > ask > allow`); short-circuit. Supersedes the partial list in 06. |
| §2 Hook subprocess protocol | [06](./06-layer-5-hooks-trace.md) | Stdin/stdout JSON; **first-line async marker**; exit code 2 = blocking deny; prompt-response round-trip protocol. |
| §3 Four hook subtypes | [06](./06-layer-5-hooks-trace.md) | command / prompt / agent / http — each with own runner and Zod schema. |
| §4 Hook policy + source layering | [06](./06-layer-5-hooks-trace.md) | managed → plugin → project → user → local; `allowManagedHooksOnly` + `disableAllHooks`; deduplication keys. |
| §5 Hook context fields per event | [06](./06-layer-5-hooks-trace.md) | Locks the per-event input record shapes. |
| §6 Slash command discriminated-union shape | [02](./02-layer-1-entry-cli.md) | `prompt` / `local` / `local-jsx` variants; `availability`, `argumentHint`, `whenToUse`, `paths`, lazy `load()`. |
| §7 Slash registry tiers + memoization | [02](./02-layer-1-entry-cli.md) | Built-in → skills → plugins → workflows; memoized by cwd; dynamic skills inserted after. |
| §8 Bootstrap sequence (8 stages) | [03](./03-layer-2-config-bootstrap.md) | Fast-path flags → setup → hooks snapshot → file watcher → worktree → MCP probe → commands → context. |
| §9 Context-file walk-up + @include | [07](./07-layer-6-prompt-and-cache.md) | walk-up rules, depth limit 20, cycle detection, external-include approval gate. |
| §10 Tool contract precision | [05](./05-layer-4-agent-core.md) | `validateInput` + `checkPermissions` + `isConcurrencySafe` + `isReadOnly` + `isDestructive` + `interruptBehavior` + `maxResultSizeChars` + `strict`. |
| §11 Edit tool atomicity + staleness | [05](./05-layer-4-agent-core.md) | mtime staleness check; atomic critical section; replace_all required-on-ambiguity error. |
| §12 Task tool / subagent isolation | [05a](./05a-coordinator-multi-agent.md) | task types, isolation modes (worktree / remote), AbortController, background launch. |
| §13 MCP tool wrapping | [09](./09-layer-8-gateway-integrations.md) | `mcp__<server>__<tool>` prefix; lazy schema; meta passthrough; auto-allow rule. |
| §14 TUI region model | [02](./02-layer-1-entry-cli.md) | transcript + sticky header + composer + floating modal + status line; virtualized list. |
| §15 Diff rendering with per-patch cache | [02](./02-layer-1-entry-cli.md) | Native colorizer; WeakMap-per-patch cache; ANSI-aware gutter slicing. |
| §16 Streaming-markdown last-block rule | [02](./02-layer-1-entry-cli.md) | Re-parse only the trailing partial block. |
| §17 Vim mode state machine | [02](./02-layer-1-entry-cli.md) | Discriminated states INSERT/NORMAL; operators × motions × counts × text objects. |
| §18 Dialog launcher pattern | [02](./02-layer-1-entry-cli.md) | Async function returning a Promise; modeless overlay; resolves via `done()`. |
| §19 Keybinding chord matcher | [02](./02-layer-1-entry-cli.md) | Default + user layers; reserved keys; context-scoped. |
| §20 Three-tier skill discovery | [08](./08-layer-7-skills-plugins-curator.md) | bundled → user `.claude/skills` → project; `realpath` dedup; conditional `paths`-scoped skills. |
| §21 Skill frontmatter superset | [08](./08-layer-7-skills-plugins-curator.md) | Adds `when-to-use`, `allowed-tools`, `user-invocable`, `disable-model-invocation`, `context: inline\|fork`, `paths`, `effort`, `shell`. |
| §22 Plugin definition record | [08](./08-layer-7-skills-plugins-curator.md) | `{name, version, skills, hooks, mcpServers, isAvailable, defaultEnabled}`. |
| §23 Coordinator pattern | [05a](./05a-coordinator-multi-agent.md) | Planner/delegator role; workers without recursion; shared scratchpad; `<task-notification>` blocks. |
| §24 Memdir Markdown-frontmatter format | [07a](./07a-layer-6-memory-and-kb.md) | YAML frontmatter (`name`, `description`, `type ∈ {user, feedback, project, reference}`); `MEMORY.md` as index; per-topic files. |
| §25 AppState immutable store | [03](./03-layer-2-config-bootstrap.md) | Single store, deep-immutable, `setState(prev => …)`, sync-to-disk hook. |
| §26 Migrations: idempotent, named, no version field | [11](./11-build-test-deploy.md) | Re-runnable; gated by current-state shape; logged as analytics events. |
| §27 Task vs Tool distinction | [05b](./05b-tasks-and-kanban.md) | Task = lifecycle-tracked unit of work (`local_bash | local_agent | remote_agent | in_process_teammate | local_workflow | monitor_mcp | dream`); Tool = invocation surface. |
| §28 Upstream-proxy pattern | [05](./05-layer-4-agent-core.md) envs + [11](./11-build-test-deploy.md) | Container subprocesses route HTTP via local relay; CA-bundle merge; NO_PROXY allowlist; `prctl(PR_SET_DUMPABLE, 0)`. |
| §29 Permission-decision aggregation | [05](./05-layer-4-agent-core.md) + [06](./06-layer-5-hooks-trace.md) | `deny > ask > allow`; permission state retained across in-session reuse via `updatedPermissions`. |
| §30 Cost tracking shape | [04](./04-layer-3-providers.md) + [11](./11-build-test-deploy.md) | Per-model counters incl. cache reads/writes; session-keyed persistence; restore on resume. |
| §31 PreCompact / PostCompact hooks | [05](./05-layer-4-agent-core.md) compaction + [06](./06-layer-5-hooks-trace.md) | trigger source (manual / auto); user-modifiable instructions; user display message. |
| §32 Dream-style memory consolidator | [08](./08-layer-7-skills-plugins-curator.md) Curator + [07b](./07b-prompt-self-improvement.md) | Background forked subagent; three-gate trigger; four-phase consolidation; read-only synthesis. |

---

## §1 — Full hook taxonomy *(refines plan/06)*

[06 §"Hook event taxonomy"](./06-layer-5-hooks-trace.md) lists ~24 events. The verified Claude Code surface adds and tightens:

```rust
pub enum HookEvent {
    // Session lifecycle
    SessionStart, Setup, SessionEnd,
    CwdChanged, FileChanged,                       // FileChanged depends on SessionStart's `watchPaths`

    // Conversation
    UserPromptSubmit,
    AgentMessage,
    Notification,

    // Tool flow
    PreToolUse, PostToolUse, PostToolUseFailure,
    PermissionRequest, PermissionDenied,           // PermissionResolved kept implicit; the Permission* hooks fire instead

    // Execution control
    Stop, StopFailure,                             // Stop hook may BLOCK stop via exit-2

    // Subagent / team
    SubagentStart, SubagentStop, TeammateIdle,     // TeammateIdle can block to keep working

    // Compaction
    PreCompact, PostCompact,                       // PreCompact may mutate custom instructions

    // Task management
    TaskCreated, TaskCompleted,

    // Configuration
    ConfigChange,
    InstructionsLoaded,                            // CLAUDE.md/AGENTS.md loaded
    WorktreeCreate, WorktreeRemove,

    // User interaction
    Elicitation, ElicitationResult,                // checkbox/option prompts
}
```

**Aggregation across multiple hooks** *(refines [06 §"Emit semantics"](./06-layer-5-hooks-trace.md))*:

`deny > ask > allow`. Concretely:
1. All matching hooks for an event run **in parallel** (each with its own timeout).
2. Once any returns `deny`, downstream hooks are short-circuited and the deny stands.
3. If no `deny` but at least one `ask`, the final decision is `ask`.
4. If all `allow`, the decision is `allow`.

This differs from a strictly-ordered chain: Lamark adopts the parallel-fan-out model because it makes hook latency the *max*, not the *sum*.

---

## §2 — Hook subprocess protocol *(refines plan/06 §"Subprocess hooks")*

The TOML config in plan/06 specifies command and timeout. Lock the wire protocol:

**Input on stdin** — single line of JSON, UTF-8:

```json
{
  "hook_event_name": "PreToolUse",
  "session_id": "…",
  "working_directory": "…",
  "permission_mode": "ask",
  "tool_name": "Bash",
  "tool_input": { "command": "rm -rf /" },
  "tool_use_id": "…"
}
```

**Output on stdout** — JSON (sync) or first-line marker (async).

*Sync:* a single JSON object anywhere in stdout (preceded/followed by plaintext logs is allowed; the parser tries JSON first, falls back to plaintext):

```json
{
  "continue": false,
  "decision": "block",
  "reason": "blocked: dangerous rm in workspace root",
  "systemMessage": "operator-visible warning",
  "permissionDecision": "deny",
  "permissionDecisionReason": "…",
  "updatedInput": { "command": "rm -rf ./node_modules" },
  "additionalContext": "extra context to inject"
}
```

*Async:* first line of stdout must be a single JSON object with `"async": true`:

```
{"async": true, "asyncTimeout": 600000}
<rest of stdout is logged, not parsed>
```

The runner detects async by parsing only the first line. Once an async marker is seen, the runner backgrounds the process and continues; the AsyncHookRegistry watches for completion.

**Prompt-response round-trip** — the hook may interactively prompt the user. Hook stdout emits:

```json
{"prompt": "req-1", "message": "Which environment?", "options": [{"key": "prod", "label": "Production"}, {"key": "stg", "label": "Staging"}]}
```

The runner displays the prompt to the user, then writes back on the hook's stdin:

```json
{"prompt_response": "req-1", "selected": "stg"}
```

Stdin remains open; multiple round-trips are allowed (serialized).

**Exit codes:**
- `0` — success, output parsed normally.
- `2` — blocking error. Equivalent to `{"decision": "block", "continue": false}`. Stderr becomes `reason`.
- non-zero non-2 — non-blocking error, logged.
- SIGTERM / timeout — treated as non-blocking error.

---

## §3 — Four hook subtypes *(refines plan/06)*

A hook is one of four kinds. Each has its own runner, but the *output schema is identical* (§2). The kinds:

```toml
[[hook]]
event = "PreToolUse"
match_tool = "bash"
type = "command"                  # shell command
command = "~/.lamark/scripts/guard.sh"
timeout = "10s"

[[hook]]
event = "PostToolUse"
type = "prompt"                   # auxiliary-model evaluation
prompt = "Did this tool succeed? Reply JSON with decision=approve|block."
model = "haiku"                   # cheap model
timeout = "30s"

[[hook]]
event = "SubagentStop"
type = "agent"                    # spawn a sub-agent to evaluate
prompt = "Verify the work the subagent claims to have done. ..."
timeout = "60s"

[[hook]]
event = "FileChanged"
type = "http"                     # POST to a webhook
url = "https://example.com/lamark/changed"
headers = { Authorization = "Bearer ${MY_TOKEN}" }
timeout = "10s"
```

**SSRF guard** for HTTP hooks: URLs must match an allowlist; private IPs (RFC1918, loopback, link-local) are denied unless the allowlist explicitly permits.

**Env-var interpolation** in HTTP headers and command args is allowlist-gated: only env vars matching `LAMARK_HOOK_ENV_*` or appearing on an explicit `allow_env` list are interpolated. Prevents accidental credential exfiltration.

---

## §4 — Hook policy + source layering *(refines plan/06)*

Hooks come from five sources, merged in priority order:

```
managed   (admin-deployed policy; cannot be disabled)
plugins   (bundled + user plugins, in order)
project   (.lamark/settings.json in repo root)
user      (~/.lamark/settings.json)
local     (.lamark/settings.local.json — git-ignored personal overrides)
```

Two policy flags:

- `policy.allow_managed_hooks_only = true` — only `managed` hooks run; everything else suppressed.
- `policy.disable_all_hooks = true` — all hook execution suppressed (managed too if set at managed level).

**Deduplication** key: `(event, matcher, type, command|prompt|url)`. Identical hooks from different sources are deduplicated; first occurrence wins.

---

## §5 — Hook context fields per event *(refines plan/06)*

Lock per-event input shape (extra to the common `hook_event_name | session_id | working_directory | permission_mode`):

| Event | Additional fields |
|---|---|
| `PreToolUse` / `PostToolUse` / `PostToolUseFailure` | `tool_name, tool_input, tool_output?, tool_use_id, mcp_server_type?` |
| `UserPromptSubmit` | `prompt_text, attachments?` |
| `SessionStart` | `trigger ∈ {manual, auto, resume}, custom_instructions?` → may return `watchPaths, initialUserMessage` |
| `SessionEnd` | `last_assistant_message?, exit_reason` — tight 1500 ms timeout |
| `Stop` / `SubagentStop` | `stop_hook_active, last_assistant_message?, agent_id?, agent_transcript_path?, agent_type?` |
| `FileChanged` | `file_paths[]` |
| `CwdChanged` | `previous_directory, new_directory` |
| `PermissionRequest` / `PermissionDenied` | `tool_name, tool_input, permission_rule` |
| `PreCompact` / `PostCompact` | `trigger, custom_instructions?` → may return `newCustomInstructions, userDisplayMessage` |
| `TaskCreated` / `TaskCompleted` | `task_id, task_type, task_meta` |
| `Elicitation` | `request_id, message, options[]` → returns `selected` |

---

## §6 — Slash command discriminated-union shape *(refines plan/02)*

[02 §"Slash commands"](./02-layer-1-entry-cli.md) lists categories but not the record shape. Pin it:

```rust
// crates/lamark-cli/src/commands.rs
pub struct CommandBase {
    pub name: &'static str,
    pub aliases: &'static [&'static str],
    pub description: &'static str,
    pub is_enabled: fn() -> bool,
    pub is_hidden: bool,
    pub availability: &'static [Surface],     // CLI | TUI | Gateway | BotMenu | empty=universal
    pub argument_hint: Option<&'static str>,
    pub when_to_use: Option<&'static str>,    // used by skill-style matching
    pub version: Option<&'static str>,
    pub user_facing_name: Option<&'static str>, // override display name (plugin prefix stripping)
}

pub enum Command {
    Prompt(PromptCommand),
    Local(LocalCommand),
    LocalInteractive(LocalInteractiveCommand),  // ratatui modal flow
}

pub struct PromptCommand {
    pub base: CommandBase,
    pub source: CommandSource,                 // Builtin | Mcp | Plugin | Bundled
    pub progress_message: Option<String>,
    pub content_length: usize,
    pub allowed_tools: Option<Vec<String>>,    // e.g. ["Bash(git *)"]
    pub hooks: Option<HooksConfig>,
    pub context: CommandContext,               // Inline | Fork
    pub agent: Option<String>,                 // agent type if forked
    pub paths: Option<Vec<String>>,            // glob patterns for conditional visibility
    pub effort: Option<EffortHint>,
    pub get_prompt_for_command: PromptResolver,
}

pub struct LocalCommand {
    pub base: CommandBase,
    pub supports_non_interactive: bool,        // works under `lamark -p`
    pub load: fn() -> CommandHandler,          // lazy load
}

pub struct LocalInteractiveCommand {
    pub base: CommandBase,
    pub load: fn() -> InteractiveHandler,
}
```

`paths` is the load-bearing field for skill-style activation: a command may be visible only when files matching the glob are touched in the current cwd. Used to keep the slash-command list manageable.

---

## §7 — Slash registry tiers + memoization *(refines plan/02)*

Resolution order at `get_commands(cwd)`:

```
1. Built-ins                    (compile-time registered, lazy-loaded handlers)
2. Bundled skills               (registered at startup via register_bundled_skill())
3. Disk skills                  (managed + user + project .lamark/skills/<name>/SKILL.md)
4. Plugins                      (bundled + user)
5. Workflows                    (.lamark/workflows/*.json)
6. Dynamic / conditional skills (paths-gated; activated when matching files are touched)
```

Memoization keyed on **cwd** — when cwd changes (e.g. via worktree), the cache is invalidated and the list rebuilds. The plugin/skill loader exposes `clear_commands_cache()` for `/reload-plugins`.

Deduplication uses canonical-path matching (`realpath`) so symlinks don't create phantom duplicates.

---

## §8 — Bootstrap sequence (8 stages) *(refines plan/03)*

The verified order of operations from process start to ready-for-prompt:

1. **Fast-path argv flags** — `--version`, `--help`, `--dump-system-prompt`, `--bare`, daemon-worker spawns. Exit before any heavy import.
2. **Profile resolution + UTF-8 + .env load** — see [00c §13](./00c-hermes-deepdive-addendum.md).
3. **Working directory lock** — `set_cwd(cwd)`. Nothing path-dependent runs before this.
4. **Hook config snapshot** — load `.lamark/settings.json` from cwd, merge sources, capture immutable hook list.
5. **File-watcher init** — only watching paths declared by SessionStart hooks (registered next).
6. **SessionStart hooks fire** — may inject `initialUserMessage`, may request `watchPaths` for future `FileChanged` events.
7. **Worktree handling** *(if `--worktree`)* — create temp git worktree, switch cwd, **re-capture hook snapshot**, re-watch files. This re-capture step is load-bearing — the original capture was for the old cwd.
8. **Async background warmups** — MCP server probes (parallel), context-file walk-up (CLAUDE.md / AGENTS.md / LAMARK.md), model catalog fetch, plugin discovery. All non-blocking; the prompt appears as soon as critical-path init is done.

Subsequent prompts may extend stage 8 lazily (e.g. MCP tool list completes after prompt is shown; the affected tools become available mid-conversation).

---

## §9 — Context-file walk-up + @include *(refines plan/07)*

[00c §20](./00c-hermes-deepdive-addendum.md) already pins the walk-up + precedence (`LAMARK.md > AGENTS.md > CLAUDE.md`). Add the **@include** mechanism:

- A context file may contain `@include <glob-or-path>` directives.
- Globs are resolved relative to the *including* file's directory.
- Cycle detection: a `processed_paths` set is kept by absolute path.
- **Depth limit: 20.** Beyond that the loader aborts the chain (logs a warning, keeps the partial result).
- **External-include approval gate**: any `@include` that resolves outside the user's home directory must be pre-approved by the user (stored in `.lamark/settings.json` as `claude_md_external_includes_approved: [<absolute-path>, ...]`). On first encounter, the user is prompted; the answer is persisted.

`--bare` flag disables auto-discovery entirely (only explicit `--add-dir <path>` includes load context).

---

## §10 — Tool contract precision *(refines plan/05, supersedes 00c §1 where they overlap)*

[00c §1](./00c-hermes-deepdive-addendum.md) added several fields. The full verified contract:

```rust
pub trait Tool: Send + Sync {
    fn name(&self) -> &str;
    fn aliases(&self) -> &[&str] { &[] }                       // backwards-compat names

    fn input_schema(&self) -> &Schema;                         // structured (Zod-equivalent)
    fn input_json_schema(&self) -> Option<&JsonSchema> { None } // for MCP tools

    fn output_schema(&self) -> Option<&Schema> { None }

    async fn description(&self, input: &Value) -> String;      // short user-facing
    async fn prompt(&self) -> String;                          // model-facing detailed prompt
    fn user_facing_name(&self) -> Option<&str> { None }        // override display

    async fn validate_input(&self, input: &Value, ctx: &ToolCtx) -> Result<(), ValidationError>;
    async fn check_permissions(&self, input: &Value, ctx: &ToolCtx) -> PermissionResult;

    fn is_concurrency_safe(&self, input: &Value) -> bool;
    fn is_read_only(&self, input: &Value) -> bool;
    fn is_destructive(&self, input: &Value) -> bool { false }
    fn is_search_or_read_command(&self, input: &Value) -> Option<SearchReadHint> { None }

    fn interrupt_behavior(&self, input: &Value) -> InterruptBehavior {
        InterruptBehavior::Block                               // default: keep running on Ctrl-C
    }

    fn max_result_size_chars(&self) -> usize { /* … config default … */ }

    fn strict(&self) -> bool { false }                         // enforce strict schema if provider supports
    fn is_enabled(&self) -> bool { true }

    async fn call(
        &self,
        input: Value,
        ctx: &ToolCtx,
        can_use_tool: &dyn CanUseTool,
        parent_message_id: Option<MessageId>,
        on_progress: Option<&dyn ToolProgressSink>,
    ) -> ToolResult;
}

pub struct ToolResult<T> {
    pub data: T,
    pub new_messages: Option<Vec<Message>>,                    // synthetic inject
    pub context_modifier: Option<Box<dyn ContextModifier>>,    // unsafe tools only
    pub mcp_meta: Option<McpMeta>,                             // MCP passthrough
}

pub enum InterruptBehavior {
    Cancel,                                                    // abort in-flight
    Block,                                                     // keep running, user must wait
}

pub struct SearchReadHint {
    pub is_search: bool,
    pub is_read: bool,
    pub is_list: bool,                                         // UI collapsing hint
}
```

`max_result_size_chars` defaults to the global, may be infinity (never persist to disk), and is a tool-specific knob — Read has a high cap, Bash has a moderate cap, WebFetch may be infinity (the spill-to-disk path activates).

---

## §11 — Edit tool atomicity + staleness *(refines plan/05)*

The canonical Edit-tool contract — implement this exactly, this is one of the load-bearing differentiators vs. naive agents:

1. **Pre-check the file's mtime against the agent's last-read mtime.** If the file changed since the agent last read it, reject the edit with a stale-content error, **unless** the file's current content is byte-identical to what the agent last saw (in which case the change is consistent and edit proceeds).
2. **Normalize quotes** (smart-quotes vs straight-quotes) before searching for `old_string` — agents often paraphrase quotes.
3. **Match-count rule:** if `old_string` matches multiple positions in the file and `replace_all=false`, return an error with the count. **Do NOT auto-pick the first occurrence.** The agent must either expand `old_string` for uniqueness or set `replace_all=true`.
4. **Atomicity boundary:** the staleness check + content replacement + write must be a single critical section with no `await` between mtime read and disk write. Use a blocking file-lock if the env supports it; otherwise a `Mutex<PathBuf>` per cwd.
5. **No partial edits on error.** On any failure, the file's content is unchanged.

This was implicit in [00c §20](./00c-hermes-deepdive-addendum.md) — now pinned with the staleness rule.

---

## §12 — Task tool / subagent isolation *(refines plan/05a)*

[05a](./05a-coordinator-multi-agent.md) covers coordinator/subagent at a high level. The Task tool's verified contract:

```rust
pub struct TaskInput {
    pub description: String,                           // 3-5 word summary, shown in UI
    pub prompt: String,                                // full task brief
    pub subagent_type: Option<String>,                 // specialized agent name
    pub model: Option<ModelTier>,                      // sonnet | opus | haiku override
    pub run_in_background: bool,
    pub name: Option<String>,                          // addressable teammate name
    pub team_name: Option<String>,
    pub mode: Option<PermissionMode>,                  // plan | auto | …
    pub isolation: Option<Isolation>,                  // Worktree | Remote
    pub cwd: Option<PathBuf>,
}

pub enum TaskKind {
    LocalAgent,                                        // foreground subagent, same process
    LocalAgentBackground,                              // run_in_background=true
    RemoteAgent,                                       // external session (CCR-style)
    InProcessTeammate,                                 // shared UI, multi-agent
    LocalBash,                                         // long-running shell
    LocalWorkflow,                                     // composed multi-tool chain
    MonitorMcp,                                        // watcher process
    Dream,                                             // background consolidator (§32)
}

pub enum Isolation {
    Worktree,                                          // temp git worktree, isolated FS view
    Remote,                                            // remote environment, always background
}
```

When `run_in_background=true`, the tool returns `{status: "async_launched", agent_id, output_file}` immediately. The launching agent can poll the output file or use `TaskList` / `TaskGet` / `TaskUpdate` / `TaskOutput` / `TaskStop` tools to monitor.

Cancellation is via `AbortController` (one per subagent); on cancel, child shell processes are killed, MCP server connections cleaned up, and a synthetic `UserInterruptionMessage` is emitted if the cancel was a user interrupt (vs. a parent-driven kill).

**Concurrency:** no hard cap in the tool itself. The coordinator-side policy file controls fan-out (`agent.delegation.max_concurrent_children`, default 3 — see [05a](./05a-coordinator-multi-agent.md)).

---

## §13 — MCP tool wrapping *(refines plan/09 Part B)*

Concrete contract for MCP tool proxying:

- Each MCP server registers a name; its tools are exposed under `mcp__<server>__<tool>` prefix.
- Schema is fetched from the server at connect time (`tools/list` endpoint) — lazy resolution allowed for slow servers; the tool appears as "loading" in the registry until schema arrives.
- `call()` delegates to MCP client's `call_tool(server, tool, args)`; result is wrapped in `ToolResult` with `mcp_meta` carrying the server's `_meta` and `structured_content` passthrough.
- **Permission rule:** if the user has set an `mcp` allow rule for the server, MCP tool calls auto-allow; otherwise, prompt. No per-tool override within an MCP server — the server is the trust boundary.
- Server lifecycle: parent process's MCP connections are inherited by subagents (memoized `connectToServer`). Subagent-private servers (declared in agent frontmatter) are spun up at subagent start and cleaned up on subagent exit.

---

## §14 — TUI region model *(refines plan/02)*

The screen is divided into:

```
┌─ Sticky header (visible during scrollback) ─────────────────┐
├─ Transcript (virtualized scrollable list) ──────────────────┤
│   ┌─ "N new messages" pill (floating) ────────────────┐     │
│   └────────────────────────────────────────────────────┘     │
├─ Modal overlay (anchored bottom, top divider) ──────────────┤  ← only when active
│   • Permission dialogs                                      │
│   • Model picker / Theme picker / Output-style picker       │
│   • Quick-open / Global-search                              │
├─ Composer (multi-line input, vim mode capable) ─────────────┤
├─ Composer footer (mode indicator, queued commands, hints) ──┤
└─ Status line (model, tokens, permissions, agent counts) ────┘
```

Layout responds to terminal size — narrow terminals (<40 cols) collapse the status line into single-row. Fullscreen mode toggles between *inline* (modal in flow) and *floating* (modal over transcript).

The transcript is a **virtualized list** — only on-screen messages are rendered; off-screen messages cost zero render work. Streaming-friendly: appending a token mutates only the trailing rendered message, not the whole list.

---

## §15 — Diff rendering with per-patch cache *(refines plan/02 + plan/05)*

Edit/Write tool output is a signature affordance. The verified renderer:

- **Native colorizer** module (Rust NAPI in reference; for Lamark, a pure-Rust crate with the same shape) computes the colored diff. Falls back to plain ANSI if the native module is unavailable.
- **WeakMap-per-patch cache**: the parsed/colored diff for a given `(file_path, before_hash, after_hash, theme, width)` is cached. Re-rendering on theme change or width change is `O(1)` cache lookup.
- **ANSI-aware gutter slicing**: the left gutter (line numbers + `+/-` markers) is rendered separately from the content; the slicer (an ANSI-aware substring function) preserves color codes when splitting.
- **Gutter width** is computed once per patch, cached, never reflows.

Total cost: one full parse per patch, near-zero re-render. Important for long diffs (Edit output of large files) on resize.

---

## §16 — Streaming-markdown last-block rule *(refines plan/02)*

When assistant tokens stream in:

- The display buffer accumulates incoming text.
- Every render frame re-parses **only the trailing partial block** of markdown — split at the last complete top-level block (paragraph end, code-block close, list item end).
- Completed blocks above the trailing partial are cached as fully-rendered terminal output.

This avoids O(n) re-parsing of the whole transcript per token. Implementation: keep a `last_stable_offset: usize`; when streaming, parse from `last_stable_offset` to end; when a complete-block boundary is detected, bump `last_stable_offset` and cache the block above.

---

## §17 — Vim mode state machine *(refines plan/02)*

A complete vim implementation in the composer:

```rust
pub enum VimState {
    Insert { buffer: TextBuffer, cursor: Cursor },
    Normal { cmd: CommandState, persistent: PersistentState },
}

pub enum CommandState {
    Idle,
    Operator { op: Operator, count: u32 },
    OperatorCount { op: Operator, count: u32 },
    OperatorFind { op: Operator, dir: FindDir, kind: FindKind },
    OperatorTextObj { op: Operator, count: u32 },
    Count(u32),
    Find { dir: FindDir, kind: FindKind },
    Replace,
    Indent { dir: IndentDir, count: u32 },
    Visual(VisualState),
}

pub struct PersistentState {
    pub last_change: Option<ChangeRecord>,     // for `.` repeat
    pub last_find: Option<FindRecord>,         // for `;`/`,` repeat
}
```

Operators (`d`, `c`, `y`, `=`, `>`, `<`, `~`, `g~`) compose with motions (`h j k l w b e $ ^ G gg H M L`) and text objects (`iw aw ip ap is as i( a( i{ a{ i" a" i' a' i\` a\``). Counts prefix everything (`5dw`, `d3w`, etc.). Find motions (`f F t T`) advance with `;` and `,`.

State transitions are exhaustive (a discriminated union with a match-statement state machine). Invalid sequences reset to Idle.

Reserved keys (`Ctrl-C`, `Ctrl-D`) cannot be rebound and are not consumed by vim mode.

---

## §18 — Dialog launcher pattern *(refines plan/02)*

Modal dialogs use an async-function-returning-Promise pattern:

```rust
pub async fn show_dialog<T>(
    app: &AppHandle,
    render: impl FnOnce(DialogDoneFn<T>) -> DialogTree,
) -> T { … }
```

- The launcher takes a closure that builds the dialog tree, given a `done: DialogDoneFn<T>` callback.
- The launcher renders the dialog into the modal slot and awaits completion.
- The dialog's `done(value)` resolves the awaiting future; the modal is unmounted.
- Modeless: the dialog can be dismissed (Escape) which resolves with a cancel variant.

In Rust, this becomes a `oneshot::Receiver<DialogResult<T>>` plus a render that mounts the dialog and exposes the `oneshot::Sender` to the dialog's "OK / Cancel" handlers.

Dialog kinds:

- **Setup-time** (run before REPL): invalid-settings, agent-memory snapshot, install-wizard, teleport-resume.
- **Mid-REPL** (overlay during conversation): model picker, output-style picker, theme picker, permission prompt, MCP-server multiselect, quick-open, global-search, export.

---

## §19 — Keybinding chord matcher *(refines plan/02)*

```rust
pub struct Keybinding {
    pub context: KeyContext,        // Global | Chat | Autocomplete | Vim | History | Visual | Replace
    pub chord: Chord,               // e.g. [Ctrl+X, Ctrl+E]
    pub action: ActionId,
    pub source: BindingSource,      // Default | User | Plugin
}
```

Resolution: a typed prefix is matched against registered chords; partial matches buffer until full match or timeout (default 600 ms). The `keybindings/parser.rs` validates user JSON (`~/.lamark/keybindings.json`); reserved keys (`Ctrl-C`, `Ctrl-D`) are rejected at validation.

Default bindings (subset, 50+):

| Context | Chord | Action |
|---|---|---|
| Global | `Ctrl-C` (1×) | Interrupt in-flight |
| Global | `Ctrl-C` (2× within 200 ms) | Exit binary |
| Global | `Ctrl-L` | Redraw |
| Global | `Ctrl-O` | Transcript toggle |
| Global | `Ctrl-R` | History search |
| Global | `Ctrl-Shift-F` | Quick open |
| Global | `Ctrl-Shift-P` | Global search |
| Chat | `Enter` | Submit |
| Chat | `Shift-Tab` | Cycle mode |
| Chat | `Ctrl-G` | External editor |
| Chat | `Meta-P` | Model picker |
| Chat | `Meta-T` | Thinking toggle |
| Chat | `Space` | Voice push-to-talk |

Plugins may register additional bindings (under their namespace); they cannot shadow reserved keys.

---

## §20 — Three-tier skill discovery *(refines plan/08, supersedes 00c §2 precedence)*

[00c §2](./00c-hermes-deepdive-addendum.md) pins the three-tier *display* model (`skills_list → skill_view → skill_view(file)`). The verified *discovery* tier is also three layers:

```
managed  (admin-deployed via policy; ~/.lamark/managed/skills/)
user     (~/.lamark/skills/)
project  (.lamark/skills/ in cwd)
```

- **Required layout:** every skill is a directory `<root>/skills/<name>/SKILL.md`. A bare `<root>/skills/foo.md` is *rejected* (only top-level directories count).
- **Legacy `/commands/` directory** supports both forms (directory + SKILL.md, or bare `<name>.md`) for backwards compatibility.
- **Deduplication** uses `realpath()` so symlinks across managed/user/project don't multiply.
- **Precedence on duplicate name:** managed > user > project (managed cannot be overridden).
- **Conditional skills** (with `paths` frontmatter) are stored in a separate map and activated when a file touched by a tool matches the glob. They never appear in the default slash-command list — they appear only when relevant.

---

## §21 — Skill frontmatter superset *(refines plan/08, supersedes 00c §2 frontmatter table)*

The complete frontmatter:

| Field | Constraint | Purpose |
|---|---|---|
| `name` | ≤ 64 chars, kebab-case | identifier |
| `description` | ≤ 1024 chars (in 00c) | short description |
| `when-to-use` | free text | triggers for the model to choose this skill |
| `argument-hint` | free text | gray hint after command name |
| `allowed-tools` | string array (e.g. `["Bash(git *)"]`) | scope tool access during skill execution |
| `user-invocable` | bool, default true | if false, hidden from user slash list (agent-only) |
| `disable-model-invocation` | bool, default false | run without calling the model (pure procedural) |
| `model` | string | override the model for this skill |
| `context` | `inline | fork` | run in-process or fork a subagent |
| `agent` | string | subagent type to use when forked |
| `paths` | gitignore globs | conditional activation (see §20) |
| `hooks` | `HooksConfig` | hooks to register on invocation |
| `effort` | enum | UI hint |
| `shell` | object | shell config for inline bash markers |
| `version` | semver | for migrations |
| `platforms` | subset of `[macos, linux, windows]` (from 00c §2) | host gating |
| `metadata.lamark.tags` | string array | discoverability |
| `metadata.lamark.related_skills` | string array | cross-links |
| `metadata.lamark.created_by` | `user | agent | bundled` (from 00c §2) | curator scope |
| `metadata.lamark.pinned` | bool (from 00c §2) | curator exemption |

**Shell markers** in the body — lines beginning with `!` are executed at invocation time and their stdout injected. **MCP skills never execute shell markers** (security: MCP source is untrusted).

**Argument substitution** in the body: `${ARG1}`, `${ARG2}`, …, `${LAMARK_SKILL_DIR}`, `${LAMARK_SESSION_ID}` are resolved post-fetch.

---

## §22 — Plugin definition record *(refines plan/08, supersedes 00c §3 partial)*

The complete plugin shape:

```rust
pub struct PluginDef {
    pub name: String,
    pub description: String,
    pub version: Option<String>,
    pub skills: Vec<BundledSkillDef>,                   // ship skills with the plugin
    pub hooks: Option<HooksConfig>,                     // declarative hook registration
    pub mcp_servers: HashMap<String, McpServerConfig>,  // ship MCP servers
    pub is_available: Option<fn() -> bool>,             // runtime gate
    pub default_enabled: bool,
}
```

User enable/disable state persists to settings as `enabled_plugins: { <plugin_id>: bool }`. Marketplace plugins additionally carry a `PluginManifest` with author + version + capabilities array, and are pinned to a git URL + branch + commit SHA.

Surfaces a plugin can extend: skills, hooks, MCP servers, output styles, agent types, tools (via in-process registration). [00c §3](./00c-hermes-deepdive-addendum.md)'s `register_hook | register_command | register_provider` is the *runtime* counterpart; this record is the *declarative* manifest. Plugins may use either or both.

---

## §23 — Coordinator pattern *(refines plan/05a)*

The verified coordinator/worker model (when `LAMARK_COORDINATOR_MODE=1`):

- **Coordinator** receives the user request. It does **not** call tools directly except `Agent` (spawn worker), `SendMessage` (re-invoke a worker), `TaskList` / `TaskUpdate` (observe), and a small set of read-only tools.
- **Workers** are spawned via `Agent` with a focused brief. Each worker has a *subset* of tools (configurable; default: `Bash, Read, Edit, Skill, MCP`). Workers **cannot spawn other workers**.
- **Task notifications:** when a worker completes, a `<task-notification>` XML block is injected into the coordinator's next user message:
  ```xml
  <task-notification>
    <agent_id>w-1</agent_id>
    <status>completed</status>
    <output>…</output>
  </task-notification>
  ```
  The coordinator synthesizes, does not repeat.
- **Shared scratchpad:** an optional gated directory (`~/.lamark/scratch/<session>/`) where workers can leave durable cross-worker state. Useful for partial results, common dependencies.
- **Coordinator system prompt** teaches orchestration patterns: don't over-delegate (some prompts are one-shot), synthesize don't repeat, parallelize when independent.

This is *adopted from* Claude Code's coordinator and is what 05a already calls the coordinator+Kanban model. Adding the synthesis discipline as an explicit prompt-construction rule.

---

## §24 — Memdir Markdown-frontmatter format *(refines plan/07a)*

Layout per profile + project:

```
~/.lamark/projects/<project-slug>/memory/
├── MEMORY.md                      # index (≤ 200 lines, ≤ 25 KB)
├── user_role.md                   # type=user
├── feedback_terse_responses.md    # type=feedback
├── project_q3_rewrite.md          # type=project
└── ref_grafana_latency.md         # type=reference
```

Each non-index file:

```markdown
---
name: feedback-terse-responses
description: User wants terse responses with no trailing summaries
type: feedback
---

Lead with the rule itself…

Why: user said "I can read the diff."
How to apply: in any TUI/gateway session, default to compact text output, no end-of-turn summary block.
```

Four constrained types (this is load-bearing — providers must validate):

- **user** — role, preferences, expertise. Private only.
- **feedback** — rules/dos/don'ts/validated practices. Private default; promote to team when cross-contributor.
- **project** — current work state, deadlines, stakeholders. Team-biased.
- **reference** — pointers to external systems (Linear projects, Grafana dashboards, Slack channels).

**MEMORY.md** is the index. The model reads it at session start, decides which topic files are relevant, then reads those topic files. This is the same progressive-disclosure pattern as skills (§20-§21 in this file) applied to memory.

The Lamark `KnowledgeBaseMemory` provider ([07a](./07a-layer-6-memory-and-kb.md)) implements this on top of the KB; the `SqliteMemory` fallback persists to local Markdown matching this layout, so KB-down sessions can still read/write.

---

## §25 — AppState immutable store *(refines plan/03)*

A single application-wide state object — Lamark equivalent:

```rust
pub struct AppState {
    pub settings: Arc<Settings>,                       // model, tools, hooks, plugins, env, keybindings
    pub session: Arc<SessionState>,                    // expanded view, selected agent, footer, spinner
    pub tasks: Arc<TasksState>,                        // running tasks list (§27)
    pub team: Arc<TeamState>,                          // teammate agents, coordinator panel selection
    pub bridge: Arc<BridgeState>,                      // remote-session connection lifecycle
    pub speculation: Arc<SpeculationState>,            // pipelined inference suggestion
}
```

Conventions:

- Wrapped in `Arc<…>`; mutations clone-and-replace.
- Updates via `app.set_state(|prev| { … })` updater functions.
- Change notifications via a broadcast channel (`tokio::sync::broadcast`) so UI, hooks, and cache subscribers see updates without polling.
- Persisted to disk asynchronously (settings → settings.json, session → session-log, tasks → task-output files).

This is the analogue of Zustand-style stores. Lamark's [crate `lamark-state`](./03-layer-2-config-bootstrap.md) hosts it.

---

## §26 — Migrations: idempotent, named, no version field *(refines plan/11)*

Migrations transform stored state when schemas change (model rename, setting restructure, deprecated flag).

Discipline:

1. Each migration is a named function: `migrate_auto_updates_to_settings()`, `migrate_sonnet45_to_sonnet46()`, …
2. **No explicit version field.** The migration's *condition* is the version test — "if `auto_updates` exists at the old path AND nothing has set the new flag yet, run".
3. **Idempotent.** Re-running a migration on already-migrated state is a no-op (cheap conditional check at the top).
4. Logged as analytics events with the migration name; surfaces in the doctor command.
5. Run sequentially at startup, in registration order, before the REPL prompt appears.

This pattern avoids version-table bookkeeping at the cost of slightly more explicit conditions per migration. Trades schema-history rigor for ease of forward-merging across feature branches.

---

## §27 — Task vs Tool distinction *(refines plan/05b)*

[05b](./05b-tasks-and-kanban.md) covers tasks/kanban/boards. Lock the conceptual distinction here:

- **Tool** — a *capability surface*. Exists only during a model turn. Synchronous or async. Permission-gated. Examples: `Bash`, `Read`, `Edit`, `Agent`, `Skill`, `MCP*`.
- **Task** — a *lifecycle-tracked unit of work* spawned by a tool call. Survives beyond the turn (especially when `run_in_background`). Each has id, status (`pending → running → completed | failed | killed`), `start_time`, `output_file`, and a kill-handler.

```rust
pub enum TaskKind {
    LocalBash,            // long-running shell
    LocalAgent,           // subagent in foreground
    LocalAgentBackground, // subagent backgrounded
    RemoteAgent,          // remote session
    InProcessTeammate,    // shared-UI multi-agent
    LocalWorkflow,        // composed multi-tool chain
    MonitorMcp,           // background MCP watcher
    Dream,                // memory consolidator (§32)
}
```

A tool invocation may produce zero (cache hit), one, or multiple tasks. The TUI's running-tasks panel (`TaskListV2`) is the user-facing surface.

---

## §28 — Upstream-proxy pattern *(refines plan/05 envs + plan/11)*

When a sandbox subprocess (docker, modal, vercel-sandbox, …) needs HTTP egress that's auth-injected or auditable, route via a local relay:

1. A session-scoped relay listens on `127.0.0.1:<port>`.
2. Subprocess env is set to `HTTPS_PROXY=http://127.0.0.1:<port>`, plus a `NO_PROXY` allowlist that explicitly excludes Anthropic API, GitHub, npm, PyPI, crates.io, Go proxy, loopback, RFC1918, link-local. **Trusted destinations bypass the proxy** — never MITM what you don't need to.
3. The relay terminates TLS using a session-local CA whose cert is mounted into the subprocess (`SSL_CERT_FILE` env var); the CA is also concatenated onto the system trust bundle for tools that ignore the env var.
4. On Linux, the relay calls `prctl(PR_SET_DUMPABLE, 0)` to prevent same-UID `ptrace`-of-heap (defense against compromised sibling subprocesses).
5. The relay's session token (a JWT identifying the session) is read from a memory-mapped file in `/run/<sandbox>/session_token` that is **deleted after the relay starts** — so the token cannot be re-read once the relay is up.
6. Fail-open: any error initializing the relay disables proxying (logged warning), never breaks a working session.

This pattern is for the `DockerSandbox` / `ModalSandbox` / `VercelSandbox` backends in [05 §"Sandbox: trait + backends"](./05-layer-4-agent-core.md) — see [05c](./05c-sandbox-and-agent-hosting.md) for the full design including agent hosting. It is also useful for hooks that must originate from a trusted process (e.g. the auxiliary-model auto-approval LLM hook of [00c §1](./00c-hermes-deepdive-addendum.md)).

---

## §29 — Permission-decision aggregation *(refines plan/05 + plan/06)*

Multiple hooks and the policy engine can all weigh in on a tool invocation:

```rust
enum Decision {
    Allow { updated_input: Option<Value>, updated_permissions: Option<PolicyDelta> },
    Ask,
    Deny { message: Option<String>, interrupt: bool },
}
```

Aggregation rule (across all sources for a single call):

1. If **any** voter says `Deny`, the decision is `Deny`. Downstream voters are short-circuited.
2. If no `Deny` but **any** voter says `Ask`, the decision is `Ask` (prompt user).
3. If all voters say `Allow`, the decision is `Allow`.

**Allow with `updated_input`** lets a hook rewrite the args (e.g. coerce `rm -rf /` into `rm -rf ./node_modules`). The rewrite is shown to the user during any subsequent `Ask` prompt.

**Allow with `updated_permissions`** persists a permission rule (e.g. "always allow `Bash(git status)`") for the remainder of the session, by appending to the session's policy state. Surfaces in the permissions panel and the trace recorder's `PolicyMutated` event.

---

## §30 — Cost tracking shape *(refines plan/04 + plan/11)*

Per-model counters, keyed by session:

```rust
pub struct ModelCost {
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub cache_read_input_tokens: u64,
    pub cache_creation_input_tokens: u64,
    pub cost_usd: f64,
    pub web_search_requests: u64,
}

pub struct SessionCost {
    pub by_model: HashMap<String, ModelCost>,
    pub total_api_duration_ms: u64,
    pub total_api_duration_without_retries_ms: u64,
    pub total_tool_duration_ms: u64,
    pub total_lines_added: u64,
    pub total_lines_removed: u64,
}
```

Persisted to the session DB; restored on `/resume`. `/cost` slash command renders a per-model table. The cost hook (06 §"Cost hook" / [00c §1](./00c-hermes-deepdive-addendum.md)) emits a session-end summary into the trace bundle.

---

## §31 — PreCompact / PostCompact hooks *(refines plan/05 compaction + plan/06)*

Compaction hooks expose user influence over the compaction prompt and its result:

```rust
// PreCompact input
{ trigger: "manual" | "auto", custom_instructions: Option<String> }

// PreCompact output (additive fields beyond the common hook output)
{ new_custom_instructions: Option<String>,  // replaces / extends compaction instructions
  user_display_message: Option<String> }   // shown post-compaction

// PostCompact input (read-only)
{ pre_tokens: u32, post_tokens: u32, summary_chars: u32 }
```

Use case: a project may want compaction to preserve specific data (current branch, open todos, recent error context); the hook injects "keep these intact" instructions before the compactor runs.

---

## §32 — Dream-style memory consolidator *(refines plan/08 Curator + plan/07b)*

[08 Curator](./08-layer-7-skills-plugins-curator.md) and [07b](./07b-prompt-self-improvement.md) describe consolidation. Pin the *Dream* execution pattern:

- **Three-gate trigger** — runs only if all three are true:
  1. ≥ N hours since last run (default 24).
  2. ≥ M sessions completed since last run (default 5).
  3. A lock-file acquire succeeds (prevents concurrent runs across profiles on the same host).
- **Four-phase consolidation:**
  1. **Orient** — read MEMORY.md index; read the curator state; identify "what changed since last run".
  2. **Gather signal** — read recent trace bundles + reinforce events from the last N hours.
  3. **Consolidate** — author/update memory files; archive stale skills (00c §2); promote validated strategies into the strategy memory.
  4. **Prune** — remove duplicates; merge near-duplicates with the auxiliary summarizer; never delete (always archive).
- **Read-only synthesis** — the Dream subagent has *no* mutation tools except the memory and skill writers. No `Bash`, no `Edit`, no network. This is a hard rule: the consolidator must not mutate the world.
- **Background** — runs in a forked subagent (TaskKind::Dream — §27). Output is a single memory/skill diff that's applied atomically at the end.

Outputs are logged as `Event::DreamRun { actions, duration }` in the trace.

---

## Implementation checklist

When you implement a layer affected by this addendum, scan the relevant sections **and** the corresponding sections of 00c. Where the two addenda overlap (e.g., 00c §1 and 00d §10 on the tool contract), 00d is the *Claude-Code-DNA flavor* and 00c is the *hermes-DNA flavor* — Lamark adopts the union (everything from both contracts), with §10 here being the authoritative final shape.

When an item here is fully merged into the affected layer file, strike it from the table at the top. When the table is empty, retire this addendum.
