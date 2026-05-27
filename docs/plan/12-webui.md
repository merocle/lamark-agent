# 12 — Layer 9: WebUI

> Browser-facing control surface for a local Lamark process. Thin Rust HTTP/WS
> host plus a SPA that consumes the same `Submission`/`Event` enums the TUI
> does. **One protocol, N transports.**

**Crate:** `crates/lamark-webui/` (server) + `webui/` (SPA source, embedded into the server crate at build time).
**Depends on:** `lamark-core`, `lamark-hooks`, `lamark-policy`, `lamark-trace`, `lamark-protocol`.
**Replaces:** nothing — Hermes-Agent has no first-party web UI; Codex has no web UI; Claude Code's terminal UI is reference for interaction patterns only.
**References:**
- `~/.cache/lemark/vendor/codex/codex-rs/protocol/src/protocol.rs:1137` — `EventMsg` we serialize to the browser.
- `plan/05-layer-4-agent-core.md:27` — SQ/EQ contract.
- `plan/09-layer-8-gateway-integrations.md:86` — `axum` REST adapter; webui shares the dependency.

---

## What the WebUI is (and is not)

It **is**:
- A local-first browser control plane. Open `http://127.0.0.1:7878`, see live sessions, send prompts, approve tool calls, browse traces.
- A second subscriber on the existing SQ/EQ broadcast — same enums as the TUI, just serialized as JSON.
- A static SPA bundle embedded into the Rust binary so `lamark webui` is one-process, zero-deploy.

It **is not**:
- A multi-tenant SaaS dashboard. (That would be a different product layered on top of `knowledge-base`.)
- A replacement for the TUI. The TUI stays the primary surface; the WebUI is for users who want richer trace browsing, side-by-side approval panes, and easier sharing of replays.
- A remote control surface. Cross-machine usage is **Remote UI** (`plan/13`).

## Architecture

```
                          host machine
   ┌──────────────────────────────────────────────────────────────┐
   │  lamark webui   (single binary, default 127.0.0.1:7878)      │
   │  ┌────────────────────────────────────────────────────────┐  │
   │  │   axum router                                          │  │
   │  │    GET  /api/sessions                                  │  │
   │  │    POST /api/sessions                                  │  │
   │  │    GET  /api/sessions/:id                              │  │
   │  │    WS   /api/sessions/:id/stream    ← SQ in / EQ out   │  │
   │  │    GET  /api/trace/:rollout_id                         │  │
   │  │    GET  /api/trace/:rollout_id/payloads/:ref           │  │
   │  │    GET  /api/schema                  ← JSON Schema dump│  │
   │  │    GET  /*                            ← embedded SPA   │  │
   │  └─────────┬──────────────────────────────────────────────┘  │
   │            │                                                 │
   │  ┌─────────▼──────────────────────────────────────────────┐  │
   │  │  Session registry  (DashMap<SessionId, Arc<Session>>)  │  │
   │  └─────────┬──────────────────────────────────────────────┘  │
   │            │                                                 │
   │  ┌─────────▼──────────────────────────────────────────────┐  │
   │  │  lamark-core  (turn loop, SQ/EQ broadcast, hooks)      │  │
   │  └────────────────────────────────────────────────────────┘  │
   └──────────────────────────────────────────────────────────────┘

                   ┌─────────────────────────────┐
   browser  ──────►│  SvelteKit SPA              │
                   │  - typed Event / Submission │
                   │    from JSON Schema codegen │
                   │  - WS client + REST client  │
                   │  - virtualized trace replay │
                   └─────────────────────────────┘
```

## Server stack (Rust)

| Concern | Crate | Why |
|---|---|---|
| HTTP | **axum** (already used by gateway REST adapter) | Tower middleware, typed extractors. |
| WS | **axum** WS upgrade + **tokio-tungstenite** under the hood | Same runtime, same cancellation. |
| Static asset embedding | **rust-embed** | One binary; no second deploy artifact. |
| Schema export | **schemars** | Re-derived from the same Rust types as `lamark-trace` uses. |
| TLS | **rustls** via `axum-server` | Off by default; opt-in for LAN exposure. |
| Auth | **tower-http** layers + `lamark-policy` | Bearer token; loopback bypass. |

Bind defaults: `127.0.0.1:7878`, no TLS, no auth (loopback-only). LAN/Tailscale exposure requires `webui.bind = "0.0.0.0:7878"` **and** `webui.auth.bearer_token = "…"` **and** `webui.tls.cert = …` — the server refuses to bind a non-loopback address without both.

### Endpoint contract

```rust
// crates/lamark-webui/src/api.rs

#[derive(Serialize, JsonSchema)]
pub struct SessionSummary { pub id: SessionId, pub rollout_id: RolloutId, /* ... */ }

#[derive(Deserialize, JsonSchema)]
pub struct OpenSessionReq { pub profile: Option<String>, pub seed_prompt: Option<String> }

// WS frame envelope — same enums as lamark-core, just JSON.
#[derive(Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum WsClientFrame {
    Submission(Submission),
    Ping,
}

#[derive(Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum WsServerFrame {
    Event(Event),
    Pong,
    Error { code: String, message: String },
}
```

`Submission` and `Event` come straight from `lamark-core` (no parallel definitions). `schemars` derives a JSON Schema for both; the SPA build step turns that schema into TypeScript types — so a Rust enum rename breaks the frontend build immediately, not at runtime.

### Trace browsing

`/api/trace/:rollout_id` streams the reduced `state.json` + `conversation.jsonl` for that bundle. Payloads (large tool outputs) are not inlined; the client requests them lazily via `/payloads/:ref`. This mirrors how `lamark trace replay` works on disk — the WebUI is just a different reader on the same bundle format.

## SPA stack (TypeScript)

**Recommended: SvelteKit + Vite + TypeScript.** Smaller bundle than React/Next, idiomatic stores map cleanly onto the broadcast stream, fast cold start matters when the binary is local. React is the acceptable fallback if existing contributors lean React.

| Concern | Choice |
|---|---|
| Framework | **SvelteKit** (SPA mode, no SSR — there's no server-rendered story for a local tool) |
| Bundler | **Vite** |
| Styling | **Tailwind CSS** + **shadcn-svelte** for unstyled headless primitives |
| State | Svelte stores; **TanStack Query** for REST cache; a typed event store for the WS stream |
| Types from Rust | `schemars` → `quicktype` (in `scripts/gen-webui-types.sh`) → `webui/src/lib/protocol.ts` |
| Virtualization | **@tanstack/svelte-virtual** for trace event lists |
| Markdown rendering | **marked** + **highlight.js** (CSP-safe build) |
| Icons | **lucide-svelte** |
| Build output | Static `webui/dist/` → `rust-embed` picks it up at compile time |

**Non-goals:**
- No SSR, no edge runtime, no Next.js. Local tool.
- No bespoke design system upfront — Tailwind + shadcn-svelte gets us production-grade without yak-shaving.
- No client-side router beyond `svelte-spa-router`-style hash routes; deep links to `/session/:id/turn/:n` and `/trace/:rollout_id` are the only required URLs.

## Views (v0.1)

1. **Home / session list** — running + recent sessions, started-by, model, last activity, cost. Click → Session view.
2. **Session view** — three panes:
   - **Conversation** (center): message + reasoning + tool-call cards, streaming deltas.
   - **Inspector** (right): selected event's raw JSON, full payload, related hook fires.
   - **Approvals dock** (bottom-right popover): pending `PermissionRequest`s with Allow/Deny/Edit buttons.
3. **Trace browser** — list of bundles in `~/.lamark/traces/`, filterable by date/model/profile. Open → replay view.
4. **Replay view** — scrubbable timeline of events; identical layout to Session view but read-only and time-indexed.
5. **Config (read-only at v0.1)** — surface of `lamark config show`; edits still happen via CLI or `~/.lamark/config.yaml`.
6. **Doctor** — same checks as `lamark doctor`, rendered as a status board.

## Auth and policy

- Loopback bind is auth-free by default. **Any** non-loopback bind requires bearer token + TLS or the server refuses to start (fail-closed).
- Bearer tokens are scoped via `lamark-policy`. Default policy for WebUI sessions matches the CLI default: `Prompt` for dangerous tools, `Allow` for read-only.
- `PermissionRequest` events fan out to **every** connected client on that session; the **first** decision wins. The UI displays a "decided by X 0.4s ago" trail.

## Subcommands

```
lamark webui [--bind 127.0.0.1:7878] [--open]   # start the server; --open launches a browser
lamark webui token                              # mint a bearer token; print + save to ~/.lamark/webui-tokens.yaml
lamark webui status                             # is it running? on what port? who's connected?
```

`lamark webui` is added to the CLI in `plan/02`. Day-1 behavior: `lamark webui --open` boots the server, opens the SPA, and `chat` works end-to-end through the browser using the same agent core the TUI does.

## Build pipeline

```
webui/                          # SPA source
  pnpm-lock.yaml
  package.json                  # scripts: dev, build, gen-types
  src/...
  dist/                         # vite build output (gitignored)

crates/lamark-webui/
  build.rs                      # runs `pnpm --dir ../../webui build` if not present
  src/
    lib.rs
    api.rs
    ws.rs
    static_assets.rs            # rust-embed: dist/**
```

CI builds the SPA first, then `cargo build --release -p lamark-webui` embeds `webui/dist/` into the binary. `cargo build` without a prior SPA build falls back to a stub assets module that serves "run `pnpm -C webui build` first" — keeps Rust-only contributors unblocked.

## Tests

- **Rust:** `axum-test` for HTTP endpoints; `tokio-tungstenite` client for WS round-trip; `schemars` golden snapshot of `/api/schema` (insta) so wire changes are deliberate.
- **TypeScript:** **Vitest** for the protocol decoder + state stores; **Playwright** for two end-to-end smoke tests (open session, send prompt, see streamed reply; open trace, scrub to last event).
- Schema-conformance test in Rust spawns a real `lamark-core` Session, drives it via WS, and asserts the events received match the latest `Event` schema — catches drift between Rust and TS.

## Cutover gate (P9 done — WebUI)

- ✅ `lamark webui --open` boots the server, opens the SPA, lists running sessions.
- ✅ Opening a session and typing a prompt streams reply tokens to the browser.
- ✅ A tool with `Prompt` policy fires a `PermissionRequest` that renders as an approval modal; Allow round-trips to the runtime and the tool runs.
- ✅ Trace replay view renders a yesterday's bundle without errors and without OOM on the largest local bundle.
- ✅ Non-loopback bind without TLS + bearer token refuses to start.
- ✅ `webui/dist/` embeds; the release binary is under 35 MB stripped (Linux/macOS).

## § CSRF protection (G-075)

The WebUI uses a single-use CSRF token to prevent cross-origin WebSocket connections:

1. On server startup, generate a cryptographically random 32-byte token: `csrf_token = rand::thread_rng().fill_bytes(32)`.
2. Embed the token as a `<meta name="lamark-csrf-token" content="<hex>">` tag in the HTML shell served by axum.
3. The SPA reads this meta tag and sends it as the `X-Lamark-CSRF` header on every WebSocket upgrade request.
4. The axum WS upgrade handler rejects any connection where `X-Lamark-CSRF` is missing or doesn't match the in-memory token. Returns HTTP 403.
5. The token is regenerated on every server start. It is never written to disk.
6. Only `127.0.0.1` and `::1` bindings are allowed by default; binding to `0.0.0.0` requires explicit `--bind 0.0.0.0:7070` and emits a startup warning.

## § WebSocket sequence numbers (G-076)

Every `WsServerFrame` carries `seq: u64` (monotonically increasing, starting at 1 per session). The axum WS handler maintains a per-session ring buffer of the last 1000 frames in memory (not persisted). On reconnect:

1. Client sends `{ "kind": "Reconnect", "last_seq": N }` as the first message after WS upgrade.
2. If `N >= min_buffered_seq`: server replays all frames with `seq > N`.
3. If `N < min_buffered_seq` (gap too large): server sends `{ "kind": "ReplayGap", "first_available_seq": M }`. The SPA must reload the full session state from `GET /api/sessions/{id}/snapshot` (returns a condensed current state) then subscribe fresh.
4. Normal new connections (no prior `last_seq`) send `{ "kind": "Subscribe" }` without a `last_seq` field; server starts from `seq = next`.

## § Session finalization on WS close (G-077)

When the WebSocket connection drops:

1. The axum handler sets a `close_timer` for `ws_close_grace_seconds` (default 30, config: `webui.ws_close_grace_seconds`).
2. If the client reconnects within the grace period (G-076), the timer is cancelled.
3. If no reconnect: the handler sends `Op::SessionEnd { reason: ConnectionLost }` to the session's SQ and removes the session from the active map.
4. The turn loop processes `SessionEnd` normally: flushes the recorder, runs the reducer (sync, awaited), marks the bundle.
5. If the server process exits before grace period, the `recover_incomplete_bundles()` startup task detects bundles with `status=in_progress` and marks them `status=aborted`.

## Alternative considered: Tauri

A `tauri` shell embedding the SPA + Rust core directly (no HTTP boundary, native window, tray, deep links) is the natural next step **if** desktop integration matters. We do not ship Tauri at v0.1 because:
- The localhost + browser approach gets us the same UX with one fewer build target.
- Tauri's mobile story is still maturing.
- The WS contract we ship for v0.1 is what Remote UI (`plan/13`) reuses; Tauri would be additive, not replacement.

If we add a Tauri shell later, the SPA is unchanged — only the transport flips from `fetch` + `WebSocket` to `invoke()`.
