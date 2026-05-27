# 18 — Remote UI

> **Phase:** P10.
> **One-liner:** Developer SSHes into a headless server or runs Lamark
> on a cloud VM; a remote browser on their laptop connects to the
> agent via a **tonic gRPC stream** (or WebSocket fallback), secured
> with mTLS device certificates — full WebUI parity without exposing
> the agent to the public internet.

---

## North-star contribution

- **Domain quality.** Teams running Lamark on GPU workstations,
  on-prem servers, or cloud VMs get the same rich UI as local users.
  Long-running batch jobs (scenario 14) can be monitored from anywhere.
- **Agent-side self-improvement.** Remote sessions produce identical
  trace bundles; all self-improvement loops are unaffected by network
  topology.
- **Model-side self-improvement.** `source: remote` tag in trace lets
  the trainer identify sessions from headless/server environments.
  Remote task profiles (longer sessions, heavier tool use) are
  valuable specialized training signal.

### Signals produced / consumed

- **Produces:** Trace bundle (`source: remote`); same schema as
  local/WebUI sessions. Remote approval / denial events.
- **Consumes:** tonic gRPC bidirectional stream (primary) or WS
  fallback; mTLS device cert pair; remote agent address from
  `~/.lamark/remotes.toml`.

---

## Idea

Developer runs `lamark agent remote --listen :9090` on the server.
This starts a tonic gRPC server that wraps the session SQ/EQ.
On the laptop, `lamark webui --remote agent@server.example.com:9090`
opens the browser as in scenario 17, but the WS bridge connects to
the remote gRPC stream rather than a local session. mTLS certs
(pinned device cert + CA cert) authenticate both ends.

Fallback: if port 9090 is blocked, the client establishes a WebSocket
tunnel over HTTPS (port 443) via a connect-proxy header.

## Actors

| Actor | Role |
|---|---|
| **`lamark agent remote`** | CLI: starts tonic gRPC server on the server-side; wraps local session SQ/EQ. |
| **tonic gRPC server** | `crates/lamark-remote::server` — bidirectional stream of `Op` (client→server) and `Event` (server→client). |
| **tonic gRPC client** | `crates/lamark-remote::client` — runs on the developer's laptop; consumed by `lamark-webui`. |
| **mTLS layer** | Mutual TLS: server cert + client device cert; pinned CA. Managed by `lamark auth device`. |
| **WS fallback** | `lamark-remote::ws_fallback` — same message framing over WSS when gRPC is blocked. |
| **`lamark webui --remote`** | Extends scenario 17 with remote transport; SPA identical. |
| **`~/.lamark/remotes.toml`** | Named remote config: address, cert path, transport (grpc | wss). |

## Trigger

**Server side:**
```
lamark agent remote --listen :9090 --cert ~/.lamark/certs/server.pem \
  --key ~/.lamark/certs/server.key --ca ~/.lamark/certs/ca.pem
```

**Client side:**
```
lamark webui --remote myserver --open
# where ~/.lamark/remotes.toml defines [remotes.myserver]
```

Or ad-hoc:
```
lamark webui --remote grpcs://server.example.com:9090 --cert ~/.lamark/certs/client.pem --open
```

## Pipeline

1. **Server start.** `lamark agent remote --listen :9090` starts tonic
   server. Loads mTLS certs. Starts or attaches to a local `Session`.
2. **Client connect.** `lamark webui --remote myserver` resolves
   address + cert from `~/.lamark/remotes.toml`. tonic client
   dials with mTLS. If gRPC dial fails (TCP blocked), falls back to WSS.
3. **Mutual auth.** Server verifies client cert against pinned CA;
   client verifies server cert. Connection rejected if either fails.
4. **Session handshake.** Same as scenario 17 step 4, over gRPC stream
   instead of local WS.
5. **Event streaming.** Server-side `Event` fan-out forwards to gRPC
   stream. Client forwards to SPA WS. SPA renders identically to
   scenario 17.
6. **User input.** SPA → local WS → gRPC stream → server SQ.
7. **Permission approval.** Same modal flow as scenario 17; `PermissionResponse` travels gRPC stream → server `permission_bus.resolve`.
8. **Stream disruption.** If gRPC stream drops (network blip), client
   reconnects with last-seen `seq`. Server replays buffered events
   since `seq` (ring buffer, 1000 events max).
9. **Session end.** User closes browser; local WS closes; gRPC stream
   closed; server sends `Op::SessionEnd`; bundle finalized on server.
10. **Trace bundle.** Finalized on server (`manifest.source = "remote"`).
    Developer can pull it with:
    ```
    lamark trace pull --remote myserver --rollout <id> --output ~/pulled/
    ```
    (`copy_out` variant over gRPC; same atomicity contract as scenario 13).

## Layers / crates touched

| Step | Crate / module | Plan |
|---|---|---|
| 1 (server start) | `lamark-remote::server` (new) | plan/13 |
| 2–3 (client + mTLS) | `lamark-remote::client` + `lamark-auth::device` | plan/13 |
| 4–7 (stream + permissions) | `lamark-remote::bridge` (new) | plan/13 |
| 8 (reconnect + replay) | `lamark-remote::replay_buffer` (new) | plan/13 |
| 9 (session end) | `lamark-core` session (unchanged) | plan/05 |
| 10 (trace pull) | `lamark-remote::trace_pull` (new) | plan/13 + plan/05c |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **mTLS cert expired** | Connection rejected; clear error: "client cert expired on <date>. Run: `lamark auth device renew`." |
| **gRPC port blocked (firewall)** | Automatic WSS fallback; if WSS also blocked, fail with message listing both transports. |
| **Network blip (stream drops)** | Client reconnects; server replays from `seq`; SPA shows "reconnecting…" indicator. |
| **Server session OOM** | Session aborts on server; client sees `SessionEnded { reason: budget_exceeded }`; SPA shows error. |
| **Concurrent clients on same session** | First client gets full control; second gets read-only view (events stream, no input). |
| **Trace pull interrupted** | Partial files ignored; pull is retried from scratch (idempotent via manifest hash). |
| **`lamark agent remote` server crash** | Session state is in-memory; trace events already flushed to disk are safe (recorder fsync per turn). |

## Acceptance criteria

- [ ] `lamark agent remote --listen :9090` starts; `lamark webui --remote` connects with mTLS.
- [ ] mTLS rejects connection with wrong / missing client cert.
- [ ] WSS fallback succeeds when gRPC port is blocked (tested with iptables drop).
- [ ] Full WebUI parity: text stream, tool cards, permission modal, cost ticker.
- [ ] gRPC stream reconnect replays missed events (seq-based, up to 1000 buffered).
- [ ] Second concurrent client gets read-only view; first keeps control.
- [ ] `lamark trace pull` retrieves bundle atomically (manifest hash verified).
- [ ] `manifest.source == "remote"` in trace.
- [ ] `lamark auth device renew` regenerates client cert without stopping the server.

## Self-improvement assertions

1. **Transport-neutral trace.** Remote sessions produce training samples
   with identical schema to local sessions. The model never knows it
   was accessed over gRPC.
2. **Long-running remote sessions.** Headless server sessions are
   typically longer (batch jobs, overnight tasks). This enriches the
   training set with long-horizon trajectories, improving the model's
   ability to manage complex multi-step tasks.
3. **Remote pull enriches per-machine adapters.** `source: remote` +
   server metadata (GPU type, OS) can be filtered by the trainer to
   build server-specialized adapters.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Remote transport architecture | plan/13 | _audit_ |
| tonic gRPC server + bidirectional stream | plan/13 | _audit_ |
| mTLS device certificate management | plan/13 + `plan/03` (auth config) | _audit_ |
| `~/.lamark/remotes.toml` config schema | plan/03 | _audit_ |
| WSS fallback transport | plan/13 | _audit_ |
| gRPC ↔ WS bridge in `lamark-webui` | plan/13 + plan/12 | _audit_ |
| Event replay buffer (seq-based reconnect) | (likely **gap G-068**) | _audit_ |
| `lamark trace pull` (remote copy_out) | plan/13 + plan/05c | _audit_ |
| Read-only concurrent client | (likely **gap G-069**) | _audit_ |
| `manifest.source = "remote"` | G-048 | _audit_ |
| `lamark auth device` cert lifecycle | plan/03 | _audit_ |
| Server crash → trace recovery | plan/06 (recorder fsync) | _audit_ |
