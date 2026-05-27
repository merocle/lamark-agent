# 19 — Multi-server fleet setup

> **Phase:** P6 (gateway + sandbox SSH/k8s) → P5 (coordinator).
> **One-liner:** An operator runs one Lamark session to provision, configure,
> and validate an arbitrary number of servers in parallel — templated configs,
> remote shell execution, per-host trace, async completion notifications, and
> a reusable fleet-setup skill that auto-improves after every run.

---

## North-star contribution

- **Domain quality (ops).** Replaces fragile ad-hoc shell scripts with a
  structured, auditable, re-runnable agent loop. Each host gets its own
  trace bundle so failures are attributable and replayable. Config templates
  are generated from facts recalled from previous fleet runs (OS version,
  package manager, role tags), so no "what did we install on that box?" archaeology.
- **Agent-side self-improvement.** Emits:
  - `memory_fact` writes per host: hostname → OS, role, installed-service
    inventory. Recalled in every future fleet operation for that host.
  - `skill_draft` candidate: if the same multi-step setup sequence (add user
    → install packages → write systemd unit → enable service) appears across
    ≥ 3 hosts in the run, Curator promotes it to a reusable skill.
  - `reinforce_signal=success` if all hosts reach the target state;
    `fail` if ≥ 1 host is left in an inconsistent state after retries.
- **Model-side self-improvement.** Emits one trace bundle per host (each a
  complete `RolloutStarted … TurnComplete` arc), plus a coordinator-level
  bundle for the orchestrating agent. All bundles cross-link via a shared
  `fleet_run_id`. Gives the trainer multi-rollout SFT samples covering:
  idempotent Bash, structured config generation, remote error diagnosis,
  retry planning.

---

## Idea

A platform engineer needs to bring up 40 application servers — fresh VMs
from different providers (some Ubuntu 22.04, some RHEL 9), all must reach
a baseline state (user accounts, firewall, Docker, Prometheus node-exporter)
then a role-specific state (api-server vs worker vs metrics). They run one
`lamark fleet setup --inventory fleet.yaml` command. Lamark spawns a
coordinator agent that fans out a worker sub-agent per host (via the SSH
sandbox), runs all hosts in parallel within a configurable concurrency cap,
collects results, and posts a final summary. Failures are diagnosed and
retried; unrecoverable hosts are flagged with structured evidence. The
operator is notified in Slack (gateway) as each host completes. After the
run a fleet-setup skill is promoted to `.lamark/skills/` so the next operator
can trigger the same sequence with one line.

---

## Actors

| Actor | Role |
|---|---|
| **Operator (Marcus)** | Platform engineer; provides `fleet.yaml` (host list + role tags + credentials pointer); watches Slack notifications; optionally approves destructive steps. |
| **Coordinator agent** | `lamark-coordinator` — reads inventory, fans out per-host sub-agents, tracks Kanban board, aggregates results (`plan/05a §"Coordinator"`, `plan/05b §"Kanban"`). |
| **Host worker agent** | One per host. Runs inside an SSH sandbox (`plan/05c §"SSH backend"`). Has its own SQ/EQ and trace recorder. |
| **SSH sandbox** | `lamark-sandbox` SSH backend — executes `Bash` tool calls on the remote host over a persistent multiplexed SSH connection (`plan/05c`). |
| **Gateway** | `lamark-gateway` Slack adapter — posts per-host status updates and the final fleet summary to Marcus's ops channel (`plan/09 §"Gateway"`). |
| **Knowledge-base** | Stores per-host memory facts and fleet-run summary; used by future sessions for recall. |
| **Trace recorder** | One instance per sub-agent, one for the coordinator. All bundles reference the shared `fleet_run_id`. |

---

## Trigger

```
$ lamark fleet setup --inventory fleet.yaml --concurrency 8 --notify slack:#ops-alerts
```

`fleet.yaml` schema:

```yaml
fleet_run_id: "2026-05-25-prod-baseline"
hosts:
  - host: 10.0.1.1
    role: api-server
    os: ubuntu-22.04
    ssh_key: ~/.ssh/prod_ed25519
  - host: 10.0.1.2
    role: worker
    os: rhel-9
    ssh_key: ~/.ssh/prod_ed25519
  # … 38 more
```

---

## Pipeline

### Step 0 — Bootstrap + inventory parse

1. `lamark fleet setup` subcommand (`plan/02 §"Subcommand surface"`).
2. `lamark-config` loads config; SSH key paths resolved against the user
   keyring or `--ssh-keys-dir`.
3. `SessionStart` hook fires; gateway adapter opens the Slack notification
   channel.
4. Coordinator agent starts: reads `fleet.yaml`, creates a Kanban board in
   KB (`POST /agents/{coordinator_id}/tasks`) with one task per host
   (`plan/05b §"Kanban board"`). Each task carries host metadata as context.

### Step 1 — Memory recall per host

5. For each host, coordinator queries `GET /memory/search?q=<hostname>` —
   recalls prior role assignment, last-known OS state, installed services.
   Recalled facts are passed as context to the per-host worker agent.
   First-time hosts get an empty context.

### Step 2 — Fan-out: spawn worker agents

6. Coordinator calls `sandbox.spawn_agent(host_config)` for up to
   `--concurrency 8` hosts simultaneously (`plan/05c §"spawn_agent"`).
7. Each worker agent gets its own SQ/EQ and SSH sandbox instance. Trace
   recorder opens `~/.lamark/traces/<fleet_run_id>/<host>/`.

### Step 3 — Per-host work loop (runs in parallel)

8. Worker agent reads the host's role task and emits a plan:
   `[probe_os, install_baseline, install_role_packages, write_configs, enable_services, validate]`.
9. **Probe**: `Bash("uname -a && cat /etc/os-release")` → resolves exact
   OS; policy is `Allow` (read-only remote probe). Result written as
   `memory_fact: {host, os_version, kernel}`.
10. **Baseline install**: agent generates a package-manager-correct install
    command from the recalled OS fact (apt vs dnf). `Bash("apt-get install -y
    …")` is `is_destructive=true` → `PermissionRequest`. Default policy in
    fleet mode is `Allow` for pre-declared package lists; unknown packages
    require `Prompt`.
11. **Config generation**: agent calls `Write` to produce `/etc/…` config
    files. Templates are recalled from KB skills if a matching fleet-setup
    skill exists; otherwise generated from scratch.
12. **Enable services**: `Bash("systemctl enable --now …")` — `Allow` for
    pre-declared services.
13. **Validate**: `Bash("systemctl is-active …")` + `Bash("curl -s
    localhost:9100/metrics | head -5")` — confirms state. Result is
    `validate_ok: true/false`.
14. Worker agent posts `TaskUpdate { status: completed/failed }` to
    coordinator Kanban. Gateway adapter posts per-host Slack notification.

### Step 4 — Coordinator aggregation

15. Coordinator polls Kanban until all tasks reach terminal state (or timeout
    per `--host-timeout` flag).
16. Builds a fleet summary: hosts OK / failed / timed-out; per-host evidence
    links to trace bundles.
17. For failed hosts: coordinator spawns a diagnosis sub-agent with the host's
    `trace.jsonl` as context; proposes a remediation task; re-queues if
    within retry budget.
18. Posts final summary to Slack via gateway.

### Step 5 — Trace flush + skill authoring

19. Each host's trace recorder flushes and reduces to `conversation.jsonl`.
20. KB client posts `POST /agents/{id}/traces` for each bundle.
21. Coordinator agent writes `memory_fact` for every successfully configured
    host: `{host, role, os, services_installed, timestamp}`.
22. If the setup sequence (steps 9–13) recurred identically across ≥ 3 hosts,
    coordinator drafts a skill `fleet-baseline-<os>.md` and writes it to
    `.lamark/skills/proposals/` for Curator review (`plan/08`).
23. `reinforce_signal` attached: `success` if all hosts in `validate_ok=true`;
    `partial` otherwise.

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0 (bootstrap, inventory) | `lamark`, `lamark-config`, `lamark-coordinator` | 02, 03, 05a |
| 1 (memory recall) | `lamark-memory`, `lamark-kb-client` | 07a |
| 2 (fan-out) | `lamark-coordinator`, `lamark-sandbox` (SSH) | 05a, 05c |
| 3 (per-host loop) | `lamark-core`, `lamark-tools`, `lamark-policy`, `lamark-hooks`, `lamark-sandbox` (SSH), `lamark-trace` | 05, 05c, 06 |
| 4 (aggregation, retry) | `lamark-coordinator`, `lamark-gateway` (Slack) | 05a, 09 |
| 5 (flush, skill draft) | `lamark-trace`, `lamark-kb-client`, `lamark-skills` | 06, 07a, 08 |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **SSH connection refused** at probe | Worker marks task `failed`; coordinator logs `{host, error: "connection_refused"}`; retries up to N times with backoff; escalates to Slack if still failing. |
| **Package install fails** (dependency conflict) | Tool returns non-zero exit; worker agent re-plans (tries alternative package name, then flags host as `needs_human`). |
| **Host times out** | Coordinator enforces `--host-timeout`; sends `Op::Interrupt` to worker SQ; worker closes bundle with `status=aborted`; host is flagged in summary. |
| **Concurrency cap reached** | Coordinator queues remaining hosts; they start as slots free. |
| **KB unreachable** | Memory recall falls back to SQLite spool; trace bundles queued in outbox. Fleet run is not blocked. |
| **Slack delivery fails** | Gateway retries with exponential backoff; falls back to TUI summary if Slack is still unreachable at run end. |
| **Partial fleet failure** | Summary clearly separates ok/failed/timed-out. Operator can re-run with `--hosts-file failed-hosts.txt` (derived from the previous summary). |

---

## Acceptance criteria

- [ ] `lamark fleet setup --inventory fleet.yaml --concurrency 8` fans out
  to all hosts and produces one trace bundle per host.
- [ ] Hosts with different OS (Ubuntu / RHEL) generate OS-correct install
  commands without operator intervention.
- [ ] A failing host is retried, then flagged in the Slack summary with
  a link to its trace bundle.
- [ ] Memory facts written for each successful host are recalled in a
  subsequent `lamark fleet status --inventory fleet.yaml` run.
- [ ] A fleet-baseline skill is drafted when the identical sequence occurs
  on ≥ 3 hosts.
- [ ] All bundles reference the same `fleet_run_id` in their `manifest.json`.
- [ ] Killing the coordinator with SIGINT during fan-out sends `Op::Interrupt`
  to all active worker SQs and closes all open bundles with `status=aborted`.

---

## Self-improvement assertions

1. **Per-host SFT samples.** Each host bundle reduces to ≥ 1 Nemotron-Agentic-v1
   entry covering remote `Bash` tool calls with structured stdout/stderr results.
2. **Cross-host skill promotion.** If the same step sequence appears in ≥ 3
   bundles in one `fleet_run_id`, Curator (scenario #05 flow) finds the pattern
   and promotes a draft skill within the weekly sweep.
3. **Memory recall improves second run.** A second fleet run against the same
   inventory uses recalled OS facts to skip the probe step's parse logic and
   goes straight to install — verifiable by comparing trace lengths.
4. **Reinforce signal propagates.** The fleet summary's `success/partial/fail`
   is attached as `reinforce_signal` to each bundle; KB reweights these
   trajectories for the trainer.

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| `fleet setup` subcommand | plan/02 §"Subcommand surface" | _audit_ |
| Coordinator fan-out + Kanban tasks | plan/05a §"Coordinator", plan/05b §"Kanban" | _audit_ |
| SSH sandbox backend | plan/05c §"SSH backend" | _audit_ |
| `spawn_agent` + trace pull-back | plan/05c §"spawn_agent" | _audit_ |
| Per-host SQ/EQ + trace recorder | plan/05, plan/06 | _audit_ |
| Memory recall + KB write | plan/07a | _audit_ |
| Skill draft on pattern detection | plan/08 §"Skill system" | _audit_ |
| Slack gateway notifications | plan/09 §"Gateway" | _audit_ |
| Concurrency cap + host timeout | plan/05a §"Coordinator" | _audit_ |
| Trace bundle cross-link via `fleet_run_id` | plan/06 §"Trace recorder" | _audit_ |

---

## Open questions

1. **Inventory schema ownership.** Should `fleet.yaml` be a first-class
   `lamark-config` type or an opaque JSON/YAML blob passed to the coordinator?
   Strongly-typed inventory allows policy rules scoped to host roles.
2. **SSH credential handling.** Where do host SSH keys live relative to
   `policy.toml`? Leaking a key path into a trace bundle is a privacy issue.
3. **Per-host approval in headless mode.** If the operator is not watching
   the TUI, how are `Prompt`-class operations handled? Auto-deny (safe) or
   auto-allow from a pre-declared allowlist per role?
4. **Trace storage at scale.** 40 hosts × N turns = potentially many GiB in
   `~/.lamark/traces/`. Auto-prune after KB upload? Configurable retention?
5. **Partial re-run UX.** How does `--hosts-file failed-hosts.txt` work —
   does it resume the original `fleet_run_id` or create a new one with a
   parent reference?
