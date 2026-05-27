# 06 — Plugin host loads WASM + dylib plugin

> **Phase:** P5.
> **One-liner:** User installs a third-party WASM plugin (`github-issues`)
> and a dylib plugin (`fast-tokenizer`); capability-gated grants happen on
> first use; the agent uses the plugin's tools in a real session; all
> calls are traced and traceable.

---

## North-star contribution

- **Domain quality.** Plugins are how Lamark grows beyond v0.1 without
  shipping kitchen-sink defaults. A research user installs an MCP-bridge
  plugin in 10 seconds. A devops user installs a `terraform-state` plugin.
  Custom workflows happen here. Quality lever: the **capability prompt**
  (`plan/08:239–247`) is informative — users see *what* the plugin wants
  before approving.
- **Agent-side self-improvement.** Plugin tool calls are trace events
  (`PluginCallStarted/Completed/Failed`, `plan/08:309–313`). Curator can
  observe "this plugin's tools are never used" → suggest disabling /
  removing. Skill drafts may emerge from successful plugin tool sequences
  (G-023).
- **Model-side self-improvement.** Plugins extend the *capability surface*
  the model is trained against. As long as plugin tools follow the same
  Tool trait (`plan/05` + 00d §10–§11), their trace events are SFT-ready.

### Signals produced / consumed

- **Produces:** `PluginLoaded`, `PluginCallStarted/Completed/Failed` trace
  events; capability grant decisions in `~/.lamark/plugins/granted.toml`.
- **Consumes:** capability manifest from each plugin (`lamark-plugin.toml`);
  user grants.

---

## Idea

Engineer installs `github-issues` (a WASM plugin providing 6 tools) via
`lamark plugins install github.com/lamark-community/github-issues`. On
first use, the agent attempts `gh_issue_create` → capability prompt asks
permission for `network=api.github.com` → user grants `once`. Later the
engineer enables `fast-tokenizer` (dylib) for performance — `allow_dylib`
must be opt-in (`plan/08:209`) because dylib crashes can take down the
runtime.

## Actors

| Actor | Role |
|---|---|
| **Engineer** | Installs plugins via CLI; grants capabilities. |
| **Plugin host** | `crates/lamark-plugins/` — discovers, manifests, loads, gates. |
| **`lamark-plugin-sdk`** | Crate plugin authors use; hides WIT boundary mechanics (`plan/08:267`). |
| **Wasmtime** | WASM runtime; per-session `Store`, `wasi-snapshot-preview2` (`plan/08:269–274`). |
| **libloading** | dylib loader (`plan/08:276–280`). |
| **Hook bus / trace recorder** | Captures `Plugin*` events. |

## Trigger

```
$ lamark plugins install github.com/lamark-community/github-issues
$ lamark chat
> create an issue in jetbrains/lamark titled "..."
```

## Pipeline

### Step 0 — Install (out-of-band)

1. `lamark plugins install <url>` clones into `~/.lamark/plugins/github-issues/`. Validates `lamark-plugin.toml` schema (`plan/08:212–235`). Computes content hash. Recorded.
2. Listed in `lamark plugins list` as `disabled` until explicitly enabled (`lamark plugins enable github-issues`).

### Step 1 — Bootstrap discovery (cold start)

3. Bootstrap step 9 (`plan/03` / `plan/08:239`) inventories plugins. **Does not load** them yet — that's lazy.
4. The manifest is read; `provides_tools` registered as *placeholders* with a `requires_grant` flag. The tool registry knows the names exist.

### Step 2 — First tool call → capability prompt

5. Agent emits `gh_issue_create { ... }`. Tool registry sees `requires_grant: true` for `network=api.github.com`. Loads the plugin (Wasmtime instantiates the WASM module, runs `init(ctx)`).
6. Capability prompt fires (UI: TUI modal; gateway: bot prompt; per `plan/02 §"TUI region model"`):
   ```
   The plugin `github-issues` (v0.3.1) wants:
     - Network access to api.github.com
   Approve? [y / N / once]
   ```
7. User chooses `y` (granted persistently). `~/.lamark/plugins/granted.toml` updated atomically.
8. `PluginLoaded { name, version, source: Wasm }` event.

### Step 3 — Tool execution

9. Plugin's WASM `gh_issue_create` is invoked through the host bridge. Host imports (capability-gated HTTP client) make the actual `POST https://api.github.com/repos/...`. Response returned across the WIT boundary.
10. `PluginCallStarted { plugin, tool_or_event }` → `PluginCallCompleted { plugin, ok: true, duration_ms }` trace events.
11. Per-plugin tool timeout: 30s default (`plan/08:319`). Timeouts → `PluginCallFailed`.

### Step 4 — Dylib plugin (opt-in)

12. Engineer flips `plugins.allow_dylib = true` in config. `lamark plugins install <path-to-fast-tokenizer>` (must be local, signed in v0.2+).
13. **Loaded once at startup** (not per session — `plan/08:278`). `extern "C" fn lamark_plugin_create() -> *mut dyn Plugin`.
14. No sandbox. Full host trust. **Warned at startup**: "dylib plugin loaded; crashes will take down the runtime."
15. Tool calls run at native speed. Same `Plugin*` events recorded.

### Step 5 — Disable / revoke / reload

16. `lamark plugins disable github-issues` — tools delisted from registry; existing in-flight calls finish; plugin's `shutdown()` called.
17. `lamark plugins revoke github-issues` — removes from `granted.toml`; next use re-prompts.
18. `lamark plugins reload github-issues` — `shutdown` + re-instantiate; WASM `Store` recreated.

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0 (install) | `lamark::cli`, `lamark-plugins::install` | 02, 08:283–286 |
| 1 (bootstrap discovery) | `lamark::runtime`, `lamark-plugins::discover` | 03 step 9, 08:239 |
| 2 (capability prompt + load) | `lamark-plugins::load`, `lamark::tui` | 08:239–247, 02 |
| 3 (WASM tool exec) | `lamark-plugins::wasm`, `wasmtime`, `wit-bindgen` | 08:269–274 |
| 4 (dylib) | `lamark-plugins::dylib`, `libloading` | 08:276–280 |
| 5 (manage) | `lamark::cli` | 02 + 08:297–306 |
| All (events) | `lamark-hooks`, `lamark-trace` | 06 |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Manifest schema invalid** | Install rejected; clear error referencing the bad field. |
| **WASM module incompatible** (WIT mismatch) | Load fails; plugin remains disabled; user told to upgrade. |
| **Plugin requests undeclared capability at runtime** | Host denies; `PluginCallFailed { error: CapabilityNotGranted }`; agent gets a structured error. |
| **Granted capability revoked mid-session** | Next call prompts; cached state in `granted.toml` is authoritative. |
| **Plugin hangs > 30s** | Tool timeout; `PluginCallFailed { error: Timeout }`; plugin marked unhealthy after N timeouts (auto-disable). |
| **Dylib crashes** | Runtime dies (by design — that's the cost of dylib). Trace bundle is durable up to crash (recorder uses O_APPEND, `plan/06:368`). User warned at startup. |
| **Two plugins register tools with same name** | Bundled wins on slot collision (00c §3). Recorded `PluginRegistrationConflict`. |
| **Network egress denied for WASM HTTP import** | Host returns error; plugin handles or surfaces. |

## Acceptance criteria

- [ ] `lamark plugins install <url>` clones + validates + records hash; appears in `lamark plugins list` as `disabled`.
- [ ] `enable` + first use triggers a capability prompt with `name + version + capabilities` (per `plan/08:241–246`).
- [ ] Approval persists in `granted.toml`; revoke clears it.
- [ ] WASM tool call traces include `PluginCallStarted/Completed` with `duration_ms`.
- [ ] Per-tool 30s timeout enforced.
- [ ] Dylib loading requires `allow_dylib=true` AND emits a startup warning.
- [ ] Slot collision: bundled tool of the same name wins; conflict recorded.
- [ ] `lamark plugins reload` issues `shutdown + init`; subsequent calls work without restarting Lamark.
- [ ] Capability denial returns a *structured* tool error, not a panic.

## Self-improvement assertions

1. **Plugin usage signal feeds Curator.** A plugin with `PluginCallStarted` count = 0 for 30 days is surfaced in `curator-suggestions.md` as "consider disabling."
2. **Skill drafts from convergent plugin tool sequences.** Same convergence detection (G-023) applies; e.g., a recurring `gh_issue_get → Grep → gh_issue_comment` becomes a skill draft.
3. **Plugin tool traces are SFT-quality.** `PluginCall*` events round-trip through the reducer into Nemotron-Agentic-v1 with `tool_name = "plugin:<plugin>:<tool>"` namespacing.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| WASM vs dylib distinction + flags | plan/08:198–210 | _audit_ |
| Plugin manifest schema (`lamark-plugin.toml`) | plan/08:212–235 | _audit_ |
| Capability gating + prompt UX | plan/08:239–247 | _audit_ |
| Plugin trait surface (init/shutdown/tools/skills/...) | plan/08:251–264 | _audit_ |
| WIT world + host imports + tokio bridge | plan/08:269–274 | _audit_ |
| Dylib FFI shape + opt-in warning | plan/08:276–280 | _audit_ |
| Install / list / enable / disable / revoke / reload CLI | plan/08:297–306 | _audit_ |
| Plugin trace events (`PluginLoaded/CallStarted/Completed/Failed`) | plan/08:309–313 + plan/06 | _audit_ |
| Per-plugin 30s timeout | plan/08:319 | _audit_ |
| Bundled-wins-on-slot-collision | 00c §3 | _audit_ |
| Declarative plugin manifest counterpart (Claude-Code shape) | 00d §22 | _audit_ |
| Plugin SDK crate hiding WIT mechanics | plan/08:267 | _audit_ |
| Plugin-emitted skills load via same skill index | plan/08 §"Part A" + §"Part C" | _audit_ |
| Capability revocation mid-session | (likely partial) | _audit_ |
| Plugin auto-disable on N failures | (likely **gap**) | _audit_ |
| Signed dylibs (v0.2 path) | (deferred) | _audit_ |

## Open questions

1. **Capability granularity.** Is `network=api.github.com` matched as exact host, glob, or prefix? Plan/08 implies exact; pin.
2. **Auto-disable threshold for unhealthy plugins.** N consecutive failures? Time-windowed?
3. **Plugin sandbox for WASM HTTP imports.** Wasmtime's `wasi-http`? Custom host fn? Affects audit story.
4. **Per-session vs per-process WASM `Store`.** Plan/08:274 says per-session. Memory cost at scale (10 plugins × 10 sessions)?
5. **Granted capabilities scope** — global per plugin, per project, per session?
