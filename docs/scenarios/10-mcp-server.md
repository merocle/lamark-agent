# 10 — MCP server — Claude Desktop / Cursor uses Lamark tools

> **Phase:** P6.
> **One-liner:** Engineer is in Cursor / Claude Desktop; configures Lamark
> as an MCP server; Lamark's tools (`Read`, `Bash`, plugin tools, even
> `kanban_*`) appear as native tools in the host IDE; tool calls flow
> through Lamark's permission / hook / trace pipeline; the host LLM
> benefits from Lamark's memory + skills without knowing they exist.

---

## North-star contribution

- **Domain quality.** Lamark plays well with the engineer's existing
  IDE. No need to abandon Claude Code / Cursor — Lamark *extends* them.
- **Agent-side self-improvement.** Every tool call from the host LLM
  becomes another trace bundle for Lamark's training data. Lamark is
  *learning from the user's existing assistant's work*. Memory facts
  written in MCP-mediated sessions are usable in native Lamark
  sessions and vice versa.
- **Model-side self-improvement.** MCP-mediated traces have a different
  prompt prefix (host LLM's prompt, not Lamark's), but tool calls +
  results round-trip the reducer cleanly. Trainer gets *diverse*
  trajectories — same tool surface invoked by different LLMs.

### Signals produced / consumed

- **Produces:** trace bundles tagged `source: mcp:<server-id>`;
  `McpToolCallStarted/Completed` events (`plan/06` event variants).
- **Consumes:** MCP protocol from the host (JSON-RPC over stdio or HTTP).

---

## Idea

Engineer configures Cursor: `~/.cursor/mcp.json` adds Lamark as a server.
Cursor starts Lamark via `lamark mcp serve --stdio`. On first message, host
LLM enumerates tools via `tools/list`; Lamark returns Read/Bash/plus a
selection of plugin tools (subject to grants). User asks Cursor "find the
TODO comments in src/". Host LLM calls Lamark's `Grep` tool; Lamark runs it
through *its own* permission policy + hook bus; result returns; the
conversation continues with the host LLM. **Lamark traces the call** — the
host LLM is now a downstream consumer in Lamark's pipeline.

## Actors

| Actor | Role |
|---|---|
| **Host IDE / LLM** | Cursor, Claude Desktop, Windsurf, VS Code, Codex. Consumes MCP. |
| **Lamark MCP server** | `crates/lamark-mcp/server.rs` — implements MCP protocol over stdio or HTTP. |
| **Tool registry adapter** | Maps host tool calls to Lamark `Tool` trait invocations; namespaces tools as `mcp__lamark__<tool>` per 00d §13. |
| **Permission gate** | Same as scenario 02. The host LLM does *not* bypass Lamark's policy. |
| **Trace recorder** | Records `McpToolCallStarted/Completed` plus the inner `ToolCallStarted/Ended` (`plan/06` lines 316–318). |
| **Memory + skills** | Available transparently. Host LLM never sees them; Lamark's tool implementations recall them as needed. |

## Trigger

```
$ lamark mcp serve --stdio
```
or, host-side, `~/.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "lamark": { "command": "lamark", "args": ["mcp", "serve", "--stdio"] }
  }
}
```

## Pipeline

1. **Cursor spawns Lamark process.** Stdio MCP. Lamark sends `initialize` response with capabilities.
2. **Host requests `tools/list`.** Lamark returns its tool registry — but only **server-level approved** tools per 00d §13 (MCP permissions are per-server, not per-tool). Plugin tools requiring grants appear if the plugin is enabled + granted; otherwise they're hidden.
3. **Tool wrap.** Lamark wraps each tool's metadata: `name: mcp__lamark__Grep`, `description`, JSON schema. `_meta` passes through (00d §13).
4. **Host calls `tools/call name=mcp__lamark__Grep`.** Lamark receives, **enters its own tool dispatch** — policy check, hook bus, PreToolUse, run, PostToolUse, trace.
5. **Permission flow.** If the call requires user approval, Lamark **must** still prompt — but the prompt UX has nowhere to land (no Lamark TUI in this mode). Options: (a) send a system notification + block until reply via a side channel; (b) auto-deny on no-UI; (c) require pre-grant via `lamark mcp grant`. Plan/00d §29 implies (c). (Gap — see below.)
6. **Result returned via MCP.** `structured_content` and `_meta` passthrough preserved.
7. **Trace events** include `McpToolCallStarted { mcp_call_id }`, then the normal `ToolCallStarted`, with `McpToolCallCorrelationAssigned` linking them (`plan/06:318`).
8. **Memory + skills + KB writes happen as side effects.** The host LLM doesn't know, but the user benefits — next Lamark CLI session sees the facts.

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 1–3 (MCP server, tools/list) | `lamark-mcp::server` | 09 §"MCP" |
| 4 (tool dispatch) | `lamark-tools`, `lamark-policy`, `lamark-hooks` | 05 + 06 |
| 5 (permission UX without TUI) | `lamark-mcp::permissions` | 09 + (gap G-045) |
| 6 (result passthrough) | `lamark-mcp::server` + 00d §13 | 09 + 00d §13 |
| 7 (trace correlation) | `lamark-trace` | 06:316–318 |
| 8 (memory/skills side effects) | `lamark-memory`, `lamark-skills` | 07a + 08 |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Host doesn't speak MCP version** | Server returns capability mismatch; host shows error to user. |
| **Tool requires permission, no UI** | Per pre-grant model: tool reports `PermissionRequired` to host; user runs `lamark mcp grant <tool>` and retries. |
| **Long-running tool exceeds host timeout** | Server signals `partial result`; host either accepts streaming or errors. |
| **Plugin disabled mid-session** | Tool delisted on next `tools/list`; host caches list briefly (host-dependent). |
| **Server crashes** | Host restarts the subprocess; new MCP session; old trace bundle sealed with `status=aborted`. |
| **Concurrent host calls** | Tool dispatch is per-call; trace recorder serializes via session lock. |
| **`structured_content` not preserved** | Bug — covered by integration test against a reference host. |

## Acceptance criteria

- [ ] `lamark mcp serve --stdio` boots and answers `initialize`.
- [ ] `tools/list` returns the expected tool set (filtered by grants).
- [ ] Tool names follow `mcp__lamark__<tool>` (00d §13).
- [ ] A tool call from the host flows through Lamark's policy + hooks; trace events present.
- [ ] `McpToolCallCorrelationAssigned` links MCP and inner tool calls.
- [ ] Permission-required tools either pre-grant successfully or report `PermissionRequired` cleanly.
- [ ] Plugin tools require corresponding plugin grant (G-031).
- [ ] Memory facts written in MCP mode are recallable in subsequent Lamark CLI sessions.

## Self-improvement assertions

1. **Diverse training data.** Trainer can filter `source=mcp:*` to get trajectories from non-Lamark LLMs invoking Lamark's tools.
2. **Cross-mode memory.** Same project: a Lamark CLI session and a Cursor-via-MCP session share KB memory; facts written in one surface in the other.
3. **Permission discipline preserved.** Host LLM cannot bypass Lamark policy by going through MCP.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| `lamark mcp serve --stdio / --port` | plan/02 + plan/09 | _audit_ |
| MCP server protocol (JSON-RPC; stdio + HTTP) | plan/09 §"Part B — MCP" | _audit_ |
| Tool wrap: `mcp__<server>__<tool>` + `_meta` passthrough | 00d §13 | _audit_ |
| Server-level (not per-tool) MCP permission rule | 00d §13 | _audit_ |
| MCP trace events + correlation | plan/06:316–318 | _audit_ |
| Plugin grant interaction (G-031) | plan/08 + scenario 06 | _audit_ |
| **Permission UX without TUI (pre-grant model)** | (likely **gap G-045**) | _audit_ |
| Long-running tool / partial result | plan/09 — likely partial | _audit_ |
| `structured_content` passthrough | 00d §13 | _audit_ |
| Server-side cache invalidation on plugin disable | plan/09 — likely partial | _audit_ |
| Lazy schema fetch via `tools/list` | 00d §13 | _audit_ |
| Parent server reuse by subagents | 00d §13 | _audit_ |
| Cross-mode memory consistency | plan/07a | _audit_ |
| Source tagging `mcp:<server-id>` in manifest | plan/06 | _audit_ |
