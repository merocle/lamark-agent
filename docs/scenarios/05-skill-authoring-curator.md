# 05 — Skill authored by agent + Curator weekly sweep

> **Phase:** P5.
> **One-liner:** During scenario 04, three fact-checker subagents converge
> on the same tool sequence. The memory-extraction step writes a
> `skill_draft` to `~/.lamark/skills/drafts/`; one week later Curator runs,
> applies its decision table, and the draft is **promoted** to a real
> agent-authored skill that the next session's prompt index advertises.

---

## North-star contribution

- **Domain quality.** Skills are *crystallized expertise*: the agent no
  longer re-discovers a 4-step procedure from scratch — it invokes a
  named recipe. Quality lever: prompt cache holds the Tier-1 skill index
  stable across sessions; only the small index section invalidates when
  skills change (`plan/08:100–103`).
- **Agent-side self-improvement.** This is *the* canonical agent-side
  loop. Reflexion / OPRO live in `plan/07b`; Curator lives here. The
  scenario validates the **separation** the audits insisted on: extraction
  is deterministic (G-014), Curator is the LLM-curation gate (Reflexion-
  style, non-deterministic, audited).
- **Model-side self-improvement.** Curator's actions (`archive`, `rewrite`,
  `consolidate`, `propose-promote`) are themselves training samples. The
  model learns "when do I archive a skill?" by SFT on `CuratorRun` traces
  — meta-learning the curation policy. Skill *lineage* in KB
  (`plan/08:122–124`) is also a clean signal for "did this skill version
  outperform the previous?"

### Signals produced / consumed

- **Produces:** new agent-authored skill files (markdown + frontmatter);
  `SkillCreated` / `SkillRotated` events; `CuratorRun` events with
  per-action audit trail; KB `SkillRecord` lineage; promotion proposals
  in `~/.lamark/curator-promotions.md`.
- **Consumes:** memory-extraction skill drafts (G-014); usage counters
  per-skill from `.curator_state.toml` (`plan/08:138–149`); embedding
  similarity for consolidation decisions.

---

## Idea

The agent has been running for three weeks. Memory-extraction has been
dropping skill drafts into `~/.lamark/skills/drafts/` whenever convergent
tool patterns appeared (per G-023, the counter-only metric: ≥ 3 independent
uses). Sunday 03:00, Curator wakes (cron via `skills.curator.interval_hours
= 168`), reads the drafts, evaluates each against the decision table
(`plan/08:159–167`), promotes some, archives stale ones, proposes one for
bundled-promotion.

## Actors

| Actor | Role |
|---|---|
| **Curator session** | Its own `AIAgent` with toolset `[SkillList, SkillView, SkillManage, MemorySearch]` (`plan/08:171`). No `Bash`, no `Write`, no `WebFetch`. |
| **`lamark-skills::loader`** | `SkillIndex` + file-watcher; hot-reloads when skills change (`plan/08:74–95, 127–129`). |
| **Memory extraction step** | Authored drafts in prior weeks (G-014 / scenario 03 / scenario 04). Deterministic; counter-only convergence detection (G-023). |
| **KB skill store** | `POST /knowledge/skills` (`plan/08:122–124`) — lineage queryable. |
| **Prompt composer** | Tier-1 holds `name + description + when_to_use` of every loaded skill; full body lazy-loaded on `SkillView` / `SkillInvoke` (`plan/08:98–103`). |
| **User (Carol)** | Reviews `~/.lamark/curator-suggestions.md` on Monday morning; can pin / unpin / approve promotions. |

## Trigger

Three triggers (any one fires Curator):

1. **Cron:** `cron::next(skills.curator.interval_hours)` (`plan/08:153`).
2. **Manual:** `lamark skills curate-now`.
3. **Post-monthly-merge** (training pipeline; `plan/10`) — re-curate after a model promotion in case the new model uses skills differently.

## Pipeline

### Step 0 — Curator session start

1. Single-instance file lock at `~/.lamark/skills/.curator.lock` (`plan/08:173`). Stale-PID detection like `WorkspaceLock` from scenario 04.
2. Curator session opens with its narrow toolset. Action budget: ≤ 30 actions per run (`plan/08:172`).
3. Reads `.curator_state.toml` to know last-run, per-skill usage stats.

### Step 1 — Inventory

4. `SkillList(scope=all)` returns:
   - Bundled skills (immutable for Curator).
   - User-authored skills (Curator **never auto-modifies**, only suggests — `plan/08:162`).
   - Agent-authored skills (Curator's primary surface).
   - Drafts in `~/.lamark/skills/drafts/` (output of G-014 / G-023).

### Step 2 — Decision table application (per `plan/08:159–167`)

5. **Pinned** → leave alone.
6. **User-authored** → write any insight to `~/.lamark/curator-suggestions.md`; do not modify.
7. **Agent-authored, used > 5×, success_rate > 0.7** → eligible for *one-shot rewrite for clarity* (using a frontier model, single inference, body update only — frontmatter immutable). The rewrite happens *inside the Curator session*, so it's recorded in the trace and KB lineage.
8. **Agent-authored, used 0× in 30 days** → `SkillManage(archive)`; moved to `archive/<date>/`. Tarball backup per `plan/00c §2`.
9. **Agent-authored, success_rate < 0.3** → archive with rationale in the audit log.
10. **Two skills with embedding-similarity > 0.85 + overlapping when_to_use** → propose **consolidation**: write a draft merged skill; emit a `SkillConsolidationProposal`. If either source skill was *used by the user explicitly*, require approval before the merge happens (proposal landed in `curator-suggestions.md`).
11. **Drafts → promotion to real skill.** A draft with `convergence_count ≥ 3` and `success_rate ≥ 0.6` over its constituent sessions is promoted from `drafts/` to `~/.lamark/skills/`; frontmatter `author: agent`, `mutable: true`, `version: 1`. `SkillCreated` event.

### Step 3 — Bundled-promotion candidates (monthly cadence)

12. For each agent-authored skill: check ≥ 20 successful uses across N sessions AND success rate ≥ 0.85 over 30 days AND no security flag AND no bundled name collision (`plan/08:179–183`).
13. Eligible skills appended to `~/.lamark/curator-promotions.md` — human-reviewable, **not auto-promoted**. Maintainer commits into `crates/lamark-skills/src/bundled/` on next release.

### Step 4 — KB mirror + lineage

14. Every action emits `CuratorAction { kind, skill_name, before_hash, after_hash, rationale }` to the trace recorder. Recorder fans out to `POST /knowledge/skills` for lineage (`plan/08:122–124`).
15. `lamark skills lineage <name>` (`plan/02 §"Subcommand surface"`) queries KB → ordered list of versions + actions + diffs.

### Step 5 — File-watcher hot-reload

16. As skills are promoted / archived / consolidated, the loader's `notify` watcher (`plan/08:127–129`) sees the file changes.
17. Tier-1 skill-index fingerprint flips; next session's prompt rebuilds the index, invalidating only that small section (cache-friendly).

### Step 6 — Session end + audit

18. Curator writes `last_run_at`, `last_run_actions`, `run_count++` back to `.curator_state.toml`.
19. Releases the file lock.
20. If `actions ≥ 30` (budget hit), Curator queues remaining work to the next run; logs as `CuratorBudgetExceeded`.

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0 (lock, toolset, budget) | `lamark-skills::curator` | 08 §"Part B" |
| 1 (inventory) | `lamark-skills::loader` | 08 §"Loader" |
| 2 (decision table) | `lamark-skills::curator::policy` | 08:159–167 |
| 2.7 (one-shot rewrite) | `lamark-providers` (frontier inference) | 04 + 08:163 |
| 3 (promotion proposals) | `lamark-skills::curator::promote` | 08:177–185 |
| 4 (KB mirror + lineage) | `lamark-kb-client` | 08:122–124 |
| 5 (hot-reload) | `lamark-skills::loader::watch`, `lamark-prompt` | 08:127–129, 07 |
| 6 (state persist) | `lamark-skills::curator::state` | 08:138–149 |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Another Curator running** | File-lock conflict; abort with `CuratorAlreadyRunning`; cron retries on next interval. |
| **Curator session crashes mid-action** | Lock auto-released on PID death; partial state in `.curator_state.toml` is recoverable (atomic writes, tmp+rename). On restart, last-uncompleted action is re-evaluated. |
| **One-shot rewrite produces invalid frontmatter** | Schema validator (`plan/08 §"frontmatter validation table"` from 00c §2) rejects; original skill body untouched; `CuratorActionFailed` logged. |
| **Embedding service down** for consolidation step | Skip consolidation; other actions proceed. Re-checked next run. |
| **Action budget exceeded (30)** | Stop, log, queue remainder. |
| **User pinned a skill mid-run** | Curator re-reads `.curator_state.toml` per-skill at action time; pinning observed → action skipped. |
| **Promotion criteria met but security-flag exists** | Skip promotion; emit `SkillPromotionBlocked { reason: security }`; surface to user. |
| **KB unavailable** | Lineage events queue in outbox (per G-017). Curator continues with local state. |
| **Draft promotion conflicts with existing skill name** | Suffix the draft (`-v2`); emit warning; user reviews in suggestions file. |

## Acceptance criteria

- [ ] Curator runs on cron; single-instance enforced via file lock.
- [ ] After three weeks of seeded drafts (≥ 3 convergence count each), Curator promotes them — verified by `lamark skills list` showing new entries.
- [ ] An agent-authored skill with 0 uses in 30 days is archived (not deleted); the file moves to `~/.lamark/skills/archive/<date>/` and a tarball backup exists (`plan/00c §2`).
- [ ] Two near-duplicate skills (embedding similarity > 0.85) generate a `SkillConsolidationProposal` in `~/.lamark/curator-suggestions.md`; merger does not happen without user approval if either was user-invoked.
- [ ] User-authored skill is **never** auto-modified — only mentioned in suggestions.
- [ ] `lamark skills lineage <name>` returns a non-empty event history from KB.
- [ ] Tier-1 skill-index fingerprint changes after a Curator run; next session's prompt observes new skills.
- [ ] Action budget (≤ 30/run) is enforced; remainder queued.
- [ ] Bundled-promotion proposals appear in `~/.lamark/curator-promotions.md` only when all four criteria pass.
- [ ] CuratorRun event contains `actions: Vec<CuratorAction>` matching what landed on disk.

## Self-improvement assertions

1. **Agent-side loop closes weekly.** A skill drafted in week N is live by Monday of week N+1 (assuming convergence + success rate gates pass).
2. **Tier-1 cache stays friendly.** Skill addition invalidates only the index section (~ few hundred tokens), not the per-skill bodies.
3. **Reflexion / LLM-curation lives only here.** No other component does LLM-driven skill modification (validates the audit's G-014 / G-023 separation).
4. **Curator actions are themselves trainable.** Reduced traces from `CuratorRun` sessions are usable as SFT data for "be a good Curator" — meta-learning the curation policy.
5. **Lineage enables A/B.** Two consecutive versions of the same skill can be compared by success rate over the same time window; the worse one can be **rolled back** automatically (next scenario for a future ADR — see G-029 below).
6. **Promotion gate is human-in-the-loop.** No skill is auto-bundled; a maintainer must commit it. This is *deliberate friction* to prevent a runaway self-modification loop.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Skill file format + frontmatter | plan/08 §"Skill system" + 00c §2 frontmatter validation | _audit_ |
| Three-tier search paths | plan/08:62–68 + 00d §20 | _audit_ |
| `SkillIndex` loader + file watcher | plan/08:74–95, 127–129 | _audit_ |
| Tier-1 skill index in prompt (lazy body) | plan/08:98–103 | _audit_ |
| Skill tools (`SkillView`, `SkillInvoke`, `SkillNew`, `SkillManage`, `SkillList`) | plan/08:104–114 | _audit_ |
| Slash commands | plan/02 + plan/08:116–120 | _audit_ |
| KB mirror + lineage | plan/08:122–124 | _audit_ |
| Curator state file `.curator_state.toml` | plan/08:138–149 | _audit_ |
| Curator triggers (cron / manual / post-merge) | plan/08:152–155 | _audit_ |
| Decision table | plan/08:159–167 | _audit_ |
| Tight toolset + 30-action budget + lock | plan/08:171–173 | _audit_ |
| Trace event `CuratorRun { actions }` | plan/06 §"Event variants" `CuratorRun` | _audit_ |
| Bundled-promotion criteria | plan/08:179–185 | _audit_ |
| Archive-not-delete + tarball backup + pinned exemption | 00c §2 | _audit_ |
| Draft promotion (output of G-014/G-023) | (likely **gap**: who writes drafts; Curator promotes) | _audit_ |
| One-shot rewrite via frontier inference | plan/08:163 | _audit_ |
| Schema validator on frontmatter (post-rewrite) | 00c §2 frontmatter validation | _audit_ |
| Embedding service for consolidation decisions | plan/07a or new | _audit_ |
| Hot-reload invalidates Tier-1 fingerprint only | plan/08:127–129 + plan/07 | _audit_ |
| Bundled-promotion is human-in-the-loop (no auto-bundle) | plan/08:184–185 | _audit_ |
| Auto-rollback on skill regression (scenario asserts) | (likely **gap**) | _audit_ |

## Open questions

1. **Where exactly does memory-extraction's skill draft land — `drafts/` subfolder or KB only?** Scenario assumes filesystem so Curator can read it without a KB call. Pin.
2. **Embedding service for similarity > 0.85** — KB-hosted? Local model? Out-of-band? Likely KB (consistent with hybrid retrieval).
3. **Curator one-shot rewrite — which model?** Frontier (Claude / GPT-4) for quality vs local-LoRA for cost/privacy. Probably config-driven.
4. **Auto-rollback policy on skill regression.** If new version of skill X drops success rate below previous by Δ, do we auto-revert? Likely an ADR.
5. **Cross-project Curator runs.** Does Curator scope to one project or globally? Probably global (since skills are user-scope), but per-project facts must not bleed.
6. **Curator vs Dream consolidator (00d §32).** 00d mentions a Dream-style memory consolidator. Is that the same as Curator or a sibling? Pin.
