# 24 — Automated Security Patching

> **Phase:** P5–P6 (coordinator, sandbox, gateway) + P8 (monitoring).
> **One-liner:** Lamark continuously scans the codebase and third‑party
> dependencies for known CVEs, drafts a patch branch, opens a PR, watches
> CI, and if the patch lands cleanly, auto‑promotes the fix to production
> while logging the remediation in knowledge‑base and notifying the
> security team.

---

## North-star contribution

- **Domain quality (security ops).** Eliminates the manual “scan → patch → PR → merge”
  workflow, replacing it with a fully auditable, repeatable agent loop that
  produces a traceable fix for every CVE‑identified vulnerability.
- **Agent-side self‑improvement.** Emits:
  - `memory_fact` writes: CVE‑ID → patch‑status, patched‑by‑agent‑ID,
    remediation‑date. Recalled by future patch‑agents to prioritize high‑severity
    CVEs and avoid re‑scanning already‑fixed issues.
  - `skill_draft` candidates: reusable patch‑template markdown (e.g., “Add
    bounds‑checking to `parse_id()`”) promoted by Curator after ≥ 3 successful
    patches.
  - `reinforce_signal=success` when a patch PR is merged without
    re‑introducing the same vulnerability; `fail` when a patch is rejected
    or a re‑scan still reports the same CVE.
- **Model-side self‑improvement.** Patch‑generation traces become high‑value
  SFT samples (vulnerability → fix → CI‑pass → merge) that the trainer can
  use to improve code‑completion and fix‑suggestion capabilities. DPO pairs
  arise when an initially rejected patch is later revised and merged.

---

## Idea

The security team wants to automatically remediate CVEs reported by
Dependabot, GitHub Advisory DB, or internal scanner feeds. Currently this
requires a human to triage, write a patch, get it reviewed, and monitor the
CI pipeline. Lamark replaces each step:

### Step 0 — Vulnerability ingestion

1. **Scanner webhook** (Dependabot, GH Advisory, internal SAST) fires on a
   new CVE entry → `lamark-gateway` receives `Op::ExternalEvent`
   `{source: "dependabot", type: "cve", payload: {...}}`.  
2. Gateway forwards the event to the **Security Coordinator** (`lamark-coordinator`
   sub‑agent).  
3. Coordinator creates a `PatchTask` on the Kanban board:
   `{cve_id, package, version_range, severity, source_url}`.

### Step 1 — Dependency resolution & context loading

4. Coordinator queries `GET /memory/search?q=dep‑ownership` — retrieves the
   module‑ownership map for the affected package (e.g., “`lodash` →
   `utils`”).  
5. Reads `GET /knowledge/vuln/<cve_id>` — pulls the detailed advisory,
   CVSS score, and any known exploit mitigations.  
6. Updates the `PatchTask` with context: severity, affected scope, and a
   short summary to be used in the patch description.

### Step 2 — Patch generation

7. **Patch agent** picks up the task.  
8. Uses `Grep`/`Read` to locate the vulnerable file(s) in the monorepo
   (restricted to the owned module via the ownership map).  
9. Calls a **code‑suggestion** tool (e.g., `claude-code-suggest` or an
   integrated LLM endpoint) with a prompt like:
   ```
   Fix CVE-2026-12345 in src/utils/lodash.js: avoid prototype‑pollution
   when merging user input. Keep existing API unchanged.
   ```
   The tool returns a diff; the agent validates it with `is_read_only=true`
   (no destructive changes).  
10. Agent writes the diff via `Write` (requires `Prompt` on first creation).
    - The diff is stored in `~/.lamark/patches/<cve_id>.diff`.  
    - A `memory_fact` records the patch location: `{cve_id, file, line_range}`.

### Step 3 — PR creation & CI gating

11. Agent creates a branch `patch/<cve_id>` and pushes the diff (`GitTool::CreateBranch`,
    `GitTool::Commit`).  
12. `GitTool::CreatePR(title="Patch CVE‑2026‑12345", body="Fixes CVE‑2026‑12345
    – <short‑summary>", labels=["security", "auto‑patch"])`.  
13. CI system automatically runs; the **CI‑Watcher Agent** polls the status.
    - If CI passes → proceed.  
    - If CI fails → CI‑Watcher posts a comment on the PR with the failure
      details and marks the task `retry_needed`.

### Step 4 — Review & merge

14. Security reviewer receives a Slack notification (`#sec‑ops`):
    ```
    🔐 Auto‑patch: CVE‑2026‑12345 (lodash 4.17.21 → 4.17.22)
    → <link to PR>
    ```  
    Reviewer can `/approve_patch`, `/request_changes`, or `/reject_patch`.  
15. Agent reacts:
    - `Approve` → merges the PR (`GitTool::MergePR`), updates the `PatchTask`
      status to `merged`.  
    - `Reject` → posts a comment with feedback, marks task `rejected`.  
    - `Changes requested` → updates the diff, pushes a new commit, and
      re‑queues CI.

### Step 5 — Post‑merge verification & rollout

16. If merged, the **Release‑Monitor Agent** watches the production deployment
    pipeline for the affected package (e.g., a library version bump in the
    artifact store).  
17. On successful deployment, the agent:
    - Posts a final Slack message: “✅ CVE‑2026‑12345 patched and deployed
      (lodash 4.17.22)”.  
    - Writes `memory_fact`: `{cve_id, status=patched, patched_version=4.17.22,
      deployment_date=2026‑05‑25}`.  
    - If a later scan still reports the CVE, the agent creates a
      `HotfixTask` to re‑evaluate the patch.

### Step 6 — Knowledge‑base sync & signal generation

18. `POST /knowledge/patches` stores the full patch bundle (diff + CI logs +
    PR metadata) under the CVE ID.  
19. `reinforce_signal=success` is attached if the patch merged cleanly and
    the vulnerability disappears from subsequent scans; `fail` otherwise.  
20. The Security Coordinator updates the Kanban board: `PatchTask` →
    `completed` or `failed`.

---

## Actors

| Actor | Role |
|---|---|
| **Security Coordinator** | `lamark-coordinator` sub‑agent. Owns the patch Kanban board, orchestrates ingestion, patch generation, PR lifecycle. |
| **Patch Agent** | Executes steps 7‑12, generates diff, writes to repo, creates PR. |
| **CI‑Watcher Agent** | Polls CI status, posts results back to the PR, triggers retries. |
| **Security Reviewer** | Human or bot that approves/rejects the auto‑generated PR via Slack reactions. |
| **Release‑Monitor Agent** | Verifies that a patched dependency reaches production; triggers hot‑fix if needed. |
| **Gateway** | Slack adapter – posts CVE alerts, PR notifications, final patch‑merged messages. |
| **Knowledge‑base** | Stores CVE advisories, patch diffs, patch‑status facts, and scan results. |
| **Dependabot / Advisory Feed** | External source that triggers the initial webhook. |

---

## Trigger

1. **Automatic CVE webhook** – any CVE entry in Dependabot, GitHub Advisory DB,
   or internal scanner that provides a JSON payload fires the gateway.  
2. **Manual triage override** – an analyst can run:  
   ```
   $ lamark security patch --cve CVE-2026-12345 --package lodash --version ">=4.17.0 <5"
   ```
   to create a `PatchTask` from the command line.

Both paths result in a new `PatchTask` on the Kanban board.

---

## Pipeline

### Step 0 — Bootstrap & task creation

1. Coordinator reads `GET /memory/search?q=security‑policy` – recalls the
   organization’s patch‑severity thresholds and required approval workflow.  
2. Creates a Kanban card `Patch CVE‑2026‑12345 – lodash` with fields:
   - `cve_id`  
   - `package`  
   - `severity` (high/medium/low)  
   - `source_url`  
   - `patch_status=pending`  

### Step 1 — Context loading

3. Queries `GET /knowledge/vuln/<cve_id>` – pulls the advisory text, CVSS
   score, and any known exploit techniques.  
4. Reads `GET /memory/search?q=dep‑ownership` – obtains the module that
   owns the affected package, restricting later file operations to that
   subtree.  

### Step 2 — Patch generation

5. Patch agent locates the vulnerable file(s) using `Grep` limited to the
   owned module.  
6. Sends a code‑suggestion request to the configured LLM endpoint with the
   crafted prompt (see step 8 of the pipeline).  
7. Agent validates the returned diff (`is_read_only=true`) and writes it
   via `Write`.  
8. `memory_fact` records: `{cve_id, file_path, diff_hash, created_at}`.  

### Step 3 — PR creation & CI gating

9. Agent creates a branch `patch/<cve_id>` and pushes the diff.  
10. `GitTool::CreatePR` opens a PR with a description that includes the CVE
    reference and a link to the advisory.  
11. CI‑Watcher Agent polls the PR status:
    - **Pass** → sets `patch_status=ci_passed`.  
    - **Fail** → posts a comment with the failure log, sets `patch_status=ci_failed`.  

### Step 4 — Review & merge decision

12. Security reviewer receives a Slack ping (`#sec‑ops`) with the PR link.
    - Reaction `👍` → `/approve_patch` → merges the PR.  
    - Reaction `❌` → `/reject_patch` → closes the PR, marks `patch_status=rejected`.  
    - Reaction `🔄` → `/request_changes` → agent updates the diff, pushes a new commit,
      and CI restarts automatically.  

### Step 5 — Post‑merge verification

13. If merged, `Release‑Monitor Agent` watches for the patched package’s
    version bump in the artifact store (e.g., `lodash@4.17.22` appears).  
14. On successful deployment, the agent:
    - Posts a final Slack success message.  
    - Updates `memory_fact`: `{cve_id, status=patched, patched_version=4.17.22,
      deployment_timestamp=2026‑05‑25}`.  
    - Marks the `PatchTask` as `completed`.  

### Step 6 — Knowledge‑base sync & signal generation

15. `POST /knowledge/patches` stores the full patch artifact (diff, CI logs,
    PR metadata) under the CVE ID.  
16. `reinforce_signal` is set based on the final outcome:
    - `success` if the CVE disappears from subsequent scans and the patch
      remains merged.  
    - `fail` if a later scan still reports the vulnerability or the merge
      fails.  
17. Kanban board updates: task moves to `completed` or `failed`.  

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0 (bootstrap, task creation) | `lamark-coordinator`, `lamark-config`, `lamark-kb-client` | 05a, 03 |
| 1 (vuln lookup) | `lamark-kb-client`, external vulnerability feed tool | 07a, 11 |
| 2 (patch generation) | `lamark-tools` (Grep/Read), `lamark-core` (turn loop), custom LLM‑suggest tool | 05, 07 |
| 3 (diff write) | `lamark-write` (Write), `lamark-policy` (Prompt) | 05, 06 |
| 4 (PR & CI) | `lamark-tools` (GitTool, GitHub/GitLab MCP), `lamark-gateway` (CI‑Watcher) | 05c, 09 |
| 5 (review & merge) | `lamark-gateway` (Slack reactions → ACP), `lamark-acp` | 12 |
| 6 (post‑merge monitoring) | `lamark-release-monitor` (custom), `lamark-kb-client` | custom |
| 7 (KB sync) | `lamark-kb-client` | 07a |
| 8 (signal generation) | `lamark-policy` (reinforce_signal handling) | 06 |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **CVE webhook missing payload** | Gateway returns a 400 and logs the malformed event; no task is created. |
| **Patch diff contains destructive changes** | `is_destructive=true` triggers `PermissionRequest`; policy defaults to `Forbidden`; agent aborts and posts a warning. |
| **CI job times out** | CI‑Watcher retries 2× with exponential backoff; on final failure, posts a Slack alert “⚠️ CI timeout for CVE‑patch – manual investigate”. |
| **Reviewer never reacts** | After N hours, Coordinator escalates with a reminder ping; if still silent, task is auto‑closed after 48 h to avoid stale PRs. |
| **Patch merges but later scan still shows CVE** | Release‑Monitor triggers a `HotfixTask` to re‑evaluate; if the CVE persists, the patch is marked `fail` and a new patch cycle begins. |
| **KB write fails** | Writes to local outbox; retries every 30 s; trace bundle remains available for later upload. |
| **Memory fact conflict (CVE already patched)** | Agent checks for existing `memory_fact` with the same CVE ID; if present, skips regeneration and directly proceeds to PR creation, avoiding duplicate work. |

---

## Acceptance criteria

- [ ] A new CVE webhook automatically creates a `PatchTask` with full context.  
- [ ] Patch generation respects module ownership and never writes outside the
  designated subtree.  
- [ ] All generated diffs are reviewed and either merged or rejected by a
  security reviewer via Slack reactions.  
- [ ] Successful merges result in a version bump of the patched dependency
  and a `memory_fact` recording the patched version and deployment timestamp.  
- [ ] If a patch fails CI, the failure is posted to the PR with a retry
  mechanism.  
- [ ] Upon successful merge and deployment, a final Slack announcement is
  posted and the `PatchTask` status is set to `completed`.  
- [ ] All patch artifacts and outcomes are stored in knowledge‑base for
  future audit and learning.  
- [ ] `reinforce_signal` correctly reflects success/failure and is used by
  the trainer to improve future patch‑generation prompts.  

---

## Self‑improvement assertions

1. **SFT samples for patch generation.** Each successful patch diff produces a
   Nemotron‑Agentic‑v2 entry covering `code‑suggest → diff → write → CI`.  
2. **Skill promotion for patch templates.** After ≥ 3 patches, Curator promotes a
   reusable “patch‑template” skill that supplies a structured comment block
   and commit‑message convention, reducing prompt length by ~25 %.  
3. **Memory recall reduces duplicate scans.** Once a CVE is marked `patched`,
   future scans skip it; the agent logs a recall hit, cutting redundant
   vulnerability checks by ≥ 30 % on subsequent runs.  
4. **Threshold learning for severity handling.** Curator may suggest raising
   the severity threshold for auto‑patch based on historical false‑positive
   rates; adoption should reduce manual triage by ≥ 15 %.  
5. **Reinforcement‑signal impact.** `reinforce_signal=success` from a merged
   patch is fed back to the trainer, increasing the probability that similar
   vulnerability‑fix patterns are selected for future code‑completion
   suggestions.

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| CVE webhook ingestion → Kanban creation | plan/05a §"Coordinator" + plan/09 §"Gateway" | _audit_ |
| Dependency‑ownership recall | plan/07a §"Memory providers" | _audit_ |
| Patch diff generation (Grep/Read/Write) | plan/05 §"Tool registry", plan/06 §"Hook bus" | _audit_ |
| PR creation & CI gating | plan/05c §"sandbox" + custom CI‑Watcher | _audit_ |
| Review workflow via Slack reactions | plan/09 §"Gateway" + plan/12 §"ACP" | _audit_ |
| Post‑merge deployment monitoring | plan/16 §"Forgetting probe → auto‑rollback" (adapted) | _audit_ |
| Knowledge‑base storage of patch artifacts | plan/07a | _audit_ |
| Memory fact writes (CVE → patched_version) | plan/07a | _audit_ |
| Failure handling & escalation | plan/11 §"build-test-deploy" | _audit_ |
| Reinforce‑signal generation & storage | plan/07a, plan/08 | _audit_ |

---

## Open questions

1. **External scanner integration.** Should we support multiple feeds (Dependabot,
   GH Advisory, internal SAST) via a unified MCP server, or keep them as separate
   webhook sources?  
2. **Patch review quorum.** Do we require a minimum number of approval reactions,
   or is a single manager sufficient? This influences permission policies.  
3. **Patch retention policy.** How long should a patched diff remain in KB before
   automatic pruning? Consider 90 days or until a new version of the package is
   released.  
4. **Rollback for bad patches.** If a merged patch later introduces a regression,
   should Lamark automatically open a hot‑fix branch, or rely on manual triage?  
5. **Patch authorship attribution.** Should the generated commit include a
   standardized author line (e.g., `Automated: CVE‑2026‑12345`) for audit
   traceability?  
