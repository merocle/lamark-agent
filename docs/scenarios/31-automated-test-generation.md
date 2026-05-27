# 31 — Automated Test Generation

> **Phase:** P5–P6 (coordinator, sandbox) + P7–P8 (memory + KB) + P9 (gateway).
> **One‑liner:** Lamark automatically generates unit‑test skeletons from code,
> validates them against test‑coverage goals, executes them in sandboxed CI,
> and tracks test health through trace bundles stored in the knowledge‑base.

---

## North‑star contribution

- **Domain quality (software testing).** Replaces manual test creation with an
  automated, context‑aware generation pipeline that:
  - Produces **structured, compilable test skeletons** that respect project
    conventions (naming, imports, mocking).  
  - **Closes the testing loop** by linking each generated test to its
    corresponding production code via trace bundles, enabling full‑stack
    coverage analytics.  
  - **Adapts to code evolution** by recalling prior test patterns and adjusting
    generation strategies when code signatures change.  
- **Agent‑side self‑improvement.** Emits:
  - `memory_fact` writes: `test_pattern:<pattern_id> → coverage_boost,
    success_rate, last_used`. Recalled by future test‑generation agents to
    prioritize high‑impact patterns.
  - `skill_draft` candidates: reusable test‑template snippets (e.g., “mock
    dependency X and test method Y”) promoted by Curator after ≥ 3 uses.  
  - `reinforce_signal=success` when a generated test passes and coverage
    improves; `fail` when a test fails to compile or contributes no coverage.  
- **Model‑side self‑improvement.** Test‑generation traces become high‑value
  SFT samples (code → test skeleton → execution → result). DPO pairs arise when
  an initially failing test is revised and passes, providing direct feedback
  for the model to learn better test synthesis.

---

## Idea

The engineering team needs to maintain high code coverage, but manually
writing unit tests is time‑consuming and error‑prone. Lamark automates test
generation:

### Step 0 — Test‑generation bootstrap

1. **Test‑Coordinator** reads `GET /memory/search?q=test‑policy` — retrieves
   coverage‑target configuration (e.g., “aim for ≥ 80 % line coverage per
   package”).  
2. Reads `GET /knowledge/search?q=code‑ownership` — loads module‑ownership map
   to know which packages need new tests.  
3. Creates a `TestTask` on the Kanban board:
   `{package, missing_coverage, target_coverage, deadline}`.

### Step 1 — Code analysis & test‑pattern retrieval

4. **Test‑Pattern Agent** queries `GET /knowledge/search?q=test‑templates` —
   retrieves reusable test skeletons (e.g., “mock dependency X, test method Y”).
5. Agent also runs `Grep`/`Read` on the target package to identify:
   - Public functions/methods lacking tests.  
   - Complex logic blocks that may need exhaustive testing.  
6. For each uncovered function, the agent selects an appropriate template from
   memory (or generates a new one) and instantiates it with the correct method
   signature and imports.

### Step 2 — Test skeleton generation

7. Agent emits `Write` calls to create test files under `tests/`:
   - `tests/utils/MockDatabase.ts` (if needed).  
   - `tests/math/prime.test.ts` for a `prime(n)` function.  
   - Each file includes:
     - Boilerplate imports.  
     - `describe`/`it` blocks mirroring the production function’s signature.  
     - Placeholder assertions (`expect(...).toBe(true)`) with TODO comments.  
8. Agent records `memory_fact`: `{test_file, generated_at, coverage_target}`.

### Step 3 — Test execution & CI integration

9. **CI Agent** adds the new test files to the next CI pipeline:
   - Runs `npm test` / `mvn test` / `go test` in a sandboxed environment.  
   - Captures the full test output and stores it as a trace bundle
     (`~/.lamark/traces/<trace_id>/`).  
10. On success, the CI status is marked `passed`; on failure, the error log is
     posted to Slack and the task is marked `failed`.  

### Step 4 — Coverage validation & feedback

11. After CI completes, **Coverage Agent** parses the coverage report:
    - Computes current coverage per package.  
    - Compares against the target defined in the `TestTask`.  
    - If the target is met, marks the task `completed` and posts a success
      Slack message with a link to the coverage report.  
    - If not, it creates a `CoverageDeficitTask` and suggests additional test
      generation for the uncovered functions.  

### Step 5 — Feedback loop & signal generation

12. **Coverage Agent** updates `memory_fact` with coverage metrics:
    - `{package, coverage_percent, target, status}`  
    - For each new test file, records `{test_file, generated_at, coverage_boost}`.  
13. `reinforce_signal=success` is attached when coverage meets or exceeds the
    target; `fail` when a test fails to compile or adds no coverage.  

### Step 5 (cont.) — Feedback to code‑owners

14. If coverage is insufficient, the agent posts a Slack message to the
    package’s maintainers:  
    ```
    ⚙️ Coverage deficit: math module at 62 % (target 80 %). 
    Suggested: add tests for `prime(n)` and `fib(n)`. 
    [Link to test skeletons]
    ```  
15. Reviewers can react with 👍 to approve additional test generation or
    👎 to reject.  

### Step 6 — Knowledge‑base sync & traceability

15. All generated test files, CI output, and coverage reports are stored in
    KB via `POST /knowledge/tests/<package>/`.  
16. The final trace bundle includes:
    - `manifest.json` (linking to production code)  
    - `test_output.jsonl` (full CI console output)  
    - `coverage_report.json` (line‑coverage percentages)  
    - `reinforce_signal` field (`success`/`fail`).  

---

## Actors

| Actor | Role |
|---|---|
| **Test‑Coordinator** | `lamark-coordinator` sub‑agent. Owns the test‑generation Kanban board, orchestrates ingestion, coverage validation, and test execution. |
| **Test‑Pattern Agent** | Retrieves and instantiates reusable test templates; generates skeleton test files. |
| **CI Agent** | Executes generated tests in sandboxed CI environment; captures output. |
| **Coverage Agent** | Parses coverage reports, validates against targets, creates deficit tasks. |
| **Response Agent** | Posts coverage results to Slack, handles reviewer reactions to deficit tasks. |
| **Gateway** | Slack adapter – posts test‑generation status, coverage reports, and alerts. |
| **Knowledge‑base** | Stores generated test files, coverage reports, and trace bundles. |
| **Stakeholders** | Review generated tests, approve/reject additional test creation via Slack reactions. |

---

## Trigger

1. **Automatic coverage shortfall** – when a package’s coverage drops below the
   configured threshold, a `CoverageDeficitTask` is auto‑created.  
2. **Manual test generation command** – an engineer can run:  
   ```
   $ lamark test generate --package math --target 85%
   ```
   to force test generation for the `math` package up to 85 % coverage.  

Both paths result in a `TestTask` on the Kanban board.

---

## Pipeline

### Step 0 — Bootstrap & task creation

1. Coordinator reads `GET /memory/search?q=test‑policy` – loads coverage targets.  
2. Creates a Kanban card for each package needing more coverage.  

### Step 1 — Code analysis & pattern retrieval

3. Test‑Pattern Agent queries `GET /knowledge/search?q=test‑templates` and
   `GET /knowledge/search?q=code‑ownership`.  
4. Retrieves uncovered functions and selects appropriate templates.  

### Step 2 — Test skeleton generation

5. Agent writes test skeleton files via `Write`.  
6. Records `memory_fact` with generation metadata.  

### Step 3 — CI execution & coverage validation

6. CI Agent runs the new tests, captures output, and stores trace bundles.  
7. Coverage Agent parses the coverage report and compares against targets.  

### Step 4 — Feedback & signal

8. Reporting Agent posts coverage results to Slack.  
9. If targets are met, task moves to `completed`; otherwise, a `CoverageDeficitTask`
   is created for additional test generation.  
10. `reinforce_signal` is set based on coverage outcome.  

### Step 5 — Knowledge‑base sync

11. All generated tests, CI output, and coverage metrics are stored in KB.  
12. Final trace bundle includes links to production code and coverage data.  

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| Task bootstrap & coverage config | `lamark-coordinator`, `lamark-config` | 05a, 03 |
| Test‑pattern retrieval | `lamark-tools` (Grep/Read), `lamark-skills` | 05, 08 |
| Test file generation | `lamark-tools` (Write) | 05 |
| CI execution & trace capture | `lamark-tools` (Shell), `lamark-trace` | 05, 06 |
| Coverage validation | custom coverage tool (add to plan/16) | custom |
| Slack notification & feedback | `lamark-gateway` | 09 |
| Knowledge‑base storage | `lamark-kb-client` | 07a |
| Reinforce‑signal handling | `lamark-policy` | 06 |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Test generation fails to compile** | Agent posts detailed compile errors to Slack; task marked `failed`; no coverage boost recorded. |
| **CI pipeline unavailable** | Agent retries 2× with backoff; on failure, posts a Slack alert “⚠️ CI unavailable – manual verification required”. |
| **Coverage target not met** | Agent creates `CoverageDeficitTask`; posts a reminder to Slack for additional test generation. |
| **Trace bundle write fails** | Writes to local outbox; retries; trace bundle preserved for later upload. |
| **Coverage target unreachable** | After multiple attempts, task is auto‑closed and a `ManualReview` task is created for human intervention. |
| **Reinforce‑signal mismatch** | System logs discrepancy and raises a `SignalMismatchAlert` for the trainer to review. |

---

## Acceptance criteria

- [ ] Every package automatically generates test skeletons until its coverage meets the configured target.  
- [ ] Generated tests compile and pass in CI; failures are reported in Slack with actionable details.  
- [ ] Coverage reports are generated daily and posted to Slack with clear pass/fail status.  
- [ ] When coverage is insufficient, the system suggests additional test generation and tracks progress.  
- [ ] All generated tests, CI output, and coverage metrics are stored in the knowledge‑base for audit.  
- [ ] `reinforce_signal` correctly reflects success/failure and informs the trainer.  
- [ ] Any failure in test generation or CI execution results in a clear Slack alert with remediation steps.  

---

## Self‑improvement assertions

1. **SFT samples for test generation.** Each generated test produces a Nemotron‑Agentic‑v2 entry covering `template_instantiation → compile → execute → coverage_report`.  
2. **Skill promotion for test templates.** After ≥ 3 successful test generations, Curator promotes a `test-template` skill that supplies a standardized skeleton, reducing boilerplate by ~30 %.  
3. **Memory recall improves targeting.** After a package reaches 80 % coverage, the system recalls its `coverage_target` and skips redundant test generation, cutting redundant work by ≥ 35 %.  
4. **Reinforcement‑signal impact.** `reinforce_signal=success` from a coverage‑meeting run is fed back to the trainer, biasing future test‑generation patterns toward higher‑yield strategies.  
5. **Coverage‑goal learning.** Curator may adjust the coverage target based on historical effort; adoption should reduce manual test‑writing effort by ≥ 25 %.  

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Test‑task bootstrap & coverage config | plan/05a §"Coordinator" + plan/03 |
| Test‑pattern retrieval & template instantiation | plan/07a §"Memory providers" + plan/08 §"Curator" |
| Test file generation (Write) | plan/05 §"Tool registry" |
| CI execution & trace capture | plan/06 §"Trace recorder" |
| Coverage validation & target comparison | custom tool (add to plan/16) |
| Slack notification of coverage status | plan/09 §"Gateway" |
| Kanban task lifecycle (pending → completed) | plan/05a §"Coordinator" |
| Reinforce‑signal generation | plan/07a §"Memory providers" |
| Failure handling & escalation | plan/11 §"build-test-deploy" |
| Memory fact writes (coverage metrics) | plan/07a | 
- All listed above are covered as indicated. |

---

## Open questions

1. **Coverage target granularity.** Should coverage targets be per‑package, per‑module, or per‑function?  
2. **Test complexity vs. coverage.** How to balance high coverage with maintainable, readable tests?  
3. **Test‑template repository.** Should templates be stored in a dedicated repo or within KB?  
4. **Test‑generation permissions.** Should only senior engineers be allowed to trigger test generation for critical modules?  
5. **Cross‑package dependency tracking.** Should generated tests reference other services’ APIs, and how are those dependencies managed?  
