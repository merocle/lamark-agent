# 20 — Enterprise monorepo: issue-to-MR automation

> **Phase:** P6 (gateway, MCP/ACP connectors) + P5 (coordinator, skills).
> **One-liner:** Lamark watches a YouTrack project for new bugs, locates the
> relevant code inside a large monorepo (JetBrains Ultimate–scale), authors a
> fix branch + MR/PR, reacts to reviewer comments, monitors TeamCity builds,
> and iterates until the MR is green-and-merged — with minimal human
> intervention.

---

## North-star contribution

- **Domain quality (engineering automation).** Compresses the time between
  "bug reported" and "MR ready for final human review" from hours/days to
  minutes. The agent understands the repository structure from memory facts,
  can grep/read targeted file sets in a large codebase without reading
  everything, and produces diffs that follow the project's established
  code conventions (recalled from prior sessions + AGENTS.md).
- **Agent-side self-improvement.** Emits:
  - `memory_fact` writes: module-to-subsystem ownership map, TeamCity build
    ID ↔ module mapping, reviewer preferences observed from past MR comments.
    These make each subsequent issue resolution faster and better-targeted.
  - `skill_draft` candidates: repeating fix patterns (e.g., "null-check before
    calling X in module Y") are promoted to skills by Curator.
  - `reinforce_signal=success` when MR merges without re-requested changes;
    `fail` when the MR is closed without merge or the fix regresses a build.
- **Model-side self-improvement.** Trace bundles for issue-triage, fix-authoring,
  review-response, and build-reaction sub-agents are rich multi-step SFT sources:
  large-codebase navigation, diff generation, natural-language
  review-comment interpretation, CI log diagnosis. DPO pairs arise naturally
  from MRs where the first diff was rejected and revised.

---

## Idea

An engineering team maintains a monorepo of 5M+ lines (think: IntelliJ Ultimate
source tree). Their YouTrack project accumulates bugs faster than they can
triage them manually. They configure Lamark as an automation agent: it polls
(or receives webhooks from) YouTrack, picks up `Auto-fix candidate` tagged
issues, reads the relevant code, proposes a fix, opens a MR in JetBrains Space
(or GitHub/GitLab), watches for review comments, revises the diff, and tracks
the TeamCity build to completion. The human reviewer only reads the final MR
and hits "Merge" — or flags the agent's approach for manual handling.

---

## Actors

| Actor | Role |
|---|---|
| **YouTrack** | Issue tracker. Source of bug reports, acceptance criteria, and issue state (Open → In Progress → In Review → Done). Accessed via MCP connector or ACP adapter. |
| **JetBrains Space / GitHub / GitLab** | Code host + MR/PR system. Lamark opens branches, pushes commits, creates MRs, reads review comments. Accessed via MCP or REST tool. |
| **TeamCity** | CI system. Lamark monitors build status for the MR branch; reacts to failures by reading build logs and proposing fixes. Accessed via REST tool. |
| **Triage agent** | `lamark-coordinator` sub-agent. Reads the YouTrack issue, classifies it, locates the relevant subsystem using the ownership map from memory, emits a `FixTask`. |
| **Fix agent** | `lamark-coordinator` sub-agent. Receives the `FixTask`, reads the targeted code, authors a fix, opens the branch + MR. |
| **Review reactor agent** | Wakes on MR review events (via gateway or ACP). Reads reviewer comments, interprets them, revises the diff, pushes an updated commit. |
| **Build watcher agent** | Polls / receives webhooks from TeamCity. On failure, reads the build log, diagnoses the cause, proposes a corrective commit. |
| **Coordinator** | `lamark-coordinator` — creates and manages the Kanban board for the issue lifecycle (`plan/05a`). |
| **Gateway** | `lamark-gateway` — Slack adapter for human-facing status updates; webhook listener for YouTrack/Space/TeamCity push events (`plan/09`). |
| **Knowledge-base** | Stores module ownership map, reviewer preference facts, build-module mapping, past fix patterns. |

---

## Trigger

**Mode A — polling:**
```
$ lamark agent run --profile youtrack-autofix \
    --youtrack-project BACKEND \
    --tag "Auto-fix candidate" \
    --interval 5m
```

**Mode B — webhook (production):**
YouTrack issue-created webhook → `lamark-gateway` HTTP listener → `Op::ExternalEvent`.

---

## Pipeline

### Step 0 — Issue intake

1. Triage agent receives the YouTrack issue (title, description, steps to
   reproduce, affected version, stack trace if present).
2. Queries `GET /memory/search?q=<component_keyword>` — retrieves module
   ownership map (e.g., "NullPointerException in `PsiManager`" → `platform/core`).
   First-time components get a fallback to `Grep` across the monorepo.
3. Issues a `YouTrackTool::TransitionIssue(state="In Progress")` — updates
   the tracker so the team knows automation is working it.
4. Creates a `FixTask` on the Kanban board: `{issue_id, subsystem, stack_trace,
   acceptance_criteria, branch_name}`.

### Step 1 — Targeted codebase navigation

5. Fix agent picks up the `FixTask`. Reads AGENTS.md (or the monorepo's
   `CONTRIBUTING.md`) for code conventions.
6. Uses `Grep` + `Read` to inspect only the relevant module (not the entire
   5M-line tree). Depth is bounded by the `--max-read-depth` config and the
   context budget. Reads stop when the agent has enough signal to produce a fix.
7. If a prior fix skill exists for this subsystem, it is loaded from
   `.lamark/skills/` and injected into the prompt as a tool-use example.

### Step 2 — Fix authoring + branch + MR

8. Fix agent emits `Edit` / `Write` calls to apply the change. Policy:
   `Prompt` for files outside the identified subsystem (guarded by the
   monorepo ownership map).
9. `GitTool::CreateBranch(name=branch_name)` + `GitTool::Commit(message=...)`.
   Commit message follows Conventional Commits recalled from memory.
10. `SpaceTool::CreateMR(branch, title, description)` — opens the MR.
    Description is generated from the YouTrack issue + fix rationale.
    Links `Fixes: BACKEND-1234` in the description.
11. `YouTrackTool::TransitionIssue(state="In Review")` + post a comment with
    the MR link.
12. Gateway posts a Slack notification: `#eng-alerts: "MR opened for
    BACKEND-1234 — <link>"`.

### Step 3 — TeamCity build watch

13. Build watcher agent (spawned by coordinator) polls `TeamCityTool::GetBuildStatus(branch)`
    every N minutes (configurable).
14. On `BUILD_FAILURE`: agent reads `TeamCityTool::GetBuildLog(build_id)`,
    identifies the failing test class, greps for the test in the monorepo,
    diagnoses the root cause (test-only breakage vs real regression).
15. If fixable: fix agent is re-invoked with the build log as context; pushes
    a corrective commit; coordinator re-queues the build watch.
16. If not fixable within retry budget: Slack alert to human + MR labelled
    `needs-human`; issue transitioned to `Needs Manual Review`.

### Step 4 — Review reaction

17. Reviewer leaves comments on the MR. Gateway receives the webhook or MCP
    event: `Op::ExternalEvent { source: "space", type: "mr_comment" }`.
18. Review reactor agent reads each comment. Classifies: `change_request`,
    `nitpick`, `question`, `approval`.
19. For `change_request`: locates the referenced line, applies the requested
    change, pushes an updated commit. Posts a reply: "Done in commit `abc123`."
20. For `question`: posts an explanatory reply inline.
21. For `approval`: posts a thank-you + requests final merge if all reviewers approved.
22. Memory fact written: `{reviewer, file_pattern, preference}` (e.g., "Alice
    always requests explicit error messages in `platform/core`").

### Step 5 — Merge + close-out

23. All reviewers approved + build green → coordinator requests auto-merge
    (if enabled) or notifies the engineer.
24. `YouTrackTool::TransitionIssue(state="Done")`.
25. Trace recorder flushes all sub-agent bundles; KB upload.
26. `reinforce_signal=success` attached. Memory facts updated (e.g., "fix
    for NPE in PsiManager requires null-check at entry of `getFile()`").
27. If the fix pattern recurred ≥ 3 times across issues, a skill draft is
    proposed to Curator.

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0–1 (intake, navigation) | `lamark-coordinator`, `lamark-tools` (Grep/Read/Git), `lamark-memory`, `lamark-kb-client` | 05a, 05, 07a |
| 2 (fix, branch, MR) | `lamark-tools` (Git, Space/GitHub REST), `lamark-policy`, `lamark-hooks` | 05, 06 |
| 3 (build watch) | `lamark-coordinator`, `lamark-tools` (TeamCity REST), `lamark-gateway` | 05a, 09 |
| 4 (review reaction) | `lamark-gateway` (webhook intake), `lamark-acp` or MCP (Space events), `lamark-tools` | 09, 11 |
| 5 (close-out) | `lamark-trace`, `lamark-kb-client`, `lamark-skills` | 06, 07a, 08 |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **YouTrack unreachable** | Polling backs off; webhook mode is unaffected. In-flight issues retain their `In Progress` state. |
| **Grep finds no owner** for a component | Triage agent falls back to broader `Grep` with expanded search radius; if still ambiguous, labels the issue `needs-triage` and skips automation. |
| **Context overrun** in large file tree | Compaction fires (`plan/07 §"Prompt cache"`); agent must re-scope its read plan to a narrower file set. Memory ownership map is updated so future issues for this component start narrower. |
| **Build never becomes green** | After N retries (configurable), coordinator labels MR `needs-human`, posts to Slack, closes the `FixTask` as `escalated`. |
| **Reviewer requests contradictory changes** | Review reactor detects the conflict, posts a question comment asking the reviewer to clarify, and halts further automated changes for that MR. |
| **TeamCity API rate-limited** | Build watcher backs off with jitter; does not spam the API. |
| **Policy blocks a file outside the subsystem** | Fix agent gets a `Forbidden` result; re-plans to stay within the boundary; if impossible, escalates to human. |

---

## Acceptance criteria

- [ ] A YouTrack issue tagged `Auto-fix candidate` transitions through
  `In Progress → In Review → Done` without human intervention on a
  representative bug (single-module fix, tests passing).
- [ ] The MR description contains the YouTrack issue ID and a human-readable
  fix rationale.
- [ ] TeamCity build failure triggers a diagnosis + corrective commit within
  one retry cycle.
- [ ] A reviewer comment results in an updated commit and a reply within the
  configured reaction SLA.
- [ ] Memory facts from past issues are recalled on the next issue in the same
  module (measurable by trace: memory hit before the first `Grep` call).
- [ ] All sub-agent trace bundles share the same `issue_id` cross-reference
  field in `manifest.json`.
- [ ] Ambiguous ownership (no memory fact, grep returns > N modules) correctly
  triggers the `needs-triage` fallback rather than a blind fix attempt.

---

## Self-improvement assertions

1. **SFT samples from full lifecycle.** Triage, fix, review-reaction, and
   build-watch bundles each produce ≥ 1 Nemotron-Agentic-v1 entry; combined
   they cover the complete issue-to-merge arc as a multi-rollout trajectory.
2. **DPO pairs from revisions.** An MR where the first diff was revised after
   a review comment yields a `(rejected_diff, accepted_diff)` pair the trainer
   can use for DPO.
3. **Module ownership map improves recall.** After ≥ 5 issues resolved in
   module X, future issues for X skip the broad `Grep` step (verifiable by
   comparing step counts across trace bundles).
4. **Fix-pattern skill reduces token usage.** After a skill is promoted for
   a recurring fix type, the fix agent's prompt is shorter (skill injected as
   a few-shot example) and the trace shows fewer `Read` calls before the
   correct `Edit` is emitted.

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Coordinator lifecycle + Kanban tasks | plan/05a, plan/05b | _audit_ |
| Tool: Grep / Read / Edit / Write / Git | plan/05 §"Tool registry" | _audit_ |
| External REST tools (YouTrack, Space, TeamCity) | plan/05 §"Tool trait", plan/09 §"MCP client" | _audit_ |
| Webhook intake via gateway | plan/09 §"Gateway event loop" | _audit_ |
| ACP / MCP adapter for Space events | plan/09 §"ACP", plan/11 §"MCP client" | _audit_ |
| Policy guard for out-of-subsystem writes | plan/05 §"Policy" | _audit_ |
| Memory recall (ownership map) | plan/07a | _audit_ |
| Context budget + compaction | plan/07 §"Prompt cache" | _audit_ |
| Skill draft on recurring pattern | plan/08 §"Curator" | _audit_ |
| Trace cross-reference via `issue_id` | plan/06 §"Trace recorder" | _audit_ |

---

## Open questions

1. **YouTrack connector shape.** Is this a native tool in `lamark-tools`, an
   MCP server, or an ACP adapter? The answer affects how webhooks and REST
   calls are modelled. Likely an MCP server wrapping the YouTrack REST API.
2. **Monorepo ownership map bootstrap.** For a 5M-line repo with no prior
   Lamark sessions, how is the initial ownership map built? Options: one-time
   `lamark index --repo .` pass; incremental from issue resolutions; import
   from CODEOWNERS.
3. **Commit identity.** What name/email does Lamark use for commits? Should be
   configurable per-project; ADR needed.
4. **MR auto-merge safety.** Should Lamark ever auto-merge without a human
   approval? Strongly suggest `Prompt` by default; `Allow` only if operator
   explicitly sets `auto_merge=true` in `policy.toml`.
5. **Context overrun in large diffs.** A 500-file diff review may exceed the
   context window. Compaction strategy for diff review (chunk-by-chunk?) needs
   explicit design.
