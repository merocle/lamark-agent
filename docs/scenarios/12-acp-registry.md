# 12 — ACP registry — Lamark calls / is called by peer agents

> **Phase:** P6.
> **One-liner:** A peer agent (Hermes, another Lamark instance, a
> third-party agent) appears in the ACP registry. Lamark can `acp call
> <peer> <task>` to delegate work; Lamark can also *be called* via its
> ACP listener. Sessions can be **forked** across peers (`fork_session`,
> 00c §9) so a long-running conversation moves between agents.

---

## North-star contribution

- **Domain quality.** ACP turns "Lamark is one agent" into "Lamark is a
  node in an agent fabric." The user routes work to the most-capable
  agent for each step.
- **Agent-side self-improvement.** Peer-agent results are *signal*:
  did delegating to Hermes for fact-checking outperform doing it
  locally? Memory facts capture "for task X, peer Y outperformed peer
  Z." Trainer can teach the routing layer.
- **Model-side self-improvement.** ACP delegations produce trace
  bundles with `interaction_edges` analogous to Kanban subagents
  (`plan/05a:262`). Cross-agent trajectories enrich the training set
  with how-to-collaborate-with-peers patterns.

### Signals produced / consumed

- **Produces:** `AcpDelegationStarted/Completed`, `AcpRequestReceived`
  trace events; cross-agent `interaction_edges`; routing-quality
  memory facts.
- **Consumes:** ACP registry from `~/.lamark/acp_registry.toml`;
  peer-agent endpoint URLs + auth.

---

## Idea

Carol has Hermes running on machine A and Lamark on machine B. She types `/acp call hermes "summarize this paper"` in her Lamark session. ACP adapter forwards the task; Hermes runs it; result returns. Or: an external orchestrator routes a research subtask to Lamark; Lamark's ACP server accepts, spawns a session, runs, replies. Either way: traces on both sides, edges in both `state.json`s.

## Actors

| Actor | Role |
|---|---|
| **ACP registry** | `~/.lamark/acp_registry.toml` — peer entries with `{name, endpoint, auth, capabilities}` (`plan/09 §"Part C — ACP"` + 00c §9). |
| **`lamark-acp::adapter`** | Outbound: `acp call peer task`. Wraps `Submission` semantics for cross-agent fidelity. |
| **`lamark-acp::server`** | Inbound: listen for delegations; spawn local `Session`; run; reply. |
| **`SessionMode`** (00c §9) | `Owned | Delegated | Forked` — affects how Lamark treats the local session relative to peer ownership. |
| **`fork_session`** (00c §9) | Move conversation context across agents while preserving identity. |
| **Permission engine** | Per-peer grants; reject peers not in registry. |

## Trigger

```
$ lamark acp register --name hermes --endpoint http://machineA:7878
$ lamark chat
> /acp call hermes "summarize this paper"
```
or, inbound:
```
$ lamark acp serve --bind 0.0.0.0:7878
```

## Pipeline

### Outbound delegation

1. Agent (or user via slash command) emits an `AcpDelegateTool` call → registry lookup → endpoint + auth resolved.
2. Permission check: per-peer grant per scenario 06 model. If first use → user prompt.
3. ACP adapter sends `Op::SpawnDelegated { peer, task_payload }` to peer.
4. Peer receives via its ACP server; spawns a local session with `SessionMode::Delegated`; runs.
5. Streaming events come back over ACP: `RemoteAgentMessageDelta`, `RemoteToolCallStarted`, etc. Local Lamark trace records *summarized* edges, not the full peer transcript (analogous to subagent visibility per `plan/05a:54`).
6. Peer's final result returns; local Lamark stitches it into its conversation as a structured tool result.
7. Memory provider authoring step (G-014) emits a routing fact: "for task type X, peer Hermes returned in T seconds with quality Q."

### Inbound delegation

8. Lamark's ACP server receives an incoming `SpawnDelegated`.
9. Validation: requester is in `acp_registry` AND `incoming_allowed=true` for that peer.
10. Permission gating: peer's task payload is treated as a privileged user input — but still subject to local policy. A peer cannot bypass `Forbidden` rules.
11. Local session runs with `SessionMode::Delegated`; trace recorder writes a normal bundle but tags it `source: acp:<peer>`.
12. Final result returned over ACP. Bundle reduces normally.

### Session fork

13. `fork_session` (00c §9): one peer says "you take it from here." Local Lamark adopts the conversation with `SessionMode::Forked`; the original session on the peer becomes read-only / archival.
14. Memory + skills + project context are *not* copied — they're recomputed from the local environment. Conversation history *is* copied.

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 1–3 (outbound) | `lamark-acp::adapter` | 09 §"Part C", 00c §9 |
| 4–6 (peer run) | (peer agent's code) | n/a |
| 7 (routing fact) | `lamark-memory` | 07a |
| 8–11 (inbound) | `lamark-acp::server` | 09 + 00c §9 |
| 13–14 (fork) | `lamark-acp::fork` | 00c §9 |
| All (events) | `lamark-trace` | 06 ProtocolEventObserved or new variants |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Peer offline** | Adapter returns structured error; agent re-plans (similar to subagent failure). |
| **Auth mismatch** | Reject; user notified; registry entry flagged. |
| **Peer streams a malformed event** | ACP framing tolerant; bad frames dropped + counted. After threshold, peer marked unhealthy. |
| **Inbound from unknown peer** | Reject with `PeerNotRegistered`. Logged. |
| **Local policy forbids the requested task** | Reject with structured error; peer sees `Forbidden`. |
| **Fork target rejects** | Original session stays Owned on the source peer. |
| **Cross-version incompatibility** | ACP version negotiation; on mismatch, downgrade to the common subset or refuse. |

## Acceptance criteria

- [ ] `lamark acp register / unregister / list` work.
- [ ] Outbound `/acp call <peer> <task>` round-trips through a test peer (another local Lamark instance simulating Hermes).
- [ ] Inbound `lamark acp serve` accepts registered peers, rejects unregistered.
- [ ] Trace events `AcpDelegationStarted/Completed` (or equivalent in `ProtocolEventObserved`) recorded on both sides.
- [ ] Session fork moves conversation; memory + skills are local to each side.
- [ ] Routing-quality memory facts written.
- [ ] Inbound tasks honor local policy (a peer cannot bypass `Forbidden`).

## Self-improvement assertions

1. **Routing-quality recall.** Future delegations to the same peer surface the prior fact ("Hermes returned in 4s with quality 0.92"); agent prefers the better-quality peer all else equal.
2. **Cross-agent training signal.** Reducer emits cross-agent edges; trainer can build orchestration-DPO over routing decisions (companion to G-021).
3. **Policy preserved across the boundary.** No peer can use ACP to bypass local `Forbidden` rules.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| ACP registry `~/.lamark/acp_registry.toml` | plan/09 §"Part C" + 00c §9 | _audit_ |
| `lamark acp register / list / unregister` CLI | plan/02 | _audit_ |
| ACP adapter outbound | plan/09 + 00c §9 | _audit_ |
| ACP server inbound | plan/09 + 00c §9 | _audit_ |
| `SessionMode` (Owned / Delegated / Forked) | 00c §9 | _audit_ |
| `fork_session` semantics | 00c §9 | _audit_ |
| Per-peer permission grants | plan/05 + scenario 06 model | _audit_ |
| ACP trace event variants or `ProtocolEventObserved` | plan/06:343 | _audit_ |
| Cross-agent `interaction_edges` | extends plan/05a:262 | _audit_ |
| Local policy enforced for inbound tasks | plan/05 — likely partial | _audit_ |
| ACP version negotiation | (likely **gap G-047**) | _audit_ |
| Memory tagging by ACP source | plan/07a | _audit_ |
| Routing-quality memory fact authoring | extends G-014 | _audit_ |
