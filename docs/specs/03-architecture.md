# 03 — High-level architecture

The runtime is **Rust from day 1** — no Python bridge, no embedded interpreter
(see [`01-scope-and-inheritance.md`](./01-scope-and-inheritance.md) §Target
language and [`../plan/01-rust-strategy.md`](../plan/01-rust-strategy.md)). Python
is **only** the training pipeline; the agent ↔ trainer boundary is the file system
(`~/.lamark/traces/`) and the knowledge-base REST API.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            Lamark Process (Rust)                         │
│                                                                          │
│  ┌──────────────────┐    ┌─────────────────┐    ┌────────────────────┐  │
│  │ CLI / Gateway /  │───▶│  AIAgent core   │───▶│  Provider router   │  │
│  │ MCP server / ACP │    │ (turn loop, SQ/ │    │  (Local | Anthropic│  │
│  │ (entry points)   │    │  EQ event bus)  │    │   | Bedrock | ...) │  │
│  └──────────────────┘    └────┬────────┬───┘    └────────────────────┘  │
│                               │        │                                 │
│                          ┌────▼──┐  ┌──▼─────────┐                       │
│                          │ Tools │  │ Hook bus   │◀─── PreToolUse,      │
│                          │ regis-│  │            │     PostToolUse,     │
│                          │ try   │  │            │     PermissionReq..  │
│                          └────┬──┘  └──┬─────────┘                       │
│                               │        │                                 │
│                       ┌───────▼───┐    ▼                                 │
│                       │ Env back- │  ┌──────────────────────────────┐    │
│                       │ ends      │  │ Trace recorder               │    │
│                       │ local/    │  │ → ~/.lamark/traces/<id>/     │    │
│                       │ docker/   │  │   manifest.json+trace.jsonl  │    │
│                       │ ssh/...   │  │   + payloads/                │    │
│                       └───────────┘  └────────┬─────────────────────┘    │
│                                               │ (on TurnEnded / close)   │
│                                               ▼                          │
│                          ┌──────────────────────────────────────────┐    │
│                          │ Knowledge-base client (HTTP)             │    │
│                          │ POST /agents/{id}/traces (reduced)       │    │
│                          │ POST /memory/facts                       │    │
│                          │ GET  /memory/search → context block      │    │
│                          └──────────────────────────────────────────┘    │
│                                                                          │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────────────┐ │
│  │ Skill mgr  │  │ Curator    │  │ Plugin host│  │ Cache mgr           │ │
│  │ (md+yaml)  │  │ (7-day)    │  │ (dyn lib + │  │ (prompt-hash +      │ │
│  │            │  │            │  │  WASM)     │  │  cache_control)     │ │
│  └────────────┘  └────────────┘  └────────────┘  └────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
            │
            │  ~/.lamark/traces/  (file system handoff)
            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                  Training pipeline (Python; cron-driven)                 │
│                                                                          │
│  Nightly:  collect → redact → transform → curate → quality → blend       │
│            → SFT-LoRA → eval-gate → forgetting-probe → promote/rollback  │
│            (results POST back to knowledge-base /knowledge/datasets,     │
│             /agents/{id}/adapters, /agents/{id}/events)                  │
│                                                                          │
│  Weekly:   DPO from preference pairs (knowledge-base reinforce signal)   │
│  Monthly:  merge-and-unload → requantize → refresh anchors               │
└──────────────────────────────────────────────────────────────────────────┘
```

Three independent processes, three lifecycles:
- **Agent runtime** (Rust) — interactive, one process per session/user/gateway.
- **knowledge-base** (Kotlin/Spring) — long-running service; system of record.
- **Training pipeline** (Python) — cron-scheduled, runs on the GPU box.

Coupling: agent → trace files on disk; agent ↔ knowledge-base over HTTP; trainer ↔ knowledge-base over HTTP. **No shared DB.**

The eight Rust runtime layers are indexed in
[`05-runtime-layers.md`](./05-runtime-layers.md); the tool surface in
[`04-tooling-and-protocol.md`](./04-tooling-and-protocol.md).
