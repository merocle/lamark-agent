# 26 — Multi‑Cloud Orchestration

> **Phase:** P6–P7 (gateway, sandbox, trainer) + P8 (monitoring).
> **One‑liner:** Lamark treats a heterogeneous fleet of VMs, containers, and
> serverless workloads as a single Kanban‑driven workload pool, automatically
> scaling, load‑balancing, and fail‑over across AWS, GCP, and Azure while
> preserving per‑cloud audit trails and generating a unified deployment
> manifest for the remote UI.

---

## North‑star contribution

- **Domain quality (cloud‑ops).** Replaces manual, siloed cloud‑resource
  management with a unified, policy‑driven orchestration layer that:
  - Dynamically migrates workloads to the cheapest available region or
    instance type.
  - Auto‑replaces unhealthy instances across clouds.
  - Generates a single, cloud‑agnostic deployment manifest for the
    remote UI.
  - Audits every action in the knowledge‑base for compliance and cost‑trace.
- **Agent‑side self‑improvement.** Emits:
  - `memory_fact` writes: cloud‑resource → current state, cost, utilization,
    migration history, and last‑action timestamp. Recalled by future
    orchestration agents to bias decisions toward cheaper or more reliable
    clouds.
  - `skill_draft` candidates: reusable orchestration playbooks (e.g., “migrate
    MySQL from us‑east‑1 to eu‑central‑1 using read‑replica promotion”) promoted
    by Curator after ≥ 3 successful migrations.
  - `reinforce_signal=success` when a migration completes without downtime;
    `fail` when a fail‑over triggers repeated health‑check failures.
- **Model‑side self‑improvement.** Migration traces become high‑value SFT
  samples (source‑cloud → target‑cloud, config drift, health‑probe results).
  DPO pairs arise when an initially rejected migration is later revised and
  merged.

---

## Idea

The platform runs workloads across three cloud providers to improve
availability and cost‑efficiency. Currently each cloud is managed by a
separate team using distinct toolchains, leading to inconsistent policies,
visibility gaps, and manual fail‑over procedures. Lamark unifies the
management surface:

### Step 0 — Multi‑cloud inventory & policy load

1. **Inventory Agent** queries each cloud provider’s inventory API (AWS EC2,
   GCP Compute Engine, Azure VMs) via dedicated MCP connectors and stores the
   result in the knowledge‑base under `cloud‑inventory/<cloud‑name>/`.  
2. Reads `GET /memory/search?q=cloud‑policy` — retrieves the global policies:
   - **Cost‑threshold** (migrate if on‑demand cost > $0.12 / hour).  
   - **Reliability‑threshold** (migrate if health‑check failures > 5 % over 5 min).  
   - **Region‑preference** (prefer `us‑east‑1` unless cost > baseline + 20 %).  
3. Creates a Kanban board `Multi‑Cloud Orchestration` with one card per
   resource group (e.g., “backend‑services‑eu”).

### Step 1 — Workload classification

4. **Classifier Agent** reads each resource’s metadata and tags it with a
   logical role (`web‑frontend`, `batch‑processor`, `db‑primary`, etc.) using
   the ownership map from `memory_fact: {resource, owner, role}`.  
5. For each role, the agent determines the **canonical manifest** (e.g.,
   Docker image reference, env‑var list, scaling parameters) and stores it in
   `docs/orchestration/<role>.manifest.yaml`.  

### Step 2 — Dynamic scaling & migration decision

6. **Orchestrator Agent** evaluates each resource against the loaded policies:
   - **Cost check:** Queries `GET /knowledge/costs/<cloud>/<instance_type>` to
     retrieve current on‑demand pricing.  
   - **Utilization check:** Reads the latest utilization metric from the
     resource’s CloudWatch/Prometheus metrics (via `MetricTool::Read`).  
   - **Reliability check:** Summarizes recent health‑check results (via
     `HealthTool::Read`).  
   - **Decision:** If any threshold is breached, the agent marks the resource
     as `migrate_candidate`.  

### Step 3 — Migration planning

7. For each `migrate_candidate`, the Orchestrator spawns a **Migration Planner**
   sub‑agent that:
   - Selects a **target cloud/region** that satisfies the policy (cheapest
     within cost‑threshold, meets reliability‑threshold).  
   - Generates a **migration manifest** (e.g., new instance type, spot‑price,
     security‑group rules, DNS target shift).  
   - Uses `ConfigTool::RenderTemplate` to produce a cloud‑agnostic YAML
     manifest stored in `docs/orchestration/migration-<resource>.yaml`.  
   - Writes `memory_fact`: `{resource, target_cloud, target_type,
     migration_manifest_path}`.  

### Step 4 — Execution & health verification

8. **Executor Agent** performs the migration:
   - Calls the appropriate MCP/ACp tool to create the target resource
     (`CloudTool::CreateInstance(target_spec)`).  
   - Migrates data (e.g., EBS snapshot copy, GCS bucket sync) via
     `DataTransferTool::Copy`.  
   - Updates DNS or service discovery to point to the new endpoint
     (`DNSTool::UpdateRecord`).  
   - Starts a **health‑watchdog** that polls the new resource every 30 s.  
   - On `HealthTool::Read` reporting `healthy`, marks the migration as
     `completed`.  

### Step 5 — Post‑migration audit & signal

9. **Audit Agent** writes a comprehensive bundle:
   - Original manifest, migration manifest, diff of configuration changes,
     health‑probe logs, and cost‑comparison (`baseline_cost` vs. `new_cost`).  
   - Posts the bundle to `POST /knowledge/migration/<resource>` and
     records a `memory_fact`: `{resource, migration_status=completed,
     target_cloud, cost_savings, roi_months}`.  
10. **Reinforce Signal:** If health‑check passes within the SLA, the agent
    attaches `reinforce_signal=success`; if repeated failures occur, it
    attaches `fail` and creates a `RollbackTask`.  

### Step 6 — Remote UI exposure & continuous monitoring

11. The unified **Deployment Manifest** (all active resources + their
    current state) is rendered by the remote UI (`lamark-remote` + `webui`).
    - Shows a single view of the entire multi‑cloud fleet.  
    - Highlights migrated resources in green, unhealthy ones in red.  
    - Allows operators to drill into any resource’s full trace bundle.  
12. **Monitor Agent** continuously watches the Kanban board for new
    `migrate_candidate` cards and automatically triggers steps 3‑5 without
    manual intervention.  

---

## Actors

| Actor | Role |
|---|---|
| **Orchestrator Agent** | `lamark-coordinator` sub‑agent. Owns the multi‑cloud Kanban board, decides migrations, triggers planners and executors. |
| **Inventory Agent** | Reads cloud provider APIs via MCP; populates `cloud‑inventory` in KB. |
| **Classifier Agent** | Tags resources with logical roles using ownership map. |
| **Migration Planner** | Generates cloud‑agnostic manifests and writes them to `docs/orchestration/`. |
| **Executor Agent** | Calls cloud APIs to create target resources, copy data, update DNS. |
| **Health‑watchdog** | Polls health‑check endpoints; triggers rollback if unhealthy. |
| **Audit Agent** | Packages migration bundles, writes to KB, attaches `reinforce_signal`. |
| **Gateway** | Slack adapter posts migration status, approval prompts, and final audit links. |
| **Remote UI** | Consumes the unified deployment manifest; provides visual fleet overview. |
| **Knowledge‑base** | Stores inventory, manifests, migration bundles, and signal facts. |

---

## Trigger

1. **Periodic policy evaluation** – the Orchestrator runs on a schedule
   (`0 */6 * * *` → every 6 hours) or can be invoked manually:  
   ```
   $ lamark orchestrate --trigger periodic
   ```  
2. **Event‑driven migration** – a health‑check failure or cost‑threshold breach
   fires a webhook to the Gateway, which immediately creates a `migrate_candidate`
   card on the Kanban board.

Both paths result in a new card appearing in the `Multi‑Cloud Orchestration`
board.

---

## Pipeline

### Step 0 — Bootstrap & inventory load

1. Coordinator reads `GET /memory/search?q=cloud‑policy` and loads the
   policy thresholds.  
2. Inventory Agent queries each cloud provider’s API and writes the raw
   inventory to `knowledge-base/cloud-inventory/`.  
3. Coordinator creates a Kanban card for each resource group.

### Step 1 — Classification

4. Classifier Agent reads each resource’s metadata, queries the ownership
   map (`GET /memory/search?q=dep‑ownership`) and tags the resource with a
   logical role.  
5. For each role, it renders the canonical manifest to
   `docs/orchestration/<role>.manifest.yaml` via `ConfigTool::RenderTemplate`.  

### Step 2 — Migration decision

6. Orchestrator evaluates each resource against the policy thresholds,
   marking candidates for migration.  
7. For each candidate, it spawns a Migration Planner sub‑agent.

### Step 3 — Migration planning

8. Planner selects a target cloud/region, renders a migration manifest,
   and writes it to `docs/orchestration/migration-<resource>.yaml`.  
9. Planner records `memory_fact` with the manifest path and target details.  

### Step 4 — Execution & verification

10. Executor Agent creates the target resource (`CloudTool::CreateInstance`).  
11. Data migration (`DataTransferTool::Copy`) and DNS update (`DNSTool::UpdateRecord`).  
12. Health‑watchdog monitors the new resource; on `healthy` state, signals
    completion.  

### Step 5 — Audit & signal

13. Audit Agent builds the migration bundle and posts it to KB.  
14. `reinforce_signal` is set based on health‑check outcome.  
15. Kanban card moves to `completed` or `failed`.  

### Step 6 — Remote UI exposure

16. Remote UI refreshes the unified deployment manifest, reflecting the new
    state.  

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0 (inventory, policy) | `lamark-coordinator`, `lamark-config`, `lamark-kb-client` | 05a, 03 |
| 1 (classification) | `lamark-tools` (Grep/Read), `lamark-core` | 05, 07 |
| 2 (policy evaluation) | `lamark-tools` (MetricTool, ConfigTool) | 05, 07 |
| 3 (manifest rendering) | `lamark-tools` (ConfigTool), `lamark-skills` (template) | 07a, 08 |
| 4 (execution) | `lamark-tools` (CloudTool, DataTransferTool, DNSTool), `lamark-gateway` | 05c, 09, 11 |
| 5 (health watchdog) | `lamark-tools` (HealthTool), `lamark-hooks` | 06 |
| 6 (audit bundle) | `lamark-trace`, `lamark-kb-client` | 06, 07a |
| 7 (remote UI exposure) | `lamark-remote`, `lamark-webui` | 12, 13 |
| 8 (monitoring loop) | `lamark-coordinator` (periodic trigger) | 05a |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Cloud API rate‑limit** | Agent backs off with exponential back‑off; after 2 retries posts a Slack alert “⚠️ API rate‑limit – migration paused”. |
| **Health‑check repeatedly fails** | Watchdog marks the resource as `unhealthy`; after 3 consecutive failures, creates a `RollbackTask` to revert to the original cloud. |
| **Manifest rendering error** | Planner logs the error, aborts migration, and posts a detailed message to Slack for manual intervention. |
| **Data‑transfer timeout** | Transfer tool retries 2×; on final failure, posts a Slack alert and marks the migration as `failed`. |
| **Audit bundle write fails** | Writes to local outbox; retries every 30 s; trace bundle remains available for later upload. |
| **No suitable target cloud** | Planner marks the candidate as “no viable target” and posts a Slack suggestion to adjust cost or reliability thresholds. |
| **Policy mis‑configuration** | Coordinator detects an undefined threshold and halts further evaluation, posting a Slack alert for manual correction. |

---

## Acceptance criteria

- [ ] The inventory job runs nightly and populates `cloud‑inventory/` with up‑to‑date resource metadata.  
- [ ] Every resource is classified with a logical role and manifest stored in `docs/orchestration/`.  
- [ ] Migration candidates are automatically identified when cost or reliability thresholds are breached.  
- [ ] A migration manifest is generated for each candidate and stored in `docs/orchestration/migration‑<resource>.yaml`.  
- [ ] Execution of a migration creates the target resource, copies data, updates DNS, and passes health‑checks.  
- [ ] Successful migrations are audited in the knowledge‑base with a full bundle and `reinforce_signal=success`.  
- [ ] Failed migrations trigger a rollback task and are clearly flagged in Slack.  
- [ ] The remote UI displays a unified view of all resources, highlighting migrated and unhealthy items.  
- [ ] All actions are traceable via the knowledge‑base; `memory_fact` records include migration status, target cloud, and cost‑savings.  
- [ ] Periodic re‑evaluation runs automatically every 6 hours (or on event) without manual intervention.  

---

## Self‑improvement assertions

1. **SFT samples for migration traces.** Each migration produces a Nemotron‑Agentic‑v2 entry covering `inventory → classification → manifest → execution → audit`.  
2. **Skill promotion for migration playbooks.** After ≥ 3 successful migrations, Curator promotes a reusable `migration-playbook` skill that encodes common patterns (e.g., “move PostgreSQL read‑replica to another region”).  
3. **Memory recall reduces re‑classification.** When a resource is re‑evaluated, its role and manifest are recalled from memory, skipping the classification step and cutting 30 s off the pipeline.  
4. **Reinforcement‑signal impact.** `reinforce_signal=success` from a completed migration is fed back to the cost‑policy trainer, biasing future threshold selections toward cheaper or more reliable clouds.  
5. **Anomaly‑precision learning.** Curator may adjust the health‑check failure threshold based on historical false‑positive rates, reducing unnecessary migrations by ≥ 10 %.  

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Multi‑cloud inventory ingestion | plan/09 §"Gateway" + custom MCP connectors | _audit_ |
| Role classification using ownership map | plan/07a §"Memory providers" | _audit_ |
| Policy evaluation (cost, reliability, region) | custom policy engine (add to plan/16) | _audit_ |
| Migration planning (manifest generation) | plan/07a §"Memory providers" + plan/08 §"Curator" | _audit_ |
| Execution of cross‑cloud resources | custom cloud‑provider tools | _audit_ |
| Health‑check watchdog & rollback | plan/16 §"Forgetting probe → auto‑rollback" (adapted) | _audit_ |
| Audit bundle creation & KB storage | plan/07a §"Knowledge‑base client" | _audit_ |
| Remote UI unified deployment manifest | plan/12 §"WebUI" | _audit_ |
| Slack notifications & approvals | plan/09 §"Gateway" | _audit_ |
| Failure handling & escalation | plan/11 §"build-test-deploy" | _audit_ |
| Periodic re‑evaluation scheduling | plan/05a §"Coordinator" | _audit_ |

---

## Open questions

1. **Policy versioning.** Should cloud‑cost and reliability policies be versioned in
   KB (so we can audit which thresholds were active during a migration) or stored
   as mutable config in `lamark-config`?  
2. **Cross‑cloud identity mapping.** How do we uniquely identify the *same*
   logical resource across clouds (e.g., a database with the same name but
   different backing services)? A naming convention or a tag‑based identifier may
   be needed.  
3. **Cost‑threshold granularity.** Should thresholds be per‑hour, per‑day, or
   averaged over a rolling window? This affects when migrations are triggered.  
4. **Data‑transfer cost accounting.** Migrations incur data‑egress fees; should
   the ROI calculation include estimated transfer costs?  
5. **Multi‑cloud service discovery.** When a service is split across clouds,
   how do we maintain a single endpoint for clients? Service mesh or DNS
   weighting may be required; design decisions affect the manifest format.  
