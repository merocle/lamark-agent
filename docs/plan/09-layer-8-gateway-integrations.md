# 09 — Layer 8: Gateway, MCP, ACP, integrations

> 📎 **See also:** [00c addendum §5–§10](./00c-hermes-deepdive-addendum.md) — pins
> the two-guard messaging model (adapter queue + runner control-command
> interception), full `PlatformEntry` field list (`standalone_sender_fn`,
> `cron_deliver_env_var`, `platform_hint`, `pii_safe`, `max_message_length`,
> `supports_draft_streaming`, `message_len_fn`), `MessageEvent` / `SessionSource`
> shape, voice-memo path (audio → cache → STT → text), ACP `fork_session` +
> `SessionMode`, and the expanded MCP server surface (`events_poll`,
> `events_wait`, `channels_list`).
>
> 📎 **See also:** [00d addendum §13](./00d-claude-code-deepdive-addendum.md) — MCP
> tool wrapping precision: `mcp__<server>__<tool>` prefix, lazy schema fetch from
> `tools/list`, `_meta` + `structured_content` passthrough through the Tool result,
> server-level (not per-tool) MCP permission rule, parent server reuse by subagents.

> The outside world's connections to Lamark. Long-running gateway with
> messaging adapters, MCP client+server, ACP registry, optional REST.

**Crates:** `crates/lamark-gateway/`, `crates/lamark-mcp/`, `crates/lamark-acp/`.
**Depends on:** `lamark-core`, `lamark-tools`, `lamark-hooks`, `lamark-policy`, all session machinery.
**References:**
- Hermes-Agent `cli.py` gateway portion + 20+ messaging adapters.
- Codex `mcp-server/`, `rmcp-client/` (Rust, Apache 2.0).
- Hermes `acp_adapter/`, `acp_registry/` for ACP protocol.

---

## Part A — Gateway

### What the gateway is

A long-running Lamark process that:
1. Listens on N transports (Telegram bot, Slack socket, Discord, REST, ACP, MCP-over-HTTP).
2. Multiplexes incoming messages into per-conversation `Session`s.
3. Streams responses back.
4. Survives restarts (sessions persist via trace bundles + KB).

Started via:
```bash
lamark gateway run [--profile P] [--adapters telegram,slack,discord]
```

### Architecture

```
                      gateway process
   ┌──────────────────────────────────────────────────────────┐
   │  ┌────────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
   │  │  Telegram  │  │  Slack   │  │  Discord │  │  REST   │ │
   │  │   adapter  │  │  adapter │  │  adapter │  │  HTTP   │ │
   │  └─────┬──────┘  └────┬─────┘  └─────┬────┘  └────┬────┘ │
   │        │              │              │             │      │
   │  ┌─────▼──────────────▼──────────────▼─────────────▼────┐│
   │  │              Adapter facade (trait)                   ││
   │  │     normalize → IncomingMessage / OutgoingMessage     ││
   │  └─────────────────────────┬─────────────────────────────┘│
   │                            │                              │
   │  ┌─────────────────────────▼─────────────────────────────┐│
   │  │                 Session router                         ││
   │  │  conversation_key → Session (spawn or resume)          ││
   │  └─────────────────────────┬─────────────────────────────┘│
   │                            │                              │
   │  ┌─────────────────────────▼─────────────────────────────┐│
   │  │                  AIAgent core                          ││
   │  └────────────────────────────────────────────────────────┘│
   └────────────────────────────────────────────────────────────┘
```

### Adapter trait

```rust
#[async_trait]
pub trait GatewayAdapter: Send + Sync {
    fn name(&self) -> &str;
    async fn start(&self, ctx: &GatewayContext) -> Result<(), GatewayError>;
    async fn send(&self, out: OutgoingMessage) -> Result<(), GatewayError>;
    async fn shutdown(&self) -> Result<(), GatewayError>;
}

pub struct IncomingMessage {
    pub conversation_key: ConversationKey, // e.g., "telegram:chat=42:user=7"
    pub from_user_id: String,
    pub display_name: Option<String>,
    pub channel_id: Option<String>,
    pub thread_id:  Option<String>,
    pub kind:       MessageKind,           // Text | VoiceMemo | Image | File | Slash
    pub content:    Vec<ContentBlock>,
    pub permissions: ExternalPermissions,  // platform-specific ACL hints
    pub timestamp:  SystemTime,
    pub raw:        Value,                  // platform payload
}
```

### Adapters at v0.1

1. **Telegram** — `teloxide` crate, long-polling or webhook.
2. **Slack** — Socket Mode (`slack-morphism` or hand-rolled WS); thread-keyed by `thread_ts`.
3. **Discord** — `serenity` or `twilight-rs`; per-channel + per-thread.
4. **REST** — `axum` HTTP server; simple `POST /v1/messages` with conversation-key in header.
5. **MCP-over-HTTP** — see Part B.
6. **ACP** — see Part C.

Other adapters from Hermes (WhatsApp/Signal/Teams/QQ/LINE/SMS/...) are scoped out of v0.1; they become plugins (plan/08 Part C).

### Conversation routing

`ConversationKey` is the unit of routing. The gateway maintains a `DashMap<ConversationKey, Arc<Session>>`. Lookup is constant-time. New keys spawn a new Session; existing keys deliver to the live one. Sessions are kept warm for `gateway.session_idle_seconds` (default 600) after the last message, then archived.

For Slack threads: `conversation_key = "slack:team:channel:thread_ts"` — multiple humans can share one conversation, identified by their `from_user_id`. (Matches Hermes's published Slack behavior.)

### Permission model

Each adapter exposes `ExternalPermissions`:

```rust
pub struct ExternalPermissions {
    pub user_authenticated: bool,
    pub user_groups: Vec<String>,
    pub channel_is_private: bool,
    pub allow_dangerous_tools: bool,
}
```

The policy engine (plan/05) gets these as additional context when evaluating `PreToolUse`. E.g.: bash is `Allow` for an admin in a private channel; `Forbidden` in a public channel; `Prompt` for everyone else.

### Replies

Replies are streaming. The adapter implements `send(OutgoingMessage)`; for streaming responses, the adapter receives `DeltaText` events and either:
- Buffers and edits a single message (Telegram pattern).
- Streams to a thread (Slack pattern).
- Posts a final message (Discord default).

### Hooks fired

- `GatewayConnected { adapter }`
- `GatewayDisconnected { adapter, reason }`
- `GatewayMessageIn { adapter, payload_ref }`
- `GatewayMessageOut { adapter, payload_ref }`

The trace recorder writes all of these.

### Configuration

```yaml
gateway:
  enable: true
  bind: 127.0.0.1:5050              # for REST
  session_idle_seconds: 600
  per_user_rate_limit: 30/m
  adapters: [telegram, slack, rest]
  telegram:
    bot_token_env: TG_BOT_TOKEN
    allow_user_ids: [123, 456]
    allow_group_ids: []
  slack:
    bot_token_env: SLACK_BOT_TOKEN
    app_token_env: SLACK_APP_TOKEN
    workspaces: ["T123"]
  rest:
    require_token: true
    token_env: LAMARK_REST_TOKEN
```

### Gateway project scoping

Gateway adapters (Telegram, Slack, Discord) have no working directory, so `project_id` cannot be derived from `cwd`. Resolution order:

1. **Per-adapter config:** `gateway.telegram.default_project = "<project_id>"` in `~/.lamark/config.toml`.
2. **Per-conversation config:** KB stores `UserProfile.default_project` for each `chat_id`. Set by the user via `/project set <name>` bot command.
3. **Fallback:** `_default` project.

The `project_id` is written into the trace manifest at session creation. Users can switch projects mid-conversation with `/project set <name>` — this starts a new session (the current session is finalized first).

### CLI

```
lamark gateway run                       # blocking; sigterm-safe
lamark gateway run --replace             # send SIGTERM to existing gateway first
lamark gateway list                      # list active gateway processes (PID/lock file)
lamark gateway stop                      # graceful shutdown of the registered gateway
lamark gateway status                    # adapters, sessions, recent message counts
```

### Lifecycle / failure modes

- Adapter outage: log + auto-reconnect with exponential backoff; metric `lamark_gateway_disconnects_total{adapter}`.
- Rate-limit hit: per-user throttle with a polite `please slow down` reply.
- Auth failure: adapter quarantined; gateway continues with the others.
- Context-compression in long group chats: solved by the standard ContextCompressor (plan/05). Don't repeat Hermes's Issue #9893 — the `--replace` flag must work reliably.

---

## Part B — MCP (client + server)

MCP = Model Context Protocol. Lamark is **bidirectional**: it consumes other MCP servers (as tools), and it exposes its own tools as an MCP server.

### Client (consume external MCP servers)

```yaml
mcp:
  servers:
    - name: figma
      transport: stdio
      command: "npx @figma/mcp-server"
    - name: notion
      transport: http
      url: "http://localhost:7000/mcp"
      auth_token_env: NOTION_MCP_TOKEN
    - name: postgres
      transport: stdio
      command: "uvx postgres-mcp"
```

For each configured server:
- Lamark connects at startup; lists its tools.
- Each external tool is registered into `ToolRegistry` with prefix `mcp:<server>:<tool>`.
- Tool invocations route through `lamark-mcp::client`.
- Tools support filtering / rate limiting per server.

OAuth 2.1 flow supported for servers that require it; tokens cached in `~/.lamark/mcp-tokens/`.

### MCP tool destructiveness default

When registering tools from an external MCP server, the tool wrapper sets `is_destructive = true` unless the tool's `inputSchema` contains `"x-lamark-destructive": false`. This causes the tool to require `Decision::Prompt` (or an explicit Allow rule in `policy.toml`).

Config escape hatch: `mcp.servers.<name>.trust_all_tools = true` marks all tools from that server as non-destructive without per-tool annotation. This escape hatch should only be used for fully trusted, local MCP servers.

### MCP authentication

Each MCP server entry in `~/.lamark/mcp_servers.toml` may specify auth:

```toml
[servers.my_server]
command = ["npx", "-y", "@my/mcp-server"]
auth_env = "MY_SERVER_API_KEY"   # env var name holding the token
# OR:
auth_file = "~/.lamark/secrets/my_server.token"  # path to a file containing the token
# OR:
auth_keyring_service = "lamark-mcp"              # OS keyring service name; key = server name
```

Resolution order: `auth_env` → `auth_file` → `auth_keyring_service`. If none are specified and the server requires auth, connection fails with a clear error.

The token is passed as a `Bearer` header for HTTP/SSE transports and as the first `initialize` message field for stdio transports. Tokens are NEVER logged or written to trace bundles.

### Server (expose Lamark tools)

```yaml
mcp:
  server:
    enable: true
    transport: stdio                    # or http
    bind: 127.0.0.1:5051
    expose_tools:
      - Read
      - Write
      - Edit
      - Bash
      - Grep
      - MemorySearch
      - SkillView
    expose_skills: true                 # treat skills as MCP "prompts"
```

Run via:
```bash
lamark mcp serve --stdio
lamark mcp serve --port 5051
```

Claude Desktop / Cursor / VS Code / Codex / Windsurf can then add Lamark as an MCP server in their settings. They see Lamark's tool catalog and can call it.

### Capabilities exposed

- **Tools**: configured subset of `lamark-tools` registry.
- **Prompts**: skills exposed as MCP prompts (with description + arguments from frontmatter).
- **Resources**: KB-backed; e.g., "memory/facts/<topic>".
- **Sampling**: optional; if `mcp.server.allow_sampling=true`, external clients can request Lamark to run sub-completions.

### Implementation

`rmcp` (the same crate codex-rs uses). Wrap our `ToolRegistry` to implement the MCP server-side trait. The `MCPProxy` tool used internally for client-side consumption.

### Hooks

- `McpToolCallStarted { server, tool }`
- `McpToolCallCompleted { server, tool, ok, duration_ms }`

### CLI

```
lamark mcp servers list
lamark mcp call <server> <tool> --args '{"key":"value"}'
lamark mcp serve --stdio
lamark mcp serve --port 5051
lamark mcp login <server>           # OAuth flow for capable servers
```

---

## Part C — ACP (Agent Communication Protocol)

ACP is the inter-agent protocol Hermes adopted: a registry + adapter that lets agents discover each other and exchange tasks.

### Why ship it

Two reasons:
1. Lamark instances should be able to call **each other** (e.g., the Coordinator on machine A can dispatch to a Lamark worker on machine B).
2. Other ACP-speaking agents (in the wider ecosystem) should be able to call Lamark — and vice versa.

### Registry

`acp.registry_url` points to an ACP-compatible registry service (could be a local one we ship, could be a remote shared one). Each Lamark with `acp.identity` registers itself:

```yaml
acp:
  registry_url: http://acp-registry.local:6060
  identity:
    name: lamark-aleksei-mbp
    key_path: ~/.lamark/acp/identity.key
    capabilities: [code, research, web]
    project_id: lamark-default
```

On startup, Lamark POSTs `/register { name, public_key, capabilities, endpoint }`. On shutdown, `/unregister`.

### Adapter (be called by other agents)

When the registry advertises Lamark, peers can call its `/acp/v1/task` endpoint:

```
POST /acp/v1/task
{
  "from": "peer-agent-foo",
  "task": { "objective": "...", "context": {...}, "deadline": "..." },
  "auth": { "signature": "..." }
}
```

Lamark spawns a `Session` for the task; results are POSTed back to the peer's callback or polled by the peer.

Authentication: ed25519 signatures by both parties; public keys from the registry.

### Policy enforcement on inbound ACP

When an external agent delegates a task via ACP inbound, the local `policy.toml` is applied:

- Inbound tool calls are evaluated as if they originated locally, but with `source = "acp:<peer_agent_id>"` added to the permission context.
- Policy rules may match on `source_prefix`:
  ```toml
  [allow]
  tool = "read_file"
  source_prefix = "acp:trusted_"
  ```
- If no matching Allow rule exists, `Decision::Prompt`. In headless mode (no TUI), `Decision::Deny` unless a pre-grant list overrides:
  ```toml
  [acp.peers.<peer_id>]
  pre_grant = ["read_file", "search"]
  ```
- `peer_agent_id` is the ACP registry entry verified against the handshake cert.
- KB event `InboundAcpTaskPolicyDenied` is emitted whenever a denial occurs, for auditability.

### ACP version negotiation

The ACP handshake must include version negotiation:

1. Initiating agent sends `{ acp_version: "1.0", capabilities: [...] }` in the initial `Connect` message.
2. Receiving agent responds with `{ acp_version: "1.0", capabilities: [...] }` if compatible, or `{ error: "version_mismatch", supported: ["1.0"] }` if not.
3. If the versions don't match, the connection is rejected cleanly — no session is created, no policy is applied.
4. Lamark v0.1 supports ACP version `"1.0"` only. The `acp_version` field is checked in the `AcpAdapter::connect()` method before any session or fork operations.
5. Future versions must be backward-compatible (additive only) within the same major version. Major version bumps require a new `acp_version` string.

### Caller (call other agents)

```rust
// lamark-acp::client
pub async fn dispatch(&self, target: &str, task: AcpTask) -> Result<AcpResult>;
```

Used by the `Agent` tool (plan/05) when invoking a remote agent instead of a local subagent: `Agent.spawn(spec)` with `spec.target = "acp:peer-name"`.

### CLI

```
lamark acp identity                 # show our identity + capabilities
lamark acp register
lamark acp unregister
lamark acp peers list
lamark acp peers ping <name>
lamark acp call <name> --task '{"objective":"..."}'
```

### Tests

(All three subsystems below.)

- **`tests/gateway/telegram_smoke.rs`** — mock TG bot; round-trip a message via a recorded tape.
- **`tests/gateway/slack_thread_routing.rs`** — two users sharing a thread land in the same Session.
- **`tests/gateway/replace.rs`** — `gateway run --replace` sigterms the prior instance and binds.
- **`tests/mcp/client_consume.rs`** — start a stub MCP server in the test; Lamark calls one of its tools.
- **`tests/mcp/server_expose.rs`** — run `lamark mcp serve --stdio`; an MCP client harness lists tools and invokes one.
- **`tests/acp/registration.rs`** — spin up a stub registry; assert Lamark registers + de-registers.
- **`tests/acp/peer_call.rs`** — two Lamark instances in tests dispatch a task to each other.

## Cutover gate (P6 done)

- ✅ `lamark gateway run --adapters telegram` accepts a real test bot's message and replies through the agent.
- ✅ Slack thread routing: two users in the same thread share one session; their messages are tagged with separate user_ids.
- ✅ Discord adapter: similar smoke test.
- ✅ `lamark mcp serve --stdio` registers in Claude Desktop and exposes ≥ 5 tools that work end-to-end.
- ✅ Lamark consumes one configured external MCP server (e.g., Notion) and the tool is callable via the agent.
- ✅ ACP: two Lamark instances on the same machine discover each other via a stub registry; one calls the other; result returns.
