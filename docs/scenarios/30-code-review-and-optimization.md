# 30 — Automated Code Review & Optimization

> **Phase:** P5–P6 (coordinator, sandbox) + P7–P8 (memory + KB) + P9 (gateway).
> **One‑liner:** Lamark automatically reviews every code change, suggests
> performance‑oriented refactorings, checks adherence to coding standards,
> generates optimization proposals, and tracks the impact of applied changes
> through trace bundles stored in the knowledge‑base.

---

## North‑star contribution

- **Domain quality (software engineering).** Transforms code review from a
  manual, subjective activity into a data‑driven, repeatable process that
  catches performance regressions, enforces style, and continuously improves
  code quality without human overhead.
- **Agent‑side self‑improvement.** Emits:
  - `memory_fact` writes: `code_pattern:<pattern_id> → issue_count, fix_count,
    success_rate, last_seen`. Recalled by future review agents to prioritize
    high‑impact patterns.
  - `skill_draft` candidates: reusable refactoring templates (e.g., “replace
    loop with `stream().filter()`”) promoted by Curator after ≥ 3 uses.
  - `reinforce_signal=success` when a proposed optimization is applied and
    benchmark shows performance gain; `fail` when the change introduces a
    regression or is rejected.
- **Model‑side self‑improvement.** Review traces become high‑value SFT samples
  (code snippet → review comment → applied patch). DPO pairs arise when a
  rejected suggestion is later revised and merged.

---

## Idea

The engineering team merges dozens of pull requests daily. Today reviews are
manual, inconsistent, and often miss performance‑related issues. Lamark
automates this workflow:

### Step 0 — PR ingestion & context loading

1. **PR ingestion webhook** (GitHub/GitLab/Merger) fires on a new PR → `lamark-gateway`
   receives `Op::ExternalEvent {source:"github", type:"pull_request", payload:{id,
   title, diff_url, author}}`.  
2. Gateway forwards the event to the **Review Coordinator** (`lamark-coordinator`
   sub‑agent).  
3. Coordinator creates a `ReviewTask` on the Kanban board:
   `{pr_id, repo, author, title, diff_url, base_branch, target_branch}`.  
4. Queries `GET /knowledge/search?q=code‑ownership` — retrieves the
   module‑ownership map for the PR’s changed paths.  
5. Recalls any prior `memory_fact` entries for the author (e.g., “Alice prefers
   `final` over `var`”).  

### Step 1 — Automated static analysis

6. **Static Analyzer Agent** checks out the PR diff (using `GitTool::Checkout`)
   and runs a series of **linters** and **performance scanners**:
   - `ESLint` / `clang-tidy` for style violations.  
   - `perf-report` (or language‑specific profiler) to identify hot loops,
     unnecessary allocations, or inefficient algorithmic patterns.  
   - `SecurityScanner` (e.g., `bandit`, `snyk`) for known vulnerabilities.  
   Each tool call is gated by `is_read_only=true` → `Allow` by default.  
7. Results are streamed back as structured JSON (e.g., `{rule:"unused-variable",
   severity:"low", location:src/utils/x.js:12}`).  

### Step 2 — Code‑review generation

8. Review Agent synthesizes the static‑analysis output into a **review comment
   bundle**:
   - **Performance concerns:** “Hot loop in `sortBy()` consumes 45 % of CPU;
     consider using `sorted-set` for O(log n) instead of `Array.sort`.”  
   - **Style issues:** “File `config.yaml` uses tabs; project uses spaces (PEP‑8).”  
   - **Security findings:** “Potential SQL injection in `UserInputParser`; use
     parameterized queries.”  
   - **Suggested refactorings:** “Extract `calculateTax()` into a pure function
     for testability.”  
9. The bundle is written to `docs/review/<pr_id>-feedback.md` via `Write`.  

### Step 3 — Review publication & approval

10. The generated markdown is posted to Slack (`#code-review`) with a link and
    a reaction prompt: “👍 Approve, 👎 Reject, 💬 Comment”.  
12. Reviewer reacts:
    - `👍` → `ReviewAgent` calls `GitTool::MergePR` → marks `ReviewTask` as
      `approved`.  
    - `👎` → posts a comment with the reason; task marked `rejected`.  
    - `💬` → opens a thread for discussion; agent updates the comment with
      additional suggestions.  

### Step 4 — Optimization proposal & execution

13. If the PR passes review, the **Optimization Agent** may propose a
    performance‑oriented change (e.g., “replace `Array.map` with
    `stream().collect(Collectors.toList())` for 30 % faster collection”).  
14. Proposal is written as a comment on the PR and as a draft `Edit` operation
    (e.g., `Edit {path: src/utils/collect.js, old_string: "map(...)", new_string:
    "reduce((a,b) => a + b, 0)"}`).  
15. The edit is **Prompted** (requires reviewer approval). Once approved,
    the change is pushed, and the **Performance Verifier Agent** runs benchmarks
    (via `BenchmarkTool::Run`) to confirm the improvement.  

### Step 5 — Impact tracking & signal

16. After merge, the **Impact Agent** queries the CI logs for performance
    metrics (e.g., `Jenkins::GetBuildTime(pr_id)`).  
17. If the change yields a measurable gain (e.g., “CPU time ↓ 22 %”), the
    agent writes `memory_fact`: `{pr_id, improvement:true, metric:cpu_time,
    before:45ms, after:35ms, gain_months=0.5}`.  
18. `reinforce_signal=success` is attached if the improvement is confirmed;
    `fail` if the benchmark shows regression.  

### Step 6 — Knowledge‑base sync & learning

19. All feedback, proposals, and benchmark results are stored in KB:
    `POST /knowledge/code_reviews/<pr_id>` includes the markdown comment,
    static‑analysis output, and performance diff.  
20. `memory_fact` records the final outcome (`status=merged`, `performance_gain=`,
    `reviewer=alice`).  
21. If a proposal is rejected or a regression occurs, the system logs a
    `reinforce_signal=fail` and creates a `LearningTask` for future pattern
    avoidance.  

---

## Actors

| Actor | Role |
|---|---|
| **Review Coordinator** | `lamark-coordinator` sub‑agent. Owns the PR Kanban board, orchestrates ingestion, static analysis, and review workflow. |
| **Static Analyzer Agent** | Executes linters, performance scanners, and security scanners on PR diffs. |
| **Review Agent** | Generates markdown feedback, publishes to Slack, handles reviewer reactions. |
| **Optimization Agent** | Proposes performance refactorings; writes edit diffs; triggers benchmark verification. |
| **Performance Verifier Agent** | Runs benchmark suites on applied changes; validates claimed gains. |
| **Impact Agent** | Tracks post‑merge metrics, updates KB, attaches `reinforce_signal`. |
| **Gateway** | Slack adapter – posts PR links, feedback, approval prompts, and remediation alerts. |
| **Knowledge‑base** | Stores PR feedback, static‑analysis output, benchmark diffs, and performance facts. |
| **Stakeholders** | Review comments, approve/reject via Slack reactions, monitor performance impact. |

---

## Trigger

1. **PR webhook** – automatic ingestion of new pull requests from GitHub/GitLab/
   Bitbucket.  
2. **Manual PR command** – an engineer can run:  
   ```
   $ lamark pr review --url https://github.com/org/repo/pull/123
   ```
   to force processing of a specific PR.  

Both paths create a `ReviewTask` on the Kanban board.

---

## Pipeline

### Step 0 — Bootstrap & PR intake

1. Coordinator reads `GET /memory/search?q=pr‑policy` – loads the
   organization’s PR policy (e.g., “all PRs must pass `ESLint` and `perf-report`”).  
2. Creates a Kanban card with fields: `pr_id`, `repo`, `author`, `title`,
   `diff_url`, `base_branch`, `target_branch`, `status=pending`.  

### Step 1 — Static analysis

3. Agent checks out the PR diff (`GitTool::Checkout`).  
4. Runs linters (`ESLint`, `clang-tidy`) and performance scanners
   (`perf-report`, `BenchmarkTool::Profile`).  
5. Each tool call returns structured results; violations are stored as
   `memory_fact`: `{pr_id, issue_type, rule, severity, location}`.  

### Step 2 — Review comment generation

6. Review Agent synthesizes findings into a markdown comment:
   - Lists each issue with severity and location.  
   - Provides a short “Suggested fix” for each.  
   - Formats the comment as `docs/review/<pr_id>-feedback.md`.  
7. Writes the file via `Write` (requires `Prompt` on first creation).  

### Step 3 — Review publication

8. Coordinator posts the markdown link to Slack (`#code-review`) and awaits
   reactions.  
9. Reviewer reacts:
   - `👍` → `GitTool::MergePR` → marks task `approved`.  
   - `👎` → posts a comment with explanation; marks `rejected`.  
   - `💬` → opens a discussion thread; agent updates the comment with follow‑up
     suggestions.  

### Step 4 — Optimization suggestion & edit

9. If the PR passes review, Optimization Agent suggests a performance refactor.  
10. Agent drafts an `Edit` operation to modify the target file.  
11. Edit is **Prompted** (requires approval). Once approved, it is executed
    (`Write` → `GitTool::Commit`).  

### Step 5 — Benchmark verification

12. Performance Verifier Agent runs the relevant benchmark suite (`BenchmarkTool::Run`).  
13. Compares before/after metrics; if gain ≥ threshold, marks the change as
    successful.  

### Step 6 — Impact tracking

13. Impact Agent queries CI for build time or performance metrics, writes
    `memory_fact`: `{pr_id, improvement:true, metric:cpu_time, before:45ms,
    after:35ms, gain_months=0.5}`.  
16. `reinforce_signal` is set to `success` on confirmed gain; `fail` on regression.  

### Step 7 — Knowledge‑base sync

17. All static‑analysis output, review comments, optimization proposals,
    and benchmark diffs are stored in KB via `POST /knowledge/code_reviews/<pr_id>`.  
18. Final outcome (`merged`, `performance_gain`, `reviewer`) is recorded as a
    `memory_fact`.  

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| PR intake & Kanban creation | `lamark-coordinator`, `lamark-config` | 05a |
| Static analysis tools (linters, perf) | `lamark-tools` (ESLint, clang-tidy, perf) | 05 |
| Review comment generation | `lamark-tools` (Write), `lamark-skills` (template) | 07a, 08 |
| Edit operation for optimization | `lamark-tools` (Edit), `lamark-policy` | 05, 06 |
| Benchmark verification | `lamark-benchmark` (custom) | custom |
| Impact tracking & KB sync | `lamark-kb-client` | 07a |
| Slack notifications | `lamark-gateway` | 09 |
| Kanban workflow & task lifecycle | `lamark-coordinator`, `lamark-task-management` | 05a, 05b |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Static analysis tool timeout** | Tool retries 2× with exponential back‑off; on final failure, posts a Slack alert “⚠️ Linter timeout – manual review required”. |
| **Diff checkout fails** | Agent aborts the review, posts a detailed error to Slack, and marks the task `failed`. |
| **Review reaction never arrives** | After configurable timeout (e.g., 24 h), Coordinator escalates with a reminder ping; if still silent, task auto‑closes. |
| **Edit operation conflicts** | Agent detects merge conflicts; suggests manual resolution; posts a detailed Slack message with conflict details. |
| **Benchmark verification fails** | Agent logs the regression, posts a Slack alert, and creates a `Re‑evaluateTask` to revisit the optimization. |
| **Knowledge‑base write fails** | Writes to local outbox; retries every 30 s; trace bundle remains available for later upload. |
| **Reinforce‑signal mismatch** | If a change shows regression but `reinforce_signal` is `success`, trainer will be flagged for review; an alert is posted to the #ai‑training channel. |

---

## Acceptance criteria

- [ ] Every new PR automatically triggers static analysis and generates a feedback
  markdown file.  
- [ ] Review comments are posted to Slack and include actionable suggestions.  
- [ ] Reviewers can approve/reject via Slack reactions, which directly control PR
  merge and edit operations.  
- [ ] Performance‑oriented suggestions are generated and require explicit approval
  before code change.  
- [ ] Benchmark verification confirms any claimed performance gain before merge.  
- [ ] All feedback, proposals, and benchmark results are stored in KB for audit.  
- [ ] `reinforce_signal` correctly reflects success/failure and is consumed by
  the trainer.  
- [ ] Failed static analysis or benchmark steps result in clear Slack alerts
  with actionable details.  
- [ ] All actions are fully traceable in the knowledge‑base; each PR’s entire
  lifecycle (ingest → comment → edit → merge → benchmark) is auditable.  

---

## Self‑improvement assertions

1. **SFT samples for review comments.** Each review cycle produces a Nemotron‑Agentic‑v2 entry covering `static_analysis → comment_generation → merge`.  
2. **Skill promotion for refactoring templates.** After ≥ 3 successful optimizations, Curator promotes a `refactor-template` skill that supplies boilerplate for common performance fixes, reducing manual code generation by ~25 %.  
3. **Memory recall improves suggestion relevance.** When a similar performance issue recurs, the system recalls prior successful fixes from memory, reducing suggestion latency by ≥ 30 %.  
4. **Reinforcement‑signal feedback.** `reinforce_signal=success` from a merged optimization is fed back to the trainer, increasing the probability of suggesting similar efficient patterns in future.  
5. **Pattern‑based learning.** Curator may abstract recurring performance issues into reusable rule templates (e.g., “replace `for` loop with `stream().collect()` for aggregation”), reducing manual pattern creation by ≥ 20 %.  

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| PR intake & Kanban creation | plan/05a §"Coordinator" + plan/09 §"Gateway" | _audit_ |
| Static analysis tool execution | plan/05 §"Tool registry" | _audit_ |
| Review comment generation (markdown) | plan/07a §"Memory providers" + plan/08 §"Curator" | _audit_ |
| Edit operation for optimization | plan/05c §"sandbox" + custom edit tool | _audit_ |
| Benchmark verification & performance testing | custom tool (add to plan/16) | _audit_ |
| Impact tracking & KB storage | plan/07a §"Knowledge‑base client" | _audit_ |
| Slack notifications (review prompts, approvals) | plan/09 §"Gateway" | _audit_ |
| Kanban task lifecycle (pending → approved → merged) | plan/05a §"Coordinator" | _audit_ |
| Failure handling & escalation | plan/11 §"build-test-deploy" | _audit_ |
| Memory fact writes (performance_gain, reviewer) | plan/07a | _audit_ |
| Reinforce‑signal generation & storage | plan/07a, plan/08 | _audit_ |

---

## Open questions

1. **Review depth.** Should comments be limited to style issues, or should they also include
   performance‑oriented suggestions? How deep should the review go?  
2. **Optimization safety.** Should every suggested optimization be automatically
   applied after approval, or should certain categories (e.g., algorithmic
   changes) always require manual expert review?  
3. **Benchmark cost.** How much computational resources should be allocated to
   performance verification? Should we run benchmarks on every PR or only on a
   sampled subset?  
4. **Benchmark baseline.** Should we maintain a baseline of “gold‑standard”
   performance numbers for each service to compare against, or rely on relative
   improvements only?  
5. **Reviewer expertise.** Should PR reviewers be required to have a minimum
   seniority level or domain expertise before they can approve performance‑related
   changes?  
