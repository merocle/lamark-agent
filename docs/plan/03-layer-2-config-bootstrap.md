# 03 — Layer 2: Config & bootstrap

> Tiny in scope, load-bearing in practice. Every other layer reads its
> settings here and depends on the bootstrap order being right.

**Crate:** `crates/lamark-config/`
**Depends on:** standard lib + `figment` + `serde` + `serde_yaml`.
**Replaces:** hermes-agent's `cli-config.yaml.example` + scattered env reads.

## Sources & precedence (lowest to highest wins)

```
1.  Compiled-in defaults                            (lamark_config::default())
2.  /etc/lamark/config.yaml                         (system)
3.  ~/.lamark/config.yaml                           (user)
4.  ./.lamark/config.yaml                           (project)
5.  Profile overlay: ~/.lamark/profiles/<name>.yaml
6.  ENV: LAMARK_… variables                         (figment env provider)
7.  CLI -o flags: -o model.provider=ollama
8.  Per-call overrides at the API boundary
```

Tests for precedence are in `tests/precedence.rs` and are the spec.

## File: `~/.lamark/config.yaml`

```yaml
# ─── Identity ─────────────────────────────────────────────────────────
project_id: lamark-default            # logical namespace in knowledge-base
agent_id:   ~                         # auto: machine fingerprint + project

# ─── Model ────────────────────────────────────────────────────────────
model:
  provider:    vllm                   # vllm | ollama | llamacpp | lmstudio | sglang
                                      # | mlx | openai | anthropic | bedrock | gemini
  base_url:    http://localhost:8000/v1
  name:        Qwen/Qwen3.6-35B-A3B
  api_key_env: LAMARK_MODEL_KEY       # name of env var holding the key (or "EMPTY")
  context_length: 32768
  max_tokens:     8192
  timeout_seconds: 600

  reasoning:
    parser: qwen3_moe                  # qwen3_moe | nano_v3 | gemma4 | hermes | none
    inject_think_in_trace: true        # write <think> to trace.jsonl payloads

  tool_call:
    parser: openai_json                # openai_json | hermes_xml | mistral
    parallel_tool_calls: true

  cache:
    strategy: auto                     # auto | cache_control | prefix_hash | off
    ttl: 1h                            # cache_control TTL hint
    min_segment_tokens: 1024           # don't bother caching below this

  fallback_chain: []                   # ordered list of provider names

# ─── Provider routing (multi-provider) ─────────────────────────────────
providers:
  vllm:
    request_timeout_seconds: 600
    stale_timeout_seconds: 900
  anthropic:
    base_url: https://api.anthropic.com
    cache_control_ttl: 1h
  ollama:
    base_url: http://localhost:11434

# ─── Agent loop ───────────────────────────────────────────────────────
agent:
  max_iterations: 40
  max_parallel_tool_calls: 8
  worktree: true                       # auto git worktree per session
  tool_use_enforcement: true
  default_policy: prompt               # allow | prompt | forbid

# ─── Sandbox ───────────────────────────────────────────────────────────
# See plan/05c for the full Sandbox/agent-hosting design.
sandbox:
  default: docker                      # local | docker | ssh    (in-tree v0.1)
                                       # modal | daytona | singularity | vercel  (plugin candidates)
  local:
    mode: in-process                   # in-process | forked-process
    unsafe_allow_host_fs: false        # opt-in: skip --unsafe-local flag check
  docker:
    image: lamark/runner:latest
    egress: model-provider-only        # none | model-provider-only | allowlist
    egress_allowlist: []               # hostnames or CIDRs when egress == allowlist
    workspace_mount: copy-on-write     # fresh | read-only | copy-on-write
    user: 1000:1000
    cap_drop: [ALL]
    memory_limit: 4g
    cpu_limit: 2.0
  ssh:
    host: ~
    user: ~
    key_path: ~
  agent_hosting:
    subagent_default: docker           # what spawn_agent uses for prompt-derived roles
    trusted_role_default: local        # for "summarizer", "plan-compactor", etc.
    interrupt_grace_seconds: 5
  timeout: 180
  lifetime_seconds: 600

# ─── Memory ───────────────────────────────────────────────────────────
memory:
  external_provider: knowledge-base    # null | knowledge-base | honcho | mem0 | hindsight
  required: false                      # if true, agent refuses to start when KB is unreachable
  knowledge_base:
    base_url: http://localhost:8080
    project_id: lamark-default
    auth_token_env: KB_TOKEN
    timeout_seconds: 5
    retry:
      max_attempts: 3
      backoff_ms: 200
  honcho:
    base_url: https://api.honcho.dev
    api_key_env: HONCHO_KEY
  mem0:
    api_key_env: MEM0_KEY
  fallback:
    sqlite_path: "~/.lamark/memory.sqlite"   # offline cache; FTS5

# ─── Skills ───────────────────────────────────────────────────────────
skills:
  search_paths:
    - "./.lamark/skills"
    - "~/.lamark/skills"
  bundled_enabled: true
  curator:
    enable: true
    interval_hours: 168                 # weekly
    auto_archive_after_days: 30
    never_touch:
      - "pinned"
      - "user_authored"

# ─── Plugins ──────────────────────────────────────────────────────────
plugins:
  enable: true
  search_paths:
    - "~/.lamark/plugins"
  allow_dylib: false                    # default deny; opt-in for trusted
  allow_wasm: true
  capability_grants:                    # default-deny capabilities; opt-in per plugin
    fs_read: []
    fs_write: []
    network: []
    exec: []

# ─── Gateway ──────────────────────────────────────────────────────────
gateway:
  enable: false
  adapters: []                          # ["telegram","slack","discord","mcp_serve","rest","acp"]
  bind: "127.0.0.1:5050"
  telegram:
    bot_token_env: TG_BOT_TOKEN
    allow_user_ids: []
  slack:
    bot_token_env: SLACK_BOT_TOKEN
    app_token_env: SLACK_APP_TOKEN
  discord:
    bot_token_env: DISCORD_TOKEN

# ─── MCP (client + server) ────────────────────────────────────────────
mcp:
  servers: []                           # consumed MCP servers
  server:
    enable: false
    transport: stdio                    # stdio | http
    bind: "127.0.0.1:5051"
    expose_tools: ["read", "write", "edit", "bash", "grep", "memory_search"]

# ─── ACP ──────────────────────────────────────────────────────────────
acp:
  registry_url: ~
  identity:
    name: lamark
    key_path: "~/.lamark/acp/identity.key"

# ─── Trace ────────────────────────────────────────────────────────────
trace:
  enable: true
  root: "~/.lamark/traces"
  capture_inference_payloads: true
  capture_tool_payloads: true
  redact_inline: false
  upload_to_kb: true
  rotate:
    keep_days: 30
    max_gb: 50

# ─── Policy (Allow|Prompt|Forbid DSL) ─────────────────────────────────
policy:
  file: "~/.lamark/policy.toml"
  default: prompt

# ─── Learning ─────────────────────────────────────────────────────────
learning:
  enable_collection: true
  consent_required: false

# ─── Observability ────────────────────────────────────────────────────
observability:
  metrics:
    enable: true
    port: 9090                          # /metrics Prometheus
  langfuse:
    enable: false
    base_url: ~
    public_key_env: LF_PUBLIC
    secret_key_env: LF_SECRET
  wandb:
    enable: false
    project: lamark

# ─── Runtime ──────────────────────────────────────────────────────────
runtime:
  worker_threads: ~                      # tokio default (CPU count); override for low-RAM hosts
  blocking_threads: 32                   # max for spawn_blocking pool
  shutdown_grace_seconds: 10
```

## Rust types

One module = one config block. Centralized in `lamark-config::types`:

```rust
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Config {
    pub project_id: String,
    pub agent_id:   Option<String>,
    pub model:      ModelConfig,
    pub providers:  HashMap<String, ProviderTuning>,
    pub agent:      AgentConfig,
    pub sandbox:    SandboxConfig,    // renamed from `terminal`; see plan/05c
    pub memory:     MemoryConfig,
    pub skills:     SkillsConfig,
    pub plugins:    PluginsConfig,
    pub gateway:    GatewayConfig,
    pub mcp:        McpConfig,
    pub acp:        AcpConfig,
    pub trace:      TraceConfig,
    pub policy:     PolicyConfig,
    pub learning:   LearningConfig,
    pub observability: ObservabilityConfig,
    pub runtime:    RuntimeConfig,
}
```

Each sub-struct has `Default` + `serde(default)` on every field. No surprises if a section is omitted.

## Loader API

```rust
pub fn load(opts: &GlobalOptions) -> Result<Arc<Config>, ConfigError>;
pub fn load_or_init() -> Result<Arc<Config>, ConfigError>; // creates a stub at ~/.lamark/config.yaml if absent
pub fn validate(cfg: &Config) -> Result<(), Vec<ConfigError>>;
pub fn reachability(cfg: &Config) -> ReachabilityReport;   // doctor backend
```

## Bootstrap sequence

`lamark::runtime::build` runs these in order, each step has a `?` failure path:

1. **Validate config** (`lamark_config::validate`). Reject before any I/O.
2. **Resolve `agent_id`**. Stable hash of `(project_id, hostname, OS user, install path)` unless explicitly set.
3. **Open trace recorder**. Creates `~/.lamark/traces/<rollout_id>/`.
4. **Open provider router**. Instantiate the configured `ModelProvider` impl; warm a "no-op" connection (HEAD or `GET /v1/models`).
5. **Open knowledge-base client**. Verify version, project_id existence; if `memory.required=true`, fail loudly.
6. **Load policy**. Parse `policy.toml`; default-prompt for tools not listed.
7. **Discover skills**. Walk `skills.search_paths`; parse frontmatter; build the bundled-+-user skill index.
8. **Discover plugins**. Inventory; do NOT load yet (load on demand or at session start).
9. **Wire hook subscriptions**. Trace recorder subscribes to ALL events; other crates subscribe to what they need.
10. **Compose the system prompt** (the stable part). See plan 07.
11. **Mark runtime ready**. Boot banner printed; metrics endpoint goes up.

If step 4 fails (provider unreachable) and the subcommand requires inference, exit 67. Config-only subcommands (`config show`, `doctor`) skip steps 4–11.

## Secrets handling

- Secrets live in `~/.lamark/.env` (NEVER commit). `figment-env` loads `.env` automatically.
- The config file **references env var names**, not values: `api_key_env: LAMARK_MODEL_KEY`. Never inline a key.
- `lamark config show` masks any field ending in `_env` after resolution; raw values appear only with `--unsafe-show-secrets`.
- The `LAMARK_NO_ENV_FILE=1` env disables loading `.env` (for hardened deployments).

## Profiles

`--profile prod` (or `LAMARK_PROFILE=prod`) overlays `~/.lamark/profiles/prod.yaml` on top of the base config. Profiles can override anything. Useful for:

- `dev` — local vllm, tiny model, no docker sandbox.
- `prod` — Anthropic, docker sandbox, KB required.
- `headless` — gateway-only, no TUI.
- `eval` — deterministic temperature, fixed seed, archive-everything.

## CLI escape hatches

- `-o model.provider=ollama` overrides one key.
- `--set model.cache.strategy=off`.
- `LAMARK_MODEL_PROVIDER=ollama lamark chat`. Env wins over file but loses to `-o`.

## `lamark config doctor`

Output a colored report:

```
config-file:          /Users/me/.lamark/config.yaml          ✓
schema-validation:    OK                                       ✓
project_id:           lamark-default                          ✓
provider:             vllm @ http://localhost:8000/v1         ✓ (3 models)
knowledge-base:       http://localhost:8080                   ✓ v0.7.3
trace-root:           /Users/me/.lamark/traces (12 GB free)   ✓
policy-file:          /Users/me/.lamark/policy.toml           ✓ 42 rules
pyworker:             /tmp/lamark.501.sock                    ✓ rev 7e3a4f
skill-paths:          2 dirs, 18 skills, 0 errors             ✓
plugin-paths:         1 dir, 0 plugins                         ⚠ (empty)
secrets:              KB_TOKEN ✓, TG_BOT_TOKEN ∅ (gateway.telegram disabled, ok)
```

This is the human-facing readout that decides whether `lamark` will boot.

## § Project-id detection (G-041)

A `project_id: ProjectId` is a stable string identifier for a project. The detection function lives in `lamark-config::project_id`. It is pure (deterministic given the same inputs) and cheap (no filesystem writes on the fast path — only reads + cache lookup).

### Detection algorithm

1. **Named project config:** Walk up from `cwd`, looking for a `.lamark/config.toml` that contains a `project.name` field. If found, check whether `~/.lamark/projects/<name>/config.toml` exists. If it does, `project_id = name`.
2. **Hash-based fallback:** If no `.lamark/config.toml` is found with a name, compute:
   ```
   project_id = "proj_" + base62(SHA-256(canonical_cwd)[0..8])
   ```
   where `canonical_cwd` resolves all symlinks and has no trailing slash. Cache this mapping in `~/.lamark/project_id_cache.toml` as `cwd → project_id`.
3. **No cwd context (gateway / MCP / ACP):** Use the adapter's `default_project` config value, or fall back to the literal string `_default`.
4. **Explicit override:** `LAMARK_PROJECT_ID` env var always wins over all other detection steps.

### Rust signature

```rust
/// Detect the canonical project_id for the given working directory.
///
/// Detection order (highest priority first):
/// 1. `LAMARK_PROJECT_ID` env var.
/// 2. `project.name` in the nearest `.lamark/config.toml` walking up from `cwd`.
/// 3. Hash-based fallback: `"proj_" + base62(SHA-256(canonical_cwd)[0..8])`.
/// 4. `default` when `cwd` is `None`.
///
/// The function performs only filesystem reads on the fast path. Cache writes
/// happen lazily through [`write_project_id_cache`] and must be called by the
/// caller when a new hash-derived id is first computed.
pub fn detect_project_id(
    cwd: Option<&Path>,
    env: &dyn EnvProvider,
    cache: &ProjectIdCache,
) -> Result<ProjectId, ConfigError>;
```

The `EnvProvider` abstraction allows tests to inject a fixed environment without touching the process environment.

### Cache format (`~/.lamark/project_id_cache.toml`)

```toml
# Automatically managed — do not edit by hand.
[mappings]
"/Users/me/projects/foo" = "proj_aB3kQ7rZ"
"/Users/me/projects/bar" = "proj_Xw2mN9vP"
```

Entries are never expired automatically; stale entries are harmless (the canonical path just won't match a live directory).

## § Capability grants and scope (G-031)

Plugin capabilities can be granted at three scopes. Scope resolution: **session > project > global**. Narrower scope wins — including revocations.

### Grant declaration

```toml
# ~/.lamark/config.toml  (global: all projects, all sessions)
[plugins.grants.global]
my_plugin = ["read_file", "network"]

# ./.lamark/config.toml  (project: only this project's sessions)
[plugins.grants.project]
my_plugin = ["write_file"]

# Runtime: session-scoped grants (not persisted; issued by operator during session)
# Issued via: lamark plugin grant --session <id> <plugin> <capability>
```

Session-scoped grants are never written to disk. They are held in the session's `PermissionState` (see plan/05) and expire when the session ends.

### Revocations

A revocation at any scope removes the capability from the effective grant set, regardless of whether a wider scope grants it:

```toml
[plugins.revocations.project]
my_plugin = ["network"]   # revokes the global network grant for this project
```

Revocation semantics: any revocation at any scope (session, project, or global) removes the capability. A grant at a narrower scope cannot restore a capability revoked at a wider scope.

### Rust types

```rust
/// Scoped capability grants and revocations for a single plugin.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct PluginCapabilityScope {
    pub global:  PluginScopeEntry,
    pub project: PluginScopeEntry,
    // Session scope lives in PermissionState (plan/05), not in config types.
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct PluginScopeEntry {
    pub grants:      HashMap<String, Vec<String>>,   // plugin → capabilities
    pub revocations: HashMap<String, Vec<String>>,
}
```

`PluginsConfig` gains two new fields:

```rust
pub grants:      PluginCapabilityScope,
pub revocations: PluginCapabilityScope,
```

### `lamark config doctor` output (grants section)

The doctor report gains a `plugin-grants` line that prints, per plugin, the effective grant set (session ∪ project ∪ global, minus any revocations) and the scope each capability came from:

```
plugin-grants:        my_plugin: read_file(global) write_file(project) network✗(revoked@project)
```

## Hot-reload (deferred)

Config hot-reload is out of scope for v0.1. SIGHUP-reload is on the roadmap and will require all layers to expose a `Reconfigure(&Config) -> Result<()>` method on their traits. Mark that work as **future**; don't try to design for it now.

## Tests

- **`tests/precedence.rs`** — layered loader behavior across all 8 source tiers.
- **`tests/secrets.rs`** — masked output assertions.
- **`tests/profiles.rs`** — overlay merge semantics.
- **`tests/doctor.rs`** — golden-file output (with `insta`), per OS.
- **`tests/no_pyworker_refs.rs`** — grep the repo for any leftover mention of `pyworker`/`python_bridge`; fail if found. Belt-and-braces against the strangler-fig idea leaking back in.

## Cutover gate (P1 done)

- ✅ All config blocks above deserialize cleanly with `serde(deny_unknown_fields)` off (we permit unknown keys for forward-compat).
- ✅ `lamark config doctor` exits 0 on a happy-path machine and 64 on a broken config.
- ✅ Layered precedence test (8 sources) all passes.
- ✅ No secret values appear in `lamark config show` output without `--unsafe-show-secrets`.
