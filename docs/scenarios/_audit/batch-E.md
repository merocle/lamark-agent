# Batch E audit — scenarios 17–18

Consolidated audit for WebUI live session and remote UI. 6 new gaps (G-075..G-080); 5 blockers.

---

## Scenario 17 — WebUI live session

**Verdict:** 🟡 Yellow — server stack and event protocol are well-specified; CSRF, WS reconnect, and session finalization on close are missing.

### Coverage matrix

| Concern | Status | Evidence |
|---|---|---|
| `lamark webui` CLI subcommand | ✅ | plan/02:154–158, 215 — `lamark webui [--bind H:P] [--open]` + `WebuiArgs` variant |
| axum HTTP + WS server | ✅ | plan/12:65–103 — server stack, endpoint contract, WS frame envelope fully specified |
| SvelteKit SPA | ✅ | plan/12:112–127 — SvelteKit + Vite + TypeScript, dependencies, build pipeline |
| WS event protocol (SQ/EQ bridge) | ✅ | plan/12:78–106 — `WsClientFrame` / `WsServerFrame` mirroring `Event`/`Submission` |
| Permission modal via browser | ⚠️ | plan/05:295–317 covers core approval flow; modal rendering in plan/12 defers specifics to G-045 (ADR-0046) — not fully locked |
| CSRF protection (localhost single-use token) | ❌ | Not in plan/12, plan/02, or plan/05. **G-075** |
| WS reconnect with seq-based replay | ❌ | plan/12 describes WS server but no seq-number protocol or reconnect replay algorithm. **G-076** |
| Session resume via `--session <id>` | ✅ | plan/02:47 — thin layer on existing session lookup |
| `manifest.source = "webui"` tagging | ⚠️ | G-048 (blocker, already tracked) — plan/06 schema not locked |
| Cost ticker (`TokensConsumed` events) | ⚠️ | plan/12:133 mentions cost ticker view; plan/05:66 defines `TokensConsumed`; full ticker→training path depends on G-007 (deferred) |
| Trace inspector (full args/result in card) | ✅ | plan/12:108–110 — `/api/trace/:rollout_id` + lazy payload fetch |
| Bundle finalization on WS close | ❌ | plan/05 + plan/06 do not specify WS-close → `Op::SessionEnd` → bundle finalization contract. **G-077** |

### New gaps (G-075..G-077)

- **G-075 [blocker]** — CSRF token generation and validation for localhost WebUI — `plan/12` — ADR-TBD
- **G-076 [blocker]** — WebSocket sequence-number protocol: seq generation, ring-buffer replay on reconnect — `plan/12` + `plan/05` — ADR-TBD
- **G-077 [blocker]** — WS connection close → `Op::SessionEnd` → bundle finalization contract — `plan/05` + `plan/06` — ADR-TBD

### Self-improvement assertions check

1. **Surface-neutral trace** — At risk: depends on G-048 (manifest.source schema not locked).
2. **Permission signal in training** — At risk: tied to G-045 + G-048; both unresolved.
3. **Cost awareness** — At risk: `TokensConsumed` → training signal path not specified; G-007 deferred.

### ADRs
- ADR-0065 CSRF protection for localhost WebUI
- ADR-0066 WebSocket seq-number + reconnect replay protocol
- ADR-0067 WS lifecycle ↔ session lifecycle (close → finalization)

---

## Scenario 18 — Remote UI

**Verdict:** 🟡 Yellow — gRPC transport, mTLS, and trace pull are well-specified; replay buffer algorithm, read-only client policy, and remotes.toml schema are missing.

### Coverage matrix

| Concern | Status | Evidence |
|---|---|---|
| Remote transport architecture | ✅ | plan/13:30–63 — gRPC + WS architecture diagram |
| tonic gRPC server + bidirectional stream | ✅ | plan/13:68–96 — `LamarkRemote` service, `Submit`/`Subscribe` streams, wire proto schema |
| mTLS device certificate management | ✅ | plan/13:108–130 — CA setup, device cert pairing, storage, revocation |
| `~/.lamark/remotes.toml` config schema | ⚠️ | plan/13:57 mentions file but schema (address, cert path, transport selection) not defined in plan/03 or plan/13. **G-080** |
| WSS fallback transport | ✅ | plan/13:44–46, 100–106 — WS endpoint on same port, fallback rationale |
| gRPC ↔ WS bridge in `lamark-webui` | ✅ | plan/13:23 + plan/12 — SPA unchanged; transport swaps at Rust layer |
| Event replay buffer (seq-based reconnect) | ❌ | plan/13:95 mentions "ring buffer, 1000 events max" but no algorithm: seq generation, replay protocol. **G-078** |
| `lamark trace pull` (remote copy_out) | ✅ | plan/13:100–104 + plan/05c — `copy_out` atomicity via manifest hash |
| Read-only concurrent client | ❌ | plan/13:156 mentions second client gets read-only view but no policy enforcement or allowed-event-set spec. **G-079** |
| `manifest.source = "remote"` | ⚠️ | G-048 (already tracked) |
| `lamark auth device` cert lifecycle | ✅ | plan/13:112–130 — pairing, enrollment, renewal |
| Server crash → trace recovery | ✅ | plan/13:127 + plan/06 recorder fsync per turn |

### New gaps (G-078..G-080)

- **G-078 [blocker]** — Remote event replay buffer: seq-number generation, ring-buffer algorithm, reconnect protocol spec — `plan/13` — ADR-TBD
- **G-079 [blocker]** — Read-only concurrent client: policy enforcement + allowed event set — `plan/13` + `plan/05` — ADR-TBD
- **G-080 [deferred]** — `~/.lamark/remotes.toml` schema: address, cert path, transport selection — `plan/03` — ADR-TBD

### Self-improvement assertions check

1. **Transport-neutral trace** — At risk: depends on G-048.
2. **Long-running remote sessions** — At risk: depends on G-048 for `source: remote` tagging.
3. **Remote pull enriches per-machine adapters** — At risk: no plan section specifies server metadata schema (GPU type, OS). Potential additional gap if targeted for v0.1.

### ADRs
- ADR-0068 Remote event replay buffer (seq + ring buffer)
- ADR-0069 Read-only concurrent remote client
- ADR-0070 `remotes.toml` schema

---

## Cross-scenario notes

- **G-048** (manifest.source tagging) blocks both scenarios' self-improvement claims. Already a blocker in `_gaps.md`.
- **G-045** (permission UX without TUI) affects scenario 17 modal rendering. Already tracked.
- **G-076** (WS local reconnect) and **G-078** (gRPC remote reconnect) are siblings — same seq-based replay need over different transports. A unified ADR with transport-specific sub-sections is recommended.
- **G-077** is a previously unidentified gap: WS lifecycle ↔ session lifecycle on browser close has no spec in plan/05 or plan/06.

---

## Summary

| Gap | Severity | Spans |
|---|---|---|
| G-075 CSRF protection for WebUI | blocker | 17 |
| G-076 WS seq + reconnect replay (local) | blocker | 17 |
| G-077 WS close → session finalization | blocker | 17 |
| G-078 Remote replay buffer (gRPC) | blocker | 18 |
| G-079 Read-only concurrent client | blocker | 18 |
| G-080 `remotes.toml` schema | deferred | 18 |

**5 blockers + 1 deferred.**
