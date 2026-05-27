# 02 — Layer 1: Entry point (CLI)

> The `lamark` binary, the first thing in Rust. The whole project's "outside
> shell." Owns process startup, subcommand dispatch, signal handling, TUI.

> 📎 **See also:** [00d addendum §6–§8, §14–§19](./00d-claude-code-deepdive-addendum.md) —
> Claude-Code slash-command discriminated-union shape (Prompt / Local /
> LocalInteractive with `availability`, `argument_hint`, `when_to_use`, `paths`,
> lazy `load()`), registry tier resolution (built-in → bundled skills → disk skills
> → plugins → workflows → conditional/`paths`-gated), 8-stage bootstrap order
> (fast-path argv → setup → hook snapshot → file watcher → SessionStart →
> worktree re-snapshot → async warmups → prompt), TUI region model (sticky header /
> virtualized transcript / modal overlay / composer / status line), diff renderer
> with per-patch cache + ANSI-aware gutter, streaming-markdown last-block rule,
> vim mode state machine, async-Promise dialog launcher, and chord-matching
> keybinding resolver with reserved keys.

**Crate:** `crates/lamark/` (the binary)
**Depends on:** `lamark-config`, `lamark-protocol` (gRPC stubs), every layer crate (lazy-loaded by subcommand).
**Replaces:** `~/.cache/lemark/vendor/hermes-agent/cli.py` (~669 KB monolith).

## Subcommand surface

Modeled on `hermes-agent`'s CLI plus Codex's `codex-cli`:

```
lamark chat [PROMPT...]                 # interactive or one-shot
lamark gateway run [--profile P]        # long-running gateway process
lamark gateway list / stop / replace    # gateway management
lamark mcp serve [--port P | --stdio]   # expose Lamark tools via MCP
lamark mcp call SERVER TOOL [--args ..] # ad-hoc MCP client invocation
lamark acp register / unregister / list # ACP registry
lamark config get/set/show/edit         # config CRUD
lamark config doctor                    # validate config + reachability checks
lamark trace reduce ROLLOUT_DIR         # bundle → conversation.jsonl
lamark trace export --since DATE        # batch export to KB
lamark trace replay ROLLOUT_DIR         # offline replay for debugging
lamark skills list / install URL / pin / unpin / new NAME
lamark plugins list / install / enable / disable
lamark memory search QUERY              # query the knowledge-base layer
lamark login PROVIDER                   # OAuth for hosted providers
lamark serve adapter PATH               # local LoRA hot-load helper
lamark version / lamark doctor          # diagnostics
lamark exec SCRIPT.lamark               # batch / scripted runs

# Local browser UI (plan/12)
lamark webui [--bind H:P] [--open]      # start the local HTTP/WS server
lamark webui token                       # mint a bearer token for non-loopback exposure
lamark webui status

# Cross-machine control (plan/13)
lamark remote init                       # one-time CA + server cert generation
lamark remote serve [--bind H:P]         # start gRPC + WS server
lamark remote pair                       # show pairing code/QR for a new device cert
lamark remote enroll host=H code=C       # client side: redeem a pairing code
lamark remote token mint/list/revoke
lamark remote clients list/revoke
lamark remote status

# Any subcommand that needs a session honors --remote URL globally:
lamark --remote grpc://host:7879 chat

# In-sandbox entrypoint (plan/05c) — spawned by Sandbox::spawn_agent.
# Not normally typed by humans; the Sandbox passes --spec + --events.
lamark agent run --spec PATH [--events PATH | --stdio]
```

Bracketed defaults: no PROMPT → interactive TUI; no subcommand → `chat` interactive.

## Module layout

```
crates/lamark/
├── Cargo.toml
└── src/
    ├── main.rs               # entry; sets up tracing, parses args, dispatches
    ├── cli.rs                # clap derive types; one struct per subcommand
    ├── runtime.rs            # boot the runtime (wires up traits)
    ├── tui/                  # interactive REPL UI (ratatui)
    │   ├── mod.rs
    │   ├── chat.rs           # main chat view
    │   ├── permission.rs     # approval dialog
    │   ├── status.rs         # status bar, model, session, cost
    │   └── input.rs          # rustyline-style input with history
    ├── commands/             # subcommand implementations
    │   ├── chat.rs
    │   ├── gateway.rs
    │   ├── mcp.rs
    │   ├── trace.rs
    │   └── ...
    ├── signal.rs             # SIGINT / SIGTERM / SIGHUP handling
    └── exit.rs               # consistent exit codes
```

## main.rs — the boot sequence

```rust
#[tokio::main]
async fn main() -> ExitCode {
    // 1. Tracing first so everything below logs cleanly.
    let _tracing_guard = lamark::tracing::install();

    // 2. Parse args (clap).
    let cli = Cli::parse();

    // 3. Load config (figment: defaults → file → env → cli flags).
    let config = lamark_config::load(&cli.global_options)?;

    // 4. Build the runtime (DI-style; trait impls behind features/config).
    let runtime = lamark::runtime::build(&config).await?;

    // 5. Install signal handlers (Ctrl-C cancels current turn; second exits).
    let _signal_guard = lamark::signal::install(runtime.cancel_token());

    // 6. Dispatch to the chosen subcommand.
    match cli.command {
        Some(Cmd::Chat(args))    => commands::chat::run(args, runtime).await,
        Some(Cmd::Gateway(args)) => commands::gateway::run(args, runtime).await,
        Some(Cmd::Mcp(args))     => commands::mcp::run(args, runtime).await,
        Some(Cmd::Trace(args))   => commands::trace::run(args, runtime).await,
        None                     => commands::chat::run(ChatArgs::default(), runtime).await,
        // ...
    }
}
```

Each subcommand returns `ExitCode`; main does no further processing.

## Tracing

`tracing_subscriber::fmt` for console (RUST_LOG-honoring) plus `tracing_appender` for `~/.lamark/logs/lamark-<pid>-<date>.log`. Span structure:

```
session=<rollout_id>
  turn=<turn_id>
    inference call=<id> model=<m>
    tool name=<n> id=<call_id>
```

These span IDs are the same as the trace recorder's IDs — set in `lamark-core` and reused. (No duplicate ID generation.)

## TUI (ratatui)

Three panes when interactive:

```
╭ Lamark — Qwen3-8B @ vllm:8000 ─────────────────────────────╮
│                                                            │
│  user> tell me what's in /tmp                              │
│                                                            │
│  ⚙ list_files({"path":"/tmp"})  ← awaiting approval        │
│    [a]llow  [d]eny  [e]dit  [s]kip                         │
│                                                            │
│  assistant> there are 14 files including ...               │
│                                                            │
├ session 3a7f · turn 12 · 8.4k/32k ctx · $0.00 ────────────┤
│ > _                                                        │
╰────────────────────────────────────────────────────────────╯
```

State machine:
- `Idle` — waiting on user input.
- `Thinking` — inference in flight; stream deltas in.
- `AwaitingApproval` — `PermissionRequest` fired; capture keyboard.
- `Tool` — tool running; stream stdout if long.
- `Done` — show response; back to `Idle`.

Event sources: inference stream, hook bus, tool stdout (for long-running shell), user keyboard. All merged into a single `tokio::select!` loop.

**Non-goal**: replicating the React/Ink TUI from hermes v0.10. ratatui's idiomatic style is enough.

## clap derive layout

```rust
#[derive(Parser)]
#[command(name = "lamark", version, about)]
struct Cli {
    #[command(flatten)]
    global: GlobalOptions,

    #[command(subcommand)]
    command: Option<Cmd>,
}

#[derive(Args)]
struct GlobalOptions {
    /// Override config file (default: ~/.lamark/config.yaml)
    #[arg(long, env = "LAMARK_CONFIG", global = true)]
    config: Option<PathBuf>,

    /// Override profile (test, dev, prod, ...).
    #[arg(long, env = "LAMARK_PROFILE", global = true)]
    profile: Option<String>,

    /// One-off config overrides, dotted-path=value (repeatable).
    #[arg(short = 'o', long, global = true)]
    set: Vec<String>,

    /// Verbose logging (-v info, -vv debug, -vvv trace).
    #[arg(short, long, action = ArgAction::Count, global = true)]
    verbose: u8,

    /// Drive a remote Lamark instead of building a local session.
    /// e.g. grpc://host:7879  (see plan/13)
    #[arg(long, env = "LAMARK_REMOTE", global = true)]
    remote: Option<Url>,
}

#[derive(Subcommand)]
enum Cmd {
    Chat(ChatArgs),
    Gateway(GatewayArgs),
    Mcp(McpArgs),
    Acp(AcpArgs),
    Webui(WebuiArgs),       // plan/12
    Remote(RemoteArgs),     // plan/13
    Agent(AgentArgs),       // plan/05c — in-sandbox entrypoint (`agent run`)
    Config(ConfigArgs),
    Trace(TraceArgs),
    Skills(SkillsArgs),
    Plugins(PluginsArgs),
    Memory(MemoryArgs),
    Login(LoginArgs),
    Serve(ServeArgs),
    Exec(ExecArgs),
    Doctor,
    Version,
}
```

## Signal handling

```rust
// Ctrl-C policy: first hit cancels current turn; second within 2s exits.
pub fn install(cancel: CancelToken) -> SignalGuard {
    let mut sigint = signal(SignalKind::interrupt()).unwrap();
    let mut sigterm = signal(SignalKind::terminate()).unwrap();
    tokio::spawn(async move {
        let mut last = None;
        loop {
            tokio::select! {
                _ = sigint.recv() => {
                    let now = Instant::now();
                    if last.map_or(false, |t: Instant| now.duration_since(t) < Duration::from_secs(2)) {
                        std::process::exit(130);
                    }
                    last = Some(now);
                    cancel.cancel_current_turn();
                }
                _ = sigterm.recv() => std::process::exit(143),
            }
        }
    });
    SignalGuard
}
```

Codex's behavior is what we copy: first interrupt is graceful; second is hard.

## Exit codes

| Code | Meaning |
|---|---|
| `0`   | Success |
| `1`   | Unhandled error |
| `2`   | Bad CLI invocation (clap default) |
| `64`  | Config invalid |
| `65`  | Runtime build failed |
| `66`  | Knowledge-base unreachable AND `memory.required=true` |
| `67`  | Provider unreachable |
| `68`  | Eval gate failed (in `trace replay` / `exec`) |
| `130` | SIGINT (double Ctrl-C) |
| `143` | SIGTERM |

Document this in `--help` for `doctor`.

## Slash commands (interactive TUI)

When the user is in an interactive chat (or via REST/gateway), slash commands work without leaving the conversation. They're implemented as a registry (mirror of Claude Code's `commands.ts` pattern, ~100 builtins + skill-discovered).

Builtin slash commands at v0.1:

```
/help [topic]                    show help inline
/clear                           clear conversation but keep session
/compact [--strategy ...]        force compaction now
/quit | /exit                    end session

# Files / workspace
/cwd [path]                      show or change cwd
/worktree                        info about current worktree
/diff                            show diff vs HEAD

# Session control
/model [name]                    show or switch model mid-session
/cache on|off|status             cache strategy
/policy show|edit                policy.toml
/budget tokens|seconds <n>       cap remaining session

# Memory + KB
/mem search "<query>"
/mem write "<key>: <value>"
/mem forget "<key>"
/profile show

# Skills
/skill list / view <name> / new <name> / pin <name> / unpin / archive
/skill_view <name>               legacy alias
/skill_manage                    Curator-style summary

# Coordinator / multi-agent
/goal <objective>                start Ralph loop  (see plan/05a)
/spawn <role> -- <prompt>        spawn one subagent
/subagents                       list children + status
/kanban [view|claim <id>|...]    Kanban operations
/team on|off                     toggle coordinator role for this session

# Trace / debug
/trace path                      show current rollout dir
/trace export                    export current bundle
/replay <rollout_id>             open offline replay

# MCP
/mcp servers                     list connected MCP servers
/mcp call <server> <tool> ...    one-shot MCP call

# Misc
/login <provider>                OAuth flow for hosted provider
/cost                            show cost-tracker estimate
/version
/remember "<note>"               quick memory write (alias /mem write)
/loop <interval> <cmd>           recurring task (see plan/05a)
/verify                          run self-checks via verify skill
```

The registry shape:

```rust
pub struct SlashCommand {
    pub name: &'static str,
    pub aliases: &'static [&'static str],
    pub kind: CommandKind,              // Local | PromptInject | InteractiveDialog | SkillBacked
    pub description: &'static str,
    pub load: fn() -> Box<dyn SlashCommandImpl>,
}
```

Discovery order: builtins → bundled skills marked `is_command: true` → user skills with `is_command: true` from `~/.lamark/skills`. The trailing two sets are dynamic; the builtins are compiled-in.

## § Project management CLI (G-043)

These subcommands are added to the CLI surface under the `project` subcommand group:

```
lamark project list
  List all known projects with their project_id, cwd-hash, last-active date, trace count.

lamark project rename <old_name> <new_name>
  Rename a project: updates config, renames trace directory, updates KB metadata.
  Requires --confirm for safety.

lamark project migrate <old_path> <new_path>
  Move a project's trace directory from one path to another.
  Atomic: writes new path first, verifies, then removes old.
  Updates project_id detection cache.

lamark project show <name>
  Show project details: path, project_id hash, adapter, memory scope, trace count.
```

Each command resolves `project_id` using the detection function from `plan/03 § Project-id detection`. `rename` and `migrate` are reversible — they write a rollback record to `~/.lamark/project_ops.log` before proceeding.

The clap variants:

```rust
#[derive(Subcommand)]
enum ProjectCmd {
    List,
    Rename {
        old_name: String,
        new_name: String,
        #[arg(long)]
        confirm: bool,
    },
    Migrate {
        old_path: PathBuf,
        new_path: PathBuf,
    },
    Show {
        name: String,
    },
}
```

Implementation lives in `commands/project.rs`. The `Rename` and `Migrate` handlers refuse to proceed without `--confirm` and always append a structured JSON entry to `~/.lamark/project_ops.log` before mutating anything.

## § Batch runner crate (G-060)

The batch runner crate is `crates/lamark-batch`. It implements `lamark exec <manifest.lamark> [--concurrency N] [--output <dir>] [--gold-set <path>] [--resume <dir>]`.

### Module layout

```
crates/lamark-batch/
├── Cargo.toml
└── src/
    ├── lib.rs
    ├── manifest.rs    # parses batch.lamark JSONL/TOML manifest
    ├── pool.rs        # bounded worker pool
    ├── cancel.rs      # CancellationToken + signal handling
    ├── progress.rs    # stderr / TUI progress bar
    ├── report.rs      # run_report.json aggregation
    └── resume.rs      # --resume scan + skip logic
```

### `lamark-batch::manifest`

Parses `batch.lamark` JSONL/TOML manifest; validates prompts + expected behaviors + per-prompt metadata. Each entry carries a `prompt_id`, the prompt text, optional expected output matchers, and per-prompt overrides (model, concurrency, timeout).

### `lamark-batch::pool`

Bounded worker pool (`--concurrency N`). Each worker owns one `Session`; dispatches prompts from the manifest queue; collects trace bundles. Workers are independent tokio tasks communicating over an `mpsc` channel.

### `lamark-batch::cancel`

Integrates `tokio_util::sync::CancellationToken`. Handles:
- **SIGINT** (graceful): stop dispatch, cancel in-flight turns, close all open bundles with `status=aborted`.
- **SIGTERM** (forceful): immediately cancel all tasks; flush any buffered trace data.

### `lamark-batch::progress`

Prints a live progress line to stderr (or a TUI bar when a TTY is detected):

```
(n/N) done / failed / aborted / in-flight
```

Updates on every state transition from the pool.

### `lamark-batch::report`

Aggregates per-prompt verdicts into `run_report.json`. Computed fields: pass rate, latency p50/p99, token cost, top failure modes (grouped by error class). Written atomically to `<output-dir>/run_report.json` on completion or graceful abort.

### `lamark-batch::resume`

Reads `~/.lamark/traces/<project_id>/` to find bundles associated with the given output directory. Skips any bundle whose `kb_upload_state.json` contains `status=uploaded`. This enables `--resume <output-dir>` to restart an interrupted run without re-running completed prompts.

## Day-1 behavior

On day 1, the Rust `lamark` binary boots and routes the chat loop directly through the Rust provider and agent crates — there is no Python bridge to fall back on. P1 closes with `lamark chat`, `lamark doctor`, and the help screens working. P2 wires in the agent loop + provider + bash tool + trace recorder for the first end-to-end round-trip.

Native UX wins from the rewrite:
- Sub-50ms startup (no interpreter).
- Real Ctrl-C handling (single cancels turn; double exits).
- Shell completions (`lamark completions zsh > ~/.zsh/completions/_lamark`).
- Stable exit codes.

## Tests

- **`tests/cli_parse.rs`** — snapshot every help screen with `insta`. Catches accidental option renames.
- **`tests/exit_codes.rs`** — assert each exit-code path returns the right number.
- **`tests/doctor_smoke.rs`** — `lamark doctor` against a mocked provider + KB returns green.
- **`tests/tui_record.rs`** — ratatui's testing harness; render-to-buffer and assert on the buffer text.

## Cutover gate (P1 done)

`lamark` binary:
- ✅ All subcommand `--help` outputs render and are stable.
- ✅ `lamark doctor` reports green when provider + KB are reachable and config validates.
- ✅ Config-only subcommands (`config show`, `config get`, `config set`) work without any provider call.
- ✅ Ctrl-C semantics match the spec (first cancel, double exit).
- ✅ The release binary is under 25 MB stripped on Linux/macOS.
