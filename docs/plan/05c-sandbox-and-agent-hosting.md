# 05c — Sandbox: trait, backends, and agent hosting

> The `Sandbox` trait is **not** just "where shell commands run." It is the
> isolation boundary that hosts whole **subagents** — their turn loop, their
> tools, their trace bundle — with controlled tool surface, network egress,
> secrets, and budget. Same call site for in-process, Docker, SSH, Modal.

**Crates:** `crates/lamark-sandbox/` (renamed from `lamark-envs`).
**Depends on:** `lamark-core`, `lamark-policy`, `lamark-trace`, `lamark-protocol`.
**Depended on by:** `lamark-tools` (file/shell ops), `lamark-coordinator` (subagent spawn), `crates/lamark` (the `lamark agent run` in-sandbox entrypoint).
**References:**
- `~/.cache/lemark/vendor/hermes-agent/tools/environments/*` — backend shapes.
- `~/.cache/lemark/vendor/codex/codex-rs/linux-sandbox/`, `windows-sandbox-rs/` — host-sandbox prior art (we do NOT copy verbatim; see ADR 0008).
- `plan/05-layer-4-agent-core.md:311` — old `Environment` trait this supersedes.
- `plan/05a-coordinator-multi-agent.md` — subagent semantics; the coordinator goes through `spawn_agent` now.
- `plan/13-remote-ui.md` — the wire protocol `spawn_agent` reuses across containers/hosts.

---

## Why agents-in-sandbox is the load-bearing primitive

Hermes-agent runs subagents through an **Environment** — same place its shell tool runs. That coupling is right. A subagent isn't an HTTP call; it's:

- A long-lived process that emits **`Event`s** continuously.
- Holding **secrets** (the model provider's API key, possibly different from the parent's).
- Reaching the **network** (the model provider, maybe Slack, maybe nothing else).
- Writing a **trace bundle** to a file path.
- Bound to a **budget** that's enforced both client-side and server-side.

If we make `Sandbox` only know about commands, the coordinator (plan/05a) ends up duplicating all of the above. Better: one trait, one wire protocol, one isolation story.

## Trait

```rust
// crates/lamark-sandbox/src/trait.rs
#[async_trait]
pub trait Sandbox: Send + Sync {
    fn name(&self) -> &str;
    fn capabilities(&self) -> SandboxCapabilities;

    // ---- file / process surface (commands and IO inside the sandbox) ----
    async fn spawn(
        &self,
        cmd: ShellCommand,
        cancel: CancellationToken,
    ) -> Result<Box<dyn ProcessHandle>, SandboxError>;

    async fn read_file (&self, path: &Path, cancel: CancellationToken) -> Result<Vec<u8>, SandboxError>;
    async fn write_file(&self, path: &Path, content: &[u8], cancel: CancellationToken) -> Result<(), SandboxError>;
    async fn list_files(&self, path: &Path, glob: Option<&str>, cancel: CancellationToken) -> Result<Vec<FileEntry>, SandboxError>;
    async fn copy_in   (&self, host_path: &Path, sandbox_path: &Path, cancel: CancellationToken) -> Result<(), SandboxError>;
    async fn copy_out  (&self, sandbox_path: &Path, host_path: &Path, cancel: CancellationToken) -> Result<(), SandboxError>;
    fn translate_path  (&self, host_path: &Path) -> Result<PathBuf, SandboxError>;   // host ↔ sandbox path mapping
    async fn workspace_root(&self) -> Result<PathBuf, SandboxError>;

    // ---- agent-hosting surface (the new piece) ----
    async fn spawn_agent(
        &self,
        spec: AgentSpec,
        cancel: CancellationToken,
    ) -> Result<Box<dyn AgentHandle>, SandboxError>;

    async fn health_check(&self) -> Result<HealthReport, SandboxError>;
    async fn lifecycle_close(&self) -> Result<(), SandboxError>;
}

#[async_trait]
pub trait ProcessHandle: Send + Sync {
    fn pid(&self) -> Option<u32>;
    fn stdin(&self)  -> Option<Pin<Box<dyn AsyncWrite + Send>>>;
    fn stdout(&self) -> Pin<Box<dyn AsyncRead  + Send>>;
    fn stderr(&self) -> Pin<Box<dyn AsyncRead  + Send>>;
    async fn wait(&self) -> Result<ExitStatus, SandboxError>;
    async fn kill_group(&self) -> Result<(), SandboxError>;
}

#[async_trait]
pub trait AgentHandle: Send + Sync {
    fn agent_id(&self) -> &str;
    async fn submit(&self, op: Op) -> Result<(), SandboxError>;
    fn events(&self) -> broadcast::Receiver<Event>;
    async fn wait(&self) -> Result<AgentOutcome, SandboxError>;
    async fn interrupt(&self) -> Result<(), SandboxError>;
    async fn kill(&self) -> Result<(), SandboxError>;
}

pub struct AgentSpec {
    pub agent_id:               String,                     // parent-generated SessionId
    pub role:                   String,                     // "researcher" | "coder" | …
    pub system_prompt_override: Option<String>,
    pub tool_allowlist:         Option<Vec<String>>,        // None = inherit; Some = restrict
    pub tool_proxy:             ToolProxyMode,              // Inherit | Restricted | ProxyToParent
    pub model:                  ModelSelector,
    pub budget:                 Budget,                     // { max_tokens, max_seconds, max_tool_calls }
    pub egress:                 EgressPolicy,
    pub secrets:                SecretScope,
    pub workspace:              WorkspaceMount,             // ReadOnly | CopyOnWrite | Fresh
    pub trace_sink:             TraceSinkRef,               // where the child writes its bundle
    pub parent_event_channel:   Option<EventChannelRef>,    // streaming sink on the parent side
}

pub enum EgressPolicy {
    None,                                     // --network=none equivalent
    ModelProviderOnly,                        // resolves to the configured provider hostnames + DNS
    Allowlist(Vec<EgressRule>),               // explicit hostnames / CIDRs / ports
}

pub enum ToolProxyMode {
    Inherit,                                  // child links lamark-tools; full set
    Restricted,                               // inherit but filtered by tool_allowlist
    ProxyToParent,                            // child has ONE tool (ParentToolProxy); every call streams back
}

pub enum WorkspaceMount {
    Fresh,                                    // empty tmpfs
    ReadOnly { host_path: PathBuf },
    CopyOnWrite { host_path: PathBuf },       // overlayfs / docker-volume copy-on-write
}

pub struct SecretScope {
    pub inherit_provider_keys:  bool,          // model-provider creds (usually true)
    pub allowlist_env_vars:     Vec<String>,   // explicit env vars to pass through
    pub mount_paths:            Vec<PathBuf>,  // host file paths to bind-mount read-only
}

pub struct AgentOutcome {
    pub final_message:  Option<String>,
    pub trace_bundle:   PathBuf,               // pulled back to parent host
    pub usage:          UsageStats,
    pub status:         TurnStatus,
}
```

**Invariant:** `spawn_agent` returns the same `Event` enum that an in-process `Session` emits. A subagent over Docker is indistinguishable from an in-process subagent at the call site. Transport is the `Sandbox` impl's problem.

## Backends (v0.1)

**Four** sandboxes ship in-tree. The remaining four become plugin candidates (plan/08, plugin host) since their integrations are non-trivial and orthogonal to the core trait.

| Sandbox | Status | Used for | Tier |
|---|---|---|---|
| `LocalSandbox` | v0.1 in-tree | Dev loop on the user's machine; coordinator subagents on the same host | **dev** (try / iterate) |
| `DockerSandbox` | v0.1 in-tree | Untrusted prompts on a single host; reproducible runs | dev / single-host prod |
| `SshSandbox` | v0.1 in-tree (small) | "Run this agent on the build server" | ops |
| `KubernetesSandbox` | v0.1 in-tree | Multi-tenant production deployments; horizontal scale; per-Pod isolation | **prod** (recommended) |
| `ModalSandbox` | post-v0.1 plugin | Ephemeral burst compute | prod-burst |
| `DaytonaSandbox` | post-v0.1 plugin | Cloud dev containers | ops |
| `SingularitySandbox` | post-v0.1 plugin | HPC clusters | ops |
| `VercelSandbox` | post-v0.1 plugin | Edge sandboxing | prod-edge |

**Positioning:** `LocalSandbox` is the default — fastest path from `lamark chat` to a working turn loop, for solo developers and trying things out. `KubernetesSandbox` is the recommended production path — fleet management, multi-tenancy, horizontal scale, Pod-per-subagent isolation, declarative egress via NetworkPolicy. `DockerSandbox` sits between them for single-host production or air-gapped environments. See [05d](./05d-sandbox-config-examples.md) for worked configs.

### LocalSandbox

Two modes; chosen per `AgentSpec`:

- **In-process (default for coordinator subagents).** `spawn_agent` builds a fresh `Session` and runs it on a `tokio::task`. `events()` is `event_tx.subscribe()`. Zero IPC; tools share parent's process. Cheap. Failure mode: a panic in the child can take the parent down.
- **Forked-process (default for untrusted-prompt subagents and `/spawn`).** `spawn_agent` execs `lamark agent run --spec /tmp/<id>/spec.json --events /tmp/<id>/events.sock`. Parent connects to the unix socket and bridges `Submission` / `Event`. On Linux, `prctl(PR_SET_PDEATHSIG, SIGTERM)` ensures the child dies if the parent dies.

`SandboxCapabilities` advertises which mode this instance is. The coordinator picks.

### DockerSandbox (default for untrusted work)

One container per child agent. Container image is the **runner image** from `docker/Dockerfile.runner` (already in plan/11:370), which ships:
- The `lamark` binary (statically linked).
- A non-root `runner` user (uid 1000).
- The minimum tool dependencies (git, ripgrep, fd, jq) — no compilers unless explicitly added by skill.

Per-child setup:

```
docker run \
  --rm \
  --network <child-private-net>     # see EgressPolicy below
  --user 1000:1000 \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --tmpfs /workspace:size=2g,nodev,nosuid \
  --tmpfs /tmp:size=1g \
  -v /var/run/lamark/<agent_id>.sock:/run/lamark/parent.sock  \  # SQ/EQ tunnel
  -v <ca-pinned-dir>:/etc/lamark/ca:ro                       \  # cert pinning
  --memory=<budget.max_memory> --cpus=<budget.max_cpus> \
  lamark/runner:<tag> \
  lamark agent run --spec /etc/lamark/spec.json --events /run/lamark/parent.sock
```

**Workspace mount:**
- `WorkspaceMount::Fresh` → tmpfs only.
- `ReadOnly { host_path }` → bind-mount `:ro`.
- `CopyOnWrite { host_path }` → Docker named volume populated from a snapshot, mounted `:rw` to a per-child volume that gets removed on `lifecycle_close`.

**Tool proxy:** `Inherit` is the default — the child has its own `lamark-tools` registry inside the container, so file ops happen on the container fs and never touch host. `Restricted` filters that registry. `ProxyToParent` is the escape hatch for tools that genuinely need parent-side credentials (e.g. a parent-only DB connection); each proxied call round-trips over `/run/lamark/parent.sock`.

**Network — EgressPolicy:**
- `None` → `--network=none`. Child cannot make outbound calls. Only viable with `ToolProxyMode::ProxyToParent` because the child must reach the model somehow.
- `ModelProviderOnly` (default) → a child-private docker network whose only route is a small sidecar (`tinyproxy`) that's allowlisted to the configured model provider hostnames + the DNS server. Implemented as a sidecar container in the same network namespace, not iptables magic in the agent container.
- `Allowlist(rules)` → same shape; broader allowlist.

The DNS server inside the sandbox network is a stub that only resolves the allowlisted names. Prevents DNS-tunneled exfiltration.

### SshSandbox

`openssh` Rust crate, persistent ControlMaster connection per session. `spawn_agent` issues `ssh host -- lamark agent run …` and tunnels SQ/EQ over a forwarded unix socket on the remote (`-L unix:…`). The remote host must have `lamark` installed; first connection runs a `lamark remote check` and refuses if version drift > 1 minor.

Workspace mount = the remote user's home unless `WorkspaceMount::Fresh` requests a `mktemp -d` scratch. `EgressPolicy` cannot be enforced by lamark on a remote shell — documented as caveat; recommend `SshSandbox` only for hosts the user controls.

### KubernetesSandbox (recommended for production)

One Kubernetes **Pod per child agent** (the unit of isolation), driven through the `kube-rs` crate against the cluster's API server. The Pod's primary container runs the same **runner image** as `DockerSandbox` (`lamark/runner:<tag>`), with the same agent entrypoint (`lamark agent run`). The SQ/EQ bridge is the same wire protocol as everywhere else (plan/13); the only thing that changes is how processes start, how the workspace is staged, and how the network is sealed.

**Pod spec built per child:**

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: lamark-agent-<short-id>
  namespace: <sandbox.kubernetes.namespace>     # one per profile or per session
  labels:
    app.lamark.dev/role: subagent
    app.lamark.dev/session: <session-id>
    app.lamark.dev/parent: <parent-agent-id>
  annotations:
    app.lamark.dev/rollout: <rollout-id>
spec:
  restartPolicy: Never                          # one-shot; we track via wait()
  serviceAccountName: <sandbox.kubernetes.service_account>
  automountServiceAccountToken: false           # children never see cluster creds
  securityContext:
    runAsUser: 1000
    runAsGroup: 1000
    runAsNonRoot: true
    fsGroup: 1000
    seccompProfile: { type: RuntimeDefault }
  containers:
    - name: agent
      image: <sandbox.kubernetes.image>          # lamark/runner:<tag>
      imagePullPolicy: IfNotPresent
      args: ["agent", "run", "--spec", "/etc/lamark/spec.json",
             "--events", "/run/lamark/parent.sock"]
      env:
        - { name: LAMARK_HOME, value: /workspace/.lamark }
        - name: LAMARK_PROVIDER_KEY
          valueFrom: { secretKeyRef: { name: <secret>, key: api-key } }
      resources:
        requests: { cpu: "<budget.cpu_request>", memory: "<budget.memory_request>" }
        limits:   { cpu: "<budget.max_cpus>",    memory: "<budget.max_memory>" }
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities: { drop: ["ALL"] }
      volumeMounts:
        - { name: workspace, mountPath: /workspace }
        - { name: tmp,       mountPath: /tmp }
        - { name: spec,      mountPath: /etc/lamark, readOnly: true }
        - { name: parent,    mountPath: /run/lamark }      # the SQ/EQ socket bridge
  volumes:
    - name: workspace                                       # see WorkspaceMount below
      <emptyDir|persistentVolumeClaim|projected>
    - name: tmp
      emptyDir: { sizeLimit: 1Gi, medium: Memory }
    - name: spec
      configMap: { name: lamark-spec-<short-id> }
    - name: parent
      emptyDir: {}                                          # bridged out via sidecar (see below)
```

**Workspace mount:**
- `WorkspaceMount::Fresh` → `emptyDir { sizeLimit: 2Gi, medium: Memory }` (tmpfs-backed).
- `ReadOnly { host_path }` → for K8s this becomes a `projected` volume from a pre-staged ConfigMap or a read-only `persistentVolumeClaim`.
- `CopyOnWrite { host_path }` → a per-child `persistentVolumeClaim` cloned from a `VolumeSnapshot` of the source PVC. Requires the cluster's CSI driver to support snapshot+clone (most modern ones do; `lamark doctor` checks at startup).

**Tool proxy / SQ-EQ bridge:** the parent process runs **outside** the Pod (could be a controller Pod, a gateway, or a `lamark` process on a host). The bridge is a small **sidecar container** in the same Pod:

```yaml
    - name: bridge
      image: lamark/bridge:<tag>                  # minimal — just the SQ/EQ relay
      env:
        - { name: LAMARK_PARENT_ENDPOINT, value: "wss://lamark-controller.<ns>.svc:7443/agent/<id>" }
        - { name: LAMARK_AUTH_TOKEN,      valueFrom: { secretKeyRef: { name: lamark-agent-<id>-creds, key: token } } }
      volumeMounts:
        - { name: parent, mountPath: /run/lamark }
```

The sidecar mounts the same `parent` emptyDir so the agent and the relay see the same unix socket; the relay tunnels SQ/EQ over mTLS WebSocket to the controller. Same protocol as plan/13.

For `ToolProxyMode::ProxyToParent`, tool calls round-trip over the same channel; for `Inherit` (default), the agent runs tools in-Pod and only events flow back.

**Network — EgressPolicy via NetworkPolicy:**

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: lamark-agent-<short-id>
spec:
  podSelector: { matchLabels: { app.lamark.dev/session: <session-id> } }
  policyTypes: [Ingress, Egress]
  egress:
    # ModelProviderOnly: only the model provider + DNS + the lamark controller
    - to:
        - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: kube-system } }
          podSelector:       { matchLabels: { k8s-app: kube-dns } }
      ports: [{ port: 53, protocol: UDP }, { port: 53, protocol: TCP }]
    - to: [{ ipBlock: { cidr: <model_provider_cidr> } }]
      ports: [{ port: 443, protocol: TCP }]
    - to:
        - podSelector: { matchLabels: { app.lamark.dev/role: controller } }
      ports: [{ port: 7443, protocol: TCP }]
  ingress: []                                         # children accept nothing inbound
```

Egress policies:
- `None` → empty `egress: []`. Child cannot reach anything; only viable with `ToolProxyMode::ProxyToParent`.
- `ModelProviderOnly` (default) → the policy shown above.
- `Allowlist(rules)` → additional `to:` blocks.

Per-namespace `default-deny` NetworkPolicy is expected to be already in place (`lamark doctor` warns if absent).

**Pod lifecycle:**
- `spawn_agent` issues `Pods.create()` plus the matching `NetworkPolicy` and a short-lived `Secret` for the agent's per-Pod credentials.
- `events()` is fed by the sidecar's tunnel; falls back to `Pods.logs(follow=true)` if the tunnel hasn't established within `event_stream_grace` (default 5 s) so users see early bring-up output.
- `wait()` polls `Pods.status.phase` and the sidecar's connection state.
- `interrupt()` → `Pods.delete(graceful=true)` with `terminationGracePeriodSeconds = <interrupt_grace>`.
- `kill()` → `Pods.delete(graceful=false, gracePeriodSeconds=0)`.
- `lifecycle_close()` deletes the Pod, the NetworkPolicy, and the Secret.

**Authentication:** `kubeconfig` resolution order: explicit `sandbox.kubernetes.kubeconfig` path → `KUBECONFIG` env → `~/.kube/config` → in-cluster `ServiceAccount` (when lamark itself is running in the cluster, e.g. as the gateway). The lamark service account needs `create/get/list/watch/delete` on `pods`, `pods/log`, `pods/exec`, `secrets`, `configmaps`, `networkpolicies`, and `persistentvolumeclaims` in the configured namespace. Manifest in [05d](./05d-sandbox-config-examples.md).

### § Image registry credentials

`KubernetesSandbox` injects image pull credentials into every spawned Pod spec using one of two mutually exclusive config keys:

**Option A — pre-existing Secret (preferred):**
```toml
[sandbox.kubernetes]
image_pull_secret_name = "my-registry-creds"
```
When set, the sandbox injects `imagePullSecrets: [{ name: "my-registry-creds" }]` into every Pod spec. The Secret must already exist in the target namespace as a `kubernetes.io/dockerconfigjson` type Secret. The sandbox does not create or delete it. This is the recommended path for production: credentials are managed by the cluster operator, not by lamark.

**Option B — credential from environment variable:**
```toml
[sandbox.kubernetes]
registry_auth_env = "LAMARK_REGISTRY_AUTH"
```
When set, at Pod creation time the sandbox reads the named environment variable (which must hold a base64-encoded Docker config JSON, i.e. the same bytes that would go in a `~/.docker/config.json`), creates a temporary `kubernetes.io/dockerconfigjson` Secret named `lamark-regcred-<short-id>` in the target namespace, injects `imagePullSecrets: [{ name: "lamark-regcred-<short-id>" }]` into the Pod spec, and deletes the Secret after `lifecycle_close()` completes. If Secret deletion fails, the error is logged at `WARN` and the cleanup is retried once; permanent failure is recorded as a `KubernetesSecretLeaked` trace event so operators can audit.

**Neither configured:** if neither key is set and the image requires authentication, the Kubernetes scheduler will fail to pull the image and the Pod enters `ErrImagePull` / `ImagePullBackOff`. Lamark surfaces this as `SandboxError::PodPullFailed { reason }` when the pod status watcher detects the condition.

**Pre-flight image pull validation:** controlled by:
```toml
[sandbox.kubernetes]
validate_image_pull = false   # default; set true to enable
```
When `true`, `health_check()` issues a dry-run Pod creation (or inspects cluster events) to verify the configured image is pullable from the target namespace before accepting any `spawn_agent` calls. This check is slow (it may require the cluster to contact the registry) and is therefore **off by default**. Operators running automated pipelines where a pull failure would be silently retried may want to enable it to surface credential issues at startup rather than at first spawn.

**Multi-tenancy:** when running multiple users/projects through the same lamark deployment, each project gets its own namespace and the controller binds a ServiceAccount + RoleBinding per namespace; lamark refuses to cross namespaces. Resource quotas live at the namespace level (`ResourceQuota`/`LimitRange`).

**GPU passthrough:** when an agent needs a GPU (vision/voice tools), the Pod spec adds `resources.limits["nvidia.com/gpu"] = <budget.gpus>` and a `nodeSelector` from `sandbox.kubernetes.gpu_node_selector`. Off by default; opt-in per agent spec.

**Observability:** every Pod carries `app.lamark.dev/session`, `app.lamark.dev/parent`, and `app.lamark.dev/rollout` labels so cluster operators can correlate with the trace bundle (rollout_id) and the session DB (session_id). Lamark emits a `KubernetesPodSpawned` trace event with `pod_name`, `namespace`, `node`, `image_sha` for full lineage.

## In-sandbox entrypoint: `lamark agent run`

The CLI gains one new subcommand (added to plan/02):

```
lamark agent run --spec PATH [--events PATH | --stdio]
```

- Reads `AgentSpec` from `--spec` (JSON file).
- Opens the SQ/EQ transport (`--events` for unix socket, `--stdio` for stdin/stdout framing).
- Builds a `Session` configured by the spec.
- Runs the turn loop until `wait()` exits or `Submission::Shutdown`.
- Writes its trace bundle to the spec's `trace_sink` (typically `~/.lamark/traces/<rollout_id>/` inside the sandbox).
- On exit, flushes the bundle and closes the channel.

This is the **only** binary surface the sandbox needs to host an agent — same binary, different entrypoint. No second crate.

## Wire protocol reuse (plan/13)

`AgentHandle` over Docker/SSH/Modal/etc. is structurally identical to a Remote UI client:

| Remote UI (`plan/13`) | Sandbox `AgentHandle` |
|---|---|
| `Submit(stream Submission) → Empty` | `AgentHandle::submit(op)` |
| `Subscribe(SessionHandle) → stream Event` | `AgentHandle::events()` |
| `OpenSession(req) → SessionHandle` | `Sandbox::spawn_agent(spec)` |
| `UploadAttachment` / `DownloadPayload` | `Sandbox::copy_in` / `copy_out` |

We reuse the `lamark.v1` proto. The transport changes (unix socket FD instead of TCP; pre-shared peer instead of mTLS), but the message types are the same `prost`-generated structs. **One protocol, two transports.** Documented as a non-negotiable consistency rule.

## Trace bundle flow

```
inside the sandbox:
  /sandbox/.lamark/traces/<rollout_id>/{manifest.json,trace.jsonl,payloads/}
                                                │
                                  Sandbox::copy_out on agent exit
                                                ▼
on the parent host:
  ~/.lamark/traces/<parent_rollout_id>/subagents/<rollout_id>/
                                                │
                                  reducer runs on parent
                                                ▼
                                  knowledge-base sync
```

The child knows nothing about the parent's trace dir. The parent's `coordinator` records a `SubagentSpawned { child_rollout_id }` event with `interaction_edges` to the child's events as they stream in. After `wait()`, the parent copies the bundle and records a `SubagentClosed { child_rollout_id, trace_bundle }` event with the local path.

This is the same shape codex's multi-agent v2 uses (plan/05a:12).

## § copy_out atomicity

`Sandbox::copy_out(src_dir, dst_dir)` must be atomic and verifiable. It follows this protocol:

1. **Read manifest and record hash.** Read `src_dir/manifest.json` as raw bytes. Compute `manifest_hash = SHA-256(manifest_bytes)` before touching `dst_dir`.

2. **Atomic per-file copy.** For each file path listed in the manifest's `files` array, copy from `src_dir/<file>` to `dst_dir/<file>` using the sequence: write to a sibling temp file (same directory, `.tmp.<random>` suffix) → `fsync` the temp file → `rename` temp file to the final destination. This is atomic on POSIX filesystems; partial writes to the final path are impossible.

3. **Post-copy manifest verification.** After all files have been copied, recompute `SHA-256(dst_dir/manifest.json bytes)` and compare to `manifest_hash`. If they differ, the copy is corrupt (interrupted rename, filesystem error, storage layer silent corruption). Delete `dst_dir` entirely and return `Err(CopyOutError::ManifestMismatch)`.

4. **Interrupted-copy recovery (detected on next run).** On startup (or before a fresh `copy_out`), if `dst_dir` exists but does not contain a `manifest.json` whose SHA-256 matches the expected hash (i.e., a previous invocation was interrupted mid-copy), treat the directory as partial: delete it unconditionally, then re-fetch the bundle from the sandbox or mark the rollout `status = aborted` if the sandbox is no longer reachable.

5. **Idempotency.** If `dst_dir/manifest.json` already exists and its SHA-256 matches `manifest_hash`, skip the copy entirely and emit a `tracing::debug!` log at level `TRACE`:
   ```
   copy_out: dst_dir already consistent with manifest_hash={hash}; skipping
   ```
   Return `Ok(())` immediately.

**Error type additions to `SandboxError`:**
```rust
pub enum CopyOutError {
    ManifestReadFailed(std::io::Error),
    FileCopyFailed { file: PathBuf, source: std::io::Error },
    ManifestMismatch { expected: [u8; 32], actual: [u8; 32] },
    PartialDirRemoved(PathBuf),
}
```

`CopyOutError` is wrapped inside `SandboxError::CopyOut(CopyOutError)` so callers can match on it without exposing the inner enum in the trait's public API surface.

## § Event stream reconnect

The event stream between an in-pod (or in-container, or forked-process) agent and the parent bridge uses monotonically increasing sequence numbers to enable gap-free reconnection.

**Sequence number contract:**
- Every event emitted by the in-sandbox agent has a `seq: u64` field. The first event of a session has `seq = 1`; each subsequent event increments by 1.
- The `seq` field is written into `trace.jsonl` alongside the event payload, making the trace independently verifiable for gaps.
- The parent bridge maintains `last_received_seq: u64` (starts at `0`; updated on every successfully received event).

**Reconnect handshake:**
- When the stream drops (TCP reset, WebSocket close, unix socket EOF), the bridge waits for the first backoff interval, then reconnects.
- After the transport connection is re-established, the bridge sends the following as the **first message** on the new connection:
  ```json
  { "kind": "ResumeFrom", "seq": <last_received_seq + 1> }
  ```
- The in-sandbox agent buffers the last **500 events** in a ring buffer in memory. On receiving `ResumeFrom(N)`, it replays all buffered events with `seq ≥ N` in order, then resumes normal emission.
- If `N < min_buffered_seq` (the oldest event in the ring buffer has a sequence number higher than `N`, i.e., the gap exceeds 500 events), the agent cannot replay. In this case:
  - The in-sandbox agent sends `{ "kind": "StreamGap", "min_available_seq": <min_buffered_seq> }`.
  - The bridge emits a `StreamGapEvent { agent_id, last_received_seq, min_available_seq }` to the parent.
  - The parent falls back to pulling the partial `trace.jsonl` directly via `Sandbox::copy_out` at completion time rather than streaming. In-flight progress is lost for the gap period but the final trace is complete.

**Reconnect backoff:**
Exponential backoff starting at 1 s, doubling on each attempt: 1 s, 2 s, 4 s, 8 s, capped at 30 s per attempt. After **5 consecutive reconnection failures** (transport cannot be established, regardless of the gap check):
- The bridge emits `SandboxError::StreamUnrecoverable { agent_id, attempts: 5 }`.
- The coordinator treats the subagent as dead: triggers zombie cleanup as specified in `plan/05a` (delete Pod / container / process, record `SubagentZombieKilled`).

**Implementation note:** sequence numbers are assigned by the in-sandbox event emitter, not the transport. The bridge must never re-number events; it forwards `seq` values as received. If an event arrives out of order (possible under unreliable transports), the bridge buffers it and delivers in-order. Out-of-order delivery beyond 50 positions is treated as a stream error.

## Budget enforcement

`Budget { max_tokens, max_seconds, max_tool_calls, max_memory, max_cpus }` is enforced **twice**:

1. **Child-side.** The child's `Session` is constructed from the spec; its config has the budget; its turn loop refuses to start a new turn when any budget is exhausted (`TurnStatus::IterationLimit` style).
2. **Parent-side.** `AgentHandle::wait()` is wrapped in `tokio::time::timeout(max_seconds + grace)`. On expiry, `interrupt()` is sent; after `interrupt_grace` (5s default), `kill()` is called. For Docker, that's `docker kill`; for in-process, the cancel token cascades.

Defense in depth: a buggy child that doesn't honor its budget still dies on the parent's timer.

## Secrets and identity

Every spawned agent has its own identity:

- A random per-agent `agent_id` (used in trace, in proto, and in container hostname).
- Its own scope-derived `RemoteScope`-like envelope (plan/13's terminology) that the policy engine reads when evaluating tool calls.
- Its own subset of secrets — `SecretScope::inherit_provider_keys = true` is the default; everything else must be allowlisted explicitly.

A compromised child can spend its budget, write to its workspace, and call the model provider. It **cannot** read the parent's API keys, read host paths outside its mount, or reach hosts outside its egress policy.

## Selecting a Sandbox

Resolution order in `runtime.rs`:

1. `AgentSpec.sandbox_override`, if set.
2. Else config `sandbox.default` (renamed from `terminal.backend` — see plan/03 update). **Default is `LocalSandbox`.** Safety lives in the policy layer (`Decision::Prompt` default for shell- and write-class tools, see plan/05 §"Approval flow" and `policy.toml`), not in the sandbox choice.
3. Else `LocalSandbox(in-process)` — uniform across dev and prod profiles. Users who want hard isolation (untrusted prompt, multi-tenant host, regulated environment) set `sandbox.default: docker` globally or pass `--sandbox docker` per invocation.

The coordinator (plan/05a) chooses per subagent: `LocalSandbox(in-process)` for trusted internal roles ("plan compactor", "summarizer"); `LocalSandbox(forked)` for prompt-derived subagents (parent-process protection via `prctl(PR_SET_PDEATHSIG)`); `DockerSandbox` only when the operator has explicitly opted into containerized children via `sandbox.agent_hosting.subagent_default: docker`.

## Plugin-host integration (plan/00c §provider-slots, plan/08)

The `Sandbox` slot in the plugin host registers third-party `Box<dyn Sandbox>` impls. The slot contract:

```rust
impl PluginHost {
    fn register_provider(&self, slot: ProviderSlot::Sandbox, provider: BoxedProvider);
}
```

A `lamark-sandbox-firecracker` plugin (hypothetical) ships a `FirecrackerSandbox` impl, registers in the slot, and is now selectable by name in config. The trait is the contract; the plugin is just another impl.

## Failure modes

| Failure | Detection | Recovery |
|---|---|---|
| Child panics | `wait()` returns non-zero `ExitStatus` | Parent emits `SubagentFailed`; coordinator decides retry |
| Docker daemon down | `health_check` fails | Refuse to spawn; emit `SandboxUnavailable`; parent falls back to LocalSandbox if config allows |
| SSH connection drops | `events()` channel closed before `SubagentResult` | Parent emits `SubagentLost`; reconnect attempted once, then fail |
| K8s API unreachable | `Pods.create()` 5xx or timeout | Refuse to spawn; emit `SandboxUnavailable`; bounded retry (3× with backoff). Never falls back to LocalSandbox in cluster mode — exposing the cluster operator's host is worse than failing the request |
| K8s Pod stuck `Pending` | `Pods.status.phase` not `Running` within `pending_timeout` (default 60s) | Emit `SandboxUnavailable { reason: pending_timeout }`; delete Pod; surface scheduler events (often `Insufficient cpu`/`memory`) in the error |
| K8s Pod evicted | `Pods.status.phase == Failed && reason == Evicted` | Treat as `SubagentLost`; coordinator decides retry on a fresh Pod |
| K8s NetworkPolicy missing default-deny | `lamark doctor` warns at startup | Refuse to use `EgressPolicy::ModelProviderOnly` (cluster cannot enforce); require operator to opt out explicitly with `sandbox.kubernetes.unsafe_no_default_deny: true` |
| Network egress blocked but child tries to call model | Child sees provider error; reports it as a tool failure | Parent observes via events; logs; budget continues |
| Budget exhausted child-side | Child emits `TurnComplete { status: IterationLimit }` | Parent treats as successful-but-truncated; usage recorded |
| Budget exhausted parent-side (child stuck) | `wait()` timeout fires | `interrupt()` → grace → `kill()`; emit `SubagentZombieKilled` |

## Tests

- **Trait conformance.** `lamark-sandbox` exposes a `conformance` module that any impl can run: spawn a tiny scripted child, drive it through 3 turns, assert events arrive in order, assert budget kills on overrun, assert trace bundle pulls back.
- **`tests/sandbox/local_in_process.rs`** — coordinator spawns 4 children, all complete; total events seen on parent matches sum of children.
- **`tests/sandbox/local_forked.rs`** — child crashes mid-turn; parent survives; `SubagentFailed` emitted; ParentDeathSignal honored (kill parent → child dies within 2s).
- **`tests/sandbox/docker.rs`** (integration; `LAMARK_E2E=1`) — full flow including egress allowlist; verify with `tinyproxy` logs that the only outbound was to the configured provider.
- **`tests/sandbox/kubernetes.rs`** (integration; `LAMARK_E2E=1`, `KUBECONFIG=…`, or `kind`/`minikube`) — full flow against a real cluster: create Pod, drive 3 turns, assert NetworkPolicy is in place, assert PVC is cleaned up. Skipped when cluster is unreachable; `lamark doctor` warns instead of failing CI.
- **`tests/sandbox/kubernetes_pending.rs`** — schedule a Pod with a node-selector that no node matches; assert `pending_timeout` triggers, scheduler events surface in the error.
- **`tests/sandbox/egress_deny.rs`** — child attempts to `curl evil.example`; egress proxy denies; child sees a network error.
- **`tests/sandbox/budget_kill.rs`** — child ignores `max_seconds`; parent timer kills it within `interrupt_grace + 1s`.
- **`tests/sandbox/proto_reuse.rs`** — the `lamark.v1` `Submission`/`Event` types serialize identically across the unix-socket transport used by the sandbox and the TCP transport used by Remote UI.

## § Rollback deferral for active sessions

When the forgetting probe (see training pipeline and knowledge-base interaction) triggers a LoRA adapter rollback, the rollback executor must not call the vLLM `unload_lora_adapter` API while sessions are actively using that adapter. Doing so would corrupt in-flight turns. The following protocol governs safe rollback:

**Step 1 — query active session count.**
Before unloading, the rollback executor calls the in-process provider endpoint:
```
GET /v1/adapters/{adapter_id}/active_sessions
→ { "count": N }
```

**Step 2 — defer if sessions are active.**
If `count > 0`, the rollback is deferred. Poll the same endpoint every **10 seconds** until one of the following is true:
- `count == 0` → proceed to unload (step 4).
- The wall-clock deadline `rollback_deadline` is reached → proceed to forced rollback (step 3). The default `rollback_deadline` is `now + 30 minutes`, configurable via `provider.vllm.rollback_deadline_minutes` (integer, default `30`).

**Step 3 — forced rollback at deadline.**
If `rollback_deadline` is reached while `count > 0`:
- Proceed with the unload regardless.
- In-flight sessions complete their **current turn** on the old adapter (the model server finishes the ongoing completion request). Subsequent turns for those sessions use the fallback base model until an adapter is reloaded.
- The rollback executor records `AdapterRolledBackForced { adapter_id, active_sessions_at_rollback: N }` to the knowledge-base.

**Step 4 — soft unload sequence (when vLLM supports drain mode).**
Before issuing the hard `unload_lora_adapter` call, attempt the soft sequence:
1. Set the adapter to `drain` mode via `POST /v1/adapters/{adapter_id}/drain`. In drain mode the model server stops assigning new sessions to this adapter but allows existing sessions to complete their current turn.
2. Wait for `count == 0` (polling every 10 s) or for `rollback_deadline`, whichever comes first.
3. Issue `DELETE /v1/adapters/{adapter_id}` (the hard unload).

If the vLLM API returns `404` or `405` on the drain endpoint (drain mode not supported by this version), skip the drain step entirely and issue the hard unload directly. Log the absence of drain support at `INFO` on first occurrence; do not warn repeatedly.

**Idempotency:** if `unload_lora_adapter` returns `404` (adapter already gone — e.g., vLLM was restarted), treat the rollback as successful. Log at `DEBUG`.

**Observability:** the rollback executor emits the following trace events at each state transition:
- `AdapterRollbackScheduled { adapter_id, trigger, deadline }`
- `AdapterRollbackDeferred { adapter_id, active_sessions: N, next_poll_in_secs: 10 }` (once per poll cycle while deferred)
- `AdapterRolledBack { adapter_id, drained_normally: bool }`
- `AdapterRolledBackForced { adapter_id, active_sessions_at_rollback: N }` (only on forced path)

## Cutover gate

Folded into P2 + P5:
- **P2 extension:** `LocalSandbox(in-process)` powers the vertical slice; `Sandbox` trait, `ProcessHandle`, `AgentHandle` all compile and pass trait-conformance tests.
- **P5 extension:** `DockerSandbox` works end-to-end. Spawning a subagent in Docker produces a trace bundle on the host. Egress policy `ModelProviderOnly` verifiably blocks `curl example.com` and allows the model endpoint.
- **P6:** `SshSandbox` alongside gateway integrations (same connection-management work).
- **P6 extension:** `KubernetesSandbox` end-to-end against a `kind` cluster in CI. Asserts Pod-per-subagent isolation, NetworkPolicy enforcement, PVC clone+cleanup, GPU passthrough (CI uses `nvidia/k8s-device-plugin` only when `LAMARK_E2E_GPU=1`).

## Open questions (recorded; not blocking)

- **Host-sandbox for `LocalSandbox`.** v0.1 ships naked — `LocalSandbox` **is** the default (no `--unsafe-local` gate). The policy layer (`Decision::Prompt` on every shell- and write-class tool by default) is the safety mechanism, not the sandbox. ADR-0008 records this decision. v0.2 plan adds optional landlock (Linux) / seatbelt (macOS) hardening behind a `host-sandbox` feature, surfaced as `sandbox.local.hardening: { off | landlock | seatbelt }` config.
- **Per-tool sandboxing.** Today the sandbox is per-session. Some use cases want per-tool (run only `Bash` in Docker; everything else in-process). Deferred to v0.2.
- **GPU passthrough for child agents.** Needed for vision/voice tools. Docker `--gpus` works; the config surface is the question. Deferred to v0.2.
