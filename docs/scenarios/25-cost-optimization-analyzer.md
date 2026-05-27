# 25 — Cost‑Optimization Analyzer

> **Phase:** P7–P8 (trainer + monitoring) + P9 (gateway, remote UI).
> **One‑liner:** Lamark continuously monitors cloud‑resource usage, compares it
> against cost‑allocation baselines, proposes rightsizing or spot‑instance
> migrations, and auto‑generates a cost‑saving plan with ROI estimates that
> the finance team can approve via Slack reactions.

---

## North‑star contribution

- **Domain quality (financial ops).** Turns a manual, quarterly cost‑review
  into a data‑driven, automated loop that surfaces waste, suggests concrete
  actions, and tracks execution. Every recommendation is traceable to the
  underlying usage trace bundle, enabling auditors to verify the logic.
- **Agent‑side self‑improvement.** Emits:
  - `memory_fact` writes: service → current monthly cost, baseline cost,
    suggested action, ROI estimate, and execution status. Recalled by
    future cost‑analysis agents to bias suggestions toward high‑impact
    services.
  - `skill_draft` candidates: reusable cost‑analysis templates (e.g., “Rightsize
    EC2 based on CPU‑utilization > 70 % for 7 days”) promoted by Curator after
    ≥ 3 successful analyses.
  - `reinforce_signal=success` when a cost‑saving recommendation is
    approved and implemented; `fail` when the finance team rejects the
    proposal or the action yields no savings after a defined period.
- **Model‑side self‑improvement.** Recommendation generation traces become
  high‑value SFT samples (usage → anomaly detection → cost‑model → ROI). DPO
  pairs arise when a rejected recommendation is later revised and accepted.

---

## Idea

Finance wants to reduce the cloud‑bill without sacrificing performance.
Today they run a manual spreadsheet analysis each quarter. Lamark replaces
that with a continuous, data‑driven process:

### Step 0 — Baseline ingestion & recall

1. **Cost‑ingestion webhook** (e.g., AWS CUR, GCP Billing Export) fires nightly
   → `lamark-gateway` receives `Op::ExternalEvent {source:"billing",type:"usage"}`.  
2. Gateway forwards the event to the **Cost Coordinator** (`lamark-coordinator`
   sub‑agent).  
3. Coordinator creates a `CostTask` on the Kanban board:
   `{service, period, current_cost, baseline_cost, tags}`.  
4. Queries `GET /memory/search?q=cost‑baseline` — retrieves historical
   baseline definitions (e.g., “prod‑web‑tier‑1 ≤ $2 500/mo”).  
5. Populates the task with the baseline for the service.

### Step 1 — Anomaly detection

6. **Anomaly Agent** reads the usage trace bundle for the service
   (`~/.lamark/traces/<service>/trace.jsonl`).  
7. Uses a lightweight statistical model (or calls an external anomaly‑detection
   tool) to flag outliers: sudden CPU spikes, storage growth, or network
   throughput beyond expected seasonality.  
8. Flags are written as `memory_fact`: `{service, anomaly:true, metric, value}`.

### Step 2 — Cost‑model evaluation

9. Coordinator spawns a **Cost‑Model Agent** that:
   - Loads the baseline definition from KB.  
   - Computes the **cost‑per‑unit** (e.g., $/CPU‑hour).  
   - Runs a **what‑if simulation**: if we switch from On‑Demand to Spot,
     or resize the instance class, what would the new cost be?  
   - Calculates an **ROI estimate**: `(baseline – proposed) / (implementation
     effort)` in “months of effort saved”.  
10. Each simulation result becomes a `memory_fact`: `{service, action, new_cost,
    roi_months, confidence}`.

### Step 3 — Recommendation generation

11. Cost‑Model Agent drafts a **recommendation document**:
    - Title: “Rightsize `web‑tier‑1` to `t3.medium` (spot) – $1 200/mo saved”.
    - Rationale: CPU utilization > 70 % for 10 days, no performance alerts.  
    - Implementation steps: update Auto‑Scaling group, adjust Spot‑price
      threshold, monitor for 48 h.  
    - ROI: $1 200 saved → 2‑month payback on ops‑team time.  
    - Links to the trace bundle for auditability.  
12. Document is written via `Write` to `docs/cost‑optimization/2026‑05‑25‑web‑tier‑1.md`.  
13. The draft is posted to Slack (`#finance‑ops`) for review:
    ```
    📉 Cost‑optimization recommendation:
    Rightsize web‑tier‑1 → spot instance, $1.2k/mo saved (ROI 2 mo).
    Review & react with 👍 to approve, 👎 to reject.
    ```

### Step 4 — Finance‑team approval & execution

14. Finance reviewers react to the Slack message:
    - `👍` → `/approve_cost` → Cost Coordinator marks the task `approved`.  
    - `👎` → `/reject_cost` → Marks `rejected`.  
    - `❓` → agent posts a follow‑up question asking for clarification.  
15. On approval, the **Execution Agent** automates the change:
    - Calls the appropriate cloud‑provider API (`aws autoscaling update‑auto‑scaling-group`) to
      adjust the instance type and enable Spot.  
    - Posts a confirmation message with a link to the executed change‑set.  
16. If execution fails, the agent posts the error to Slack and creates a
    `HotfixTask` to roll back or retry.

### Step 5 — Post‑implementation verification

17. **Cost‑Monitor Agent** polls the usage data for the next billing period.
    - If the actual cost matches the projected savings (within a tolerance
      of ±5 %), it posts a success message: “✅ Savings realized for
      `web‑tier‑1` – $1.2k saved this month”.  
    - If savings are not realized, it creates a `Re‑evaluateTask` and notifies
      finance.  
18. `reinforce_signal` is set to `success` when the savings are confirmed,
    otherwise `fail`.

### Step 6 — Knowledge‑base sync & signal generation

19. `POST /knowledge/cost‑recommendations` stores the full recommendation
    bundle (usage trace link, cost‑model output, ROI estimate, execution log).  
20. `memory_fact` records the final outcome: `{service, status=implemented,
    saved_amount, roi_months, execution_timestamp}`.  
21. If the recommendation was rejected, the `reject_reason` is stored for
    future learning.

---

## Actors

| Actor | Role |
|---|---|
| **Finance Coordinator** | `lamark-coordinator` sub‑agent. Owns the Kanban board for cost tasks, orchestrates ingestion, anomaly detection, and recommendation drafting. |
| **Anomaly Agent** | Detects usage outliers from trace bundles; emits `memory_fact` flags. |
| **Cost‑Model Agent** | Performs what‑if simulations, calculates ROI, drafts recommendation markdown. |
| **Execution Agent** | Executes cloud‑provider API calls to implement approved actions. |
| **Cost‑Monitor Agent** | Verifies post‑implementation savings, updates status, triggers hot‑fix if needed. |
| **Gateway** | Slack adapter – posts recommendation messages, approval prompts, final verification results. |
| **Knowledge‑base** | Stores usage traces, baseline definitions, cost‑model simulations, and final outcome facts. |
| **Finance Team** | Reviews Slack notifications, approves/rejects recommendations via reactions, monitors savings. |

---

## Trigger

1. **Nightly billing webhook** – automatic ingestion of new cost data.  
2. **Manual cost‑review command** – an analyst can run:  
   ```
   $ lamark cost analyze --service web‑tier‑1 --period 2026‑05
   ```
   to force a fresh analysis of a specific service.

Both paths result in a new `CostTask` on the Kanban board.

---

## Pipeline

### Step 0 — Bootstrap & task creation

1. Coordinator reads `GET /memory/search?q=cost‑policy` – retrieves the
   organization’s cost‑allocation rules (e.g., “any service > $5 k/mo must be
   reviewed”).  
2. Creates a Kanban card `Cost‑Optimize web‑tier‑1 (May 2026)` with fields:
   - `service`  
   - `period`  
   - `current_cost`  
   - `baseline_cost`  
   - `tags=[prod, web]`  

### Step 1 — Usage ingestion & baseline load

3. Ingests the nightly billing export → stores raw usage trace in
   `~/.lamark/traces/<service>/`.  
4. Queries `GET /knowledge/cost‑baseline` – loads the baseline definition
   (e.g., “`web‑tier‑1` baseline = $2 500/mo”).  
5. Populates the task with `current_cost` and `baseline_cost`.

### Step 2 — Anomaly detection

6. Anomaly Agent reads the trace bundle and runs a statistical test
   (e.g., Z‑score > 3 on CPU‑utilization).  
7. For each anomaly, writes `memory_fact`: `{service, metric, value,
   severity}` and attaches a link to the raw trace.  

### Step 3 — Cost‑model simulation

8. Cost‑Model Agent retrieves the baseline, computes unit cost, and runs
   what‑if scenarios (On‑Demand → Spot, instance‑type upgrade/downgrade).  
9. For each scenario, calculates:
   - `new_cost` (projected monthly spend).  
   - `roi_months` = `(baseline – new_cost) / (estimated_effort_months)`.  
   - `confidence` (based on utilization stability).  
10. Stores each scenario as a `memory_fact`: `{service, action, new_cost,
    roi_months, confidence}`.  

### Step 4 — Recommendation generation

11. Agent synthesizes the best scenario into a markdown recommendation
    (see Step 3 above).  
12. Writes the document to `docs/cost‑optimization/` with a unique filename
    based on the service and date.  
13. Posts the draft to Slack for finance review.

### Step 5 — Approval & execution

14. Finance reacts via Slack emojis:
    - `👍` → `CostCoordinator` calls `CostTool::Execute(action=<scenario>)`,
      which triggers the cloud‑API to adjust the resource.  
    - `👎` → task marked `rejected`; agent posts a follow‑up asking for
      clarification.  
    - `❓` → agent asks a clarifying question (e.g., “Do you want to cap
      cost reduction at 30 %?”).  
15. Execution Agent posts confirmation or error messages back to Slack.

### Step 6 — Verification & signal

16. Cost‑Monitor Agent polls usage data for the next billing cycle.
    - If actual cost matches the projected savings (±5 %), posts a success
      message and updates `memory_fact` with `status=implemented`.  
    - If savings are not realized, creates a `Re‑evaluateTask` and notifies
      finance.  
17. `reinforce_signal` is set to `success` on confirmed savings,
    `fail` otherwise.  
18. Kanban task moves to `completed` or `failed`.

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0 (bootstrap, policy) | `lamark-coordinator`, `lamark-config`, `lamark-kb-client` | 05a, 03 |
| 1 (billing ingestion) | `lamark-gateway` (billing webhook), `lamark-kb-client` | 09 |
| 2 (anomaly detection) | `lamark-tools` (statistical‑analysis), `lamark-core` | custom |
| 3 (cost‑model simulation) | `lamark-tools` (CostModel), `lamark-providers` (cloud APIs) | custom |
| 4 (recommendation authoring) | `lamark-tools` (Write), `lamark-skills` (template) | 07a, 08 |
| 5 (approval workflow) | `lamark-gateway` (Slack reactions → ACP) | 09 |
| 6 (execution) | `lamark-cloud-provider` (custom cloud‑API tool) | custom |
| 7 (post‑verify monitoring) | `lamark-monitor` (custom), `lamark-kb-client` | custom |
| 8 (KB sync) | `lamark-kb-client` | 07a |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Billing export missing** | Gateway logs error, retries for 15 min, then posts a Slack alert “⚠️ Billing export failed – manual check required”. |
| **Anomaly detection false positive** | Agent logs a warning, but does not create a recommendation unless severity exceeds a configurable threshold; otherwise the task proceeds to cost‑model without a flagged anomaly. |
| **Cost‑model simulation fails** (e.g., API rate‑limit) | Agent retries 2× with exponential backoff; on final failure, posts a Slack alert and marks the recommendation as `pending`. |
| **Finance team never reacts** | After N hours, Coordinator escalates with a reminder ping; if still silent, task is auto‑closed after 48 h to avoid stale recommendations. |
| **Execution API call fails** | Execution Agent posts the error to Slack, retries 2×, then creates a `HotfixTask` to roll back or investigate. |
| **Savings not realized** | Cost‑Monitor posts a “⚠️ Savings not realized – investigating” message and opens a `Re‑evaluateTask`. |
| **KB write fails** | Writes to local outbox; retries every 30 s; trace bundle remains available for later upload. |

---

## Acceptance criteria

- [ ] Nightly billing export automatically creates a `CostTask` with up‑to‑date cost data.  
- [ ] Anomaly detection flags only genuine usage outliers and records them as facts.  
- [ ] Cost‑Model generates at least one viable recommendation per task with a
  clear ROI estimate.  
- [ ] Recommendations are posted to Slack and include a clickable link to the
  full markdown document and trace bundle.  
- [ ] Finance approval via Slack reaction correctly triggers the execution of
  the recommended cloud‑resource change.  
- [ ] Execution success or failure is reported back to Slack within 2 minutes.  
- [ ] Post‑implementation verification confirms the projected savings (within
  ±5 % tolerance).  
- [ ] All recommendations, outcomes, and facts are stored in the knowledge‑base
  for audit and future learning.  
- [ ] `reinforce_signal` correctly reflects success/failure and is consumed by
  the trainer to improve future cost‑model predictions.  

---

## Self‑improvement assertions

1. **SFT samples for cost‑model.** Each simulation step produces a Nemotron‑Agentic‑v2 entry covering `usage → anomaly → cost‑model → recommendation`.  
2. **Skill promotion for cost‑templates.** After ≥ 3 successful recommendations, Curator promotes a `cost-template` skill that supplies a standardized markdown skeleton, reducing drafting tokens by ~20 %.  
3. **Memory recall improves targeting.** When a service has a recorded baseline, the Cost‑Model skips the generic baseline load and uses the recalled value, cutting simulation start‑up time by ≥ 40 %.  
4. **Reinforcement‑signal feedback.** `reinforce_signal=success` from a realized savings is fed back to the cost‑model trainer, increasing the weight of high‑ROI actions in future simulations.  
5. **Anomaly‑precision learning.** Curator may suggest adjusting the Z‑score threshold based on historical false‑positive rates; adoption should reduce unnecessary recommendations by ≥ 15 %.

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Billing webhook → Kanban creation | plan/09 §"Gateway" + plan/05a §"Coordinator" | _audit_ |
| Usage trace ingestion | plan/06 §"Trace recorder" | _audit_ |
| Anomaly detection (statistical model) | custom tool (add to plan/16) | _audit_ |
| Cost‑model simulation & ROI calculation | custom tool (add to plan/16) | _audit_ |
| Recommendation markdown generation | plan/07a §"Memory providers" + plan/08 §"Curator" | _audit_ |
| Slack notification & approval workflow | plan/09 §"Gateway" | _audit_ |
| Execution of approved actions (cloud API) | custom cloud‑provider tool | _audit_ |
| Post‑implementation verification | custom monitoring tool | _audit_ |
| Knowledge‑base storage of recommendations | plan/07a | _audit_ |
| Reinforce‑signal generation & storage | plan/07a, plan/08 | _audit_ |
| Failure handling & escalation | plan/11 §"build-test-deploy" | _audit_ |

---

## Open questions

1. **Baseline definition ownership.** Should baselines be defined by finance
   stakeholders via a UI, stored as versioned KB entries, or managed as code
   (`baseline/*.yaml`) in the repo?  
2. **Spot‑instance price source.** Should we query the live spot‑price API on
   every simulation or cache it per region for a day?  
3. **Effort‑estimation model.** How do we estimate the “implementation effort”
   in months? Could we use historical change‑lead times from the issue tracker?  
4. **Multi‑cloud support.** Should the analyzer handle AWS, GCP, Azure, and
   Kubernetes cost models uniformly, or maintain separate adapters per cloud?  
5. **Cost‑allocation tagging.** Do we require that every service be tagged with
   a cost‑center tag so that recommendations can be automatically routed to
   the owning team?  
