# 02 — Permission-gated dangerous command

> **Phase:** P3 (`plan/00-overview.md`).
> **One-liner:** Agent proposes a destructive shell command (`rm -rf`, `git
> push --force`, `psql DELETE`); the permission policy + hook bus catch it
> **before** sandbox exec; user decides; the full decision lattice — including
> the *denied* path — is captured as a first-class training signal.

---

## North-star contribution

- **Domain quality.** The Hermes-Agent ALLOW-ALL default was the public
  failure mode (see `plan/00b` — Hermes Issue #7826). Lamark's permission-
  first stance *is* a quality lever: prevents data loss + builds trust so
  users actually run the agent with non-trivial tools enabled. The TUI must
  show **what** the agent is about to do, **why**, and **the reversible
  alternatives** — not just "Allow / Deny."
- **Agent-side self-improvement.** Emits:
  - `memory_fact` writes of the form *"on this repo, `git push --force` is
    forbidden absolutely; suggest `--force-with-lease` first"* — recalled
    when the same intent surfaces later.
  - `policy_rule` proposals — if the user denies the same shape of command
    N times, the policy compiler proposes an `Allow|Prompt|Forbidden` rule
    to add to `.lamark/policy.toml`. (Closes the loop from one-off
    decisions to durable rules.)
  - Hook-author signal — if a subprocess hook (`~/.lamark/hooks.toml`)
    blocks a tool call with `deny`, that block is recorded so the curator
    can recommend hardening / loosening rules.
- **Model-side self-improvement.** **Denial is the most valuable trace
  shape there is.** Each rejected proposal yields a clean DPO pair:
  - *chosen* = the recovery trajectory (what the agent did after denial)
  - *rejected* = the trajectory that would have run had the user approved
    the destructive call

  Without this, the trainer only sees what *worked*; it never learns to
  prefer the safer path. Scenario 02 is where the DPO-pair schema must
  actually exist (Gap G-004 from scenario 01's audit).

### Signals produced / consumed

- **Produces:** trace bundle with at least one `PermissionDenied` event;
  manifest carries `reinforce_signal=denied`; reduced bundle contains a
  DPO pair if the agent recovered (re-planned and finished).
- **Produces:** `policy_rule_proposal` (config-edit suggestion) if the
  same denial fires ≥ N times.
- **Consumes:** existing `policy.toml` rules; existing per-session
  permission cache (from scenario 01's G-002 fix).

---

## Idea

A devops engineer is letting Lamark drive a release. Mid-session the agent
decides it needs to `git push --force origin main` to "clean up history."
This is exactly the case where a permission-first runtime earns its keep:
the agent is *correct* that the goal needs a push, but *wrong* about which
flavor of push. The user denies, the agent re-plans toward
`--force-with-lease`, the denial becomes a memory fact, and — crucially —
both trajectories are captured for the trainer.

## Actors

| Actor | Role |
|---|---|
| **Dev (Boris)** | SRE; running `lamark chat` against a real production repo. Has a `policy.toml` that marks `git push --force` as `Prompt` (not Forbidden — sometimes legitimate). |
| **Policy engine** | `crates/lamark-policy/` — evaluates `Allow | Prompt | Forbidden` rules per `(tool_name, args)` (`plan/05 §"Permission policy"`). |
| **Hook bus** | `crates/lamark-hooks/` — emits `PreToolUse`, `PermissionRequest`, `PermissionDenied`, `PostToolUse` with `deny > ask > allow` aggregation (`plan/06 §"Hook bus"`, 00d addendum §1–§5, §29). |
| **Subprocess hook** | User-authored shell script at `~/.lamark/hooks/git-push-guard.sh` that consults a remote allowlist (`plan/06 §"Subprocess hooks"`). |
| **Trace recorder** | Records `PermissionRequest`, `PermissionResolved { decision: Deny }`, and the subsequent re-plan turn (`plan/06`). |
| **Sandbox** | `LocalSandbox` — **never invoked**; permission denial short-circuits before exec. |

## Trigger

Boris is in an existing chat session. Agent emits:

```jsonc
{
  "tool": "Bash",
  "args": { "command": "git push --force origin main" },
  "rationale": "rewrite history to drop the test commit"
}
```

The trigger is the `ToolCallBegin` event for that command.

## Pipeline

### Step 0 — Policy first-pass (cheap, in-process)

1. `Session::dispatch_tool_call` consults `policy.evaluate(&tool_call)` against the merged rule set (project → user → built-in defaults). Result for `git push --force ...` is `Prompt` (per Boris's `policy.toml`).
2. Pure `Allow` here would skip the bus emission and execute directly. Pure `Forbidden` would short-circuit with a structured `ToolCallEnd { ok: false, reason: "policy: forbidden" }` and **never emit a `PermissionRequest`** — the UI must distinguish "policy denied without asking" from "user denied after asking" (00d addendum §29: `deny > ask > allow` aggregation).

### Step 1 — PreToolUse hooks run (deny > ask > allow)

3. Bus emits `PreToolUse` to all subscribers (internal: trace recorder; user: `~/.lamark/hooks/git-push-guard.sh`).
4. The subprocess hook runs with a 3-second per-hook timeout. It receives the tool call as JSON on stdin and may respond on stdout with `{ "decision": "deny" | "ask" | "allow", "reason": "..." }` and exit code 0; exit code 2 = block (Claude-Code convention; see 00d §3).
5. The hook checks an internal allowlist and returns `{"decision": "ask", "reason": "production main branch — confirm with operator"}`.
6. Bus aggregates: policy says `Prompt`, hook says `ask`. Aggregated result: **escalate to user**.

### Step 2 — PermissionRequest → UI modal

7. `PermissionRequest { request_id, tool, args, rationale, hint: Decision::Ask, sources: ["policy", "hook:git-push-guard"], reasons: [...] }` event emitted.
8. TUI renders a **structured approval modal** (`plan/02 §"TUI region model"`):
   - Tool: `Bash`
   - Command: `git push --force origin main`
   - Why the agent wants this (one-line rationale)
   - Why we're asking (policy + hook reasons stacked)
   - Reversibility note (computed from a tool-side metadata field — `is_destructive=true, reversibility=hard`)
   - Buttons: **Approve once** / **Approve & remember** / **Deny + explain** / **Suggest alternative**
9. Boris chooses **Deny + explain** and types: *"never force-push to main — use `--force-with-lease`."*

### Step 3 — Decision flows back

10. `Op::ApprovalDecision { request_id, decision: Deny, explanation: "..." }` posted to SQ.
11. `PermissionResolved { decision: Deny, explanation: "..." }` event fans out.
12. Tool call resolves as `ToolCallEnd { ok: false, error: PermissionDenied { explanation } }`. The agent sees this as a *structured tool error*, not an exception — it's expected to re-plan.

### Step 4 — Agent re-plans + memory write

13. Next turn: agent reads the explanation, proposes `git push --force-with-lease origin main`. Policy says `Prompt` again; hook says `allow` this time. Aggregate: `Allow` (lower-precedence `Prompt` overridden by higher-precedence hook `allow`? — depends on `deny > ask > allow` semantics; **per 00d §29, `allow` does NOT override `ask`**, so this still pings the user. The intent is for the user to confirm the safer command once.)
14. Boris approves. Push succeeds.
15. On `TurnComplete`, the memory authoring step writes a fact: *"In repo `prod-infra`, never `--force` to main; use `--force-with-lease`."* via `POST /memory/facts` with tags `{repo: prod-infra, branch: main, intent: force-push}`.

### Step 5 — Policy-rule proposal (the durable improvement)

16. The policy compiler (lives in `crates/lamark-policy/`) maintains a counter of "denied with explanation" outcomes per `(tool, arg-shape)` hashed signature.
17. When the counter for `git push --force origin main` crosses a threshold (default: 3 distinct denials in 14 days), it emits a `PolicyRuleProposal` event and prints a suggestion at session end:

    ```
    💡 Lamark noticed you've denied `git push --force` to main 3 times.
       Add this to .lamark/policy.toml?
         [[rule]] tool="Bash"
         match.command_prefix="git push --force"
         match.refspec="*main*"
         decision="Forbidden"
         note="Use --force-with-lease (autogenerated 2026-05-25)"
       Apply? [Y/n/edit]
    ```
18. If accepted, the rule is appended to `.lamark/policy.toml` and committed via `git` (if the user opted in). This is the *agent-side self-improvement* closing on rule durability.

### Step 6 — Trace + DPO pair

19. Trace recorder has captured: `PreToolUse`, hook output, `PermissionRequest`, `PermissionResolved { Deny }`, the agent's re-plan turn, the safer command's full flow, `TurnComplete`.
20. Reducer emits two trajectories from the *same* bundle:
    - **trajectory A (rejected)**: the world where the destructive command ran. This is *projected* from the event stream — we **never execute** it, but the trainer wants the prefix + the rejected tool call + the policy reasoning. Schema: `dpo_rejected_trajectory.jsonl`.
    - **trajectory B (chosen)**: the actual continued trajectory after denial. Schema: `dpo_chosen_trajectory.jsonl`.
21. Both are bundled into the KB upload as a *paired sample*; the trainer ingests them as DPO preference data (`plan/10 §"DPO"`).

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0 (policy eval) | `lamark-policy` | 05 §"Permission policy" |
| 1 (PreToolUse + subprocess hook) | `lamark-hooks` | 06 §"Hook bus", §"Subprocess hooks" |
| 2 (TUI modal) | `lamark::tui` | 02 §"TUI region model", 00d §16 |
| 3 (PermissionResolved → tool error) | `lamark-core`, `lamark-tools` | 05 §"Turn loop", 00d §29 |
| 4 (memory fact write) | `lamark-memory`, `lamark-kb-client` | 07a |
| 5 (policy-rule proposal) | `lamark-policy` (new submodule) | 05 §"Permission policy" — **likely a gap** |
| 6 (DPO pair authoring) | `lamark-trace` reducer | 06 §"Reducer", 10 §"DPO" — **G-004 from scenario 01** |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Hook timeout (3s exceeded)** | Hook treated as `ask` per 00d §3 (timeout is non-fatal but conservative). Recorded in trace as `HookCompleted { outcome: Timeout }`. |
| **Hook exits with code 2** | Treated as `deny` (Claude-Code convention; 00d §3). User can't override at this turn; agent re-plans. |
| **Hook returns malformed JSON** | Treated as `ask` (conservative); printed warning at session end so the user can fix the hook. |
| **User closes the modal without choosing** | Times out after configurable `permission_prompt_timeout` (default 5 min); treated as `Deny` with explanation `"timed out"`. |
| **Forbidden rule fires** | No modal; `ToolCallEnd { ok: false, error: PolicyForbidden }`. Agent must re-plan with a different approach; recorded as a stronger signal than user-denial (binary "never this shape" vs "not this time"). |
| **Sandbox down at the time of post-approval re-execution** | Approval was granted; exec fails with `SandboxUnavailable`. Approval is **not** re-prompted on retry (sticky for the session per G-002). |
| **Policy file syntax error at load** | `lamark doctor` fails; `lamark chat` refuses to start (fail-closed). |
| **Policy-rule proposal accepted but `git commit` fails** | Rule still appended to `policy.toml`; commit deferred; user notified. |

## Acceptance criteria

- [ ] `Forbidden` rule never emits a `PermissionRequest` event (skipped, not asked).
- [ ] `Prompt` rule always emits a `PermissionRequest`.
- [ ] Subprocess hook deny **overrides** policy `Allow` (deny > allow); allow does NOT override policy `Prompt` (ask wins).
- [ ] Denial-with-explanation appears as a structured tool error visible to the model in the next turn (not an exception).
- [ ] A `memory_fact` is POSTed to KB on denial-with-explanation, tagged with `intent` derived from the rationale.
- [ ] After N (default 3) distinct denials of the same arg-shape, a `PolicyRuleProposal` is emitted; accepting it writes to `.lamark/policy.toml`.
- [ ] Bundle reducer produces a `dpo_rejected_trajectory.jsonl` + `dpo_chosen_trajectory.jsonl` pair on this scenario, validated against the trainer's DPO schema in `plan/10`.
- [ ] No destructive command ever reaches `sandbox.exec` when denied — verified by mock sandbox that records calls.

## Self-improvement assertions

1. **Denial trace round-trips to DPO sample.** After this scenario runs, the trainer can ingest the bundle and observe **exactly one** DPO pair with `chosen.last_tool != rejected.last_tool` and identical prefixes.
2. **Memory recall on next session.** Next session in the same repo, the agent reasons about a force-push and the prompt composer surfaces the memory fact in the agent layer — *before* the agent emits the tool call. The agent's first proposal is `--force-with-lease`, not `--force`.
3. **Policy rule is durable.** After accepting the `PolicyRuleProposal`, a session three days later in the same repo never emits a `PermissionRequest` for `git push --force` — it short-circuits as `Forbidden`.
4. **Hook decisions are traceable.** Trace bundle contains a `HookOutcome` event for every subscribed hook on `PreToolUse`, with reason strings; auditable post-hoc.
5. **Forgetting-probe coverage.** The `eval_set` for "permission discipline" includes a planted regression: a model that proposes `--force` after seeing the memory fact in context should **fail** the probe. (Wires into scenario 10.)

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Permission policy DSL (Allow/Prompt/Forbidden) | plan/05 §"Permission policy" | _audit_ |
| `deny > ask > allow` aggregation across policy + hooks | 00d addendum §29 | _audit_ |
| Subprocess hook JSON I/O contract (stdin + stdout + exit-2) | 00d addendum §3 | _audit_ |
| PreToolUse / PermissionRequest / PermissionResolved / PermissionDenied events | plan/06 §"Hook event taxonomy" | _audit_ |
| Forbidden path skips modal entirely | plan/05 + 00d §29 | _audit_ |
| Structured tool-error on denial (not exception) | plan/05 §"Turn loop" | _audit_ |
| TUI permission modal with rationale + reversibility | plan/02 §"TUI region model" + 00d §16 | _audit_ |
| Memory fact write tagged by intent | plan/07a | _audit_ |
| **Policy rule proposal flow (counter, threshold, append to policy.toml)** | (likely **gap**) | _audit_ |
| **DPO pair authoring in reducer** (G-004 from scenario 01) | plan/06 + plan/10 | _audit_ |
| Approval stickiness within a session (G-002) | plan/05 / 00d §29 | _audit_ |
| Hook timeout = `ask`; exit-2 = `deny`; bad JSON = `ask` | 00d §3 | _audit_ |
| Permission prompt user timeout default | plan/03 config | _audit_ |
| Mock sandbox proof: denied call never reaches `sandbox.exec` | plan/05c §"Sandbox trait" | _audit_ |

## Open questions

1. **Counter window for policy-rule proposals.** Is it 14 days, 30 days, last N sessions, all-time? Per-repo vs per-user-globally? Needs ADR.
2. **Does `ask`-from-hook override `Allow`-from-policy?** Per `deny > ask > allow` it should — but that means a hook can effectively force a prompt the policy author tried to suppress. Acceptable, but document.
3. **Where do `PolicyRuleProposal` UX strings live?** Hardcoded in Rust? In a `~/.lamark/messages.toml` so they can be localized? Affects gateway UX (scenario 09).
4. **Can the agent itself propose a policy rule?** E.g., during re-plan after denial, agent suggests "add this to your policy.toml." Today this is the *compiler's* job; should the model be allowed to also nominate?
5. **DPO rejected-trajectory authoring — projected vs executed.** We never execute the rejected branch (would be destructive). But the trainer needs the trajectory shape. How much of the "would-have-happened" is faithfully reconstructable? (Probably: only the tool call + reasoning that led to it; not its hypothetical outputs.) Pin the schema in plan/10.
