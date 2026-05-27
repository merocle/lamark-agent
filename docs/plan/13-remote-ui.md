# 13 — Layer 10: Remote UI

> Cross-machine control of a Lamark instance. Phone, laptop, or another shell
> drives a Lamark process running elsewhere. Same SQ/EQ contract as the TUI
> and WebUI — just over a network transport with auth.

**Crate:** `crates/lamark-remote/` (server + Rust client adapter). Generated client SDKs live under `clients/{swift,kotlin,ts,rust}/` and are NOT in the cargo workspace.
**Depends on:** `lamark-core`, `lamark-policy`, `lamark-trace`, `lamark-protocol`.
**References:**
- `~/.cache/lemark/vendor/codex/codex-rs/app-server/` — codex's daemon process pattern.
- `plan/01-rust-strategy.md:56` — "optional remote-trigger" reserved slot.
- `plan/05-layer-4-agent-core.md:27` — SQ/EQ enums we expose on the wire.
- `plan/12-webui.md` — WS shape we reuse for browser remote clients.

---

## What Remote UI is

"Remote UI" is the network-control surface. Three concrete shapes:

1. **`lamark --remote grpc://host:port chat`** — the existing CLI binary, with `RemoteSession` swapped in for the in-process `Session`. Same TUI, same slash commands.
2. **Mobile/desktop native clients** — generated from the same `.proto`. Lamark ships the schema and one reference Swift + Kotlin client; the full apps live outside this repo.
3. **Browser remote** — the WebUI SPA (`plan/12`) pointed at a remote host instead of localhost, via gRPC-Web or the existing WS endpoint.

Remote UI is **not**:
- The gateway. The gateway (`plan/09`) is messaging-platform adapters (Telegram, Slack, …) where the *user* talks to the agent through Slack. Remote UI is direct agent control by an authenticated user from their own device.
- A multi-tenant cloud. One Remote UI server fronts one local Lamark instance. Multi-instance routing is a separate problem outside v0.1.

## Architecture

```
   user's phone / laptop / another shell
    │
    │   gRPC over HTTP/2 (TLS) — primary
    │   WS over HTTPS         — fallback (browsers, restrictive proxies)
    ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  lamark remote serve   (on the agent host)                  │
   │                                                             │
   │  ┌─────────────────────────────────────────────────────┐    │
   │  │  tonic gRPC server   :7879                          │    │
   │  │   LamarkRemote service                              │    │
   │  └────────────────┬────────────────────────────────────┘    │
   │  ┌────────────────┴────────────────────────────────────┐    │
   │  │  axum WS endpoint    :7879 /ws (same port, h2 ALPN) │    │
   │  └────────────────┬────────────────────────────────────┘    │
   │                   │                                         │
   │  ┌────────────────▼────────────────────────────────────┐    │
   │  │  Auth middleware                                    │    │
   │  │   - mTLS device certs (paired devices)              │    │
   │  │   - Bearer tokens (less-trusted clients)            │    │
   │  │   - Policy scope per credential                     │    │
   │  └────────────────┬────────────────────────────────────┘    │
   │                   │                                         │
   │  ┌────────────────▼────────────────────────────────────┐    │
   │  │  Session registry  (shared with WebUI)              │    │
   │  └────────────────┬────────────────────────────────────┘    │
   │                   │                                         │
   │  ┌────────────────▼────────────────────────────────────┐    │
   │  │  lamark-core                                        │    │
   │  └─────────────────────────────────────────────────────┘    │
   └─────────────────────────────────────────────────────────────┘
```

Same `Session` registry as the WebUI — a single source of truth for live sessions. A user can open a session from their laptop's TUI (via `--remote`), check on it from their phone (native client), and watch the same approval requests fan out to both.

## Wire protocol

**Versioned independently** from internal Rust types. Internal `Event`/`Submission` enums can evolve freely; the proto file changes only by semver process.

`proto/lamark/v1/remote.proto`:

```proto
syntax = "proto3";
package lamark.v1;

service LamarkRemote {
  // Discovery / lifecycle
  rpc Hello(HelloRequest)           returns (HelloResponse);
  rpc ListSessions(ListSessionsReq) returns (SessionList);
  rpc OpenSession(OpenSessionReq)   returns (SessionHandle);
  rpc CloseSession(SessionHandle)   returns (Empty);

  // SQ/EQ — bidirectional streams.
  rpc Submit(stream Submission) returns (Empty);            // client → server
  rpc Subscribe(SessionHandle) returns (stream Event);      // server → client

  // Bulk payload transfer (tool outputs, attachments).
  rpc UploadAttachment(stream Chunk) returns (PayloadRef);
  rpc DownloadPayload(PayloadRef)    returns (stream Chunk);

  // Trace browsing (read-only)
  rpc ListBundles(ListBundlesReq)    returns (BundleList);
  rpc GetBundle(BundleId)             returns (stream Event);
}
```

`Submission` and `Event` are mirrored from `lamark-core` via a tiny adapter in `lamark-remote/src/convert.rs`. We do **not** `prost`-derive directly off the core enums — that would couple the wire to internals. Mapping is explicit so a Rust enum rename surfaces in code review.

### Why not WS-only?

- **Mobile SDKs.** `grpc-swift` and `grpc-kotlin` give us strongly-typed, cancellable, streaming clients with one codegen step. WS clients are hand-written per platform.
- **Bidirectional streaming.** gRPC's `stream → stream` matches SQ/EQ exactly. WS requires us to invent framing.
- **Wire size.** Protobuf beats JSON for thousands of `AgentMessageDelta` frames.

We keep the WS endpoint because (a) browsers don't speak h2-gRPC natively without `grpc-web` plus a translation layer, and (b) corporate proxies sometimes block h2. WS is the safety net.

## Auth and pairing

Two credential types, both fail-closed:

### Device certs (mTLS) — for trusted devices

Used by the user's own laptop / phone / desktop. Pairing flow:

```
$ lamark remote pair
Pairing code: 7Q-3F-9N-Z2     (valid 120s)
QR shown on stdout.
```

Client side (CLI):
```
$ lamark remote enroll host=10.0.0.5 code=7Q-3F-9N-Z2
✓ Issued device cert "alex-laptop". Saved to ~/.lamark/remote/clients/alex-laptop.{crt,key}
```

A Lamark-internal CA (one cert in `~/.lamark/remote/ca.{crt,key}`, generated on first `lamark remote init`) signs device certs. The server pins the CA; no public PKI involved. Device certs carry a scope identifier (`subject = CN=alex-laptop,OU=full-access`); policy uses that scope.

### Bearer tokens — for less-trusted clients

Browser remote, scripts, third-party tools.

```
$ lamark remote token mint --scope read-only --ttl 7d
Token: lmrk_…  (save it; not shown again)
```

Tokens are random 32 bytes, hashed-stored, prefixed `lmrk_`. The scope maps to a `lamark-policy` profile. The web frontend can be paired by scanning a QR that encodes `(host, port, ca_fingerprint, token)`.

### Policy integration

Every credential resolves to a `RemoteScope`:

```rust
pub struct RemoteScope {
    pub credential_id: String,
    pub policy_profile: String,   // e.g. "full", "read-only", "approve-only"
    pub default_tool_decision: Decision,
    pub allowed_tools: Option<HashSet<String>>,
    pub forbidden_tools: HashSet<String>,
    pub session_quota: Quota,
}
```

`PermissionRequest` events are routed to the credential that initiated the session. If the initiator's connection is dead and `policy.fallback_approvers` is non-empty, the request fans out to those credentials with a hold time before auto-deny.

## Discovery

- **LAN:** mDNS service `_lamark-remote._tcp`. The mobile client scans, finds the host, asks for a pairing code shown on the host's terminal.
- **Tailscale / WireGuard:** transparent — the host is just an IP from the client's view; mDNS is optional.
- **Public Internet:** out of scope at v0.1. The config block reserves `remote.tunnel = { provider: "cloudflared" | "ngrok", … }` but Lamark itself does not host a relay. Users opt in deliberately.

## Subcommands

```
lamark remote init                          # one-time: generate CA, server cert, default config
lamark remote serve [--bind 0.0.0.0:7879]   # start the gRPC + WS server
lamark remote pair                          # show pairing code + QR; valid 120s
lamark remote enroll host=H code=C          # client side: redeem code, save device cert
lamark remote token mint --scope S --ttl D  # mint bearer
lamark remote token list / revoke ID
lamark remote clients list / revoke ID      # device-cert management
lamark remote status                        # who is connected, last heartbeat, scope
```

The existing CLI grows a `--remote URL` global flag (`plan/02`). With it set, every subcommand that needs a `Session` routes through the remote adapter instead of building one locally. `lamark --remote grpc://host:7879 chat` is the smoke test.

## Client SDKs

We ship the wire schema; we ship one reference client per language. Full apps live outside this repo.

| Language | Location | Generator | Status v0.1 |
|---|---|---|---|
| Rust   | `clients/rust/` (separate crate, not in workspace) | `tonic-build` | First-class. `RemoteSession` adapter used by `lamark --remote`. |
| TypeScript | `clients/ts/` (npm package) | `@bufbuild/protoc-gen-es` + `connect-es` | Used by the WebUI when `?remote=grpc://…`. |
| Swift  | `clients/swift/` | `grpc-swift` | Reference stub + README; full iOS app is downstream. |
| Kotlin | `clients/kotlin/` | `grpc-kotlin` | Reference stub + README; full Android app is downstream. |

`buf` (or a hand-rolled `scripts/gen-clients.sh`) runs in CI on tagged releases and publishes the SDKs.

## Versioning

- `lamark.v1` is frozen by v0.1 ship.
- Breaking changes → `lamark.v2` proto and a new `LamarkRemote` service running in parallel for one release.
- Non-breaking field additions: protobuf field numbers reserved; new fields default-optional.
- The Rust `Submission`/`Event` enums on internals can break freely; only the converter in `lamark-remote/src/convert.rs` cares.

## Threat model

| Threat | Mitigation |
|---|---|
| Cred theft from a paired device | `lamark remote clients revoke ID` immediately invalidates the cert; CRL checked on every connection. |
| Token leak in shell history | Tokens are hashed at rest; revoke command exists; default TTL 7d. |
| MITM on LAN | mTLS — server pins CA fingerprint shared at pair time. |
| Privilege escalation via policy bypass | `lamark-policy` evaluates with `RemoteScope` in context; tests assert no scope can exceed its `allowed_tools`. |
| DoS on the server | Tower middleware: rate limit per credential; max concurrent sessions per credential; max open streams. |
| Replay of pairing code | Codes are one-shot, 120s TTL, server-side mutex on redemption. |

`docs/security/remote.md` is the long-form version of this table.

## Tests

- **Rust:** `tonic`'s in-memory transport for unit tests of every RPC; `tokio-tungstenite` for the WS fallback path.
- **Integration:** `scripts/e2e/remote.sh` spawns `lamark remote serve` on a random port, enrolls a device, runs `lamark --remote chat "echo hi"`, asserts the trace bundle on the server side matches what the client saw.
- **Schema:** `buf breaking` runs in CI against the previous tag — breaks the build if `lamark.v1` is mutated.
- **Auth:** property tests that no credential can submit an `Op` outside its `RemoteScope`.

## Cutover gate (P10 done — Remote UI)

- ✅ `lamark remote init` + `lamark remote serve` on host A; `lamark --remote grpc://A:7879 chat` from host B round-trips a prompt.
- ✅ `lamark remote pair` flow works end-to-end; revoked device certs are refused on the next connection.
- ✅ Bearer tokens scope correctly: a `read-only` token cannot trigger a tool whose policy is `Allow` for `full`.
- ✅ WS fallback works for a browser client behind a strict HTTP proxy.
- ✅ `buf breaking` is green; protobuf field numbers are committed.
- ✅ One Swift and one Kotlin reference client compile against the published proto.

## § Event replay buffer (G-078)

The gRPC server maintains a per-session in-memory ring buffer of the last 1000 `Event` messages, each tagged with a monotonically increasing `seq: u64`. On stream reconnect:

1. Client sends a `ResumeRequest { session_id, last_received_seq: N }` on the `Subscribe` stream.
2. Server replays all buffered events with `seq > N`.
3. If `N < min_buffered_seq`, server sends a `ReplayGap { first_available_seq: M }` message. The client must re-fetch the session snapshot via `GetSessionSnapshot` RPC and subscribe fresh.
4. Reconnect uses exponential backoff: 1s, 2s, 4s, 8s, cap 30s. After 10 failures, the client emits a local `ConnectionLost` event and the SPA shows a "connection lost" banner.
5. The ring buffer is in-memory per session; it is not persisted across server restarts. A server restart = full reconnect required.

The following messages must be added to `proto/lamark/v1/remote.proto`:

```proto
message ResumeRequest {
  string session_id = 1;
  uint64 last_received_seq = 2;
}
message ReplayGap {
  uint64 first_available_seq = 1;
}
message SessionSnapshotRequest {
  string session_id = 1;
}
```

The `Subscribe` RPC signature changes from `rpc Subscribe(SessionHandle) returns (stream Event)` to accept a `ResumeRequest` instead of a plain `SessionHandle`, and `GetSessionSnapshot` is added to the `LamarkRemote` service.

## § Concurrent clients and read-only mode (G-079)

A single remote session allows at most one **control client** (full bidirectional control) and unlimited **observer clients** (read-only event stream):

- The first client to connect with `{ "role": "control" }` in the handshake becomes the control client.
- Subsequent clients connecting with `{ "role": "control" }` receive `SessionAlreadyControlled { control_client_id }` and are downgraded to observer.
- Observer clients receive all `Event` messages in the stream but their `Submit` RPCs are rejected with `Status::PERMISSION_DENIED`.
- `PermissionRequired` events are forwarded to all clients but only the control client's response is accepted.
- When the control client disconnects, the session enters the `ws_close_grace_seconds` countdown (G-077 logic, adapted for gRPC). After the grace period, the next client that reconnects with `{ "role": "control" }` becomes the new control client.
- Config: `remote.allow_observers = true` (default); `remote.max_observers = 10` (default).

## What Remote UI deliberately leaves out at v0.1

- Public-Internet tunneling. (Reserved config slot; user-driven.)
- Federation between Lamark instances. (Different problem; ACP — `plan/09` Part C — is the agent-to-agent surface.)
- Full mobile apps. We ship clients, not finished apps.
- Multi-user authorization beyond per-credential scopes. (No groups, no orgs at v0.1.)
