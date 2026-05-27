# 29 — Logging & Metrics Automation

> **Phase:** P5–P6 (coordinator, sandbox) + P9 (gateway) + P10 (monitoring).
> **One‑liner:** Lamark automatically captures, structures, and analyzes operational
> telemetry — logs, metrics, traces — across all crates, surfacing anomalies,
> generating summary reports, and notifying stakeholders when a metric breaches
> a threshold or when a new log pattern emerges.

---

## North‑star contribution

- **Domain quality (observability).** Replaces fragmented logging and metrics
  collection with a unified, agent‑driven pipeline that:
  - **Ingests** raw logs, metrics, and traces from every crate and sandbox.  
  - **Structures** them into canonical JSON schemas for downstream analysis.  
  - **Aggregates** time‑series data and detects anomalies without manual dashboard
    configuration.  
  - **Generates** daily/weekly summary reports and posts them to Slack, email,
    or the remote UI.  
  - **Triggers** corrective actions (e.g., scaling a service, restarting a pod)
    when a metric crosses a threshold.  
- **Agent‑side self‑improvement.** Emits:
  - `memory_fact` writes: `metric:<name> → {timestamp, value, source, status}`,
    recalled by future monitoring agents to bias sampling toward historically
    important metrics.  
  - `skill_draft` candidates: reusable log‑analysis templates (e.g., “detect
    memory‑leak pattern in GC logs”) promoted by Curator after ≥ 3 uses.  
  - `reinforce_signal=success` when an anomaly is correctly diagnosed and
    resolved; `fail` when a metric breach goes unhandled.  
- **Model‑side self‑improvement.** Log and metric traces become rich SFT
  samples (raw log → structured JSON → anomaly detection → action). DPO pairs
  arise when an initial anomaly detection is revised after a human correction.

---

## Idea

The platform generates a massive volume of logs and metrics from its many
crates, sandboxes, and gateway adapters. Today this data is collected manually,
requires manual dashboard creation, and often leads to missed anomalies.
Lamark replaces this with an automated observability loop:

### Step 0 — Telemetry ingestion configuration

1. **Telemetry coordinator** reads `GET /memory/search?q=telemetry‑config` — retrieves
   the list of enabled telemetry sources (e.g., `crates/*/logs/*.log`,
   `teamcity/builds/*/metrics.json`, `gateway/http/*/access.log`).  
2. Reads `GET /knowledge/search?q=telemetry‑policy` — loads the organization’s
   logging & metrics policy (e.g., “all `ERROR` logs must be tagged with
   `severity=high`”, “retain Prometheus metrics for 30 days”).  
3. Creates a `TelemetryTask` on the Kanban board with fields:
   - `source`  
   - `pattern` (regex for log lines)  
   - `metric_name`  
   - `threshold` (if applicable)  

### Step 1 — Raw telemetry ingestion

4. **Log Agent** subscribes to each source (file watch, HTTP endpoint, Kafka
   topic) and streams raw lines into `~/.lamark/telemetry/<source>/`.  
5. For each line, the agent runs a **structured parsing step**:
   - Uses `Grep`/`Read` to locate known log formats (e.g., JSON, key‑value).  
   - Emits a **structured event** (`LogEvent`) with fields: `timestamp`,
     `level`, `service`, `message`, `trace_id`, `span_id`.  
   - Writes each `LogEvent` to `~/.lamark/telemetry/<source>/events.jsonl`.  

### Step 2 — Metric ingestion

6. **Metric Agent** queries external systems (Prometheus, CloudWatch, TeamCity)
   via their MCP/REST connectors and pulls metric samples at configured
   intervals.  
7. Each sample is written as a `MetricEvent` with fields:
   `{metric_name, timestamp, value, labels, source}`.  
8. All `MetricEvent`s are appended to `~/.lamark/telemetry/metrics/events.jsonl`.  

### Step 3 — Structured storage & memory facts

9. Both `LogEvent` and `MetricEvent` streams are **reduced** into immutable
   trace bundles (`~/.lamark/traces/<trace_id>/`) by the **Trace Recorder**
   (`plan/06 §"Trace recorder"`).  
10. The recorder also writes `memory_fact` entries:
    - `{service, metric, value, timestamp}` for each metric sample.  
    - `{service, log_level, count}` summarizing log level frequencies per hour.  

### Step 4 — Anomaly detection & quality gates

11. **Anomaly Agent** consumes the structured events and applies detection
    strategies:
    - **Statistical:** Z‑score on metric value vs. rolling baseline.  
    - **Pattern‑matching:** `Grep` against known anomaly signatures (e.g., “GC
      pause > 5 s”, “disk I/O > 1 GB/s”).  
    - **ML‑based:** Calls a lightweight anomaly‑detection model (via `MLTool`)
      trained on historical labeled anomalies.  
    - When an anomaly is detected, the agent writes a `memory_fact`:
      `{anomaly:true, metric, value, severity, detected_at}`.  

### Step 5 — Reporting & alerting

12. **Reporting Agent** aggregates events into daily/weekly summary reports:
    - Top‑5 error messages per service.  
    - Metric trends (e.g., “CPU usage increased 30 % over 24 h”).  
    - Anomaly digests (list of detected anomalies with severity).  
    - Generates a markdown report (`docs/observability/YYYY-MM-DD-summary.md`).  
13. Posts the report to Slack (`#observability`) and to the remote UI.  
14. If any metric crosses its configured threshold, the agent creates a
    `ThresholdBreachTask` on the Kanban board.  

### Step 6 — Automated response & remediation

15. **Response Agent** watches for `ThresholdBreachTask` cards:
    - If a CPU metric exceeds its limit, the agent calls `CloudTool::ScaleUp`
      (or `DockerTool::Restart`) to scale the affected service.  
    - If a log pattern indicates a crash loop, the agent triggers a `RestartTask`.  
    - All actions are recorded as `memory_fact`: `{action, target, outcome,
      timestamp}`.  
16. Upon successful remediation, the agent posts a confirmation Slack message
    and updates the related `MetricEvent` with `status=resolved`.  

### Step 7 — Knowledge‑base sync & signal generation

17. All telemetry data (raw events, aggregated reports, anomaly digests) are
    stored in the knowledge‑base via `POST /knowledge/telemetry/`.  
18. `reinforce_signal` is set to `success` when a breach is resolved; `fail`
    when a breach remains unhandled for > N minutes.  

### Step 8 — Continuous monitoring loop

19. The **Monitoring Agent** runs on a schedule (e.g., every 5 min) to:
    - Refresh metric samples.  
    - Re‑run anomaly detection.  
    - Regenerate the daily summary report.  
    - React to any newly created `ThresholdBreachTask`.  

---

## Actors

| Actor | Role |
|---|---|
| **Telemetry Coordinator** | `lamark-coordinator` sub‑agent. Owns the telemetry Kanban board, ingests configs, creates ingestion tasks. |
| **Log Agent** | Reads raw log files, parses structured events, writes `LogEvent`s. |
| **Metric Agent** | Pulls metrics from external systems via MCP/REST connectors. |
| **Anomaly Agent** | Detects anomalies using statistical, pattern‑matching, and ML approaches. |
| **Reporting Agent** | Generates daily/weekly summary reports and posts to Slack/UI. |
| **Response Agent** | Executes corrective actions (scale, restart, etc.) on `ThresholdBreachTask`. |
| **Gateway** | Slack adapter – posts telemetry alerts, summary reports, and remediation confirmations. |
| **Knowledge‑base** | Stores raw telemetry events, structured summaries, anomaly facts, and resolution logs. |
| **Stakeholders** | Receive telemetry reports, approve or intervene on threshold breaches. |

---

## Trigger

1. **Automatic ingestion** – any new log file, metric endpoint, or telemetry
   webhook automatically creates a `TelemetryTask` on the Kanban board.  
2. **Manual trigger** – an engineer can run:  
   ```
   $ lamark telemetry ingest --source prometheus --schedule "*/5 * * * *"
   ```
   to force a telemetry ingestion cycle.  

Both paths result in a new telemetry task awaiting processing.

---

## Pipeline

### Step 0 — Bootstrap & task creation

1. Coordinator reads `GET /memory/search?q=telemetry‑config` – loads enabled
   telemetry sources and their schedules.  
2. Creates a Kanban card for each source (e.g., `Telemetry – Prometheus
   metrics`).  

### Step 1 — Raw telemetry ingestion

3. Log Agent subscribes to file watches or HTTP endpoints, streams raw data,
   parses structured events, writes `LogEvent`s to `events.jsonl`.  
4. Metric Agent queries external systems (Prometheus, CloudWatch) and writes
   `MetricEvent`s to `events.jsonl`.  

### Step 2 — Structured storage & memory facts

5. Trace Recorder reduces the streams into trace bundles and writes
   `memory_fact` entries for each metric and log‑level summary.  

### Step 3 — Anomaly detection

6. Anomaly Agent reads the structured events, applies detection strategies,
   and emits `memory_fact`: `{anomaly:true, metric, value, severity}`.  

### Step 4 — Reporting & alerting

7. Reporting Agent builds summary reports, writes markdown files, and posts to
   Slack.  
8. If a metric exceeds its threshold, a `ThresholdBreachTask` is created.  

### Step 5 — Automated remediation

9. Response Agent watches the Kanban board for `ThresholdBreachTask`s,
   executes corrective actions (scale, restart), and records outcomes as
   `memory_fact`.  

### Step 6 — Knowledge‑base sync & signal

10. All telemetry data and reports are stored in KB via `POST /knowledge/telemetry`.  
11. `reinforce_signal` is attached based on resolution success.  

### Step 7 — Continuous monitoring loop

11. Monitoring Agent repeats steps 1‑6 on a schedule, ensuring continuous
    observation and response.  

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| Telemetry bootstrap & task creation | `lamark-coordinator`, `lamark-config` | 05a, 03 |
| Log ingestion & parsing | `lamark-tools` (Grep/Read), `lamark-trace` | 05, 06 |
| Metric ingestion (MCP/REST) | `lamark-mcp`, `lamark-mcp-client` | 11, 12 |
| Structured storage & KB sync | `lamark-kb-client` | 07a |
| Anomaly detection | custom anomaly tool (add to plan/16) | custom |
| Reporting generation | `lamark-tools` (Write), `lamark-skills` (template) | 07a, 08 |
| Alert threshold handling | custom response tool | custom |
| Remediation execution | `lamark-tools` (CloudTool, DockerTool) | 05c |
| Slack notifications | `lamark-gateway` | 09 |
| Continuous monitoring loop | `lamark-coordinator` (schedule) | 05a |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Log file unreadable** | Agent retries 2× with backoff; on final failure, posts a Slack alert “⚠️ Log file unavailable – manual inspection required”. |
| **Metric connector rate‑limited** | Backs off with exponential back‑off; after retries, posts a Slack alert and marks the metric as `unavailable`. |
| **Anomaly detection timeout** | Agent aborts detection for that metric, logs a warning, and continues with other metrics. |
| **Report generation fails** | Writes to local outbox; retries; trace bundle remains available for later upload. |
| **Threshold breach not detected** | Agent logs the missed breach, creates a `MissedAlertTask` for manual review. |
| **Remediation command fails** | Agent retries 2×; on final failure, posts a Slack alert and creates a `RemediationFailureTask`. |
| **Knowledge‑base write fails** | Writes to local outbox; retries; trace bundle preserved for later upload. |

---

## Acceptance criteria

- [ ] All configured telemetry sources are ingested automatically and produce structured events.  
- [ ] Structured events are stored in the knowledge‑base and referenced by trace bundles.  
- [ ] Anomalies are detected with configurable sensitivity and logged as facts.  
- [ ] Daily/weekly summary reports are generated and posted to Slack and the remote UI.  
- [ ] Threshold breaches create `ThresholdBreachTask`s and trigger automated remediation.  
- [ ] Successful remediation is confirmed via Slack and reflected in the knowledge‑base.  
- [ ] All telemetry, anomalies, and actions are stored in KB for audit and training.  
- [ ] `reinforce_signal` correctly reflects success/failure and informs the trainer.  
- [ ] The monitoring loop runs continuously without manual intervention.  

---

## Self‑improvement assertions

1. **SFT samples for telemetry analysis.** Each telemetry ingestion produces a Nemotron‑Agentic‑v2 entry covering `ingest → parse → store → anomaly → report`.  
2. **Skill promotion for log‑analysis templates.** After ≥ 3 successful anomaly detections, Curator promotes a `log‑anomaly-template` skill that encodes common patterns, reducing manual rule creation.  
3. **Memory recall improves detection precision.** When a metric shows a recurring anomaly, the system recalls its historical baseline and adjusts the detection threshold, improving true‑positive rate by ≥ 15 %.  
4. **Reinforcement‑signal impact.** `reinforce_signal=success` from resolved breaches is fed back to the anomaly‑detection trainer, improving future detection accuracy.  
5. **Anomaly‑precision learning.** Curator may suggest adjusting Z‑score thresholds based on historical false‑positive rates; adoption should reduce unnecessary alerts by ≥ 20 %.  

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Telemetry ingestion (logs, metrics) | plan/05 §"Tool registry", plan/09 §"MCP client" | _audit_ |
| Structured event creation | plan/06 §"Trace recorder" | _audit_ |
| Trace bundle creation | plan/06 §"Reducer" | _audit_ |
| Anomaly detection logic | custom tool (add to plan/16) | _audit_ |
| Reporting generation (markdown, Slack) | plan/07a §"Memory providers", plan/09 §"Gateway" | _audit_ |
| Threshold breach detection & alerting | custom tool (add to plan/16) | _audit_ |
| Automated remediation actions | custom tool (add to plan/16) | _audit_ |
| Knowledge‑base storage of telemetry | plan/07a §"Knowledge‑base client" | _audit_ |
| Continuous monitoring loop | plan/05a §"Coordinator" | _audit_ |
| Failure handling & escalation | plan/11 §"build-test-deploy" | _audit_ |
| Reinforce‑signal handling | plan/07a §"Memory providers" | _audit_ |

---

## Open questions

1. **Telemetry sampling strategy.** Should we sample logs/metrics at fixed intervals,
   event‑driven bases, or adaptive rates based on volume?  
2. **Anomaly detection model hosting.** Should anomaly models be hosted inside the
   sandbox (safe but slower) or as external microservices (faster but external
   dependency)?  
3. **Metric retention policy.** How long should raw metric samples be retained
   before aggregation or deletion? Consider cost vs. auditability.  
4. **Cross‑service correlation.** Should the system correlate metrics across
   services (e.g., “CPU spike + error rate increase”) to detect composite
   failures?  
5. **User‑facing telemetry.** Should telemetry be exposed to end‑users via a
   dashboard, or remain internal to operations?  
