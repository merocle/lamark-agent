# 08 — Layer 7: Skills + plugin host + Curator

> 📎 **See also:** [00c addendum §2, §3](./00c-hermes-deepdive-addendum.md) — pins
> the three-tier skill progressive disclosure (`skills_list → skill_view →
> skill_view(file)`), frontmatter validation table, curator's archive-not-delete
> policy with tarball backup + pinned-skill exemption + agent-created-only scope,
> and the full plugin `ctx` contract (`register_hook | register_command |
> register_provider` with bundled-wins-on-slot-collision).
>
> 📎 **See also:** [00d addendum §20–§22, §32](./00d-claude-code-deepdive-addendum.md) —
> Claude-Code skill discovery tiers (managed > user > project; `realpath` dedup;
> directory-format only with `SKILL.md`), the complete frontmatter superset
> (`when-to-use`, `allowed-tools`, `user-invocable`, `disable-model-invocation`,
> `context: inline|fork`, `paths` for conditional activation, `shell`, `effort`),
> declarative plugin definition record (`{name, version, skills, hooks, mcpServers,
> isAvailable, defaultEnabled}`) — the *manifest* counterpart to 00c §3's *runtime*
> plugin `ctx`, and the Dream-style memory consolidator (three-gate trigger +
> four-phase consolidation + read-only synthesis) for the Curator.

> Three subsystems sharing one ideology: the agent's capability surface grows
> over time, in artifacts the user can read, version, and review.

**Crates:** `crates/lamark-skills/`, `crates/lamark-plugins/`.
**Depends on:** `lamark-core`, `lamark-hooks`, `lamark-prompt`, `lamark-tools`, `lamark-kb-client`, `wasmtime`, `libloading`.
**References:**
- `~/.cache/lemark/vendor/hermes-agent/agent/skill_*.py`, `agent/curator.py` — skill lifecycle, Curator state machine.
- `~/.cache/lemark/vendor/claude-code/skills/loadSkillsDir.ts`, `skills/bundledSkills.ts` — discovery conventions.

---

## Part A — Skill system

### What a skill is

A markdown file. Frontmatter + body. Versioned. Either bundled (compiled-in) or filesystem-loaded.

```yaml
---
name: simplify
description: Review changed code for reuse, quality, and efficiency, then fix any issues found.
when_to_use: |
  When the user asks for a code review, or after a multi-step refactor.
aliases: [simp, review-simplify]
version: 3
author: agent              # agent | user | bundled
is_command: true           # exposes as /simplify slash command
tools_required: [Read, Grep, Edit]
inputs:
  - name: scope
    description: file path or glob (default: current cwd)
    required: false
mutable: true              # eligible for Curator rewrite
---

# What this skill does

Walks the diff vs HEAD, looks for duplication, dead code, unused complexity, and bad abstractions. Reports findings, then fixes the high-confidence ones in-place.

...body continues...
```

### Search paths (claude-code convention)

```
1. ./.lamark/skills/                  # project (highest priority)
2. ~/.lamark/skills/                  # user
3. <bundled, compiled in>             # built-in
```

First match wins by `name`. Aliases resolve after `name`.

### Loader

```rust
// crates/lamark-skills/src/loader.rs
pub struct SkillIndex {
    skills: HashMap<String, Arc<Skill>>,
    by_alias: HashMap<String, String>,
    file_watcher: Option<notify::RecommendedWatcher>,
}

impl SkillIndex {
    pub async fn discover(paths: &[PathBuf], bundled: &'static [BundledSkill]) -> Result<Self>;
    pub fn get(&self, name_or_alias: &str) -> Option<Arc<Skill>>;
    pub fn list(&self, filter: &SkillFilter) -> Vec<Arc<Skill>>;
    pub fn watch(&mut self, on_change: Arc<dyn Fn() + Send + Sync>) -> Result<()>;
}

pub struct Skill {
    pub meta: SkillMeta,                     // parsed frontmatter
    pub body: String,                        // markdown after frontmatter
    pub path: SkillSource,                   // Bundled | UserFile(PathBuf) | ProjectFile(PathBuf)
    pub fingerprint: u64,
    pub kb_record_id: Option<String>,        // if mirrored to KB
}
```

### Prompt integration

Tier-1 of the system prompt (plan/07) contains a **skill index** — name + description + when_to_use for every loaded skill. The full **body** is fetched on demand when the skill is invoked.

That preserves prompt cache: adding/removing skills only invalidates the small index section, not the full body of every skill.

### Tools that operate on skills

(Registered with `lamark-tools` from the skills crate, optional via `skills.enable`.)

| Tool | What it does |
|---|---|
| `SkillView`    | Read a skill body by name. |
| `SkillInvoke`  | "Run" a skill — i.e., inject the body as a user message in the current conversation, then continue. |
| `SkillNew`     | Create a new agent-authored skill. Triggers the authoring flow described in plan/07b Loop D. |
| `SkillManage`  | Pin / unpin / archive / restore. Audit-logged. |
| `SkillList`    | List with filters. |

### Slash commands (already listed in plan/02)

```
/skill list / view <name> / new <name> / pin <name> / unpin / archive / lineage
```

### Skill draft lifecycle

Skill drafts (agent-authored, not yet curated) live at:

```
~/.lamark/skills/drafts/<skill_slug>.<session_id>.md
```

Lifecycle states are tracked in the YAML frontmatter `status:` field:

| Status | Meaning |
|---|---|
| `draft` | Freshly emitted by the agent, not yet reviewed. |
| `pending_curation` | Submitted to Curator queue. |
| `accepted` | Curator accepted; moved to `~/.lamark/skills/<skill_slug>.md`. |
| `rejected` | Curator rejected; stays in `drafts/` with rejection reason in frontmatter; deleted after 30 days. |
| `superseded` | A newer draft or accepted skill covers the same intent; deleted after 7 days. |

On session end, the reducer scans the trace for any `write_file` events targeting `~/.lamark/skills/drafts/` and tags them with `kind=SkillDraft` in the KB upload. The Curator reads these on its weekly sweep.

Project-scoped skills live at `./.lamark/skills/` (no `drafts/` subdirectory — project skills are curated by the operator, not the Curator).

### Mirroring to knowledge-base

Every skill change (`create`, `update`, `archive`, `restore`, `pin`, `unpin`, Curator-action) emits a `SkillRecord` to KB. Lineage is queryable: "show me the history of this skill" → KB returns ordered events.

### Local file-system watcher

`notify` crate. When a user edits a skill file on disk, the watcher hot-reloads the index. The new fingerprint invalidates the Tier-1 skill-index section in the prompt composer (plan/07).

---

## Part B — Curator background agent

A small recurring agent that grooms the skill library. Hermes pattern: weekly cycle, never deletes (only archives), respects pinned status.

### Curator state file

`~/.lamark/skills/.curator_state.toml`:

```toml
last_run_at = "2026-05-24T03:14:00Z"
last_run_actions = 7
paused = false
run_count = 39

[per_skill_state]
simplify  = { last_used_at = "2026-05-22T...", successful_uses = 18, total_uses = 22 }
review    = { last_used_at = "2026-05-04T...", successful_uses = 5,  total_uses = 8  }
```

### When Curator runs

- Cron-style: `skills.curator.interval_hours` (default 168 = weekly).
- Manually: `lamark skills curate-now`.
- Triggered: post-monthly-merge (plan/10).
- Triggered: on `AdapterPromoted` event (see § Curator trigger on adapter promotion below).

### Curator trigger on adapter promotion

The Curator's event loop subscribes to `AdapterPromoted` events from KB in addition to its weekly cron:

- `POST /agents/{id}/events/subscribe?kind=AdapterPromoted` returns an SSE stream.
- On receiving `AdapterPromoted`, the Curator schedules a re-evaluation run within 1 hour (not immediately, to allow the adapter to warm up and generate fresh traces).
- The re-evaluation run is **scoped**: only skills that reference tool calls or patterns mentioned in the `AdapterPromoted.eval_scorecard` are re-evaluated. Full sweep is reserved for the weekly cron.
- The re-evaluation run respects the 48h quiet window (G-035): if `rollout_lock.json` is active, defer to after the lock expires.
- After the run, emit `CuratorRan { trigger: "adapter_promotion", adapter_id, skills_evaluated, skills_updated }` to KB.

### Embedding service

The Curator uses embeddings to detect semantic similarity between skills for consolidation and deduplication. Embedding service resolution order:

1. **KB-hosted:** `POST /knowledge/embeddings` on the sibling knowledge-base service. Preferred when KB is available (supports batch, caches, model-agnostic).
2. **Local model:** `crates/lamark-skills::embed::local` — uses `fastembed-rs` with `BAAI/bge-small-en-v1.5` (quantized, ~23 MB). Fallback when KB is down.
3. **Aux model provider:** If `curator.embedding_provider = "aux"` in config, uses the provider configured at `providers.aux` (e.g., a local Ollama instance with `nomic-embed-text`).

Similarity threshold for consolidation: Jaccard ≥ 0.85 on 13-gram **OR** cosine ≥ 0.92 on embedding. Either condition triggers a consolidation proposal.

### What Curator does (decision table)

| Skill state | Action |
|---|---|
| Pinned | Leave alone. Period. |
| User-authored | Never auto-modify; only suggest in `~/.lamark/curator-suggestions.md`. |
| Agent-authored, used > 5×, success_rate > 0.7 | Keep; possibly rewrite for clarity (one-shot improve pass with frontier model). |
| Agent-authored, used 0× in 30 days | Move to `archive/` subfolder. Don't delete. |
| Agent-authored, success_rate < 0.3 | Archive with rationale. |
| Two skills with embedding-similarity > 0.85 + overlapping when_to_use | Consolidate (propose merger; require approval if either was used by user explicitly). |
| Cross-cutting topic with 0 skills | Suggest authoring a skill (write a placeholder). |

### Curator constraints

- Runs in its own session (own AIAgent) with a tightly scoped toolset: `[SkillList, SkillView, SkillManage, MemorySearch]`. NO `Bash`, NO `Write`, NO `WebFetch`.
- Cannot exceed 30 actions per run.
- Cannot run while another Curator is running (file lock).
- Every action emits a `CuratorAction` event to the trace recorder.
- KB mirrors all actions for forensic reproducibility.

### Curator promotion

Once a month, Curator proposes skills for promotion to bundled. Promotion requires:
- ≥ 20 successful uses across N sessions.
- Success rate ≥ 0.85 over 30 days.
- No security flag.
- No name collision with existing bundled skill.

Proposals land in `~/.lamark/curator-promotions.md` as a human-reviewable list. A maintainer commits them into `crates/lamark-skills/src/bundled/` for the next release.

### Loop D wiring (cross-ref to plan/07b)

Curator + skill authoring + KB lineage = the "Voyager-style skill library" loop in plan/07b §"Loop D — Across-month: Skill authoring."

---

## Part C — Plugin host

Plugins extend the runtime *without* being agent-authored. They are written by humans, distributed as binaries (.wasm or .so/.dylib), capability-gated, and load on demand.

### Two flavors

| Flavor | Crate | Why |
|---|---|---|
| **WASM**  (default, recommended) | `wasmtime` + `wasi-snapshot-preview2` | Sandboxed, portable, language-agnostic source (Rust/Go/C/TinyGo/...). Slower than native. |
| **Dylib** (opt-in, advanced) | `libloading` | Native speed. No sandbox; full host trust. Used for performance-sensitive integrations (e.g., model-specific tokenizers, custom DB drivers). |

Default config:
```yaml
plugins:
  enable: true
  allow_wasm: true
  allow_dylib: false                   # explicit opt-in
```

### Plugin manifest

Every plugin ships with `lamark-plugin.toml`:

```toml
[plugin]
name = "github-issues"
version = "0.3.1"
license = "MIT"
description = "Read/write GitHub issues; bundle of 6 tools."
provides_tools = ["gh_issue_list", "gh_issue_get", "gh_issue_create", ...]
provides_skills = []
provides_gateway_adapters = []
provides_memory_providers = []

[capabilities]
fs_read = []                          # paths it needs read access to
fs_write = []
network = ["https://api.github.com"]  # explicit allow-list
exec = []                             # exec'd binaries

[hooks]
listens_to = ["UserPromptSubmit"]
```

### Capability gating

Bootstrap (plan/03 step 9) inventories plugins but does NOT load them. On first use of a plugin's exposed tool/skill/etc., the user is prompted:

```
The plugin `github-issues` (v0.3.1) wants:
  - Network access to api.github.com
Approve? [y/N/once]
```

Approvals persist in `~/.lamark/plugins/granted.toml`. Revocation: `lamark plugins revoke <plugin>`.

### Plugin API surface (host → guest)

A plugin can register:

```rust
pub trait Plugin: Send + Sync {
    fn manifest(&self) -> &PluginManifest;
    async fn init(&self, ctx: &PluginInitCtx) -> Result<()>;
    async fn shutdown(&self) -> Result<()>;

    fn tools(&self) -> Vec<Box<dyn Tool>> { vec![] }
    fn skills(&self) -> Vec<Skill> { vec![] }
    fn gateway_adapters(&self) -> Vec<Box<dyn GatewayAdapter>> { vec![] }
    fn memory_providers(&self) -> Vec<Box<dyn MemoryProvider>> { vec![] }
    fn hooks(&self) -> Vec<HookSubscription> { vec![] }
}
```

In WASM, this trait crosses the boundary via the `wit-bindgen`-generated stub (we ship a `lamark-plugin-sdk` crate that hides the boundary mechanics).

### WASM-specific

- WIT (WASM Interface Type) world definition in `crates/lamark-plugins/wit/lamark.wit`.
- Host imports: HTTP client (capability-gated), memory operations, hook emit, tool registration, logging.
- Guest exports: lifecycle (`init`, `shutdown`), tool callbacks, hook callbacks.
- Tokio-friendly: each plugin runs on a dedicated WASM `Store` per session; host async calls bridge via `wasmtime-wasi-poll`.

### Dylib-specific

- Cdylib with `extern "C" fn lamark_plugin_create() -> *mut dyn Plugin`.
- Loaded once at startup (not per session).
- Crashes can take down the runtime — DO NOT ENABLE in production unless the plugin is trusted.

### Distribution

- Plugins live in `~/.lamark/plugins/<name>/`. Contents: `lamark-plugin.toml`, `plugin.wasm` (or `.so`/`.dylib`), `README.md`, optional `skills/`.
- Installation: `lamark plugins install <git-url-or-path>` (clones into the plugins dir; runs `lamark-plugin.toml` schema check; computes hash; records).
- Future: a registry. Out of scope for v0.1.

### Tools the plugin host exposes

| Tool | What it does |
|---|---|
| `PluginInvoke` | Bridge tool used internally by the registry to call a plugin's tool by name; users don't invoke directly. |
| `PluginInfo`   | Read manifest + granted capabilities of a loaded plugin. |

### CLI

```
lamark plugins list
lamark plugins install <url|path>
lamark plugins remove <name>
lamark plugins enable <name>
lamark plugins disable <name>
lamark plugins revoke <name>
lamark plugins grants <name>
lamark plugins reload <name>
```

### Hooks fired by the plugin host

- `PluginLoaded { name, version, source }`
- `PluginCallStarted { plugin, tool_or_event }`
- `PluginCallCompleted { plugin, ok, duration_ms }`
- `PluginCallFailed   { plugin, error }`

All recorded by the trace recorder.

### Plugin auto-disable and quarantine

A plugin is auto-disabled when either of the following conditions is met:

- It fails (panics, returns `Err`, or times out after 30s) on `N` consecutive tool calls, where `N = plugins.auto_disable_threshold` (default 3).
- It triggers a `Forbidden` policy decision.

On auto-disable:

1. The plugin is moved to quarantine state: its `plugins.toml` entry is updated with `status = "quarantined"`, `disabled_at`, `failure_count`, and `last_error`.
2. All pending tool calls to the plugin return `ToolCallResult::Err("Plugin quarantined: <reason>")`.
3. An `OperatorAlert` event is emitted (plan/11 ops channel).
4. The plugin is **not** auto-re-enabled. The operator must run `lamark plugin enable <name>` to restore, which resets `failure_count` and clears quarantine.
5. KB records `PluginQuarantined { plugin_id, reason, failure_count }` as an agent event.

### § Plugin capability scope enforcement (G-031)

The plugin host evaluates capability grants at request time — never cached across calls — using the three-scope model defined in plan/03 § Capability grants and scope.

**Resolution algorithm (called on every tool invocation):**

1. **Collect grants:** merge capabilities from session cache → project config → global config. A capability present at any scope is initially included.
2. **Apply revocations:** iterate revocations from session cache → project config → global config. Any revocation at any scope removes the capability from the effective set. A narrower-scope grant cannot reinstate a capability that a revocation at any scope has removed.
3. **Evaluate:** if the requested capability is not in the final effective grant set, the tool call returns immediately with `ToolCallResult::Err("Capability not granted: <cap>")` — the plugin is never invoked.
4. **Session-scoped grants expire at session end.** They are stored in the session's `PermissionState` (plan/05) and are never written to disk.

**Rust sketch (inside `lamark-plugins::host`):**

```rust
/// Compute the effective capability set for `plugin` in the current session.
/// Revocations at any scope remove a capability regardless of which scope granted it.
fn effective_grants(
    plugin: &str,
    session: &PermissionState,
    project_cfg: &PluginsConfig,
    global_cfg: &PluginsConfig,
) -> HashSet<String> {
    let mut granted = HashSet::new();

    // Collect (widest scope first — order does not matter; all go in)
    for cap in global_cfg.grants.global.grants.get(plugin).into_iter().flatten() {
        granted.insert(cap.clone());
    }
    for cap in project_cfg.grants.project.grants.get(plugin).into_iter().flatten() {
        granted.insert(cap.clone());
    }
    for cap in session.plugin_grants(plugin) {
        granted.insert(cap.clone());
    }

    // Remove revocations (any scope wins)
    for cap in global_cfg.revocations.global.revocations.get(plugin).into_iter().flatten() {
        granted.remove(cap);
    }
    for cap in project_cfg.revocations.project.revocations.get(plugin).into_iter().flatten() {
        granted.remove(cap);
    }
    for cap in session.plugin_revocations(plugin) {
        granted.remove(cap);
    }

    granted
}
```

**`lamark plugin list --verbose`** prints the effective grant set per plugin for the current session and project, annotated by the scope each capability came from and any active revocations:

```
github-issues  v0.3.1  enabled
  network       ✓  (global)
  fs_read       ✓  (project)
  exec          ✗  (not granted)
my_plugin       v1.0.0  enabled
  network       ✗  (revoked @ project; was granted @ global)
  read_file     ✓  (global)
  write_file    ✓  (project)
```

### Safety / observability

- Per-plugin timeout: 30s default for tool invocations.
- Memory limit (WASM): 128 MB default; configurable per plugin.
- CPU fuel limit (WASM): 10B instructions / call; OOM behavior is fail-call, not crash-runtime.
- Auto-disable threshold: see § Plugin auto-disable and quarantine above.
- Metrics: `lamark_plugin_calls_total{plugin,outcome}`, `lamark_plugin_duration_seconds{plugin}`.

---

## Tests

### Skills

- **`tests/skills/loader.rs`** — bundled + user + project; project wins ties.
- **`tests/skills/frontmatter_parse.rs`** — golden file; all known fields; unknown fields are tolerated.
- **`tests/skills/hot_reload.rs`** — write a new file → index picks up in <1s.
- **`tests/skills/kb_sync.rs`** — every change emits a KB record.

### Curator

- **`tests/curator/idle_skip.rs`** — Curator skips if last_run within interval.
- **`tests/curator/no_unauthorized_writes.rs`** — Curator's tool registry excludes Write/Bash/WebFetch; attempted use rejects.
- **`tests/curator/archive_unused.rs`** — skill not used in 30 days → moved to archive.
- **`tests/curator/never_modify_user.rs`** — user-authored skill never modified; only suggestion emitted.

### Plugins

- **`tests/plugins/wasm_smoke.rs`** — load a stub WASM plugin that exposes one tool; invoke it; assert result.
- **`tests/plugins/capability_deny.rs`** — plugin without `network` cap tries to call `http_get` → host denies.
- **`tests/plugins/quarantine.rs`** — 3 failures → quarantined; next call within 15 min skipped with telemetry.
- **`tests/plugins/dylib_load_default_off.rs`** — `allow_dylib=false` (default) rejects loading a `.so`.

## Cutover gate (P5 done)

- ✅ At least 3 bundled skills loaded; `/skill list` shows them.
- ✅ One agent-authored skill written and mirrored to KB; visible via `lamark skills lineage <name>`.
- ✅ Curator runs on schedule; produces a non-empty `curator-suggestions.md` after a week of mock traffic.
- ✅ One WASM plugin (`hello-world` test plugin) loads with `network=[]` capability and exposes a tool.
- ✅ Capability deny test passes — plugin without network cap can't call HTTP.
- ✅ `lamark plugins list` shows installed plugins with grant status.
