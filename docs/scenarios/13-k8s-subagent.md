# 13 — Sandbox: subagent in a Kubernetes pod

> **Phase:** P5–P6.
> **One-liner:** Coordinator spawns a subagent that runs in an isolated
> Kubernetes pod (separate namespace, NetworkPolicy egress allowlist,
> ResourceQuota), executes its task, streams events back over the
> `AgentHandle` wire protocol, and the trace bundle is **pulled back**
> to the parent's filesystem on completion.

---

## North-star contribution

- **Domain quality.** Production-grade isolation. A scraped-from-the-web
  subagent cannot reach internal services unless the NetworkPolicy
  allows it. Resource exhaustion is bounded by ResourceQuota. This is
  the *recommended production default* per `plan/00b §"Two-tier sandbox
  default"`.
- **Agent-side self-improvement.** Subagent traces are pulled back
  intact; memory writes, skills, reinforce signal — everything that
  works on `LocalSandbox` works on K8s. Identical training signal,
  different blast radius.
- **Model-side self-improvement.** Pod-isolated traces are domain-tagged
  (`source: sandbox:kubernetes`). Trainer can build adapters for
  production deployment scenarios specifically.

### Signals produced / consumed

- **Produces:** subagent trace bundle (transferred via `copy_out` from
  pod to host); `SandboxLifecycle*` events on the parent; resource-use
  facts ("this task type uses ~ 4 GiB / 12 min").
- **Consumes:** cluster manifests (Namespace + NetworkPolicy + RBAC +
  ResourceQuota, `plan/05c §"Cluster manifests for Kubernetes"`); pod
  template; per-subagent egress rules; image registry credentials.

---

## Idea

Coordinator (scenario 04) is about to spawn a scraper subagent. Config
has `coordinator.subagent_isolation: kubernetes`. Sandbox backend
provisions a pod from the configured template, sets per-pod
NetworkPolicy (egress = api.github.com only), passes the `AgentSpec`
via `--spec` to `lamark agent run` inside the pod (`plan/02:64`),
streams events back to the parent over the wire protocol from
`plan/13`. On completion, the pod's trace bundle is `copy_out`-ed to
the host's `~/.lamark/traces/<rollout_id>/`.

## Actors

| Actor | Role |
|---|---|
| **Coordinator session** | Same as scenario 04. Picks `KubernetesSandbox` by config. |
| **`KubernetesSandbox`** | `crates/lamark-sandbox::backends::kubernetes` — implements `Sandbox` trait (`plan/05c §"Trait"`). |
| **Cluster controller** | The Kubernetes API server. Lamark talks to it via `kube-rs`. |
| **Pod template** | YAML/JSON manifest with `lamark agent run --spec /spec.json --events /events.sock`. |
| **NetworkPolicy** | Pod-level egress allowlist. Enforced by the CNI (Calico / Cilium / etc.). |
| **ResourceQuota** | Per-namespace CPU/memory/storage caps. |
| **`lamark agent run` (in-pod)** | The in-sandbox entrypoint (`plan/02:64`). Reads spec, runs session, writes trace inside the pod, exits. |
| **Event stream** | Bidirectional (Submission ↔ Event) over a Unix socket inside the pod, port-forwarded or sidecar-tunneled to the parent. |

## Trigger

Coordinator (scenario 04) calls `sandbox.spawn_agent(spec)` where `spec.sandbox_override = Kubernetes` (or config default = `kubernetes`).

## Pipeline

1. **Resolve template.** `KubernetesSandbox` reads its config: namespace, base pod template, NetworkPolicy template, RBAC role binding.
2. **Build pod spec.** Inject:
   - `AgentSpec` JSON as a ConfigMap mounted at `/spec.json`.
   - Per-subagent egress allowlist as a NetworkPolicy attached to the pod.
   - Container image (`lamark/agent:<version>`) — same binary as host, in-pod entrypoint `lamark agent run`.
   - Budget translated to K8s primitives: CPU + memory limits; pod activeDeadlineSeconds = `budget.seconds`.
3. **Submit to cluster.** `kube-rs` creates the Pod + NetworkPolicy. Wait for `Running`.
4. **Wire up event stream.** Two options: (a) port-forward via `kube-rs` to a port the in-pod agent listens on; (b) sidecar that forwards a Unix socket. v0.1: port-forward. The parent connects; receives `SessionStarted` from the pod.
5. **Submission flow.** Parent posts `Op::UserInput` etc. as JSON over the stream; pod-side `lamark agent run` enqueues them on its SQ.
6. **Heartbeat.** The in-pod agent emits `SubagentHeartbeat` events ≥ every 30s (same as scenario 04). Coordinator's zombie watcher applies (`plan/05a:148`).
7. **In-pod tool calls.** Run inside the pod's filesystem; the pod's NetworkPolicy enforces egress. Trace bundle writes to `/var/lamark/traces/<rollout>/` (pod-local).
8. **Completion.** In-pod agent emits `TurnEnded { SessionEnd }`; closes recorder; exits.
9. **Trace pull-back.** Parent calls `sandbox.copy_out(/var/lamark/traces/<rollout>/, ~/.lamark/traces/<rollout>/)`. Atomic per-file via tmp+rename. Verified by manifest hash.
10. **Pod teardown.** `lifecycle_close` deletes the pod + NetworkPolicy. Audit-logged.
11. **Reducer + KB upload.** Run on the host as normal (`plan/06 §"Reducer"`).

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 1–3 (pod provisioning) | `lamark-sandbox::backends::kubernetes` | 05c + 05d (cluster manifests) |
| 4 (event stream) | `lamark-sandbox::wire` (shared with plan/13) | 05c + 13 |
| 5 (submission) | `Sandbox::spawn_agent` API | 05c §"AgentHandle" |
| 6 (heartbeat) | `lamark-coordinator::zombie_watch` | 05a |
| 7 (in-pod recorder) | `lamark-trace` (unchanged) | 06 |
| 9 (copy_out) | `Sandbox::copy_out` | 05c |
| 10 (teardown) | `Sandbox::lifecycle_close` | 05c |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Pod scheduling fails (no capacity)** | `SandboxError::ProvisionFailed`; coordinator: (a) retry with reduced budget, or (b) fail the card, or (c) fall back to `DockerSandbox`. Config policy. |
| **NetworkPolicy denies a required egress** | In-pod tool call returns connection refused / timeout; recorded as `ToolCallEnded { ok: false }`; agent gets structured error. **No bypass mechanism** by design. |
| **Pod OOM-killed** | Pod restart not desired (would lose trace state); `restartPolicy: Never`. Recorder's last-flush is on disk (PVC or emptyDir flushed via fsync) — pull-back recovers what's there; bundle marked `status=aborted`. |
| **`activeDeadlineSeconds` exceeded** | Pod terminated; same as OOM path. Coordinator sees `budget_exhausted`. |
| **Image pull failure** | Pod stuck `ErrImagePull`; sandbox surfaces clear error; admin notified. |
| **kube-rs auth expires mid-session** | Reconnect with refreshed creds; if persistent, abort with `SandboxControlPlaneError`. |
| **Event stream drops mid-run** | Reconnect; bridge resynchronizes from last `seq` of `trace.jsonl` (idempotent). |
| **Partial trace pull-back (some files transferred)** | Mark bundle `incomplete`; integration tests confirm pull-back is atomic. |
| **NetworkPolicy CNI not installed** | `health_check` reports `MissingNetworkPolicySupport`; refuse to spawn; explicit error. |

## Acceptance criteria

- [ ] `KubernetesSandbox` passes the `Sandbox` trait conformance test suite.
- [ ] Provisioning a pod with the default template + per-subagent NetworkPolicy + ResourceQuota succeeds.
- [ ] An in-pod tool that tries to reach a non-allowlisted host fails cleanly.
- [ ] Heartbeat flows over the event stream; coordinator's zombie watcher works against pod subagents.
- [ ] On normal completion, `copy_out` produces a complete trace bundle on the host.
- [ ] On pod OOM / deadline, the trace bundle is marked aborted and what was flushed is recovered.
- [ ] `lifecycle_close` deletes the pod + NetworkPolicy.
- [ ] Worked config examples (`plan/05d`) match the production manifest.

## Self-improvement assertions

1. **Identical signal across sandboxes.** A subagent run in K8s vs Local produces *equivalent* training samples — only the `source` tag differs.
2. **Resource-use memory facts.** Facts like "task type X uses ~4 GiB / 12 min" enable budget tuning over time.
3. **Egress-policy training.** Trace events `ToolCallEnded { ok: false, error: NetworkPolicyDenied }` are negative samples teaching the model not to reach for forbidden destinations.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| `Sandbox` trait + `spawn_agent` API | plan/05c §"Trait" | _audit_ |
| Kubernetes backend implementation outline | plan/05c §"Backends" | _audit_ |
| Cluster manifests (Namespace + NetworkPolicy + RBAC + ResourceQuota) | plan/05d | _audit_ |
| In-pod entrypoint `lamark agent run --spec --events` | plan/02:64 + plan/05c | _audit_ |
| Per-subagent NetworkPolicy attach | plan/05c §"Egress policy" + plan/05d | _audit_ |
| Budget → activeDeadlineSeconds + CPU/mem limits translation | plan/05c §"Budget enforcement" | _audit_ |
| Event-stream protocol (Sub/Event over Unix socket / port-forward) | plan/05c §"AgentHandle" + plan/13 | _audit_ |
| `copy_out` atomicity for trace pull-back | plan/05c §"Trait" | _audit_ |
| `health_check` for missing CNI / quota / RBAC | plan/05c §"health_check" | _audit_ |
| Pod `restartPolicy: Never` (don't lose trace state) | (likely **gap G-048**) | _audit_ |
| Stream-reconnect with seq-based resume | (likely **gap G-049**) | _audit_ |
| Fallback policy (k8s failure → Docker) | (likely **gap G-050**) | _audit_ |
| Image registry credentials handling | (likely **gap**) | _audit_ |
| Pod teardown idempotency on coordinator crash | plan/05c — likely partial | _audit_ |
| Concurrent N-pod subagents under one coordinator | plan/05a + plan/05c | _audit_ |
