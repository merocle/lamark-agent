# 06 — Trace bundle (training-data format)

We adopt **Codex's `rollout-trace`** bundle format because it is the cleanest
training-ready representation of agent execution in the wild. The runtime
implementation (recorder, reducer, KB sync) is in
[`../plan/06-layer-5-hooks-trace.md`](../plan/06-layer-5-hooks-trace.md).

## Raw layer (on-disk, append-only)

```
~/.lamark/traces/<rollout_id>/
├── manifest.json       # rollout_id, root_thread_id, started_at, ended_at, schema_version, agent_version
├── trace.jsonl         # one event per line
└── payloads/
    ├── inference-0001-req.json
    ├── inference-0001-resp.json
    ├── tool-0001-in.json
    └── ...
```

`trace.jsonl` event shape:

```json
{
  "seq": 0,
  "wall_time_unix_ms": 1748128345123,
  "thread_id": "t-...",
  "turn_id": "trn-...",
  "kind": "InferenceStarted",
  "payload_ref": "payloads/inference-0001-req.json"
}
```

Event `kind` values are the union of:
- Codex `RawTraceEventPayload` variants (rollout, thread, turn, inference, tool, code-cell, compaction, agent-result, edge).
- Lamark hook events (PermissionRequest/Denied/Granted; UserPromptSubmit; FileChanged; GatewayMessageIn/Out; SkillInvoked; CuratorRun).

Large bodies live in `payloads/` as referenced JSON files; `trace.jsonl` stays cheap to scan and replay.

## Reduced layer (offline, `lamark trace reduce`)

Reducer ingests one bundle and emits:

- `state.json` — Codex-style reduced graph (`threads`, `conversation_items`, `inference_calls`, `tool_calls`, `compactions`, `interaction_edges`, `raw_payload_refs`).
- `conversation.jsonl` — Nemotron-Agentic-v1 (one rollout = one line), ready for the training pipeline.

**Nemotron-Agentic-v1** is the same OpenAI-style tool-call shape used at runtime
(see [`04-tooling-and-protocol.md`](./04-tooling-and-protocol.md) §2): `messages[]`
with assistant `tool_calls[]`, `tool` results keyed by `tool_call_id`, and a
top-level `tools[]` array. Runtime wire format == training format.

## Knowledge-base sync

On `TurnEnded(status=session_end)`:

```http
POST {kb}/agents/{agent_id}/traces
Content-Type: application/json

{
  "rollout_id": "...",
  "manifest": { ... },
  "reduced_state": { ... },          # state.json
  "conversation": [ { ... }, ... ],  # conversation.jsonl content inlined
  "outcome": "success|tool_failed|user_aborted|...",
  "metrics": { "turn_count": N, "tool_count": N, "ms_total": N }
}
```

knowledge-base then indexes for retrieval (RAPTOR + KG + pgvector) and exposes via `GET /agents/{id}/traces?since=...` for the training pipeline.
