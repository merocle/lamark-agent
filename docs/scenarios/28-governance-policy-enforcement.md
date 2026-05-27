# 28 — Governance Policy Enforcement

> **Phase:** P5–P6 (coordinator, sandbox) + P9 (gateway) + P10 (monitoring).
> **One‑liner:** Lamark interprets a declarative policy DSL, enforces it across
> all tools and sandboxes, and automatically generates audit trails when a
> permission request, resource creation, or tool invocation violates policy.
> Policies are versioned, reviewed, and can be rolled back through the
> knowledge‑base.

---

## North‑star contribution

- **Domain quality (security & compliance).** Replaces ad‑hoc permission checks
  with a **single source of truth** policy engine that:
  - Evaluates every incoming request (tool call, file write, network request)
    against a hierarchical, version‑controlled policy set.
  - Blocks prohibited actions in real‑time, logs the denial with full context,
    and optionally suggests an approved alternative.
  - Generates a **policy‑audit bundle** that links the violating event to the
    requesting user, timestamp, and affected resource.
- **Agent‑side self‑improvement.** Emits:
  - `memory_fact` writes: `policy_violation:<id> → user, rule, timestamp,
    decision`. Recalled by future enforcement agents to avoid repeating the
    same violation pattern.
  - `skill_draft` candidates: reusable policy‑rule templates (e.g., “block
    `rm -rf /` in production sandboxes”) promoted by Curator after ≥ 3 uses.
  - `reinforce_signal=success` when a policy rule is correctly applied;
    `fail` when a violation slips through.
- **Model‑side self‑improvement.** Policy‑enforcement traces become high‑value
  SFT samples (request → policy‑evaluation → decision → outcome). DPO pairs
  arise when an initially rejected request is later approved after a rule
  revision, providing rich feedback for the policy‑engine model.

---

## Idea

The platform must enforce a **security‑by‑design** posture across all
development sandboxes, CI pipelines, and production deployments. Today this
is handled by a patchwork of shell scripts and manual reviews, leading to
inconsistent enforcement and audit gaps. Lamark introduces a **Policy Engine**
that:

1. **Stores policies** as versioned markdown/YAML files in `policy/` (e.g.,
   `policy/deny-exec.yml`, `policy/allow-ssh.yml`).  
2. **Compiles** them into a fast, in‑memory matcher used by the **Policy
   Evaluator** tool.  
3. **Evaluates every request** that passes through a `PermissionRequest`
   hook (e.g., `Edit`, `Write`, `Bash`, `GitTool::CreateBranch`).  
4. If a request violates policy, the evaluator returns a structured `Denial`
   response that includes:
   - The violating rule (e.g., `Deny: destructive_file_path`)  
   - The requesting user / service identity  
   - The requested resource path  
   - A **suggested safe alternative** (e.g., “use `mkdir -p /tmp/app` instead of
     `mkdir -p /`”).  
5. The denial is logged as a `policy_violation` memory fact and posted to the
   **Gateway** for Slack notification to the security team.  
6. Approved actions proceed; denied actions are blocked and recorded.

### Policy enforcement pipeline

1. **Policy Load** – On startup, the Coordinator loads all policies from
   `policy/` into a matcher cache (`plan/05 §"Policy registry"`).  
2. **Request Initiation** – Any tool call that may modify the system (e.g.,
   `Edit`, `Write`, `Bash`) first emits a `PermissionRequest` event to the
   **Hook Bus** (`plan/06 §"Hook bus"`).  
3. **Policy Evaluation** – The **Policy Evaluator** tool fetches the most
   relevant rule(s) based on request metadata (user, resource path, action
   type).  
4. **Decision** –  
   - **Allow** → request proceeds.  
   - **Deny** → request is aborted; a `PolicyViolation` trace event is recorded
     and sent to the Gateway for Slack notification.  
   - **Prompt** → request is paused; a `PermissionRequest` UI modal appears in
     the TUI or Slack for manual approval (e.g., “Approve `rm -rf /data`?”).  
5. **Audit Generation** – Upon denial or approval, an **Audit Agent** writes a
   `policy_audit.json` bundle to `~/.lamark/audits/<policy_id>/` containing:
   - The violating request payload  
   - The matched rule and rationale  
   - The decision outcome  
   - The user identity  
   - Links to the relevant trace bundle.  
6. **Signal Generation** – If the decision was `Allow`, `reinforce_signal=success`
   is attached; if `Deny`, `reinforce_signal=fail` is attached.  

### Example policy rule (deny destructive file paths)

```yaml
# policy/deny-destructive.yml
rules:
  - id: deny-destructive-file
    description: "Block destructive file system operations on production paths"
    action: deny
    matches:
      - op: Edit
        path: |
          ^/(?:etc|var|lib|usr|home)/.*\.(conf|sh|py|cfg)$
      - op: Bash
        command: |
          ^rm\s+-[rf]\s+/
    remediation: |
      Use a temporary directory under $HOME or request a privileged admin
      approval before modifying system paths.
```

When a user attempts `Edit /etc/prod-config.yaml`, the evaluator matches the
rule, returns a `Deny` with the remediation text, and the Gateway posts to
`#security-ops`:

```
⚠️ Policy violation: Deny-destructive-file
User: alice
Path: /etc/prod-config.yaml
Remediation: Use a temporary directory under $HOME or request admin approval.
[View bundle] <link>
```

### Policy versioning & governance

- Policies live in `policy/` and are version‑controlled via Git.  
- Any change to a policy triggers a **Policy Change Request** (`POST /knowledge/policy_changes`).  
- The **Policy Review Agent** automatically creates a Kanban card for the change,
  routes it to the security committee for approval, and only activates the new
  rule after a `✅` reaction from the required number of reviewers.  
- Approved policies are **hot‑reloaded** without restarting the agent; the cache
  is refreshed and a `PolicyUpdated` memory fact is written.

---

## Actors

| Actor | Role |
|---|---|
| **Policy Engine** | Core tool that loads, matches, and evaluates policies against incoming requests. |
| **Hook Bus** | `lamark-hooks` system that captures `PermissionRequest` events from all tools. |
| **Policy Evaluator** | Executes the matching logic; returns `Allow`, `Deny`, or `Prompt`. |
| **Audit Agent** | Generates `policy_audit.json` bundles and posts them to KB. |
| **Gateway** | Slack adapter – posts policy violation alerts and remediation suggestions. |
| **Security Committee** | Human reviewers who approve policy changes via Kanban cards. |
| **Knowledge‑base** | Stores policy files, audit bundles, and version‑history of policy changes. |
| **Users / Services** | Entities that make tool calls; their identities are attached to every request for audit. |

---

## Trigger

1. **Tool call that requires permission** – any `Edit`, `Write`, `Bash`, `GitTool::CreateBranch`,
   `DockerTool::CreateImage`, etc., automatically fires a `PermissionRequest`
   event.  
2. **Manual policy update** – an engineer runs:  
   ```
   $ lamark policy update --file policy/allow-new-feature.yml
   ```
   to add a new rule; the change creates a `Policy Change Request` card on the
   Kanban board for review.

Both paths lead to the Policy Engine evaluating subsequent requests against the
updated policy set.

---

## Pipeline

### Step 0 — Policy bootstrap & caching

1. Coordinator reads all `policy/*.yml` files at startup and compiles them into
   a fast matcher cache (`plan/05 §"Policy registry"`).  
2. Each rule is assigned a `policy_id` and stored in KB (`POST /knowledge/policies/<id>`).  

### Step 1 — Permission request flow

3. User issues a permitted operation (e.g., `lamark edit src/main.rs`).  
4. `UserPromptSubmit` hook fires; the request is passed to the Hook Bus.  
5. Hook Bus emits a `PermissionRequest` event with metadata:
   - `user`  
   - `op` (Edit/Write/Bash)  
   - `path` or `command`  
   - `resource_id`  

### Step 2 — Policy evaluation

6. Policy Evaluator loads the relevant rule(s) and performs the match.  
7. Decision outcomes:  
   - **Allow** → proceed; no further action.  
   - **Deny** → abort request; generate a `PolicyViolation` trace event.  
   - **Prompt** → pause execution, await manual approval via Slack reaction.  

### Step 3 — Audit generation & notification

8. Audit Agent creates a `policy_audit.json` bundle containing:
   - The original request payload  
   - The matched rule and its description  
   - The decision (`Allow`/`Deny`/`Prompt`)  
   - User identity and timestamp  
   - Links to the full trace bundle.  
9. The bundle is stored in `~/.lamark/audits/policy/<policy_id>/` and linked
   via `POST /knowledge/audits/policy`.  
10. Gateway posts a Slack message summarizing the violation (or approval) with
    a clickable link to the audit bundle.  

### Step 4 — Policy change governance

11. When a policy file is updated, the **Policy Change Agent** creates a
    `PolicyUpdateTask` on the Kanban board.  
12. The **Policy Review Agent** moves the task through the approval workflow:
    - Requires a configurable number of `✅` reactions from designated reviewers.  
    - Upon approval, the new rule is hot‑reloaded and a `PolicyUpdated` memory
      fact is written.  

### Step 5 — Enforcement & reinforcement

12. Every enforced decision (Allow/Deny/Prompt) attaches a `reinforce_signal`
    (`success`/`fail`) that is stored in KB.  
13. The training pipeline consumes these signals to improve future policy‑matching
    accuracy (e.g., weighting rules that frequently prevent harmful actions).  

---

## Actors

| Actor | Role |
|---|---|
| **Policy Engine** | Core matcher; enforces rules on every permission request. |
| **Hook Bus** | Central event bus for `PermissionRequest` events (`plan/06`). |
| **Policy Evaluator** | Executes rule matching; returns decision. |
| **Audit Agent** | Generates audit bundles and stores them in KB. |
| **Gateway** | Slack adapter – notifies users of denials and policy updates. |
| **Security Committee** | Reviews and approves policy changes via Kanban cards. |
| **Knowledge‑base** | Stores policy files, audit bundles, version history. |
| **Users / Services** | Generate permission requests; their identities are tracked for audit. |

---

## Trigger

1. **Automatic permission request** – any tool call that can modify state
   (e.g., `Edit`, `Write`, `Bash`) automatically fires a `PermissionRequest`.  
2. **Manual policy update** – an engineer runs:  
   ```
   $ lamark policy update --file policy/allow-new-feature.yml
   ```
   This creates a `Policy Change Request` card on the Kanban board for review.  

Both paths lead to the Policy Engine evaluating subsequent requests against the
updated policy set.

---

## Pipeline

### Step 0 — Bootstrap & policy loading

1. Coordinator loads all policy files from `policy/` into the matcher cache
   (`plan/05 §"Policy registry"`).  
2. Each policy is stored in KB as a versioned document (`plan/05a §"Coordinator"`).  

### Step 1 — Permission request handling

3. Any tool call that may modify state emits a `PermissionRequest` event.  
4. Policy Evaluator matches the request against the loaded rules and decides
   `Allow`/`Deny`/`Prompt`.  

### Step 2 — Decision handling

5. **Allow** → request proceeds.  
6. **Deny** → request is aborted; Audit Agent creates a `policy_audit.json`
   bundle and posts it to KB; Gateway notifies the user via Slack.  
7. **Prompt** → execution pauses; a Slack message offers `Approve`/`Reject`
   reactions; upon reaction the request either continues or aborts.  

### Step 3 — Audit generation & signal

8. Audit Agent writes `policy_audit.json` to `~/.lamark/audits/policy/`.  
9. `reinforce_signal` is attached (`success` for `Allow`, `fail` for `Deny`).  
10. Memory fact `policy_violation:<id>` is written with user, rule, and timestamp.  

### Step 3 (cont.) — Policy change governance

11. When a policy file is modified, a `PolicyUpdateTask` is created.  
12. Policy Review Agent orchestrates approval via Kanban; upon approval,
    the new rule is hot‑reloaded and a `PolicyUpdated` fact is written.  

### Step 4 — Continuous enforcement

13. Every subsequent request passes through the Policy Engine, ensuring
    ongoing compliance with the latest policy set.  

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| Policy bootstrap & caching | `lamark-config`, `lamark-core` | 03, 05a |
| Permission request emission | `lamark-hooks` | 06 |
| Policy evaluation | custom `PolicyEvaluator` tool | custom |
| Audit bundle creation | `lamark-trace`, `lamark-kb-client` | 06, 07a |
| Slack notification | `lamark-gateway` | 09 |
| Policy change governance | `lamark-coordinator`, `lamark-kb-client` | 05a, 11 |
| Memory fact writes (policy_violation) | `lamark-kb-client` | 07a |
| Reinforce‑signal handling | `lamark-policy` | 06 |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Policy file syntax error** | Coordinator aborts startup and logs an error; the broken rule is marked `invalid` in KB; deployment is halted until fixed. |
| **Cache refresh fails** | Agent falls back to a read‑only copy of the previous policy set; posts a Slack alert “⚠️ Policy reload failed – using previous policy set”. |
| **Denial without remediation** | Agent must provide a remediation suggestion in the `Deny` response; if missing, it logs an error and escalates to a human via Slack. |
| **Audit bundle write fails** | Writes to local outbox; retries every 30 s; trace bundle remains available for later upload. |
| **Policy change fails approval** | Task remains `pending`; after configurable timeout, it is auto‑closed and a Slack reminder is sent to the security committee. |
| **Overly broad rule blocking legitimate actions** | Agent logs a warning, creates a `PolicyViolation` event with a high‑severity tag, and suggests a rule refinement; the incident is recorded for later policy‑review. |

---

## Acceptance criteria

- [ ] Every permission‑requiring operation is evaluated against the current policy set.  
- [ ] Denied actions are blocked and logged with a full audit bundle in KB.  
- [ ] Approved actions proceed without interruption.  
- [ ] Policy updates are reviewed, approved, and hot‑reloaded without service disruption.  
- [ ] All denials generate a Slack notification with a link to the audit bundle.  
- [ ] `reinforce_signal` correctly reflects success/failure and is consumed by the trainer.  
- [ ] All audit bundles are stored durably and are reproducible from the trace bundles.  
- [ ] Policy changes require a configurable quorum of approvals before activation.  

---

## Self‑improvement assertions

1. **SFT samples for policy enforcement.** Each decision (Allow/Deny/Prompt) produces a Nemotron‑Agentic‑v2 entry covering `PermissionRequest → PolicyEval → decision → audit`.  
2. **Skill promotion for policy templates.** After ≥ 3 successful rule implementations, Curator promotes a `policy-template` skill that supplies a structured YAML skeleton for new rules, reducing boilerplate by ~30 %.  
3. **Memory recall reduces repeated violations.** When a user repeatedly attempts a blocked operation, the system recalls the prior `policy_violation` fact and suggests a more specific rule, reducing repeated violations by ≥ 40 %.  
4. **Reinforcement‑signal impact.** `reinforce_signal=success` from a correctly enforced policy is fed back to the trainer, increasing the weight of that rule in future policy‑generation suggestions.  
5. **Policy‑gate learning.** Curator may suggest adjusting a rule’s regex or threshold based on historical false‑positive rates; adoption should reduce unnecessary denials by ≥ 20 %.  

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Policy bootstrap & caching | plan/05a §"Coordinator" + custom registry | _audit_ |
| Permission request emission | plan/06 §"Hook bus" | _audit_ |
| Policy evaluation & decision logic | custom tool (add to plan/16) | _audit_ |
| Audit bundle generation | plan/07a §"Knowledge‑base client" | _audit_ |
| Slack notification of violations | plan/09 §"Gateway" | _audit_ |
| Policy change governance (Kanban workflow) | plan/05a §"Coordinator" | _audit_ |
| Reinforce‑signal generation | plan/07a §"Memory providers" | _audit_ |
| Failure handling & escalation | plan/11 §"build-test-deploy" | _audit_ |
| Memory fact writes (policy_violation) | plan/07a | _audit_ |

---

## Open questions

1. **Policy versioning strategy.** Should policies be stored as immutable Git objects,
   or should they be mutable with a version number that can be rolled back?  
2. **Rule expressiveness.** Should policies support complex conditions (e.g., “if
   user belongs to group X AND operation is `Write` AND path matches `/secure/*`)?
   How do we model this safely?  
3. **Policy‑as‑code vs. policy‑as‑UI.** Should policy editing be done via files
   (Git‑based) or via a web UI that validates syntax before saving?  
4. **Cross‑team policy ownership.** Should different teams own separate policy
   namespaces, or should there be a centralized governance board?  
5. **Performance at scale.** How does policy evaluation perform when thousands of
   requests per second hit the matcher? Should we cache per‑user or per‑resource
   decision results?  
