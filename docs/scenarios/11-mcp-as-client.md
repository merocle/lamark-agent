# 11 — MCP-as-client — Lamark consumes third-party MCP servers

> **Phase:** P6.
> **One-liner:** User configures Lamark to consume external MCP servers
> (`github`, `postgres`, `puppeteer`). Their tools become first-class
> Lamark tools, prefixed `mcp__<server>__<tool>`; Lamark's policy, hooks,
> and trace recorder wrap every call exactly as they would for a native
> tool.

---

## North-star contribution

- **Domain quality.** MCP's whole point is composability. A Lamark
  install that *can't* consume the standard MCP servers is worse than a
  vanilla IDE. This scenario tests bidirectionality (#10 = server-side,
  #11 = client-side).
- **Agent-side self-improvement.** Skill drafts can emerge from
  convergent third-party tool sequences (e.g., `mcp__github__list_prs →
  mcp__github__get_diff → Edit → mcp__github__post_comment`). Memory
  facts capture API quirks ("this repo's main branch is `master`, not
  `main`").
- **Model-side self-improvement.** Third-party tool calls are traced as
  normal tool calls with `mcp:` namespacing. Trainer ingests them as
  Nemotron-Agentic-v1 samples; the trained adapter learns the *shapes*
  of external APIs (good MCP servers act as a tool-use curriculum).

### Signals produced / consumed

- **Produces:** `McpToolCallStarted/Completed` events; KB memory facts
  about external APIs; skill drafts from convergent third-party
  sequences.
- **Consumes:** MCP server configurations from
  `~/.lamark/mcp_servers.toml`; capability grants (parallel to plugin
  grants).

---

## Idea

User adds `github-mcp` and `postgres-mcp` to `~/.lamark/mcp_servers.toml`
with auth tokens. On `lamark chat`, the MCP client connects to both,
fetches `tools/list`, registers them in Lamark's tool registry with
`mcp__github__*` / `mcp__postgres__*` names. Agent now uses them
naturally. Permission policy treats MCP-server tools as **server-level
grants** (one approval covers all tools from that server), per 00d §13.

## Actors

| Actor | Role |
|---|---|
| **`lamark-mcp::client`** | Connects to each configured server (stdio subprocess or HTTP). Fetches schemas. Wraps tools. |
| **Tool registry** | Adds the wrapped tools with stable namespacing per 00d §13. |
| **MCP servers (external)** | `github-mcp`, `postgres-mcp`, etc. — start as subprocesses or remote endpoints. |
| **Permission engine** | Per-server grant; persisted in `~/.lamark/mcp_servers.toml` `[grants]`. |
| **Trace recorder** | Captures inbound + outbound MCP frames at debug level; the agent-visible call is a normal `ToolCallStarted` linked to `McpToolCallCorrelationAssigned`. |

## Trigger

```
$ lamark mcp call github tools/list           # ad-hoc
$ lamark chat                                  # MCP servers auto-connect on bootstrap
```

## Pipeline

1. **Bootstrap, stage N.** Read `~/.lamark/mcp_servers.toml`. For each entry: spawn subprocess (stdio) or open HTTP connection.
2. **Capability gate.** First call to a server prompts: "Server `github-mcp` wants network=api.github.com + reads from your $GITHUB_TOKEN. Approve? [y/N/once]". Persisted.
3. **`tools/list`** lazily fetched (00d §13). Schema cached per session; refresh on `lamark mcp reload <server>`.
4. **Tool registration.** Each tool added to registry as `mcp__github__list_prs` etc. Tool trait `is_read_only`, `is_destructive` derived from schema metadata; if absent, default to `is_destructive=true` (conservative, requires prompt).
5. **Agent calls a tool.** Lamark's policy engine + hook bus fire as normal. Server-level grant short-circuits the prompt (parallel to plugin grant, G-031).
6. **Dispatch.** MCP client sends `tools/call`; receives result; wraps in normal `ToolResult`.
7. **Trace.** Outer `ToolCallStarted` event; `McpToolCallStarted` with `mcp_call_id`; correlation event `McpToolCallCorrelationAssigned` linking the two; `ToolCallEnded` on completion (`plan/06:316–318`).
8. **Subagent reuse (00d §13).** A subagent spawned by the coordinator inherits the parent's MCP server connections — no re-handshake.

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 1 (bootstrap) | `lamark::runtime`, `lamark-mcp::client` | 02, 09 |
| 2 (capability gate) | `lamark-mcp::permissions` | 09 + scenario 06 (parallel) |
| 3–4 (tools/list, wrapping) | `lamark-mcp::client`, `lamark-tools` | 09 + 00d §13 |
| 5–6 (dispatch + policy) | `lamark-policy`, `lamark-hooks`, `lamark-tools` | 05 + 06 |
| 7 (trace correlation) | `lamark-trace` | 06:316–318 |
| 8 (subagent inherit) | `lamark-coordinator` | 05a + 00d §13 |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **MCP server fails to start** | `MCPServerUnavailable` event; tools delisted; agent told via tool registry. |
| **Schema fetch fails** | Retry with backoff; if persistent, server marked unhealthy; auto-disable per G-030. |
| **Server returns tool result schema mismatch** | Tool call fails with structured error; trace recorder marks `McpResultInvalid`. |
| **Token expired** | Auth failure; user prompted (CLI) or notified (gateway). |
| **Tool name collision across servers** | Resolution: include server name (`mcp__github__foo` vs `mcp__gitlab__foo`). No collision possible. |
| **Server hangs > 30s** | Per-tool timeout (config) → `ToolCallEnded { ok: false, error: Timeout }`. |
| **Coordinator subagent inherits but server crashed** | Subagent sees the tool disappear; structured tool-not-found error. |

## Acceptance criteria

- [ ] `~/.lamark/mcp_servers.toml` registry works (add/remove/enable/disable).
- [ ] `lamark mcp call <server> <tool>` works ad-hoc.
- [ ] On `lamark chat` bootstrap, configured servers connect; failures don't block startup.
- [ ] First use triggers server-level capability prompt; persists.
- [ ] Tool names follow `mcp__<server>__<tool>`.
- [ ] `is_destructive=true` is the conservative default for tools without metadata.
- [ ] Trace contains correlated MCP + tool events.
- [ ] Subagents inherit parent's MCP connections without re-handshake.
- [ ] Tool collision across servers is impossible by construction.

## Self-improvement assertions

1. **MCP servers as a curriculum.** A Lamark adapter trained on traces with many MCP-server tool calls outperforms one trained on local-only tool calls when faced with novel-but-similar tool shapes.
2. **Skill drafts from third-party patterns.** Same convergence detection (G-023) applies across MCP tool sequences.
3. **Memory facts capture API quirks.** Facts written about external APIs persist and are recalled in future MCP-mediated sessions.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| MCP client trait + connection setup | plan/09 §"Part B" | _audit_ |
| `~/.lamark/mcp_servers.toml` schema | plan/03 + plan/09 | _audit_ |
| Server-level (not per-tool) grant | 00d §13 | _audit_ |
| Tool name wrapping `mcp__<server>__<tool>` | 00d §13 | _audit_ |
| Lazy `tools/list` schema fetch + cache | 00d §13 | _audit_ |
| `is_destructive` default when metadata missing | (likely **gap G-046**) | _audit_ |
| MCP trace events + correlation | plan/06:316–318 | _audit_ |
| Auto-disable on N failures (shared with G-030) | scenario 06 | _audit_ |
| Subagent inheriting MCP connections | 00d §13 | _audit_ |
| Ad-hoc `lamark mcp call` | plan/02 | _audit_ |
| Auth token storage | plan/03 + scenario 06 | _audit_ |
| Schema refresh on `reload` | (likely partial) | _audit_ |
| Tool collision impossible by namespacing | 00d §13 | _audit_ |
