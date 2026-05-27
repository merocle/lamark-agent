# 27 — Dataset Versioning Automation

> **Phase:** P4–P5 (memory + KB) + P7–P8 (trainer + monitoring).
> **One‑liner:** Lamark continuously tracks changes to raw data sources, creates
> immutable versioned snapshots in the knowledge‑base, links them to the
> originating trace bundles, and automatically registers new versions for
> SFT/DPO training while notifying stakeholders when a dataset version
> satisfies predefined quality gates.

---

## North‑star contribution

- **Domain quality (data engineering).** Replaces fragile manual version‑control
  of large datasets with an automated, auditable pipeline that:
  - Captures every source change as a first‑class artifact.
  - Links each version to the exact training trace that produced it.
  - Enforces quality gates (schema validation, statistical sanity checks) before
    a version becomes *trainable*.
  - Provides stakeholders with a clear, searchable history of dataset evolution.
- **Agent‑side self‑improvement.** Emits:
  - `memory_fact` writes: `dataset:<name> → version_id, checksum, timestamp,
    quality_metric, approved_flag`. Recalled by future dataset‑ingestion agents
    to skip re‑processing of unchanged versions.
  - `skill_draft` candidates: reusable dataset‑ingest templates (e.g., “ingest
    PostgreSQL dump → Parquet → validate schema”) promoted by Curator after ≥ 3
    successful ingests.
  - `reinforce_signal=success` when a version passes all quality gates and is
    promoted to the training queue; `fail` when a version is rejected or
    later found to be corrupted.
- **Model‑side self‑improvement.** Versioned datasets become precise SFT/DPO
  sources: each version’s `trace.jsonl` is immutable, enabling exact
  reproducibility of training runs. DPO pairs naturally arise when a rejected
  version is later revised and accepted.

---

## Idea

The data‑engineering team ingests raw logs, survey responses, and sensor streams
into a central repository. Today this is done manually, leading to
inconsistent naming, missing checksums, and no automatic quality checks.
Lamark replaces the workflow:

### Step 0 — Source change detection

1. **Ingestion webhook** (Kafka topic, S3 event, DB trigger) fires when a new
   batch of data arrives → `lamark-gateway` receives
   `Op::ExternalEvent {source:"ingest", type:"batch", payload:{source_id,
   timestamp}}`.  
2. Gateway forwards to **Dataset Coordinator** (`lamark-coordinator` sub‑agent).  
3. Coordinator creates a `DatasetTask` on the Kanban board:
   `{source_id, batch_id, expected_schema, quality_gates}`.

### Step 1 — Raw ingestion & snapshot creation

4. **Ingest Agent** pulls the batch from the source (e.g., `s3://raw-logs/2026-05-25/*.parquet`).  
5. Computes a **content hash** (`SHA‑256`) and writes it as a `memory_fact`:
   `{dataset_id, batch_hash, ingested_at}`.  
6. Stores the raw batch in an **immutable snapshot** under
   `~/.lamark/datasets/<dataset_name>/v<version>/` (e.g., `v2026-05-25-01`).  
7. Writes a **snapshot manifest** (`manifest.json`) containing:
   - `version`, `hash`, `timestamp`, `source_url`, `checksum`, `schema_version`.  
8. Posts the manifest to `POST /knowledge/datasets/<dataset_name>/versions`.

### Step 2 — Schema validation & quality gates

9. Agent loads the **expected schema** from `GET /memory/search?q=dataset‑schema`.
   - Runs a validation job (`SchemaTool::Validate(path=..., schema=...)`).  
   - If validation fails, the agent posts a detailed Slack message with the
     error breakdown and marks the task `failed`.  
10. If validation passes, the agent executes any **additional quality gates**
    defined for the dataset (e.g., “null‑rate < 0.1 %”, “row‑count > 10⁴”).
    - Each gate is a separate tool call; all must return `Allow`.  
    - On failure, the agent logs the gate result and posts a Slack summary.  

### Step 3 — Version registration & training readiness

11. Upon passing all gates, the agent:
    - Updates the dataset’s **version pointer** in KB (`POST /knowledge/datasets/<name>/latest_version`).  
    - Writes a `memory_fact`: `{dataset, version, approved=true, quality_score=X}`.  
    - Adds the version to the **training queue** (`POST /agents/<trainer_id>/trainset`).  
    - Posts a Slack notification: “✅ New dataset version `v2026‑05‑25‑01` approved for training (quality=0.97)”.  

### Step 4 — Training bundle creation

12. **Trainer Coordinator** consumes the newly approved version and triggers
    the training pipeline (`plan/15`).  
13. The resulting **training bundle** (`manifest.json` + `trace.jsonl` + `payloads/`) is
    stored under `~/.lamark/traces/<rollout_id>/`.  
14. The bundle’s `manifest.json` includes a `dataset_version` field that links
    back to the KB version record, creating a permanent provenance chain.  

### Step 5 — Stakeholder notification & rollback handling

15. Slack message is broadcast to the data‑engineering channel with a link to
    the new version and its quality metrics.  
16. If a later audit discovers a problem (e.g., corrupted payload), the
    **Rollback Agent** can revert to the previous approved version by:
    - Restoring the prior manifest from KB.  
    - Issuing a `POST /knowledge/datasets/<name>/revert` command.  
    - Posting a “🔁 Dataset version reverted to `v2026‑05‑24‑02`” message.  

### Step 6 — Continuous monitoring

17. **Dataset Monitor Agent** watches the KB for changes to the `latest_version`
    pointer. When a new version is added, it:
    - Updates a dashboard graph showing dataset evolution over time.  
    - If a version’s `quality_score` drops below a threshold (e.g., due to a
      downstream regression), it creates a `QualityAlertTask` and notifies the
      engineering team.  

---

## Actors

| Actor | Role |
|---|---|
| **Dataset Coordinator** | `lamark-coordinator` sub‑agent. Owns the ingestion Kanban board, orchestrates snapshot creation and quality‑gate evaluation. |
| **Ingest Agent** | Reads raw data batches, computes hashes, writes immutable snapshots. |
| **Schema Validator** | Calls `SchemaTool::Validate` to ensure the batch conforms to the expected schema. |
| **Quality‑Gate Runner** | Executes each defined gate (null‑rate, row‑count, etc.) via `Bash`/`CustomTool`. |
| **Trainer Coordinator** | Consumes approved dataset versions and triggers the training pipeline (`plan/15`). |
| **Rollback Agent** | Handles version reverts when a later audit discovers a problem. |
| **Dataset Monitor** | Watches the KB for new approved versions and updates monitoring dashboards. |
| **Gateway** | Slack adapter posts ingestion alerts, quality‑gate results, and version‑approval notifications. |
| **Knowledge‑base** | Stores raw snapshots, manifests, version pointers, and quality facts. |
| **Stakeholders** | Review Slack messages, approve/reject versions via reactions, monitor dashboard graphs. |

---

## Trigger

1. **Automatic batch arrival** – any new object in the ingestion bucket, Kafka
   message, or DB row insertion fires the webhook.  
2. **Manual ingestion command** – an engineer can run:  
   ```
   $ lamark dataset ingest --source s3://raw-logs/2026-05-25 --name user‑behaviour
   ```
   to force a new ingestion cycle.

Both paths create a `DatasetTask` on the Kanban board.

---

## Pipeline

### Step 0 — Bootstrap & task creation

1. Coordinator reads `GET /memory/search?q=dataset‑policy` – loads the
   organization’s dataset‑ingestion policy (e.g., “all raw batches must be
   hashed”, “quality gates: schema, null‑rate, row‑count”).  
2. Creates a Kanban card `Ingest user‑behaviour 2026‑05‑25` with fields:
   - `source_id`  
   - `batch_id`  
   - `expected_schema`  
   - `quality_gates` (list)  

### Step 1 — Raw ingestion & snapshot

3. Ingest Agent retrieves the batch from the source (S3, Kafka, etc.).  
4. Computes SHA‑256 hash and writes `memory_fact`: `{batch_hash, ingested_at}`.  
5. Writes the immutable snapshot to `~/.lamark/datasets/<name>/v<version>/`.  
6. Generates `manifest.json` and posts it to `POST /knowledge/datasets/<name>/versions`.  

### Step 2 — Schema validation

7. Agent calls `SchemaTool::Validate` with the batch file and the expected schema.  
8. On success, proceeds; on failure, posts a Slack message with the validation
   error breakdown and marks the task `failed`.  

### Step 3 — Quality‑gate evaluation

9. Agent iterates over each defined gate:
   - `Bash("python validate_null_rate.py")` → `Allow` if exit 0.  
   - Custom gate scripts may be called via `CustomTool::Run`.  
   - All gates must return `Allow`; any `Reject` aborts the pipeline.  

### Step 4 — Version registration

10. If all gates pass, Coordinator updates the dataset’s `latest_version` pointer
    in KB (`POST /knowledge/datasets/<name>/latest_version`).  
11. Writes `memory_fact`: `{dataset, version, approved=true, quality_score=X}`.  
12. Posts a Slack notification with the version link and quality score.  

### Step 5 — Training bundle creation

13. Trainer Coordinator picks up the newly approved version and triggers the
    training pipeline (`plan/15`).  
14. The resulting bundle is stored under `~/.lamark/traces/<rollout_id>/` and
    includes a `dataset_version` field that references the KB version record.  

### Step 6 — Stakeholder notification & rollback

15. Slack broadcast announces the approved version and its quality metrics.  
16. If a later regression is detected, Rollback Agent restores the previous
    approved version and posts a revert notice.  

### Step 7 — Continuous monitoring

17. Dataset Monitor watches the KB for changes to `latest_version` and updates
    the dashboard, alerting on any quality‑score degradation.  

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0 (bootstrap, policy) | `lamark-coordinator`, `lamark-config`, `lamark-kb-client` | 05a, 03 |
| 1 (ingest, snapshot) | `lamark-tools` (S3/HTTP client), `lamark-trace` | 05, 06 |
| 2 (hash & manifest) | `lamark-write` (Write), `lamark-kb-client` | 05, 07a |
| 3 (schema validation) | `lamark-tools` (SchemaTool) | custom |
| 4 (quality gates) | `lamark-tools` (Bash/CustomTool), `lamark-policy` | 05, 06 |
| 5 (version registration) | `lamark-kb-client` | 07a |
| 6 (training trigger) | `lamark-coordinator` (trainer integration) | 15 |
| 7 (rollback) | `lamark-coordinator`, `lamark-gateway` | 16 |
| 8 (monitoring) | `lamark-monitor` (custom), `lamark-gateway` | custom |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Batch arrival fails** (e.g., S3 access denied) | Gateway logs error, retries 2×, then posts a Slack alert “⚠️ Ingestion failed for batch – manual check required”. |
| **Hash collision** | Agent detects duplicate hash; treats as a re‑ingested batch and skips snapshot creation, logs a warning. |
| **Schema validation error** | Agent posts detailed validation errors to Slack, marks task `failed`, and does not proceed to quality gates. |
| **Quality‑gate timeout** | Gate tool retries 2× with exponential back‑off; on final failure, posts a Slack alert and marks the task `failed`. |
| **Quality‑gate false negative** | Agent logs the failure, creates a `QualityAlertTask` for manual review, and does not register the version as approved. |
| **KB version pointer update fails** | Writes to local outbox; retries every 30 s; trace bundle remains available for later upload. |
| **Training pipeline crashes** | Trainer Coordinator marks the rollout as `failed`; posts a Slack error; retry is scheduled after 5 min. |
| **Rollback needed** | Rollback Agent restores the previous approved manifest, posts a revert message, and updates the KB pointer. |

---

## Acceptance criteria

- [ ] A new raw batch automatically creates a `DatasetTask` with full metadata.  
- [ ] The batch is hashed, stored as an immutable snapshot, and its manifest is
  posted to KB.  
- [ ] All defined quality gates must pass; failures are reported in Slack with
  actionable details.  
- [ ] Upon successful gates, the version is registered as `latest` in KB and
  linked to the training queue.  
- [ ] A training bundle is generated, stored, and includes a `dataset_version`
  reference to the KB record.  
- [ ] Stakeholders receive a Slack notification with a link to the new version
  and its quality metrics.  
- [ ] If a later issue causes a regression, the Rollback Agent can restore the
  previous approved version and announces the revert.  
- [ ] Continuous monitoring updates the dashboard and alerts on any quality‑score
  degradation.  
- [ ] All actions are fully traceable in the knowledge‑base; `memory_fact` entries
  link batches → manifests → versions → training bundles.  

---

## Self‑improvement assertions

1. **SFT samples for dataset ingestion.** Each ingestion cycle produces a
   Nemotron‑Agentic‑v2 entry covering `ingest → hash → snapshot → validate →
   quality_gate → memory_fact`.  
2. **Skill promotion for ingestion templates.** After ≥ 3 successful ingests,
   Curator promotes a `dataset-ingest-template` skill that embeds the
   ingestion‑pipeline steps as a reusable YAML configuration, reducing
   boilerplate by ~30 %.  
3. **Memory recall reduces re‑processing.** When a batch hash already exists in
   `memory_fact`, the ingest step is skipped; the agent logs a recall hit,
   cutting ingestion time by ≥ 40 % for repeated batches.  
4. **Reinforcement‑signal impact.** `reinforce_signal=success` from an
   approved dataset version is fed back to the trainer, increasing the weight
   of high‑quality data in future SFT runs.  
5. **Quality‑gate learning.** Curator may suggest adjusting a gate threshold
   (e.g., null‑rate tolerance) based on historical regression rates; adoption
   should reduce false‑negative rejections by ≥ 15 %.  

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Dataset ingestion webhook → Kanban creation | plan/05a §"Coordinator" + plan/09 §"Gateway" | _audit_ |
| Raw snapshot creation & hash storage | plan/06 §"Trace recorder" | _audit_ |
| Schema validation tool | custom tool (add to plan/16) | _audit_ |
| Quality‑gate execution (Bash/CustomTool) | plan/05 §"Tool registry" | _audit_ |
| Version registration in KB | plan/07a §"Memory providers" | _audit_ |
| Training queue integration | plan/15 §"Training pipeline" | _audit_ |
| Slack notifications (ingest, approval) | plan/09 §"Gateway" | _audit_ |
| Rollback handling | plan/16 §"Forgetting probe → auto‑rollback" (adapted) | _audit_ |
| Continuous monitoring dashboard | custom monitoring tool | _audit_ |
| Failure handling & escalation | plan/11 §"build-test-deploy" | _audit_ |
| Memory fact writes (dataset version, quality_score) | plan/07a | _audit_ |

---

## Open questions

1. **Dataset version immutability.** Should snapshots be stored as truly immutable
   (append‑only, write‑once) or can they be overwritten if a newer version of the
   same data arrives?  
2. **Quality‑gate authorship.** Who defines the gate functions (engineers,
   data‑scientists, or a configuration file)? How are they versioned?  
3. **Cross‑dataset references.** When a dataset version is used as input to
   another pipeline, how is that dependency tracked?  
4. **Retention policy.** How long should raw snapshots be retained before
   archival or deletion? Consider cost vs. auditability.  
5. **Version‑level permissions.** Should different teams have different
   permissions to approve or consume specific dataset versions?  
