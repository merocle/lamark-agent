# Batch C audit — scenarios 13–14

Consolidated audit for Kubernetes pod sandbox and batch runner. 10 new gaps (G-055..G-064); 5 blockers.

---

## Scenario 13 — Kubernetes pod sandbox

**Verdict:** 🟡 Yellow — core isolation architecture is well-specified; five operational details are unresolved.

### Coverage matrix
- ✅ `Sandbox` trait + `spawn_agent` API (plan/05c §"Trait")
- ✅ `KubernetesSandbox` backend implementation outline (plan/05c §"Backends")
- ✅ Cluster manifests: Namespace + NetworkPolicy + RBAC + ResourceQuota (plan/05d)
- ✅ In-pod entrypoint `lamark agent run --spec --events` (plan/02:64 + plan/05c)
- ✅ Per-subagent NetworkPolicy attach (plan/05c §"Egress policy" + plan/05d)
- ✅ Budget → `activeDeadlineSeconds` + CPU/mem limits translation (plan/05c §"Budget enforcement")
- ✅ Event-stream protocol (Sub/Event over Unix socket / port-forward) (plan/05c §"AgentHandle" + plan/13)
- ✅ `health_check` for missing CNI / quota / RBAC (plan/05c §"health_check")
- ✅ Concurrent N-pod subagents under one coordinator (plan/05a + plan/05c)
- ❌ **`copy_out` atomicity for trace pull-back** — pull-back uses tmp+rename per file, but no spec for manifest verification or partial-transfer recovery — **G-055**
- ❌ **Stream reconnect with seq-based resume** — seqno-based recovery mentioned in scenario but not in plan/05c — **G-056**
- ❌ **Fallback policy** (k8s scheduling failure → DockerSandbox) — policy exists by description, not spec — **G-057**
- ❌ **Image registry credentials handling** — plan/05c does not specify how `lamark/agent:<version>` image is pulled from private registries — **G-058**
- ⚠️ **Pod teardown idempotency on coordinator crash** — plan/05c covers graceful teardown but not crash recovery — **G-059**

### Self-improvement assertions
- Identical signal across sandboxes — **at risk** (G-055: incomplete pull-back could drop trace events)
- Resource-use memory facts — feasible via `SandboxLifecycle*` events
- Egress-policy negative samples — covered by design; NetworkPolicy enforced

### Gaps (G-055..G-059)
- **G-055 [blocker]** — `copy_out` atomicity spec: manifest hash verification + partial-transfer recovery.
- **G-056 [blocker]** — Stream reconnect algorithm: seqno-based bridge resynchronization spec in plan/05c.
- **G-057 [deferred]** — Fallback policy: k8s provisioning failure → `DockerSandbox` / error, config-driven.
- **G-058 [blocker]** — Image registry credentials: imagePullSecrets injection / credential helper spec.
- **G-059 [deferred]** — Pod teardown idempotency: coordinator crash leaves dangling pods; cleanup-on-start strategy.

### Critical insight
The architecture is production-quality and well-specified for the happy path. All five gaps are operational plumbing, not conceptual. G-055 is highest urgency: a partial `copy_out` silently drops training samples. Solution: manifest-hash verification and atomic rename must be spelled out in plan/05c §"copy_out" before the `KubernetesSandbox` crate is written.

### ADRs
- ADR-0057 `copy_out` atomicity and partial-transfer recovery
- ADR-0058 Event stream reconnect protocol (seqno-based)
- ADR-0059 K8s image credentials management

---

## Scenario 14 — Batch runner

**Verdict:** 🔴 Red — the CLI surface exists in plan/02; the batch runner crate and five supporting systems are not yet specified.

### Coverage matrix
- ✅ `lamark exec SCRIPT.lamark` CLI (plan/02:44)
- ✅ SPEC §2.1 references "Batch runner" as a component
- ✅ Eval scoring pipeline (plan/10)
- ✅ Gold-set format (plan/10)
- ✅ Cancellation semantics (plan/05c + tokio cancel)
- ✅ Per-prompt metadata propagation to manifest.json (plan/06)
- ✅ DPO pair authoring at scale (G-008 + G-012 — pending, but schema is defined)
- ✅ Determinism contract for replay (G-003 — pending)
- ❌ **Batch runner crate** — `crates/lamark-batch` not in plan file list; only mentioned in SPEC §2.1 — **G-060**
- ❌ **Provider rate limiter** — `crates/lamark-providers::rate_limiter` not specified — **G-061**
- ❌ **Bulk KB upload endpoint** — plan/07a does not include batch ingest path — **G-062**
- ❌ **Resume support** (`--resume <output-dir>`) — not covered in any plan file — **G-063**
- ⚠️ **Output directory layout** — layout implied by scenario, not explicitly specified — **G-064**
- ⚠️ Live progress UI — partially covered by plan/12 (TUI events) but batch-specific worker-pool view not specified

### Self-improvement assertions
- Batch IS the training-data faucet — **at risk** (G-060: crate not spec'd; G-061: no rate limiter; rate storms will starve the faucet)
- Eval baseline reproducible — depends on G-003 (reducer determinism)
- Failure clustering in report — no plan coverage; **gap G-064** (output layout includes report)
- DPO pair production — G-008 + G-012; at scale, G-061 becomes critical (rate storms during paired runs)

### Gaps (G-060..G-064)
- **G-060 [blocker]** — `crates/lamark-batch` crate structure not in any plan file.
- **G-061 [blocker]** — Provider rate limiter (`lamark-providers::rate_limiter`) unspecified.
- **G-062 [blocker]** — Bulk KB upload endpoint (plan/07a); fallback to sequential idempotent upload.
- **G-063 [blocker]** — Resume support (`--resume <dir>`): completed-bundle tracking (idempotency file per bundle).
- **G-064 [deferred]** — Output directory layout spec: `<output>/<rollout_id>/{manifest,trace,reduced,...}` + `run_report.json`.

### Critical insight
The batch runner is the pipeline's training-data faucet (scenario 14 §"Self-improvement assertions"). Without it, nightly training (scenario 15) is starved. The CLI entry point exists (`plan/02:44`), but the four blocking gaps mean the crate cannot be implemented yet. Priority order: G-060 (crate spec) → G-061 (rate limiter; without it, 1000-prompt runs hit 429 storms) → G-062 (KB upload) → G-063 (resume; without it, an interrupted 8h run must restart from zero).

### ADRs
- ADR-0060 `lamark-batch` crate structure and worker-pool protocol
- ADR-0061 Provider rate limiter design (token bucket + Retry-After)
- ADR-0062 Bulk KB upload and sequential fallback
- ADR-0063 Batch resume: per-bundle idempotency token and checkpoint file

---

## Summary

| Gap | Severity | Spans |
|---|---|---|
| G-055 `copy_out` atomicity | blocker | 13 |
| G-056 Stream reconnect seqno | blocker | 13 |
| G-057 K8s fallback policy | deferred | 13 |
| G-058 Image registry credentials | blocker | 13 |
| G-059 Pod teardown idempotency | deferred | 13 |
| G-060 `lamark-batch` crate spec | blocker | 14 |
| G-061 Provider rate limiter | blocker | 14 |
| G-062 Bulk KB upload | blocker | 14 |
| G-063 Batch resume support | blocker | 14 |
| G-064 Output directory layout | deferred | 14 |

**5 blockers + 5 deferred.**
