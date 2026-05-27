# Cross-scenario gap log

Rolling list of gaps surfaced by scenario audits. Each gap is owned by one
`plan/` file; severity is **blocker** (v0.1 can't ship without it) or
**deferred** (post-v0.1).

Format: `[severity] G-NNN — title — owner file — surfaced by — proposed ADR`

## Blockers

- **[blocker] G-001 — Edit-tool atomicity contract (old_string uniqueness + mtime staleness)** — `plan/05` — scenario 01 — ADR-003
- **[blocker] G-002 — Session permission-state retention (refined as G-011)** — `plan/05` or `plan/06` — scenario 01 — ADR-004 / -0011
- **[blocker] G-003 — Reducer determinism guarantee + algorithm spec** — `plan/06` — scenario 01 — ADR-005
- **[blocker] G-004 — `reinforce_signal` authorship in manifest + DPO-pair schema** — `plan/06` (+ `plan/05`) — scenario 01 — **split into G-008 + G-012**
- **[blocker] G-008 — DPO pair authoring in reducer (project rejected trajectory without executing)** — `plan/06` reducer + `plan/10` — scenario 02 — ADR-0009
- **[blocker] G-010 — On-denial memory-write integration point + intent extraction** — `plan/07a` + `plan/05` turn loop — scenario 02 — ADR-0012
- **[blocker] G-012 — DPO rejected-trajectory schema specifics (fields, JSON Schema)** — `plan/10` — scenario 02 — ADR-0009 (companion to G-008)
- **[blocker] G-014 — Memory candidate authoring step (heuristic-only, deterministic)** — new `lamark-memory::extract` + section in `plan/07a` — scenario 03 — ADR-0014
- **[blocker] G-020 — Tree-level `reinforce_signal` aggregation (AND-of-critical-path + user-veto)** — `plan/05a` + `plan/07a` — scenario 04 — ADR-0020
- **[blocker] G-021 — Counterfactual DPO for orchestration (spawn-N, cancel, re-plan)** — `plan/06` reducer + `plan/10` — scenario 04 — ADR-0021 (companion to ADR-0009 / G-008+G-012)
- **[blocker] G-027 — Skill-draft filesystem path + lifecycle (`~/.lamark/skills/drafts/`)** — `plan/07a` + `plan/08` — scenario 05 — ADR-0027
- **[blocker] G-028 — Embedding service for Curator consolidation (KB / local / aux model)** — `plan/08` + `plan/07a` — scenario 05 — ADR-0028
- **[blocker] G-030 — Plugin auto-disable threshold + quarantine policy** — `plan/08` Curator + `plan/03` — scenario 06 — ADR-0031
- **[blocker] G-031 — Granted-capability scope (global vs project vs session)** — `plan/03` + `plan/08` — scenario 06 — ADR-0032
- **[blocker] G-033 — `section_id` per-turn logging in trace events** — `plan/06` recorder — scenario 07 — ADR-0035
- **[blocker] G-034 — A/B traffic-split runner crate** — new `crates/lamark-prompt::ab` — scenario 07 — ADR-0036
- **[blocker] G-035 — Adapter-rollout serialization (48h quiet hours)** — `plan/10` + `plan/07b §C5` — scenario 07 — ADR-0037
- **[blocker] Architectural — Curator vs Dream consolidator boundary** — `plan/08` + `plan/00d §32` — scenario 05 — ADR-0030
- **[blocker] G-040 — Trace directory layout: flat vs per-project namespace** — `plan/06` — scenario 08 — ADR-0042
- **[blocker] G-041 — Project-id detection function (cwd hash vs pinned name)** — `plan/03` + `plan/02` — scenario 08 — ADR-0043
- **[blocker] G-042 — `MemoryQuery.project_filter` field** — `plan/07a` — scenario 08 — ADR-0044
- **[blocker] G-043 — `lamark project list / rename / migrate` CLI** — `plan/02` — scenario 08 — ADR-0045
- **[blocker] G-045 — Permission UX without TUI (gateway / MCP / ACP)** — `plan/05` + `plan/09` — scenarios 09 / 10 / 12 — ADR-0046
- **[blocker] G-046 — Telegram project scoping (no cwd)** — `plan/03` + `plan/09` — scenario 09 — ADR-0047
- **[blocker] G-048 — Source field in trace manifest (`mcp:` / `gateway:` / `acp:`)** — `plan/06` — scenarios 10 / 11 / 12 — ADR-0049
- **[blocker] G-049 — `is_destructive=true` default for MCP tools w/o metadata** — `plan/05` + `plan/09` — scenario 11 — ADR-0050
- **[blocker] G-050 — MCP auth token storage (env / file / keyring)** — `plan/03` + `plan/09` — scenario 11 — ADR-0051
- **[blocker] G-053 — Local policy applied to inbound ACP tasks** — `plan/05` + `plan/09` — scenario 12 — ADR-0054
- **[blocker if ACP in v0.1] G-054 — ACP protocol version negotiation** — `plan/09` + 00c §9 — scenario 12 — ADR-0053
- **[blocker] G-055 — `copy_out` atomicity: manifest hash verification + partial-transfer recovery** — `plan/05c` — scenario 13 — ADR-0057
- **[blocker] G-056 — Stream reconnect protocol: seqno-based bridge resynchronization spec** — `plan/05c` — scenario 13 — ADR-0058
- **[blocker] G-058 — Image registry credentials: imagePullSecrets injection / credential helper** — `plan/05c` — scenario 13 — ADR-0059
- **[blocker] G-060 — `crates/lamark-batch` crate structure not in any plan file** — new `plan/` section or SPEC update — scenario 14 — ADR-0060
- **[blocker] G-061 — Provider rate limiter (`lamark-providers::rate_limiter`) unspecified** — `plan/04` — scenario 14 — ADR-0061
- **[blocker] G-062 — Bulk KB upload endpoint + sequential idempotent fallback** — `plan/07a` — scenario 14 — ADR-0062
- **[blocker] G-063 — Resume support (`--resume <dir>`): per-bundle idempotency token** — new `lamark-batch` plan section — scenario 14 — ADR-0063
- **[blocker] G-065 — Curator event trigger on `AdapterPromoted`** — `plan/08` + `plan/10` — scenarios 15/16 — ADR needed
- **[blocker] G-066 — Blend config file path, TOML schema, anchor-bump logic and normalization** — `plan/10` — scenarios 15/16 — ADR needed
- **[blocker] G-067 — KB event schemas for `AdapterSuspended` and `ForgettingWarn`** — `plan/10` — scenario 16 — ADR needed
- **[blocker] G-068 — Reflexion lesson authoring triggered by rollback events** — `plan/07b` + `plan/10` — scenarios 15/16 — ADR needed
- **[blocker] G-069 — Rollback deferral during active sessions: drain, soft-unload, deadline timer** — `plan/10` + `plan/05c` — scenario 16 — ADR needed
- **[blocker] G-075 — CSRF token generation and validation for localhost WebUI** — `plan/12` — scenario 17 — ADR-0065
- **[blocker] G-076 — WebSocket seq-number protocol + reconnect ring-buffer replay (local)** — `plan/12` + `plan/05` — scenario 17 — ADR-0066
- **[blocker] G-077 — WS connection close → `Op::SessionEnd` → bundle finalization contract** — `plan/05` + `plan/06` — scenario 17 — ADR-0067
- **[blocker] G-078 — Remote event replay buffer: seq generation + ring-buffer algorithm (gRPC)** — `plan/13` — scenario 18 — ADR-0068
- **[blocker] G-079 — Read-only concurrent remote client: policy enforcement + allowed event set** — `plan/13` + `plan/05` — scenario 18 — ADR-0069

## Deferred

- **[deferred] G-005 — vLLM cache strategy: first-turn vs second-turn expectations** — `plan/04` or `plan/07` — scenario 01 — ADR-007
- **[deferred] G-006 — Context-file walk-up precedence (LAMARK.md / AGENTS.md / CLAUDE.md)** — `plan/07` — scenario 01 — ADR-008
- **[deferred] G-007 — Cost tracker per-turn persistence + session resume** — `plan/02` or `plan/04` — scenario 01 — ADR-009
- **[deferred] G-009 — Policy-rule-proposal subsystem (counter, threshold, write-back to policy.toml)** — new `crates/lamark-policy/proposals.rs` — scenario 02 — ADR-0010 (becomes blocker if closed-loop policy is v0.1 scope)
- **[deferred] G-011 — Session permission cache data structure + scope variants** — `plan/05` — scenario 02 (refines G-002) — ADR-0011
- **[deferred] G-013 — `permission_prompt_timeout` config key + timeout-as-deny** — `plan/03` config schema — scenario 02 — ADR-0013
- **[deferred] G-015 — 4xx-quarantine workflow on KB schema mismatch** — `plan/06` §"Knowledge-base upload" — scenario 03 — ADR-0015
- **[deferred] G-016 — Memory confidence threshold + drafts directory** — `plan/03` + `plan/07a` — scenario 03 — ADR-0016
- **[deferred] G-017 — Concurrent sessions in same project (dedup keys + reinforce aggregation)** — `plan/07a` — scenario 03 — ADR-0017
- **[deferred] G-018 — Disk-full lossy ring buffer (v0.2)** — `plan/06` recorder — scenario 03 — ADR-0018
- **[deferred] G-019 — Tier-3 cache invalidation policy when memory block changes mid-session** — `plan/07` §"Cache" — scenario 03 — ADR-0019
- **[deferred] G-022 — Aggregate-statistic memory kind (`#stat:` in v0.1, `Statistic` variant v0.2)** — `plan/07a` `MemoryKind` enum — scenario 04 — ADR-0022
- **[deferred] G-023 — Skill convergence-detection metric (counter-only in v0.1)** — `plan/08` Curator — scenario 04 — ADR-0023
- **[deferred] G-024 — Cascading-cancel timing SLA (`coordinator.cancel_deadline_seconds`)** — `plan/05a` config + `plan/05c` interrupt budget — scenario 04 — ADR-0024
- **[deferred] G-025 — Kanban board durability on coordinator crash (local file mirror)** — `plan/05a` board — scenario 04 — ADR-0025
- **[deferred] G-026 — Per-subagent tool-proxy mode defaults by role** — `plan/05c` + `plan/05d` configs — scenario 04 — ADR-0026
- **[deferred] G-029 — Auto-rollback policy on regressed skill versions** — `plan/08` Curator decision table — scenario 05 — ADR-0029
- **[deferred] G-032 — Plugin-tool-naming collision prevention (`plugin:*` prefix reserved)** — `plan/05` tool registry — scenario 06 — ADR-0033
- **[deferred] G-036 — Prompt-section lineage in KB** — `plan/10` KB client + KB API — scenario 07 — ADR-0038
- **[deferred] G-037 — Constitutional rules definition (`~/.lamark/CONSTITUTION.yaml`)** — `plan/07b` + `plan/10` — scenario 07 — ADR-0039
- **[deferred] G-038 — Reflexion-lesson expiration policy (decay + TTL)** — `plan/07a` + `plan/07b` — scenario 07 — ADR-0040
- **[deferred] G-039 — Section version promotion atomicity (2PC)** — `plan/07` + `plan/10` — scenario 07 — ADR-0041
- **[deferred] G-044 — Symlink canonicalization rule** — `plan/03` — scenario 08
- **[deferred] G-047 — Long-running MCP tool / partial result streaming** — `plan/09` — scenario 10 — ADR-0048
- **[deferred] G-051 — MCP schema refresh algorithm on `reload`** — `plan/09` — scenario 11 — ADR-0052
- **[deferred] G-052 — Cross-agent interaction edges (`DelegationEdge`)** — `plan/05a` + `plan/06` reducer — scenario 12 — ADR-0056
- **[deferred] G-057 — K8s fallback policy (provisioning failure → DockerSandbox)** — `plan/05c` — scenario 13
- **[deferred] G-059 — Pod teardown idempotency on coordinator crash (cleanup-on-start)** — `plan/05c` — scenario 13
- **[deferred] G-064 — Batch output directory layout spec + `run_report.json`** — new `lamark-batch` plan section — scenario 14 — ADR-0063
- **[deferred] G-080 — `~/.lamark/remotes.toml` schema: address, cert path, transport selection** — `plan/03` — scenario 18 — ADR-0070

## Resolved by plan doc updates (2026-05-25)

All gaps below were filled by adding spec sections to the relevant plan files. ADRs may still be written separately, but the implementation-blocking ambiguity is removed.

| Gap | Resolved in |
|---|---|
| G-001 Edit-tool atomicity | plan/05 §Edit-tool atomicity |
| G-003 Reducer determinism | plan/06 §Reducer determinism |
| G-008 DPO pair authoring | plan/06 §DPO pair authoring |
| G-010 On-denial memory write | plan/07a §On-denial memory write |
| G-012 DPO rejected-trajectory schema | plan/10 §DPO pair schema |
| G-014 Memory candidate authoring | plan/07a §Memory candidate extraction |
| G-020 Tree-level reinforce aggregation | plan/05a §Coordinator reinforce signal aggregation |
| G-021 Counterfactual DPO orchestration | plan/05a §Counterfactual DPO for orchestration decisions |
| G-027 Skill-draft filesystem path | plan/08 §Skill draft lifecycle |
| G-028 Embedding service for Curator | plan/08 §Embedding service |
| G-030 Plugin auto-disable + quarantine | plan/08 §Plugin auto-disable and quarantine |
| G-031 Granted-capability scope | plan/03 §Capability grants and scope + plan/08 §Plugin capability scope enforcement |
| G-033 `section_id` per-turn logging | plan/06 §Prompt section IDs in trace |
| G-034 A/B traffic-split runner | plan/07b §A/B traffic-split runner |
| G-035 Adapter-rollout serialization | plan/07b §Adapter-rollout quiet window |
| G-040 Trace directory layout | plan/06 §Trace directory layout |
| G-041 Project-id detection function | plan/03 §Project-id detection |
| G-042 `MemoryQuery.project_filter` | plan/07a §MemoryQuery — project_filter field |
| G-043 `lamark project` CLI | plan/02 §Project management CLI |
| G-045 Permission UX without TUI | plan/05 §Permission UX in headless surfaces + plan/09 §Gateway project scoping |
| G-046 Telegram project scoping | plan/09 §Gateway project scoping |
| G-048 Source field in trace | plan/06 §Trace manifest source field |
| G-049 `is_destructive` default | plan/05 §MCP tool destructiveness default + plan/09 §MCP tool destructiveness default |
| G-050 MCP auth token storage | plan/09 §MCP authentication |
| G-053 Policy on inbound ACP | plan/05 §Policy enforcement on inbound ACP tasks + plan/09 §Policy enforcement on inbound ACP |
| G-054 ACP version negotiation | plan/09 §ACP version negotiation |
| G-055 `copy_out` atomicity | plan/05c §copy_out atomicity |
| G-056 Stream reconnect seqno | plan/05c §Event stream reconnect |
| G-058 Image registry credentials | plan/05c §Image registry credentials |
| G-060 `lamark-batch` crate spec | plan/02 §Batch runner crate |
| G-061 Provider rate limiter | plan/04 §Rate limiter |
| G-062 Bulk KB upload | plan/07a §Bulk KB upload |
| G-063 Batch resume support | plan/02 §Batch runner crate (lamark-batch::resume module) |
| G-065 Curator trigger on AdapterPromoted | plan/08 §Curator trigger on adapter promotion |
| G-066 Blend config schema | plan/10 §Blend configuration |
| G-067 KB event schemas AdapterSuspended/ForgettingWarn | plan/10 §KB forgetting probe event schemas |
| G-068 Reflexion on rollback | plan/07b §Reflexion on rollback (Loop B extension) |
| G-069 Rollback deferral during active sessions | plan/05c §Rollback deferral for active sessions + plan/10 §Promote/rollback |
| G-075 CSRF for WebUI | plan/12 §CSRF protection |
| G-076 WS seq + reconnect | plan/05 §WebSocket sequence numbers + plan/12 §WebSocket sequence numbers |
| G-077 WS close → session finalization | plan/05 §WebSocket lifecycle + plan/12 §Session finalization on WS close |
| G-078 Remote replay buffer | plan/13 §Event replay buffer |
| G-079 Read-only concurrent client | plan/13 §Concurrent clients and read-only mode |

**Architectural gap** — Curator vs Dream consolidator boundary: Curator (plan/08) is the skill/memory consolidator running weekly and on adapter-promotion events. "Dream" is not a Lamark concept — it was referenced from Hermes-Agent (plan/00c) but was not adopted. The boundary is: Curator owns all consolidation; there is no separate Dream process.

---

## Resolved during audit (no ADR needed)

- Scenario 01 OQ#3 (reducer inline vs deferred) — answered in `plan/06:378`.
- Scenario 01 OQ#5 (trace bundle retention) — answered in `plan/03:189–192`.
- Scenario 02 OQ#2 (`ask`-from-hook vs `Allow`-from-policy) — answered in `plan/00d §29`.
- Scenario 03 OQ#4 (single `/memory/search` endpoint vs typed) — answered in `plan/07a:143–155`.
- Scenario 03 OQ#6 (manifest schema migration) — trainer accepts v1 + v2; backward-compat note in `plan/10`.

## Critical architectural decisions surfaced by audits (promote to ADRs)

- **Determinism vs LLM-assisted memory extraction (audit 03 §6).** Memory candidate authoring in v0.1 **must be deterministic** (heuristic-only). Reflexion-style LLM curation belongs in Curator (scenario 05), not in the reducer-adjacent extraction step. Closes G-014 cleanly; protects training reproducibility (G-003). → **ADR-0014**.
- **Counterfactual trajectories at three granularities (audit 04 §6).** G-008 (tool-call), G-012 (rejected-trajectory schema), G-021 (orchestration decision) are the same family at three scales. One umbrella decision could cover projection algebra + admissibility (which counterfactuals are well-grounded vs speculative) + DPO weighting. Recommend folding ADR-0009 + ADR-0021 into a single "Counterfactual trajectories" ADR with three sub-sections.
- **Scenario 04 is the inflection point (audit 04 §6).** Single-agent traces teach "be a good tool user." Multi-agent traces teach "be a good coordinator." These are qualitatively different decisions and need their own training signal. Until G-020 + G-021 close, scenario 04 is a parallel-orchestration demo, not a learning system.
