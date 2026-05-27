# Batch B audit — scenarios 08–12

Consolidated audit for multi-project isolation, Telegram gateway, MCP server, MCP-as-client, and ACP registry. 15 new gaps (G-040..G-054); 10 blockers.

---

## Scenario 08 — Multi-project isolation

**Verdict:** 🟡 Yellow — strategically sound, but four operational details haven't migrated from scenario doc into plan files.

### Coverage matrix
- ✅ `kb_project_id` in manifest (plan/06:275)
- ✅ Multi-project isolation as KB capability (SPEC §2.4)
- ✅ Project skills at `./.lamark/skills/` (plan/08:62–68)
- ✅ Project config override (plan/03:16–21)
- ❌ **Project-scoped trace directory** (plan/06:243 shows flat; scenario asserts per-project) — **G-040**
- ❌ **`project_id` detection function** (cwd hash vs pinned name) — **G-041**
- ❌ **`MemoryQuery.project_filter` field** (plan/07a:64–71 doesn't include it) — **G-042**
- ⚠️ Curator scope (per-project vs global) — ambiguous
- ❌ **`lamark project list / rename / migrate` CLI** — **G-043**
- ⚠️ Cross-project search UX — mentioned, not detailed
- ⚠️ Symlink canonicalization — not specified

### Self-improvement assertions
- Recall stays in-project — **at risk** (G-042)
- Skill promotion across projects — feasible (extends G-023)
- Per-project adapters — feasible
- No bleed under concurrency — likely OK
- Migration safety — **untested** (G-043)

### Gaps (G-040..G-044)
- **G-040 [blocker]** — Trace directory layout: flat vs per-project namespace.
- **G-041 [blocker]** — Project-id detection function spec.
- **G-042 [blocker]** — `MemoryQuery.project_filter` field.
- **G-043 [blocker]** — `lamark project list / rename / migrate` CLI.
- **G-044 [deferred]** — Symlink canonicalization rule.

### Critical insight
Multi-project isolation is architecturally sound; KB already supports it (SPEC §2.4). The four blockers are not conceptual — they're details that must move from this scenario doc into `plan/03`, `plan/06`, `plan/07a`, and `plan/02`. G-040 is highest urgency because the trace path is set at bootstrap and must be stable for resume.

### ADRs
- ADR-0042 Multi-project trace isolation
- ADR-0043 Project detection and naming
- ADR-0044 Memory scoping by project (`project_filter`)
- ADR-0045 `lamark project` CLI surface

---

## Scenario 09 — Telegram gateway

**Verdict:** 🟡 Yellow — messaging mechanics solid, permission UX without TUI is unresolved.

### Coverage matrix
- ✅ `lamark gateway run` (plan/02 + plan/09)
- ✅ Adapter trait + facade (plan/09 §"Adapter trait")
- ✅ Session router (`conversation_key`) (plan/09)
- ✅ Two-guard model (00c §6)
- ✅ PlatformEntry fields (00c §5)
- ✅ Voice-memo path (00c §5–§10)
- ✅ Session resume + idle TTL
- ✅ `UserProfile` memory kind (plan/07a:54)
- ❌ **Permission prompts via gateway** — **G-045**
- ✅ Pagination + sequence markers (00c §6)
- ✅ `GatewayMessageIn/Out` events (plan/06:329–330)
- ⚠️ Webhook vs long-poll — capability present, choice config-driven
- ❌ **Project scoping for messaging (no cwd)** — **G-046**

### Gaps
- **G-045 [blocker]** — Permission UX without TUI: inline prompt + reply / auto-deny / pre-grant model. Affects scenarios 09, 10, 12.
- **G-046 [blocker]** — Telegram project scoping (no cwd). Per-conversation config or per-adapter fallback.

### Critical insight
Telegram messaging mechanics are well-specified (adapter, routing, streaming, two-guard). The blocker is **permission UX**: when a tool requires approval and there's no TUI, what happens? Telegram doesn't support modal dialogs. Lock one of: (a) inline prompt + reply, (b) auto-deny, (c) pre-grant required. This affects scenarios 09, 10, and 12.

### ADRs
- ADR-0046 Gateway permission UX without TUI
- ADR-0047 Gateway project scoping

---

## Scenario 10 — MCP server

**Verdict:** 🟡 Yellow — protocol solid; permission UX, streaming, source tagging deferred.

### Coverage matrix
- ✅ `lamark mcp serve --stdio / --port` (plan/02 + plan/09)
- ✅ MCP server protocol (rmcp; JSON-RPC; stdio + HTTP)
- ✅ Tool wrap `mcp__<server>__<tool>` + `_meta` (00d §13)
- ✅ Server-level permission rule (00d §13)
- ✅ MCP trace correlation (plan/06:316–318)
- ⚠️ Plugin grant interaction (depends on G-031)
- ❌ **Permission UX without TUI** — same as G-045
- ❌ **Long-running tool / partial result protocol** — **G-047**
- ✅ `structured_content` passthrough (00d §13)
- ⚠️ Server-side cache invalidation on plugin disable
- ✅ Lazy `tools/list` (00d §13)
- ✅ Parent server reuse by subagents (00d §13)
- ✅ Cross-mode memory consistency
- ❌ **`source: mcp:<server-id>` in trace** — **G-048**

### Gaps
- **G-045 [blocker]** — Same as scenario 09.
- **G-047 [deferred]** — Long-running MCP tool / partial result protocol.
- **G-048 [blocker]** — Source field in trace manifest (also affects 11 and 12).

### Critical insight
MCP server is architecturally sound; the gaps are operational: (1) permission UX when there's no TUI (G-045), (2) long-running tools and streaming results (G-047), (3) source tagging so the trainer can filter by surface (G-048). None are show-stoppers; all need decisions before coding starts.

### ADRs
- ADR-0048 Long-running MCP tool streaming
- ADR-0049 Source tagging in traces (manifest + per-event)

---

## Scenario 11 — MCP-as-client

**Verdict:** 🟡 Yellow — client protocol sound; destructiveness default + auth storage + schema refresh need pins.

### Coverage matrix
- ✅ MCP client trait + connection setup (plan/09)
- ✅ `~/.lamark/mcp_servers.toml` schema (plan/03 + plan/09)
- ✅ Server-level grant (00d §13)
- ✅ Tool name wrapping (00d §13)
- ✅ Lazy `tools/list` + cache (00d §13)
- ❌ **`is_destructive` default when metadata missing** — **G-049**
- ✅ MCP trace events + correlation
- ⚠️ Auto-disable on N failures (depends on G-030)
- ✅ Subagent inheriting MCP connections (00d §13)
- ✅ Ad-hoc `lamark mcp call`
- ❌ **Auth token storage** — **G-050**
- ⚠️ Schema refresh on `lamark mcp reload <server>` — **G-051**
- ✅ Tool collision impossible by namespacing (00d §13)

### Gaps
- **G-049 [blocker]** — `is_destructive=true` default for tools without metadata.
- **G-050 [blocker]** — Auth token storage (env / file / keyring).
- **G-051 [deferred]** — Schema refresh algorithm on `mcp reload`.

### Critical insight
Well-designed surface; closes cleanly with three small decisions. (1) Conservative `is_destructive=true` default; (2) tokens via env-var-name per server (parallel to model auth); (3) `mcp reload` clears cache + re-fetches `tools/list`.

### ADRs
- ADR-0050 Default destructiveness for external tools
- ADR-0051 MCP auth token management
- ADR-0052 Schema refresh and cache invalidation

---

## Scenario 12 — ACP registry

**Verdict:** 🔴 Red — architecture sound, but four critical details unspecified.

### Coverage matrix
- ✅ ACP registry (plan/09 + 00c §9)
- ✅ `lamark acp register / list / unregister` CLI (plan/02)
- ✅ ACP adapter outbound (plan/09 + 00c §9)
- ✅ ACP server inbound (plan/09 + 00c §9)
- ✅ `SessionMode` Owned/Delegated/Forked (00c §9)
- ✅ `fork_session` semantics (00c §9)
- ✅ Per-peer permission grants
- ✅ `ProtocolEventObserved` carries ACP events (plan/06:343)
- ⚠️ **Cross-agent `interaction_edges`** — extends plan/05a:262 — **G-052**
- ❌ **Local policy enforced for inbound tasks** — **G-053**
- ❌ **ACP version negotiation** — **G-054**
- ✅ Memory tagging by ACP source
- ⚠️ Routing-quality memory facts (depends on G-014)

### Gaps
- **G-052 [deferred]** — Cross-agent edges in reducer (`DelegationEdge` / `RemoteAgentEdge` variant).
- **G-053 [blocker]** — Local policy applied to inbound ACP tasks (which rules apply?).
- **G-054 [blocker if ACP is v0.1 scope]** — ACP protocol version negotiation.

### Critical insight
ACP is the most ambitious batch B scenario. Architecture is sound (adapter + server + fork semantics), but four critical details are unspecified: version negotiation, policy on inbound tasks, routing-fact authoring (depends on G-014), and cross-agent edges. **If ACP is in v0.1 scope, all four are blockers.** If P6+, three can defer.

### ADRs
- ADR-0053 ACP protocol version negotiation
- ADR-0054 Policy application to inbound ACP tasks
- ADR-0055 Routing-quality memory facts (extends G-014)
- ADR-0056 Cross-agent interaction edges (extends plan/05a)

---

## Summary

| Gap | Severity | Spans |
|---|---|---|
| G-040 Trace dir per-project | blocker | 08 |
| G-041 project_id detection | blocker | 08 |
| G-042 MemoryQuery.project_filter | blocker | 08 |
| G-043 `lamark project` CLI | blocker | 08 |
| G-044 Symlink canonicalization | deferred | 08 |
| G-045 Permission UX without TUI | blocker | 09 / 10 / 12 |
| G-046 Telegram project scoping | blocker | 09 |
| G-047 Long-running MCP / partial result | deferred | 10 |
| G-048 Source field in trace | blocker | 10 / 11 / 12 |
| G-049 `is_destructive` default | blocker | 11 |
| G-050 MCP auth token storage | blocker | 11 |
| G-051 MCP schema refresh on reload | deferred | 11 |
| G-052 Cross-agent edges | deferred | 12 |
| G-053 Policy on inbound ACP | blocker | 12 |
| G-054 ACP version negotiation | blocker (if v0.1) | 12 |

**10 blockers + 5 deferred.** Cross-scenario blockers: G-045 (3 scenarios), G-048 (3 scenarios).
