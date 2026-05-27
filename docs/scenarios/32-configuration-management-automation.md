# 32 — Configuration Management Automation

> **Phase:** P5–P6 (coordinator, sandbox) + P8 (monitoring).
> **One‑liner:** Lamark treats configuration as code, automatically validates,
> synchronizes, and audits configuration across environments, generating
> compliance reports and triggering rollbacks when drift is detected.

---

## North‑star contribution

- **Domain quality (infrastructure operations).** Replaces manual, error‑prone
  configuration management with a **self‑healing, auditable system** that:
  - Enforces declarative configuration as code across clouds, clusters, and
    services.  
  - Detects drift in real time, rolls back unauthorized changes, and notifies
    stakeholders.  
  - Generates compliance reports for auditors automatically.  
- **Agent‑side self‑improvement.** Emits:
  - `memory_fact` writes: `config:<path> → desired_state, current_state,
    drift_timestamp, resolution_status`. Recalled by future config agents to
    prioritize high‑risk drifts.  
  - `skill_draft` candidates: reusable config‑validation templates (e.g.,
    “ensure `ssl_protocols` includes `TLSv1.3`”) promoted by Curator after ≥ 3
    uses.  
  - `reinforce_signal=success` when a drift is detected and automatically
    rolled back; `fail` when drift persists or rollback fails.  
- **Model‑side self‑improvement.** Configuration drift traces become SFT
  samples (desired_state → drift → remediation). DPO pairs arise when a
  previously rejected rollback is later applied after a rule update.

---

## Idea

Infrastructure engineers must keep configuration consistent across dozens of
servers, containers, and cloud services. Today this is done manually, leading
to drift, security gaps, and audit failures. Lamark replaces this with a
fully automated loop:

### Step 0 — Policy ingestion & baseline creation

1. **Policy Ingestion Agent** reads declarative config policies from
   `policy/` (e.g., `policy/ssh-hardening.yml`, `policy/k8s-namespace.yml`).  
2. Queries `GET /knowledge/search?q=config‑baseline` — loads the current
   baseline state stored in KB for each managed resource (e.g., `nginx.conf`,
   `docker-compose.yml`).  
3. Creates a `ConfigTask` on the Kanban board:
   `{resource, desired_state, baseline_version, environment}`.

### Step 1 — Drift detection & synchronization

4. **Config Agent** periodically (or on webhook) reads the current state of
   each resource (e.g., `GET /nginx/vhosts` for Nginx configs, `kubectl get
   configmap my‑app-config`).  
5. Compares current state to the desired state stored in KB.  
   - If **identical** → no action.  
   - If **drift detected** → proceeds to remediation.  

### Step 2 — Automated remediation

6. For each drifted resource, the agent:
   - Generates a **remediation manifest** (e.g., `PUT /api/config` payload,
     `kubectl apply -f configmap.yaml`).  
   - Validates the manifest against a **policy schema** (e.g., `policy-schema.json`).  
   - Executes the remediation via appropriate tool (`HttpTool`, `KubeTool`).  
   - If remediation succeeds, updates the resource’s state in KB.  
   - If remediation fails, posts a detailed error to Slack and creates a
     `RollbackTask`.  

### Step 3 — Audit & compliance reporting

7. **Audit Agent** compiles a **compliance bundle**:
   - Original desired state  
   - Drifted state (diff)  
   - Remediation manifest and execution log  
   - Timeline of events (timestamped)  
   - Links to trace bundles for each operation.  
2. Posts the bundle to `POST /knowledge/config-audits/<resource_id>`.  
3. Writes `memory_fact`: `{resource, drift_detected=true, resolved=true,
   resolution_timestamp, remediation_status}`.  

### Step 4 — Compliance reporting & stakeholder notification

4. **Compliance Agent** schedules periodic reports (e.g., nightly) that:
   - Summarize all resources, their drift status, and remediation outcomes.  
   - Highlight any **critical drifts** (e.g., security‑relevant settings).  
   - Generate a PDF/HTML report and post it to `#compliance‑reports` Slack
     channel.  
5. If a **critical drift** is detected (e.g., `ssl_protocols` missing `TLSv1.3`),
   the agent immediately posts an urgent alert and may trigger an automatic
   rollback if policy permits.  

### Step 5 — Knowledge‑base sync & reinforcement

6. All drift events, remediation logs, and compliance reports are stored in
   KB (`POST /knowledge/config-audits`).  
7. `reinforce_signal=success` is attached when a drift is resolved; `fail` when
   remediation fails or a drift persists.  
8. The `ConfigTask` status is updated (`completed`, `failed`, `rollback_needed`).  

---

## Actors

| Actor | Role |
|---|---|
| **Config Coordinator** | `lamark-coordinator` sub‑agent. Owns the config drift Kanban board, orchestrates ingestion, drift detection, and remediation. |
| **Config Agent** | Executes drift detection, generates remediation manifests, and executes fixes via tools. |
| **Audit Agent** | Creates compliance bundles, stores them in KB, posts to Slack. |
| **Remediation Agent** | Executes config changes via `HttpTool`, `KubeTool`, or `SshTool`. |
| **Compliance Agent** | Generates periodic compliance reports and posts to Slack. |
| **Gateway** | Slack adapter — posts drift alerts, remediation confirmations, and compliance reports. |
| **Knowledge‑base** | Stores desired configs, drift diffs, remediation logs, and compliance reports. |
| **Stakeholders** | Review compliance reports, approve remediation actions via Slack reactions. |

---

## Trigger

1. **Periodic polling** – the Config Agent runs on a schedule (e.g., `0 */6 * * *` → every 6 hours).  
2. **Manual trigger** – an engineer can run:  
   ```
   $ lamark config validate --resource nginx --environment prod
   ```
   to force a validation of a specific resource.  

Both paths create a `ConfigTask` on the Kanban board.

---

## Pipeline

### Step 0 — Bootstrap & policy load

1. Coordinator reads `GET /knowledge/search?q=config‑policy` — loads all
   declarative policies (e.g., `policy/ssh-hardening.yml`, `policy/k8s-namespace.yml`).  
2. Stores each policy in KB under `policy/<id>` and creates a cache for fast
   matching.  

### Step 1 — Drift detection

3. Config Agent queries the current state of each managed resource via
   appropriate connectors (`HttpTool`, `KubeTool`, `SshTool`).  
4. Compares current state to the desired state stored in KB.  
5. If **drift** is detected, creates a `ConfigDriftTask` on the Kanban board
   with fields: `{resource, desired_state, drifted_state, timestamp}`.  

### Step 2 — Remediation manifest & execution

4. For each drifted resource, the agent:
   - Generates a remediation manifest (e.g., updated config file, kubectl
     manifest, or API call payload).  
   - Validates the manifest against the policy schema (`policy-schema.json`).  
   - Executes the remediation (e.g., `HttpTool::Post`, `KubeTool::Apply`).  
   - On success, updates the desired state in KB; on failure, creates a
     `RollbackTask`.  

### Step 3 — Audit generation

5. Audit Agent compiles a **compliance bundle** containing:
   - Original desired state  
   - Drifted state (diff)  
   - Remediation manifest and execution log  
   - Timeline of events (timestamped)  
   - Links to trace bundles for each operation  
   - `reinforce_signal` (`success`/`fail`)  
6. Posts the bundle to `POST /knowledge/config-audits/<resource_id>` and writes
   `memory_fact`: `{resource, drift_detected=true, resolved=true,
   resolution_timestamp, remediation_status}`.  

### Step 4 — Compliance reporting & stakeholder notification

7. Compliance Agent schedules periodic reports (e.g., nightly) that:
   - Summarize all resources, their drift status, and remediation outcomes.  
   - Highlight any **critical drifts** (e.g., `ssl_protocols` missing `TLSv1.3`).  
   - Generate a PDF/HTML report and post to `#compliance‑reports`.  
8. If a **critical drift** is detected, the agent immediately posts an urgent
   alert to Slack and may trigger an automatic rollback if policy permits.  

### Step 5 — Knowledge‑base sync & reinforcement

9. All drift events, remediation logs, and compliance reports are stored in
   KB via `POST /knowledge/config-audits`.  
10. `reinforce_signal=success` is attached when a drift is resolved; `fail` when
   remediation fails or a drift persists.  
11. ConfigTask status is updated (`completed`, `failed`, `rollback_needed`).  

---

## Actors

| Actor | Role |
|---|---|
| **Config Coordinator** | `lamark-coordinator` sub‑agent. Owns the config drift Kanban board, orchestrates ingestion, drift detection, and remediation. |
| **Config Agent** | Executes drift detection, generates remediation manifests, and executes fixes via tools. |
| **Audit Agent** | Creates compliance bundles and stores them in KB. |
| **Remediation Agent** | Executes config changes via `HttpTool`, `KubeTool`, or `SshTool`. |
| **Compliance Agent** | Generates periodic compliance reports and posts to Slack. |
| **Gateway** | Slack adapter – posts drift alerts, remediation confirmations, and compliance reports. |
| **Knowledge‑base** | Stores desired configs, drift diffs, remediation logs, and compliance reports. |
| **Stakeholders** | Review compliance reports, approve remediation actions via Slack reactions. |

---

## Trigger

1. **Periodic polling** – the Config Agent runs on a schedule (e.g., `0 */6 * * *` → every 6 hours).  
2. **Manual trigger** – an engineer can run:  
   ```
   $ lamark config validate --resource nginx --environment prod
   ```
   to force a validation of a specific resource.  

Both paths create a `ConfigTask` on the Kanban board.

---

## Pipeline

### Step 0 — Bootstrap & policy loading

1. Coordinator loads all policy files from `policy/` into a fast matcher cache
   (`plan/05 §"Policy registry"`).  
2. Each policy is stored in KB as a versioned document (`plan/05a §"Coordinator"`).  

### Step 1 — Drift detection

3. Config Agent queries current state of each managed resource via
   connectors (`HttpTool`, `KubeTool`, `SshTool`).  
4. Compares current state to the desired state stored in KB.  
5. If **drift** is detected, creates a `ConfigDriftTask` on the Kanban board.  

### Step 2 — Remediation & execution

4. For each drifted resource, the agent:
   - Generates a remediation manifest (e.g., updated config file, kubectl
     manifest, or API call payload).  
   - Validates the manifest against the policy schema.  
   - Executes the remediation via `HttpTool`, `KubeTool`, or `SshTool`.  
   - On success, updates the desired state in KB; on failure, creates a
     `RollbackTask`.  

### Step 3 — Audit generation & notification

5. Audit Agent compiles a compliance bundle and posts it to KB.  
6. Gateway posts a Slack message summarizing the drift and remediation.  

### Step 4 — Reinforcement & status update

7. `reinforce_signal` is set to `success` on successful remediation; `fail` on
   failure.  
8. Kanban task status is updated (`completed`, `failed`, `rollback_needed`).  

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| Bootstrap & policy loading | `lamark-config`, `lamark-config` | 03 |
| Drift detection (connectors) | `lamark-tools` (HttpTool, KubeTool, SshTool) | 05c |
| Remediation execution | `lamark-tools` (HttpTool, KubeTool, SshTool), `lamark-policy` | 05c, 06 |
| Audit bundle creation | `lamark-trace`, `lamark-kb-client` | 06, 07a |
| Compliance reporting | `lamark-gateway` (Slack) | 09 |
| Knowledge‑base storage | `lamark-kb-client` | 07a |
| Failure handling & escalation | `plan/11 §"build-test-deploy"` | _audit_ |
| Reinforce‑signal handling | `lamark-policy` | 06 |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Policy file syntax error** | Coordinator aborts startup and logs an error; the broken policy is marked `invalid` in KB; deployment halted until fixed. |
| **Drift detection timeout** | Agent retries with exponential back‑off; on final failure, posts a Slack alert “⚠️ Drift detection failed – manual inspection required”. |
| **Remediation execution fails** | Agent retries 2× with exponential back‑off; on final failure, posts a Slack alert “⚠️ Remediation failed – manual intervention required”. |
| **Compliance report generation fails** | Writes to local outbox; retries every 30 s; trace bundle remains available for later upload. |
| **Critical drift persists** | After configurable retries, system creates a `CriticalDriftAlert` task that pings the security team via Slack. |
| **Knowledge‑base write fails** | Writes to local outbox; retries every 30 s; trace bundle remains available for later upload. |

---

## Acceptance criteria

- [ ] All managed resources are periodically checked for drift against their
  desired state.  
- [ ] Any drift is automatically remediated or escalated to a rollback task.  
- [ ] Every drift event generates a compliance audit bundle stored in KB.  
- [ ] Slack notifications are sent for drift detection, remediation success,
  and failure.  
- [ ] Critical drifts trigger immediate alerts and possible automatic rollback.  
- [ ] All drift events, remediation logs, and compliance reports are stored in
  KB for audit.  
- [ ] `reinforce_signal` correctly reflects success/failure and is consumed by
  the trainer.  
- [ ] Periodic polling runs automatically without manual intervention.  

---

## Self‑improvement assertions

1. **SFT samples for drift resolution.** Each drift‑resolution event produces a
   Nemotron‑Agentic‑v2 entry covering `drift_detection → remediation → audit`.  
2. **Skill promotion for config‑validation templates.** After ≥ 3 successful
   drift resolutions, Curator promotes a `config-validation-template` skill
   that encodes common hardening rules, reducing manual policy authoring by
   ~25 %.  
3. **Memory recall improves drift detection.** When a resource shows recurring
   drift on the same setting, the system recalls the previous fix and
   automatically applies it, reducing manual intervention by ≥ 40 %.  
4. **Reinforcement‑signal impact.** `reinforce_signal=success` from a resolved
   drift is fed back to the trainer, improving future drift‑detection models.  
5. **Audit trail learning.** Curator may suggest adding a new rule when a
   recurring drift pattern emerges, reducing future violations by ≥ 20 %.  

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Config bootstrap & policy loading | plan/05a §"Coordinator" + plan/03 | _audit_ |
| Drift detection (state comparison) | custom tool (add to plan/16) | _audic_ |
| Remediation execution (tools) | custom tools (add to plan/16) | _audit_ |
| Audit bundle creation | plan/07a §"Knowledge‑base client" | _audit_ |
| Slack notification of drifts | plan/09 §"Gateway" | _audit_ |
| Knowledge‑base storage of drift diffs | plan/07a | _audit_ |
| Reinforce‑signal generation | plan/07a §"Memory providers" | _audit_ |
| Failure handling & escalation | plan/11 §"build-test-deploy" | _audit_ |
| Periodic polling & scheduling | plan/05a §"Coordinator" | _audit_ |
| Memory fact writes (drift_status) | plan/07a | _audit_ |

---

## Open questions

1. **Policy storage strategy.** Should policies be stored as code in Git, as
   versioned KB entries, or as a hybrid?  
2. **Drift tolerance.** Should we allow a small tolerance (e.g., 5 % deviation)
   before marking a drift, or require exact compliance?  
3. **Rollback safety.** Should rollbacks be automatic or require manual approval?
   What permissions should be required?  
4. **Cross‑resource dependencies.** If a drift in one resource affects another,
   how should the system handle cascading failures?  
5. **Compliance reporting format.** Should reports be PDF, HTML, or JSON-based
   dashboards? What metadata should be included for auditor traceability?  
