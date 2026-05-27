# 23 — Deployment Pipeline Automation

> **Phase:** P8 (CI/CD + build) + P9 (gateway, remote UI) + P10 (monitoring).
> **One-liner:** Lamark watches a CI build finish, validates the artifact against
> quality gates, automatically generates a release‑notes draft, publishes the
> versioned package to the internal artifact store, and notifies stakeholders
> through Slack and the product roadmap board — all without manual CLI
> interactions.

---

## North-star contribution

- **Domain quality (release engineering).** Turns a multi‑step, error‑prone
  manual release process into a deterministic, auditable, single‑click flow.
  Every release artifact is traceable from source commit → build → test →
  promotion → deployment, with full trace bundles stored in knowledge‑base.
- **Agent-side self‑improvement.** Emits:
  - `memory_fact` writes: version → deployment date, target environment,
    rollback URL, and “promoted‑by” reason (e.g., “all eval gates passed”).
    Recalled by future release agents to auto‑populate version metadata.
  - `skill_draft` candidates: reusable release‑notes templates (changelog
    generator, semantic‑versioning formatter) promoted by Curator after ≥ 3
    successful releases.
  - `reinforce_signal=success` when a release reaches *green* status on all
    monitoring dashboards; `fail` if any gate aborts.
- **Model-side self‑improvement.** Release‑notes generation uses the same
  prompt‑composer and cache logic as `plan/07`, so successful drafts become
  SFT samples for the document‑authoring agent. Evaluation‑gate failures
  generate DPO pairs (rejected → accepted notes) that improve future
  drafting quality.

---

## Idea

The release engineering team needs to ship a new version of the core
backend service every two weeks. Currently this involves manually:
1. Verifying CI passes on all affected repos.
2. Promoting the built Docker image to the staging registry.
3. Running a script that generates a release‑notes markdown file from
   changelog PRs.
4. Posting a Slack announcement with upgrade instructions.
5. Updating the product roadmap board with the new version milestone.

Lamark replaces the entire workflow:

### Step 0 — Build completion notification

1. CI system (TeamCity) fires a webhook on successful build → `lamark-gateway`
   receives `Op::ExternalEvent { source: "teamcity", type: "build_success" }`.  
2. Gateway forwards the event to the **Release Coordinator** (`lamark-coordinator`
   sub‑agent).  
3. Coordinator creates a `ReleaseTask` on the Kanban board:
   `{build_id, version, target_env, artifact_path}`.

### Step 1 — Quality‑gate validation

4. Coordinator spawns a **Gate Agent** that checks:
   - **Test coverage** (`Bash("coverage report --fail-under=80")`) → passes?  
   - **Security scan** (`clamav-scan $artifact_path`) → clean?  
   - **Performance benchmark** (`Bash("$benchmark_tool $artifact_path")`) → meets SLA?  
   - **Policy guard** (`lamark-policy` allows only pre‑approved Docker tags).  
   Each check is a separate tool call; all must return `Allow`.  
   On failure, the agent posts a detailed Slack message with error logs and
   marks the task `failed`.

### Step 2 — Artifact publishing

5. If all gates pass, the agent runs `DockerTool::Push(image=$version,
   repository=internal/registry/service)` — pushes the versioned image to the
   internal registry.  
6. `ArtifactTool::Upload(path=$artifact_path, dest=/releases/$version)` uploads
   any additional binaries, helm charts, or config bundles to the artifact
   store (`../knowledge-base` object store endpoint).  
7. `POST /knowledge/releases` writes a **release‑metadata** record:
   `{version, sha, build_id, promoted_by=coordinator, timestamp}`.

### Step 3 — Release‑notes generation

8. Release‑notes agent reads all merged PR titles/descriptions from
   `GET /knowledge/prs?since=<last_release_tag>` (via GitHub/GitLab MCP).  
9. Using the recalled changelog‑template skill, it composes a markdown file
   `CHANGELOG-$version.md` in the `docs/release/` directory.  
10. The draft is written via `Write` (requires `Prompt` on first creation;
    subsequent updates are `Allow`).  
11. Draft is posted to Slack for review: `#release‑ops: “Release
    $version draft ready – <link>”.`

### Step 4 — Stakeholder notification & roadmap sync

12. Once the Slack reviewers approve (via reactions or `/approve_release`),
    the coordinator:
    - Calls `RoadmapTool::AddMilestone(version, due_date, description)` to
      create a new milestone on the product roadmap board.  
    - Posts a final Slack announcement with upgrade instructions and a link
      to the release notes.  
    - Updates the `memory_fact`: `{version, deployed=true, env=staging}`.

### Step 5 — Post‑release monitoring

13. A **Monitor Agent** watches the production deployment status (via
    `TeamCityTool::GetBuildStatus` on the release‑promotion build).  
14. If the build turns green, the agent records `reinforce_signal=success`
    and marks the release task `completed`.  
15. If a post‑deployment alert fires (e.g., high error rate), the agent
    creates a `HotfixTask` and notifies the on‑call engineer through the
    Slack gateway.

---

## Actors

| Actor | Role |
|---|---|
| **Release Coordinator** | `lamark-coordinator` sub‑agent. Owns the release Kanban task, orchestrates gate checks, artifact publishing, and milestone creation. |
| **Gate Agent** | Runs quality‑gate tool calls (`Bash`, `clamav-scan`, custom benchmark). |
| **Artifact Publisher** | Uses `DockerTool` / `ArtifactTool` to push images and upload binaries. |
| **Release‑notes Agent** | Synthesises changelog data into markdown using recalled template skill. |
| **Roadmap Tool** | Updates the product roadmap board (via MCP/ACP). |
| **Monitor Agent** | Watches post‑deployment health, triggers hotfix if needed. |
| **Gateway** | Slack adapter – posts build‑success, review requests, final release announcements. |
| **Knowledge‑base** | Stores release‑metadata, changelog drafts, and version‑to‑metadata facts. |
| **CI System (TeamCity)** | Emits webhook events that trigger the whole pipeline. |

---

## Trigger

1. **CI build success webhook** – automatic entry point.  
2. **Manual promotion** – if a release is hot‑fixed out‑of‑band, an operator can run:  
   ```
   $ lamark release promote --build-id 12345 --version 2.5.1 --target prod
   ```

Both paths create a `ReleaseTask` on the Kanban board.

---

## Pipeline

### Step 0 — Bootstrap & Kanban creation

1. Coordinator reads config (`lamark-config`) for registry URLs, target
   environments, and required thresholds.  
2. Reads `GET /memory/search?q=release‑policy` – recalls the current release
   policy (e.g., “requires 80 % coverage, no critical CVEs”).  
3. Creates a Kanban card `Release 2.5.1 → prod` with fields:
   - `build_id` (from webhook)  
   - `version`  
   - `target_env`  
   - `artifact_path` (Docker image tag, etc.)  

### Step 1 — Quality‑gate validation

4. Gate Agent executes a series of **is_read_only** or **is_destructive** tool
   calls:
   - `Bash("cargo test --locked --check")` → `Allow` if exit 0.  
   - `clamav-scan $artifact_path` → `Allow` if clean.  
   - `BenchmarkTool::Run($benchmark_cfg)` → `Allow` if latency ≤ SLA.  
   - `GitTool::ReadFile(path=.github/RELEASE_POLICY.md)` → policy check.  
   All results are streamed back to the Kanban card as evidence attachments.  
5. If any gate returns `Reject`, the task is marked `failed` and a Slack
   message lists the failures with links to logs.

### Step 2 — Artifact publishing

6. On `Allow` from all gates, Coordinator calls `DockerTool::TagAndPush`
   (`image=internal/registry/service:$version`).  
7. `ArtifactTool::Upload` stores any secondary assets (helm charts, config
   JSON) in the artifact store under `releases/$version/`.  
8. `POST /knowledge/releases` persists a **release‑metadata** record and
   returns a `release_id` for later reference.

### Step 3 — Release‑notes generation

9. Release‑notes agent queries `GET /knowledge/prs?since=<last_tag>` via the
   GitHub MCP connector, receiving a list of merged PRs with titles and bodies.  
10. Using the recalled **changelog‑template** skill, it builds a markdown
    changelog:  
    ```markdown
    ## 2.5.1 (2026‑05‑25)
    - **Feature:** Added batch‑import API (`POST /import/batch`)
    - **Bugfix:** Fixed race condition in connection pool
    - **Performance:** Reduced query latency by 12 %  
    ```  
11. The draft is written to `docs/release/CHANGELOG-2.5.1.md` via `Write`.  
12. A Slack message pings the `#release‑ops` channel with a link to the draft
    and asks for approval reactions (`👍` = approve, `❌` = reject).

### Step 4 — Stakeholder notification & roadmap sync

13. Once the Slack message gathers the required `👍` reactions, the
    **Roadmap Tool** creates a new milestone:
    ```
    milestone:
      id: "rel-2.5.1"
      title: "Release 2.5.1 – May 2026"
      description: "Backend service v2.5.1 – batch import, performance fixes"
      due_date: "2026‑06‑01"
      owner: "backend-team"
    ```  
14. Coordinator posts the final announcement:  
    ```
    🚀 Release 2.5.1 is live on prod!
    • Upgrade steps: https://internal/docs/upgrade/2.5.1
    • Changelog: https://repo/docs/release/CHANGELOG-2.5.1.md
    ```  
15. `memory_fact` written: `{version=2.5.1, deployed=true, env=prod,
    promoted_by=coordinator, release_id=abc123}`.

### Step 5 — Post‑release monitoring

16. **Monitor Agent** polls the production deployment build (`TeamCityTool::GetBuildStatus`).  
17. If the build turns green, the agent:
    - Posts a “✅ Release 2.5.1 healthy” Slack message.  
    - Updates the release‑metadata record with `status=green`.  
    - Marks the Kanban task as `completed`.  
18. If an alert fires (e.g., error‑rate > 5 %), the agent creates a
    `HotfixTask` on the Kanban board and notifies the on‑call engineer via
    Slack.

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0 (bootstrap, Kanban) | `lamark-coordinator`, `lamark-config`, `lamark-kb-client` | 05a, 03 |
| 1 (gate checks) | `lamark-tools` (Bash, clamav-scan, BenchmarkTool), `lamark-policy` | 05, 06 |
| 2 (artifact publish) | `lamark-tools` (DockerTool, ArtifactTool), `lamark-providers` | 05c, 04 |
| 3 (release‑notes) | `lamark-tools` (Write), `lamark-skills` (changelog‑template) | 07a, 08 |
| 4 (roadmap sync) | `lamark-mcp` / `lamark-acp` (RoadmapTool) | 11, 12 |
| 5 (monitoring) | `lamark-coordinator`, `lamark-gateway`, `lamark-tools` (TeamCity) | 05a, 09 |
| 6 (Slack notifications) | `lamark-gateway` (Slack adapter) | 09 |
| 7 (memory fact writes) | `lamark-kb-client` | 07a |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Gate tool timeout** | Agent aborts the gate, marks the task `failed`, and posts a Slack summary with the timeout duration; retries are scheduled after exponential backoff. |
| **Artifact push fails** (registry auth) | Agent retries 3 times; on final failure, posts a Slack alert “❌ Docker push failed – manual intervention required”. |
| **Changelog‑template skill not found** | Agent falls back to a minimal markdown skeleton; logs a warning and escalates to a human Slack channel. |
| **Roadmap API rate‑limited** | Agent backs off with jitter; if still failing after 2 retries, posts a “manual roadmap update required” notice. |
| **Post‑release monitor misses green state** | Monitor retries every minute for up to 10 min; if still red, escalates to on‑call via Slack. |
| **Slack message delivery failure** | Retries with exponential backoff; if still failing, writes the message to a local outbox and includes a link in the next successful delivery. |
| **Memory fact write to KB fails** | Writes to local outbox; retries every 30 s; trace bundle remains available for later upload. |

---

## Acceptance criteria

- [ ] A CI build success automatically creates a `ReleaseTask` and begins gate validation.  
- [ ] All quality gates must pass before any artifact is pushed; failures are reported in Slack with actionable details.  
- [ ] Upon successful gates, the versioned Docker image and any secondary artifacts are published to the internal store.  
- [ ] A changelog draft is generated, written to `docs/release/`, and posted to Slack for approval.  
- [ ] After Slack approval, a roadmap milestone is created and the final release announcement is posted.  
- [ ] The release‑metadata record (version, build_id, promoted_by, timestamp) is stored in KB.  
- [ ] Post‑release monitoring detects a green build and updates the release status to `green`; any failure triggers a hotfix task.  
- [ ] All steps are idempotent – re‑running the same pipeline on an already‑deployed version is a no‑op unless a new build is detected.  
- [ ] Memory facts for version → deployment info are recalled correctly in a subsequent `lamark release status` query.

---

## Self‑improvement assertions

1. **SFT samples for gate validation.** Each gate‑check turn produces a Nemotron‑Agentic‑v2 entry covering `Bash → ExecCommand → memory_fact`.  
2. **Skill promotion for changelog templates.** After ≥ 3 successful releases, Curator promotes a `changelog-template` skill; subsequent releases use it, reducing drafting tokens by ≥ 20 %.  
3. **Threshold learning.** Curator may suggest a new coverage‑gate threshold based on historical success rates; after adoption, false‑negative failures drop by ≥ 10 %.  
4. **Memory recall accelerates gate prep.** Future releases recall the list of required gates from memory, skipping the `GET /memory/search?q=release-policy` step and cutting 15 s off pipeline start‑up.  
5. **Reinforce signal propagation.** Successful releases generate `reinforce_signal=success` in the release‑metadata; the trainer up‑weights the successful pipeline configuration for future runs.

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| CI webhook intake → Kanban creation | plan/05a §"Coordinator" + plan/09 §"Gateway" | _audit_ |
| Quality‑gate tool calls (Bash, scan, benchmark) | plan/05 §"Tool registry", plan/06 §"Hook bus" | _audit_ |
| Artifact publishing (Docker, generic upload) | plan/05c §"sandbox" + custom ArtifactTool | _audit_ |
| Release‑notes generation via skill | plan/07a §"Memory providers" + plan/08 §"Curator" | _audit_ |
| Roadmap milestone creation (MCP/ACP) | plan/11 §"MCP client", plan/12 §"ACP" | _audit_ |
| Slack notifications (build, approval, final) | plan/09 §"Gateway" | _audit_ |
| Post‑release monitoring & hotfix task | plan/16 §"Forgetting probe → auto‑rollback" (adapted) | _audit_ |
| Memory fact writes (release‑metadata) | plan/07a | _audit_ |
| Failure handling & retries (outbox, backoff) | plan/11 §"build-test-deploy" | _audit_ |

---

## Open questions

1. **Gate tool ownership.** Should each quality gate live in its own dedicated tool crate (e.g., `lamark-gate-coverage`, `lamark-gate-security`) for clearer versioning?  
2. **Artifact store schema.** Do we store checksums, signatures, and provenance metadata alongside each uploaded asset? This affects auditability.  
3. **Roadmap integration format.** Is the roadmap board a generic MCP service or a specific Linear/YouTrack connector? The answer dictates the exact tool implementation.  
4. **Approval quorum.** Should a release require a fixed number of 👍 reactions, or can a single manager approve? This impacts policy design.  
5. **Rollback for release.** If a released version must be rolled back, should Lamark support atomic image rollback via the registry, or only mark the release as failed and let humans handle it?  
