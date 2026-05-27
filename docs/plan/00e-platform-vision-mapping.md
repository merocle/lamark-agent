# Platform target vision — building-block mapping

> Maps the "Platform target vision: Building block" diagram to Lamark crates and subsystems.
> Source image reviewed 2026-05-25.

The diagram organises the platform into five horizontal layers (bottom-up) plus two cross-cutting columns (Security, Infrastructure). The table below shows where each block is designed or implemented in Lamark.

---

## Infrastructure services (bottom layer)

| Block | Lamark component | Plan file |
|---|---|---|
| **LLM Fine-tuning** | `~/lamark-trainer/` Python pipeline — nightly SFT, weekly DPO, monthly merge | [10](./10-training-pipeline.md) |
| **Cloud LLM Proxy** | `lamark-providers` — `AnthropicCompat` + any OpenAI-compat cloud endpoint | [04](./04-layer-3-providers.md) |
| **Custom LLMs** | `lamark-providers` — `LocalOpenAICompat` targeting vLLM / Ollama / llama.cpp / SGLang | [04](./04-layer-3-providers.md) |
| **Data storage & Vector DB** | `../knowledge-base` — Postgres + pgvector + KG + RAPTOR | [07a](./07a-layer-6-memory-and-kb.md) |
| **Cloud Functions** | `lamark-sandbox` — Docker / SSH / Kubernetes / Modal / Vercel-Sandbox backends | [05c](./05c-sandbox-and-agent-hosting.md) |
| **Schedulers** | **Gap** — nightly cron triggers the trainer externally; no in-agent scheduling crate yet | — |

---

## Logic layer

| Block | Lamark component | Plan file |
|---|---|---|
| **Action Planner** | `lamark-coordinator` — SQ/EQ op-in/event-out loop; Kanban inter-agent protocol | [05a](./05a-coordinator-multi-agent.md) |
| **Self reflection** | `lamark-trace` reducer → KB upload → training pipeline (Reflexion / OPRO / ProTeGi loops) | [06](./06-layer-5-hooks-trace.md), [07b](./07b-prompt-self-improvement.md) |
| **Responsible AI tools** | `lamark-policy` — `Allow \| Prompt \| Forbidden` DSL; `PermissionRequest` hook fires on every shell/write-class tool | [05](./05-layer-4-agent-core.md) |
| **Local Memory** | `lamark-memory` — SQLite spool (warm standby when KB is unreachable) + Honcho / Mem0 / Hindsight impls | [07a](./07a-layer-6-memory-and-kb.md) |
| **Tools** | `lamark-tools` — ~70 built-in tools across ~28 toolsets; tool registry + plugin-contributed tools | [05](./05-layer-4-agent-core.md) |
| **LLM Connector** | `lamark-providers` — `ModelProvider` trait; provider selection is config not code | [04](./04-layer-3-providers.md) |

---

## Knowledge services

| Block | Lamark component | Plan file |
|---|---|---|
| **Global Memory** | `lamark-kb-client` → `../knowledge-base` `/memory /knowledge /search` (5 s timeout; fire-and-forget writes with SQLite spool) | [07a](./07a-layer-6-memory-and-kb.md) |
| **Data graph** | `../knowledge-base` `/graph` endpoint — entity + skill knowledge graph; drives Curator recommendations | [07a](./07a-layer-6-memory-and-kb.md), [08](./08-layer-7-skills-plugins-curator.md) |
| **External Connectors** | `lamark-mcp` (MCP client — consume third-party servers) + `lamark-acp` (ACP adapter — call and be called by other agents) | [09](./09-layer-8-gateway-integrations.md) |

---

## Communications layer

| Block | Lamark component | Plan file |
|---|---|---|
| **Messaging / Session-based** | `lamark-gateway` — Telegram + Slack + Discord adapters; long-running session wrapping the core agent | [09](./09-layer-8-gateway-integrations.md) |
| **Notification** | `lamark-hooks` event stream — `PostToolUse`, `SessionStart`, etc.; gateway re-emits these as platform notifications | [06](./06-layer-5-hooks-trace.md) |
| **Request-Response** | `lamark-remote` — tonic gRPC + WebSocket fallback server | [13](./13-remote-ui.md) |
| **Custom Public APIs** | `lamark-webui` axum server + `lamark-remote` expose the full agent surface over HTTP/WS/gRPC | [12](./12-webui.md), [13](./13-remote-ui.md) |

---

## QA & DevOps (top layer)

| Block | Lamark component | Plan file |
|---|---|---|
| **Quality evaluation** | Eval gates in trainer + `knowledge-base` `/knowledge/eval_sets`; forgetting-probe diagnostics prevent regression promotion | [10](./10-training-pipeline.md) |
| **Testing** | `lamark-test-utils` — fixtures, recorded tapes, `wiremock` HTTP mocks, `insta` snapshots, `ratatui` test helpers | [11](./11-build-test-deploy.md) |
| **Playground** | `lamark-webui` SvelteKit SPA — **planned**, not yet scaffolded | [12](./12-webui.md) |
| **Environments** | `lamark-sandbox` — local / docker / ssh / kubernetes; per-environment YAML config worked examples | [05c](./05c-sandbox-and-agent-hosting.md), [05d](./05d-sandbox-config-examples.md) |
| **Deployment** | Docker + Kubernetes manifests, GitHub Actions CI, release process | [11](./11-build-test-deploy.md) |

---

## Cross-cutting: Security

| Block | Lamark component | Plan file |
|---|---|---|
| **Permissions** | `lamark-policy` + `lamark-hooks` `PermissionRequest` — every shell-class and write-class tool fires it; declarative `policy.toml` rules | [05](./05-layer-4-agent-core.md) |
| **Auditing** | `lamark-trace` — every event (tool call, approval decision, error, gateway event) appended to `trace.jsonl`; immutable append-only store | [06](./06-layer-5-hooks-trace.md) |
| **Encryption** | **Gap** — transport-level TLS is expected at the gateway / gRPC layer but not yet designed; at-rest encryption of `~/.lamark/traces/` not specified | — |
| **DDoS & Spam protection** | **Gap** — rate-limiting and message-filtering at the gateway adapter level not yet planned | [09](./09-layer-8-gateway-integrations.md) |

---

## Cross-cutting: Infrastructure

| Block | Lamark component | Plan file |
|---|---|---|
| **Scaling** | `lamark-sandbox` Kubernetes backend (ResourceQuota, NetworkPolicy, RBAC); `lamark-coordinator` multi-agent Kanban distributes load | [05a](./05a-coordinator-multi-agent.md), [05c](./05c-sandbox-and-agent-hosting.md) |
| **Fault tolerance** | KB writes are fire-and-forget with SQLite spool; KB reads degrade gracefully to the spool when KB is down (5 s timeout) | [07a](./07a-layer-6-memory-and-kb.md) |
| **Logging** | `tracing` crate throughout; `println!` / `eprintln!` are CI-banned per CLAUDE.md | [11](./11-build-test-deploy.md) |
| **Tracing** | `lamark-trace` — `manifest.json` + append-only `trace.jsonl` + `payloads/` bundle (Codex rollout-trace format) | [06](./06-layer-5-hooks-trace.md) |
| **Monitoring** | **Gap** — no structured metrics / alerting surface yet; candidate home is `lamark-remote` or a future `lamark-telemetry` crate with Prometheus / OTLP export | — |
| **Backups** | `../knowledge-base` is the canonical persistent store; local SQLite spool is the warm standby | [07a](./07a-layer-6-memory-and-kb.md) |

---

## Gap summary

Four capabilities from the diagram have no current plan coverage:

| Gap | Severity | Candidate approach |
|---|---|---|
| **Schedulers** (in-agent) | Medium — cron triggers the trainer externally but there is no in-agent task scheduler | Add a `lamark-scheduler` crate or hook into an async job queue (e.g. `tokio-cron-scheduler`) wired to `lamark-coordinator` |
| **Encryption** | High — traces contain sensitive tool outputs and user data | TLS at gateway/gRPC boundaries (already implied); optional `age`-encrypted `payloads/` blobs in trace bundles |
| **DDoS & Spam protection** | Medium — gateway adapters are public-facing | Per-adapter rate-limiting middleware in `lamark-gateway`; allowlist/blocklist stored in `lamark-policy` |
| **Monitoring** | Medium — no observable metrics surface | `lamark-telemetry` crate: `opentelemetry` + Prometheus exporter; OTLP traces forwarded from `lamark-trace` events |
