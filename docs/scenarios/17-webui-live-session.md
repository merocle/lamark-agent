# 17 — WebUI live session

> **Phase:** P9.
> **One-liner:** User opens `http://localhost:7070` in a browser
> (launched by `lamark webui --open`); a SvelteKit SPA connects via
> WebSocket to a local axum server; the agent session streams turn
> events in real time — tool calls, permission modals, trace tail,
> cost ticker — and the user approves or denies without leaving the
> browser.

---

## North-star contribution

- **Domain quality.** Power users who dislike TUI (or who work on a
  tablet) get the same first-class session experience in a browser,
  including full trace inspection and approval modals.
- **Agent-side self-improvement.** WebUI sessions produce identical
  trace bundles; Curator, Reflexion, and memory extraction run without
  modification. Session surface does not affect training signal quality.
- **Model-side self-improvement.** `source: webui` tag in trace lets
  the trainer distinguish browser sessions. Different session surfaces
  may produce different user behavior; the adapter can specialize.

### Signals produced / consumed

- **Produces:** Trace bundle identical in schema to TUI/CLI sessions;
  `source: webui` in manifest; any user approval / denial events as
  `PermissionResolved` trace events.
- **Consumes:** Live `Event` stream from the agent session (SQ/EQ
  protocol); `GET /sessions/{id}/events` SSE or WebSocket endpoint.

---

## Idea

`lamark webui --open` starts an axum HTTP/WS server on `127.0.0.1:7070`
and opens the default browser. The SvelteKit SPA connects immediately.
A new session is created (or an existing session resumed) — the same
`Session` that would exist in TUI mode. Events stream over WebSocket;
the frontend renders them as a chat timeline with expandable tool-call
cards, cost ticker, and permission modals that block forward progress
until the user approves.

## Actors

| Actor | Role |
|---|---|
| **`lamark webui`** | CLI subcommand (`plan/02`). Starts axum server; opens browser. |
| **axum HTTP server** | `crates/lamark-webui::server` — HTTP + WebSocket; serves the compiled SvelteKit SPA as static assets. |
| **WebSocket handler** | Bridges browser ↔ agent `Session`'s SQ/EQ channels. |
| **SvelteKit SPA** | `frontend/` — bundled into `crates/lamark-webui/assets/`; served as static files. |
| **Session** | Same `lamark-core` session, unchanged. |
| **Permission modal** | Browser-side: blocks user interaction until `Allow / Deny / Allow-session` choice. |
| **Cost ticker** | Live `TokensConsumed` events render running cost in status bar. |
| **Trace inspector** | Expandable tool-call cards showing full args + result; scrollable session log. |

## Trigger

```
lamark webui --open
# or resume an existing session:
lamark webui --session <rollout_id> --open
```

## Pipeline

1. **Start server.** axum binds `127.0.0.1:7070`. Generates a
   single-use CSRF token; embeds it in the HTML shell so only the
   locally-served SPA can connect to the WebSocket.
2. **Open browser.** `open` (macOS) / `xdg-open` (Linux) /
   `start` (Windows) points to `http://localhost:7070/`.
3. **SPA loads.** SvelteKit app hydrates; reads embedded CSRF token;
   establishes WebSocket to `ws://localhost:7070/ws`.
4. **Session handshake.** WS message: `{ kind: "SessionAttach",
   rollout_id: <id> | null }`. Server creates or resumes a `Session`.
   WS reply: `{ kind: "SessionStarted", rollout_id, base_model, ... }`.
5. **Event streaming.** Every `Event` emitted on the session's EQ is
   forwarded to the WS as JSON. Frontend renders:
   - `TurnStarted` / `TurnEnded` — chat bubble boundaries.
   - `TextDelta` — streamed assistant text.
   - `ToolCallStarted` / `ToolCallEnded` — collapsible tool-call card
     (args, result, duration, cost).
   - `TokensConsumed` — cost ticker update.
   - `PermissionRequired` — modal appears (blocks new user input).
   - `PermissionResolved` — modal closes.
6. **User input.** User types in the input box; sends `{ kind: "UserInput", text }` over WS. Server enqueues `Op::UserInput` on the session SQ.
7. **Permission approval.** When `PermissionRequired` arrives, modal
   shows tool name, args summary, risk level. User clicks
   `Allow / Allow-session / Deny`. WS sends `{ kind:
   "PermissionResponse", decision, scope }`. Server calls
   `permission_bus.resolve(...)`.
8. **Tool-call inspection.** Each tool-call card is expandable; shows
   full args JSON, result or error, timing, token cost. Scroll back in
   history.
9. **Session end.** User sends `/exit` or closes browser tab. WS close
   → server sends `Op::SessionEnd` to session. Bundle finalized normally.
10. **Trace bundle.** Identical to TUI-mode output, with
    `manifest.source = "webui"`. Reducer runs on the host as normal.

## Layers / crates touched

| Step | Crate / module | Plan |
|---|---|---|
| 1–2 (server + browser) | `lamark-webui::server` (new) | plan/12 |
| 3–4 (SPA + handshake) | `frontend/` (SvelteKit; new) | plan/12 |
| 5 (event streaming) | `lamark-webui::ws_bridge` (new) | plan/12 |
| 6 (user input) | `lamark-core` session SQ (unchanged) | plan/05 |
| 7 (permission) | `lamark-core` permission bus (unchanged) | plan/05 |
| 8 (trace inspector) | SvelteKit frontend (new) | plan/12 |
| 9–10 (session end + bundle) | `lamark-core` + `lamark-trace` (unchanged) | plan/05 + plan/06 |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Port 7070 in use** | Try 7071..7079; if all busy, fail with clear message. |
| **Browser WS connection drops mid-session** | Session continues on server; WS reconnect replays events since last `seq`; no data loss. |
| **User closes browser without ending session** | WS close → 30s grace; if no reconnect, `Op::SessionEnd` sent; bundle finalized. |
| **Permission modal times out** | Configurable `permission_modal_timeout` (default 5 min); timeout = Deny. |
| **CSRF token mismatch** | WS connection refused; error shown in browser; no session created. |
| **Session not found (`--session <id>`)** | Clear HTTP 404; WebUI shows "Session not found" message. |
| **Agent produces large tool output** | Truncated in modal (first 4 KB); full result available via "Show full" expand. |

## Acceptance criteria

- [ ] `lamark webui --open` starts server and opens browser within 2s.
- [ ] SPA loads; WebSocket connects; `SessionStarted` event visible in browser devtools.
- [ ] Assistant text streams in real time (text delta by delta, not buffered).
- [ ] Tool-call card renders args + result; collapsible.
- [ ] Permission modal appears for `Prompt`-class tools; approval / denial propagates to agent.
- [ ] Cost ticker updates on every `TokensConsumed` event.
- [ ] Browser tab close → session ends cleanly; trace bundle finalized.
- [ ] WebSocket reconnect replays missed events (seq-based).
- [ ] Trace bundle `manifest.source == "webui"`.
- [ ] CSRF protection: WS connection from a different origin rejected.

## Self-improvement assertions

1. **Surface-neutral trace.** WebUI sessions produce training samples
   indistinguishable in schema from TUI/CLI sessions. The agent learns
   from what the user does, not which surface they use.
2. **Permission signal in training.** Browser approval/denial events are
   `PermissionResolved` trace events — same as TUI. DPO pairs can be
   constructed from browser sessions.
3. **Cost awareness.** Users who see the cost ticker make different
   choices; those choices are in the trace. The model learns
   cost-consciousness from the training data.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| `lamark webui` CLI subcommand | plan/02 + plan/12 | _audit_ |
| axum HTTP + WS server | plan/12 | _audit_ |
| SvelteKit SPA | plan/12 | _audit_ |
| WS event protocol (SQ/EQ bridge) | plan/12 + plan/05 | _audit_ |
| Permission modal via browser | G-045 (resolved for webui?) | _audit_ |
| CSRF protection (localhost single-use token) | (likely **gap G-065**) | _audit_ |
| WS reconnect with seq-based replay | (likely **gap G-066**) | _audit_ |
| Session resume via `--session <id>` | plan/12 | _audit_ |
| `manifest.source = "webui"` tagging | G-048 | _audit_ |
| Cost ticker (TokensConsumed events) | plan/06 + plan/12 | _audit_ |
| Trace inspector (full args/result in card) | plan/12 | _audit_ |
| Bundle finalization on WS close | (likely **gap G-067**) | _audit_ |
