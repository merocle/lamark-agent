# Audit — scenario 06: Plugin host loads WASM + dylib plugin

**Verdict:** 🟡 Yellow-to-green. Coverage is strong on binary loading, manifest, capability prompts, and trace events (14 of 16 plan references are substantive). Two **policy** questions remain — auto-disable threshold for unhealthy plugins, and granted-capability scope. Architecture is sound; what's missing is decision docs.

---

## 1. Plan coverage matrix (16 rows)

| Concern | Plan | Verdict | Evidence |
|---|---|---|---|
| WASM vs dylib distinction + config flags | plan/08:198–210 | ✅ | `allow_wasm: true`, `allow_dylib: false` (opt-in); locked in 00c §3:173 |
| Plugin manifest schema | plan/08:212–235 | ✅ | `[plugin]` + `[capabilities]` + `[hooks]` sections specified |
| Capability gating + UX | plan/08:239–247 | ✅ | Bootstrap discovery (no load) → first-use prompt → persist to `granted.toml` → revoke via CLI |
| Plugin trait surface | plan/08:251–264 | ✅ | `init / shutdown / tools / skills / gateway_adapters / memory_providers / hooks` |
| WIT + host imports + tokio bridge | plan/08:269–274 | ✅ | Per-session `Store`; `wasi-snapshot-preview2`; async via `wasmtime-wasi-poll` |
| Dylib FFI + opt-in warning | plan/08:276–280 | ✅ | `lamark_plugin_create()`; loaded at startup; crash warning |
| CLI commands | plan/08:297–306 | ✅ | `list / install / remove / enable / disable / revoke / grants / reload` |
| Trace events | plan/08:309–313 + plan/06 | ✅ | `PluginLoaded / CallStarted / Completed / Failed`; recorder durable to crash |
| Per-tool 30s timeout | plan/08:319 | ✅ | Default 30s; timeout → `PluginCallFailed` |
| Bundled-wins slot collision | 00c §3:173 | ✅ | First registration wins; dedup key clear |
| Claude-Code plugin manifest record | 00d §22 | ✅ | `{name, version, skills, hooks, mcpServers, isAvailable, defaultEnabled}` |
| Plugin SDK hides WIT | plan/08:267 | ⚠️ | Claimed but `lamark-plugin-sdk` crate not yet read |
| Plugin-emitted skills load via skill index | plan/08 Part A + Part C | ⚠️ | Plumbing implied; not explicitly stated |
| Capability revocation mid-session | Scenario | ⚠️ | `granted.toml` is authoritative; in-session cache invalidation unclear |
| **Plugin auto-disable threshold** | — | ❌ | plan/08:322 mentions 15 min quarantine; threshold + owner unspecified (**G-030**) |
| **Plugin tool registry naming** | plan/05 (tool registry namespace) | ⚠️ | Scenario asserts `plugin:<plugin>:<tool>`; plan/05 namespace policy unread; collision risk (**G-032**) |

---

## 2. Self-improvement assertions

| # | Assertion | Verdict | Evidence |
|---|---|---|---|
| 1 | Plugin usage → Curator surfaces unused plugins | ⚠️ | Curator base in plan/08; **temporal-aggregation logic** (rolling 30d) unspecified |
| 2 | Skill drafts from convergent plugin sequences | ⚠️ | Blocked on G-023 (convergence-detection metric) |
| 3 | Plugin tools are SFT-ready | ✅ | Trace events exist; reducer processes; naming convention stated (needs G-032 to pin namespace) |

---

## 3. Gaps surfaced

### G-030. Plugin auto-disable threshold + quarantine policy — **blocker**
- **Owner:** `plan/08` Curator + `plan/03` config schema.
- **Resolution:** Specify `plugins.health.consecutive_failures` (default 3), `plugins.health.quarantine_minutes` (default 15), scope (per-plugin global vs per-session), Curator-suggestion integration, operator notification path. ADR-0031.

### G-031. Granted-capability scope (global / per-project / per-session) — **blocker**
- **Owner:** `plan/03` config + `plan/08` capability gating.
- **Resolution:** Pick one: (a) global `~/.lamark/plugins/granted.toml`; (b) per-project override at `.lamark/plugins/grants.toml`; (c) per-session ephemeral grants. Recommend (a) for v0.1 with hook for (b) in v0.2. Specify merge semantics if (b) adopted. ADR-0032.

### G-032. Plugin-tool-naming collision prevention — **deferred**
- **Owner:** `plan/05` tool registry.
- **Resolution:** Reserve `plugin:*` prefix in the tool registry; reject registration of user-authored tools matching that pattern. Document in plan/05 §"Tool naming". ADR-0033.

---

## 4. Cross-references to prior gaps

- **G-023** (deferred, scenario 04) — Convergence-detection. Blocks self-improvement assertion #2.
- **G-020** (blocker, scenario 04) — Tree-level signal aggregation. Indirect: multi-agent coordinators must decide whether plugin health is per-agent or global.
- **G-009** (deferred, scenario 02) — Policy-rule-proposal subsystem. Curator-style suggestions for plugins follow similar shape; one-way coupling today.

## 5. Open-question resolutions

| # | Question | Resolution |
|---|---|---|
| 1 | Capability matching granularity (exact / glob / prefix) | Assume **exact** for v0.1; document in ADR-0032. |
| 2 | Auto-disable threshold | → G-030. plan/08:322 hints 3-fail / 15 min but owner unclear. |
| 3 | WASM HTTP imports — `wasi-http` vs custom | Deferred. Likely Wasmtime `wasi-http`. |
| 4 | Per-session vs per-process WASM `Store` | **Answered:** per-session (plan/08:274). Memory cost: 10 plugins × 10 sessions × ~50 MB ≈ 5 GB worst-case. v0.2 may pool. |
| 5 | Granted-capability scope | → G-031. |

## 6. Critical insight

The plugin host is **architecturally sound** — sandboxing (WASM default, dylib opt-in), capability gating, trace quality, and management CLI are all well-specified. The two blockers are **policy questions, not architecture questions**: they require decision docs + config-schema updates, not redesign. The largest **runtime footgun** is per-session WASM `Store` memory at scale (5 GB worst-case for 10 plugins × 10 concurrent sessions); v0.1 is fine, but Store pooling will become necessary as soon as the gateway scenario (#09) brings many concurrent sessions. Recommend shipping v0.1 with global per-plugin capability grants and per-session isolation; revisit if a real multi-project user emerges who needs finer-grained scoping.

## 7. Recommended ADRs

| ADR | Title | Severity |
|---|---|---|
| ADR-0031 | Plugin health + auto-disable policy | **Blocker** (G-030) |
| ADR-0032 | Granted-capability scope (global vs project vs session) | **Blocker** (G-031) |
| ADR-0033 | Tool-namespace isolation (`plugin:` prefix reserved) | Deferred (G-032) |
| ADR-0034 | WASM Store pooling strategy (v0.2 if memory pressure) | Deferred |
