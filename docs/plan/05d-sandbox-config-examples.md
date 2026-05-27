# 05d — Sandbox config examples

> Worked configs for every sandbox backend. Companion to [05c](./05c-sandbox-and-agent-hosting.md)
> (trait + behavioral spec). When you copy an example, replace anything in
> `<angle-brackets>`. Each example has a **minimum** form (gets you running)
> and a **recommended** form (what to ship to a colleague or to production).

## Quick chooser

| Use case | Pick |
|---|---|
| I want to try Lamark on my laptop, right now | [`local`](#local) |
| I want a single-host install with isolation per session | [`docker`](#docker) |
| I want to run the agent on a remote build server I own | [`ssh`](#ssh) |
| I'm shipping Lamark to a fleet, multi-tenant, with proper RBAC and quotas | [`kubernetes`](#kubernetes) |
| I want serverless burst compute, hibernates when idle | [`modal`](#modal) (plugin) |
| I want a persistent cloud dev container | [`daytona`](#daytona) (plugin) |
| I'm running on an HPC cluster | [`singularity`](#singularity) (plugin) |
| Edge / sandboxed function-runtime | [`vercel-sandbox`](#vercel-sandbox) (plugin) |

If none of these fit, see [05c §"Plugin-host integration"](./05c-sandbox-and-agent-hosting.md#plugin-host-integration-plan00c-provider-slots-plan08) for writing a custom sandbox plugin.

---

## Profile shape (recap)

Every example below goes into `~/.lamark/config.yaml` (or a profile-specific `~/.lamark/profiles/<name>/config.yaml`) under the `sandbox:` key. The top-level shape is fixed:

```yaml
sandbox:
  default: <backend-name>           # which backend the next session uses
  <backend-name>:                   # per-backend config block
    …
  agent_hosting:                    # how subagents are spawned (plan/05a)
    subagent_default: <mode>
    trusted_role_default: <mode>
  lifetime_seconds: 600             # global wall-clock budget per session; override per agent
```

Any backend you don't intend to use can be omitted — the key just needs to exist when `default` references it.

---

## `local`

> Dev / try-it tier. No infrastructure. Permission-first policy is the safety layer.

### Minimum

```yaml
sandbox:
  default: local
```

That's the entire config. Lamark starts in-process, runs tools on your host, gates risky operations through `Decision::Prompt`.

### Recommended (dev profile)

```yaml
sandbox:
  default: local
  local:
    workspace_root: ~/code           # default cwd for sessions
    hardening: off                   # off | landlock | seatbelt (v0.2)
    forked_subagents: true           # untrusted prompts spawn forked-process children
  agent_hosting:
    subagent_default: forked         # coordinator children isolated by process boundary
    trusted_role_default: in-process # summarizer / compactor share parent process
  lifetime_seconds: 1800             # 30-minute soft cap per session
```

**When to leave `local`:** when you're about to let an untrusted prompt run arbitrary tool calls *with* `auto-approve` rules in your `policy.toml` (i.e. you've turned off the safety layer). At that point, switch to `docker` or `kubernetes`.

---

## `docker`

> Single-host production / air-gapped / "I want every session in its own container."

### Minimum

```yaml
sandbox:
  default: docker
  docker:
    image: lamark/runner:0.1.0
```

Lamark expects the Docker daemon on the host. First run pulls the runner image; subsequent runs reuse it.

### Recommended (single-host prod)

```yaml
sandbox:
  default: docker
  docker:
    image: lamark/runner:0.1.0
    workspace_mount: copy-on-write    # fresh | read-only | copy-on-write
    workspace_path: ~/code            # source mount when copy-on-write
    egress: model-provider-only       # none | model-provider-only | allowlist
    egress_allowlist:                 # only when egress: allowlist
      - api.anthropic.com
      - api.openai.com
      - api.github.com
    cpu_limit: "2"                    # docker --cpus
    memory_limit: 4g                  # docker --memory
    tmpfs_workspace_size: 2g
    tmpfs_tmp_size: 1g
    keep_alive_seconds: 0             # 0 = remove on session end; >0 = pool warm containers
    user_namespace_remap: true        # daemon must have userns-remap=default
  agent_hosting:
    subagent_default: docker          # each child agent in its own container
    trusted_role_default: in-process  # except trusted roles
  lifetime_seconds: 1800
```

**Caveats:**
- `docker.egress: model-provider-only` requires the `tinyproxy` sidecar (ships in `lamark/runner` image).
- `workspace_mount: copy-on-write` populates a Docker volume from a snapshot of `workspace_path` and removes it on session close — your source tree is never mounted writable.
- `user_namespace_remap: true` is strongly recommended; without it, container `uid=1000` maps to host `uid=1000` (your user).

---

## `ssh`

> "Run this agent on the build server I own."

### Minimum

```yaml
sandbox:
  default: ssh
  ssh:
    host: build.example.com
```

Lamark uses your `~/.ssh/config` for auth. The remote host must have `lamark` installed and on PATH.

### Recommended (remote build server)

```yaml
sandbox:
  default: ssh
  ssh:
    host: build.example.com
    user: lamark-runner               # dedicated unprivileged account
    port: 22
    identity_file: ~/.ssh/lamark_ed25519
    known_hosts_file: ~/.ssh/known_hosts_lamark
    strict_host_key_checking: yes
    control_master: auto              # reuse one TCP connection
    control_path: ~/.ssh/lamark-cm-%C
    control_persist: 600
    workspace_mount: fresh            # fresh = mktemp -d; or "user-home"
    remote_lamark_path: /usr/local/bin/lamark
    min_remote_version: 0.1.0         # refuses to start if remote drifts > 1 minor
  lifetime_seconds: 3600
```

**Caveats:**
- `EgressPolicy` cannot be enforced from the lamark side on a remote shell. Document this for the operator; use `ssh` only on hosts you control or hosts that already have their own egress controls.
- If the remote host runs `lamark gateway` on its own (multi-user shared box), prefer `kubernetes` — `ssh` doesn't multi-tenant cleanly.

---

## `kubernetes`

> **Recommended production default.** Pod-per-subagent, NetworkPolicy egress, namespace RBAC, declarative resource quotas.

### Minimum (works against a `kind` cluster on your laptop)

```yaml
sandbox:
  default: kubernetes
  kubernetes:
    namespace: lamark-agents
    image: lamark/runner:0.1.0
```

Lamark resolves the kubeconfig in order: explicit `kubeconfig` field → `$KUBECONFIG` env → `~/.kube/config` → in-cluster ServiceAccount. The namespace must exist and the resolved identity needs the RBAC manifest below.

### Recommended (multi-tenant production)

```yaml
sandbox:
  default: kubernetes
  kubernetes:
    kubeconfig: ~                     # null = use cluster discovery order above
    namespace: lamark-agents-<tenant> # per-tenant namespace; lamark refuses to cross
    service_account: lamark-runner
    image: lamark/runner:0.1.0
    image_pull_secret: ghcr-pull
    workspace_mount: copy-on-write    # requires CSI snapshot+clone (lamark doctor checks)
    workspace_source_pvc: lamark-tenant-src
    egress: model-provider-only       # enforced via NetworkPolicy (default-deny required)
    egress_cidrs:                     # used to build the NetworkPolicy
      - 0.0.0.0/0                     # collapsed to provider CIDRs in practice
    pending_timeout: 60s              # Pod must reach Running within this window
    resource_requests:
      cpu: 500m
      memory: 1Gi
    resource_limits:
      cpu: "2"
      memory: 4Gi
    gpu_limit: 0                      # set to 1 to allocate one GPU
    gpu_node_selector:                # only used when gpu_limit > 0
      nvidia.com/gpu.product: H100-SXM5-80GB
    pod_labels:
      app.lamark.dev/tier: production
      app.lamark.dev/team: platform
    pod_annotations:
      app.lamark.dev/cost-center: "<your-allocation-tag>"
    controller_endpoint: wss://lamark-controller.lamark-agents.svc:7443
    controller_ca_bundle: /etc/lamark/controller-ca.pem
  agent_hosting:
    subagent_default: kubernetes      # each child agent → own Pod
    trusted_role_default: in-process  # summarizer/compactor stay in parent
  lifetime_seconds: 1800
```

### Cluster manifests (apply once per tenant namespace)

#### `Namespace` + default-deny `NetworkPolicy`

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: lamark-agents-acme
  labels:
    app.lamark.dev/tenant: acme
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny
  namespace: lamark-agents-acme
spec:
  podSelector: {}                       # selects all Pods in the namespace
  policyTypes: [Ingress, Egress]
  ingress: []                           # no Pod accepts ingress by default
  egress:                               # only kube-dns by default
    - to:
        - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: kube-system } }
          podSelector:       { matchLabels: { k8s-app: kube-dns } }
      ports: [{ port: 53, protocol: UDP }, { port: 53, protocol: TCP }]
```

#### `ServiceAccount` + `Role` + `RoleBinding`

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: lamark-runner
  namespace: lamark-agents-acme
automountServiceAccountToken: false
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: lamark-runner
  namespace: lamark-agents-acme
rules:
  - apiGroups: [""]
    resources: [pods, pods/log, pods/exec, configmaps, secrets, persistentvolumeclaims]
    verbs: [get, list, watch, create, delete]
  - apiGroups: [networking.k8s.io]
    resources: [networkpolicies]
    verbs: [get, list, watch, create, delete]
  - apiGroups: [snapshot.storage.k8s.io]
    resources: [volumesnapshots]
    verbs: [get, list, watch, create, delete]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: lamark-runner
  namespace: lamark-agents-acme
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: lamark-runner
subjects:
  - kind: ServiceAccount
    name: lamark-runner
    namespace: lamark-agents-acme
```

#### `ResourceQuota` + `LimitRange` (recommended)

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: lamark-agents-quota
  namespace: lamark-agents-acme
spec:
  hard:
    pods: "50"
    requests.cpu: "20"
    requests.memory: 40Gi
    limits.cpu: "40"
    limits.memory: 80Gi
    requests.nvidia.com/gpu: "0"
---
apiVersion: v1
kind: LimitRange
metadata:
  name: lamark-agents-default
  namespace: lamark-agents-acme
spec:
  limits:
    - type: Container
      default:
        cpu: "1"
        memory: 1Gi
      defaultRequest:
        cpu: 250m
        memory: 512Mi
      max:
        cpu: "4"
        memory: 8Gi
```

#### Controller deployment (optional; for the SQ/EQ relay)

When the parent process runs *outside* the cluster (e.g. a developer's laptop driving cluster-hosted children), you need a controller in-cluster that brokers the SQ/EQ stream:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: lamark-controller
  namespace: lamark-agents-acme
spec:
  replicas: 2
  selector: { matchLabels: { app.lamark.dev/role: controller } }
  template:
    metadata:
      labels: { app.lamark.dev/role: controller }
    spec:
      serviceAccountName: lamark-runner
      containers:
        - name: controller
          image: lamark/controller:0.1.0
          args: ["controller", "serve", "--bind", "0.0.0.0:7443"]
          ports: [{ name: wss, containerPort: 7443 }]
          env:
            - { name: LAMARK_CONTROLLER_TLS_CERT, value: /etc/tls/tls.crt }
            - { name: LAMARK_CONTROLLER_TLS_KEY,  value: /etc/tls/tls.key }
          volumeMounts:
            - { name: tls, mountPath: /etc/tls, readOnly: true }
      volumes:
        - name: tls
          secret: { secretName: lamark-controller-tls }
---
apiVersion: v1
kind: Service
metadata:
  name: lamark-controller
  namespace: lamark-agents-acme
spec:
  selector: { app.lamark.dev/role: controller }
  ports: [{ port: 7443, targetPort: 7443, name: wss }]
```

The controller's TLS cert (`lamark-controller-tls`) should be issued by your cluster's cert-manager or fed in from outside.

### Verifying

```bash
lamark doctor                              # checks kubeconfig + RBAC + NetworkPolicy + CSI
lamark sandbox kubernetes test             # spawns an empty Pod, runs 3 turns, tears down
kubectl -n lamark-agents-acme get pods     # watch them come and go
```

---

## `modal` *(plugin)*

> Serverless burst compute. Hibernates between sessions; near-zero idle cost.

```yaml
sandbox:
  default: modal
  modal:
    app_name: lamark-agents             # one Modal app per profile
    image: lamark/runner:0.1.0          # mirrored to Modal at first use
    cpu: 2.0
    memory_mb: 4096
    gpu: null                           # "T4" | "A10G" | "A100" | "H100"
    region: us-east
    keep_warm: 0                        # number of pre-warmed containers
    idle_timeout_seconds: 60
    workspace_mount: copy-on-write
    egress: model-provider-only
    secret_names:                       # Modal-managed secrets to mount
      - lamark-model-keys
```

Requires the `lamark-sandbox-modal` plugin and `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` env vars.

---

## `daytona` *(plugin)*

> Persistent cloud dev containers. Each session keeps the workspace between turns.

```yaml
sandbox:
  default: daytona
  daytona:
    api_url: https://daytona.example.com
    api_token_env: DAYTONA_TOKEN
    workspace_template: lamark-runner-v1
    region: us-east-1
    persistent: true                    # workspace survives session end
    idle_hibernate_seconds: 1800
    egress: allowlist                   # Daytona controls egress per workspace
    egress_allowlist:
      - api.anthropic.com
      - github.com
```

Requires the `lamark-sandbox-daytona` plugin.

---

## `singularity` *(plugin)*

> HPC clusters: rootless, file-based image format, Slurm-friendly.

```yaml
sandbox:
  default: singularity
  singularity:
    image_path: /opt/lamark/runner.sif
    bind_mounts:                        # host → container
      - /scratch/$USER:/workspace
      - /data/shared:/data:ro
    enable_nv: true                     # --nv flag for GPU
    enable_rocm: false
    cleanenv: true                      # don't inherit host env
    slurm:                              # optional: wrap each agent in sbatch
      enabled: false
      partition: gpu
      account: lamark-eval
      time_minutes: 30
      qos: normal
```

Requires the `lamark-sandbox-singularity` plugin and `singularity` on PATH (or `apptainer`).

---

## `vercel-sandbox` *(plugin)*

> Edge function-style sandboxing.

```yaml
sandbox:
  default: vercel-sandbox
  vercel_sandbox:
    api_token_env: VERCEL_TOKEN
    region: iad1                        # Vercel region
    runtime: nodejs22                   # not used by lamark but required by Vercel
    max_duration_seconds: 900
    memory_mb: 1024
    egress: allowlist
    egress_allowlist:
      - api.anthropic.com
```

Requires the `lamark-sandbox-vercel` plugin.

---

## Multi-tier example: dev → staging → prod

A single repo can ship three profiles that switch between sandboxes by name. Drop these in `~/.lamark/profiles/{dev,staging,prod}/config.yaml`:

### `~/.lamark/profiles/dev/config.yaml`

```yaml
profile: dev
sandbox:
  default: local
  local:
    workspace_root: ~/code/lamark
    hardening: off
    forked_subagents: true
  agent_hosting:
    subagent_default: forked
    trusted_role_default: in-process
  lifetime_seconds: 3600
```

### `~/.lamark/profiles/staging/config.yaml`

```yaml
profile: staging
sandbox:
  default: docker
  docker:
    image: ghcr.io/example/lamark-runner:staging
    workspace_mount: copy-on-write
    workspace_path: ~/code/lamark
    egress: model-provider-only
    cpu_limit: "2"
    memory_limit: 4g
  agent_hosting:
    subagent_default: docker
    trusted_role_default: in-process
  lifetime_seconds: 1800
```

### `~/.lamark/profiles/prod/config.yaml`

```yaml
profile: prod
sandbox:
  default: kubernetes
  kubernetes:
    namespace: lamark-agents-prod
    service_account: lamark-runner
    image: ghcr.io/example/lamark-runner:prod
    image_pull_secret: ghcr-pull
    workspace_mount: copy-on-write
    workspace_source_pvc: lamark-prod-src
    egress: model-provider-only
    resource_requests: { cpu: 500m, memory: 1Gi }
    resource_limits:   { cpu: "2",  memory: 4Gi }
    pending_timeout: 60s
    controller_endpoint: wss://lamark-controller.lamark-agents-prod.svc:7443
  agent_hosting:
    subagent_default: kubernetes
    trusted_role_default: in-process
  lifetime_seconds: 1800
```

Switch via `lamark --profile dev|staging|prod` (or `LAMARK_PROFILE=prod lamark ...`). The `active_profile` file stickies the last used choice.

---

## Common knobs across all backends

| Knob | Semantics | Default |
|---|---|---|
| `egress` | `none` \| `model-provider-only` \| `allowlist` | `model-provider-only` |
| `egress_allowlist` | List of hostnames (string match) or CIDRs (k8s only) | `[]` |
| `workspace_mount` | `fresh` \| `read-only` \| `copy-on-write` | `fresh` |
| `workspace_path` / `workspace_source_pvc` | source for `copy-on-write` | — |
| `lifetime_seconds` | global per-session wall-clock kill | `600` |
| `agent_hosting.subagent_default` | how the coordinator spawns children | `forked` (local), `docker` (docker), `kubernetes` (k8s) |
| `agent_hosting.trusted_role_default` | summarizer / compactor isolation | `in-process` |

When a backend doesn't support a knob (e.g. `ssh` and `egress`), the value is silently ignored with a warning at `lamark doctor`.

---

## Where this connects to the rest of the plan

- The Sandbox trait + behavior: [05c](./05c-sandbox-and-agent-hosting.md).
- How config loads (precedence, profile resolution): [03](./03-layer-2-config-bootstrap.md) + [00c §13](./00c-hermes-deepdive-addendum.md).
- How subagents are spawned via Sandbox: [05a](./05a-coordinator-multi-agent.md).
- Upstream-proxy pattern (the per-backend egress story): [00d §28](./00d-claude-code-deepdive-addendum.md).
- The plugin contract for adding more backends: [08 §"Plugin host"](./08-layer-7-skills-plugins-curator.md) + [00d §22](./00d-claude-code-deepdive-addendum.md).
