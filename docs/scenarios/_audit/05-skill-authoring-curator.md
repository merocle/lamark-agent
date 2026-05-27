# Audit — scenario 05: Skill authored by agent + Curator weekly sweep

**Verdict:** 🟡 Yellow — Coverage of Curator's *local* operations is complete and well-grounded (`plan/08:138–185` + `plan/00c §2` cover decision table, archive-not-delete, locking, budget, KB lineage). Three bridging contracts missing — draft path, embedding service, auto-rollback — plus a fourth-subsystem boundary issue (Curator vs Dream, `plan/00d §32`).

---

## 1. Plan coverage matrix (21 rows)

| Concern | Covered in | Status | Evidence |
|---|---|---|---|
| Skill file format + frontmatter | plan/08 §"Skill system" + 00c §2 | ✅ | plan/08:34–60, 00c:121–131 |
| Three-tier search paths | plan/08:62–68 + 00d §20 | ✅ | plan/08:62–68; 00d:646–660 |
| `SkillIndex` loader + file watcher | plan/08:74–95, 127–129 | ✅ | plan/08:74–96 (struct), :127–129 (watcher) |
| Tier-1 skill index in prompt (lazy body) | plan/08:98–103 | ✅ | Explicit |
| Skill tools (View, Invoke, New, Manage, List) | plan/08:104–114 | ✅ | |
| Slash commands | plan/02 + plan/08:116–120 | ✅ | |
| KB mirror + lineage | plan/08:122–124 | ✅ | plan/07a:225–231 mirrors |
| Curator state file `.curator_state.toml` | plan/08:138–149 | ✅ | |
| Curator triggers (cron / manual / post-merge) | plan/08:152–155 | ✅ | |
| Decision table | plan/08:159–167 | ✅ | Detailed |
| Tight toolset + 30-action budget + lock | plan/08:171–173 | ✅ | |
| Trace event `CuratorRun { actions }` | plan/06 §"Event variants" | ⚠️ | plan/06:328 defines payload only; **`CuratorAction` struct undefined** |
| Bundled-promotion criteria | plan/08:179–185 | ✅ | Four gates; human-in-loop |
| Archive-not-delete + tarball + pinned exemption | 00c §2 | ✅ | 00c:134–139 |
| Draft promotion (output of G-014 / G-023) | Scenario 05 assumes `drafts/` | ⚠️ | **Gap G-027 (draft path spec)** |
| One-shot rewrite via frontier inference | plan/08:163 | ⚠️ | Mentions; **no model / config specified** |
| Schema validator on frontmatter (post-rewrite) | 00c §2 | ✅ | 00c:121–131 |
| Embedding service for consolidation decisions | Plan assumes KB | ⚠️ | **Gap G-028 (embedding provider unspecified)** |
| Hot-reload invalidates Tier-1 fingerprint only | plan/08:127–129 + plan/07 | ✅ | |
| Bundled-promotion human-in-the-loop | plan/08:184–185 | ✅ | Explicit |
| Auto-rollback on skill regression | Scenario asserts | ❌ | **Gap G-029 (no rollback policy)** |

---

## 2. Self-improvement assertions

| # | Assertion | Verdict | Evidence |
|---|---|---|---|
| 1 | Agent-side loop closes weekly | ✅ | plan/08:153 cron 168h |
| 2 | Tier-1 cache stays friendly (index-only invalidation) | ✅ | plan/08:127–129 + plan/07 tiering |
| 3 | Reflexion / LLM-curation lives only here | ⚠️ | Curator present, but **Dream consolidator (00d §32) is a separate system; overlap unspecified** (OQ #6) |
| 4 | Curator actions are themselves trainable | ✅ | Scenario emits traces to KB; 00d §32 logs `Event::DreamRun` |
| 5 | Lineage enables A/B + auto-rollback | ⚠️ | Lineage ✅ (07a:225–231, 08:122–124); auto-rollback ❌ (G-029) |
| 6 | Promotion gate human-in-the-loop | ✅ | plan/08:184–185 |

---

## 3. Gaps surfaced

### G-027. Skill-draft filesystem path + lifecycle — **blocker**
- **Owner:** `plan/07a` (memory extraction output) + `plan/08` (Curator inventory).
- **Resolution:** Pin the path: `~/.lamark/skills/drafts/<YYYY-MM-DD>_<name>.md` (timestamped to allow multiple drafts of the same skill in one week). Specify KB mirror contract (drafts are KB `SkillRecord` with status=`draft`). Curator reads filesystem first, KB as fallback.

### G-028. Embedding service for skill consolidation — **blocker**
- **Owner:** `plan/08` Curator + `plan/07a` KB capabilities.
- **Resolution:** Pick one: (a) KB-hosted (consistent with hybrid retrieval; new endpoint `POST /knowledge/skills/embed`); (b) local `sentence-transformers` model (private, slower); (c) auxiliary-model call. Recommend (a) for v0.1; fallback (b) if KB offline. Config: `skills.curator.embedding_provider`.

### G-029. Auto-rollback policy for regressed skill versions — **deferred**
- **Owner:** `plan/08` Curator decision table.
- **Resolution:** Specify Δ-threshold (e.g., new version's success rate ≥ 0.10 lower than prior over 50+ uses → auto-archive newer, restore prior). v0.1: manual rollback via `lamark skills rollback <name>`; v0.2: automatic with the threshold above.

---

## 4. Cross-references to prior gaps

- **G-014** (blocker; scenario 03) — Memory-extraction step. Scenario 05 *consumes* its output. G-027 plugs the handoff filesystem location.
- **G-023** (deferred; scenario 04) — Convergence-detection metric. Scenario 05 uses it (≥ 3 independent uses) for draft eligibility.
- **G-017** (deferred; scenario 03) — Concurrent sessions dedup. If N agents extract the same draft in parallel, Curator should deduplicate; ties back to KB upsert semantics.

## 5. Open-question resolutions

| # | Question | Resolution |
|---|---|---|
| 1 | Draft path | → G-027. Pin to `~/.lamark/skills/drafts/`. |
| 2 | Embedding service | → G-028. KB-hosted default; local fallback. |
| 3 | Curator one-shot rewrite model | Frontier preferred; configurable; falls through to local LoRA if disabled. Pin in `plan/08:163`. |
| 4 | Auto-rollback policy | → G-029. Manual v0.1; automatic with threshold v0.2. |
| 5 | Cross-project Curator scope | Global per user; per-project facts must not bleed (tag enforcement at KB layer). |
| 6 | Curator vs Dream consolidator | **Still open.** `plan/00d §32` describes Dream as four-phase read-only synthesis; `plan/08` describes Curator with skill decision table. Siblings, not the same. **ADR needed**: define division of labor — Curator owns skills, Dream owns memory/strategy consolidation; clarify whether Dream invokes Curator or vice versa. |

## 6. Critical insight

Scenario 05 is **well-scoped locally but architecturally incomplete at the boundaries**. Curator's internal operations (locking, decision table, budget, KB lineage, hot-reload) map cleanly to `plan/08`. The breakage is at the **interfaces with neighboring subsystems**: where do drafts come from (G-027 / G-014), what powers similarity decisions (G-028), and where does Curator end and Dream begin (`plan/00d §32`)? The Dream consolidator is the most architecturally concerning — `plan/00d §32` and `plan/08 §"Part B"` independently each claim to *be* "the consolidator," and that ambiguity will block implementation until reconciled. The audit recommends folding Curator + Dream into a single ADR that defines their respective scopes: **Curator owns agent-authored skills (decision table, promotion); Dream owns memory/strategy consolidation (Reflexion-aware, read-only synthesis)**. Both can co-exist as two scheduled jobs sharing the same locking and budget primitives, but the spec must say so.

## 7. Recommended ADRs

| ADR | Title | Severity |
|---|---|---|
| ADR-0027 | Skill-draft lifecycle + filesystem path | **Blocker** (G-027) |
| ADR-0028 | Embedding service for Curator consolidation | **Blocker** (G-028) |
| ADR-0029 | Auto-rollback policy for regressed skill versions | Deferred (G-029) |
| ADR-0030 | Curator vs Dream consolidator boundary (unify or define disjoint scopes) | **Blocker** (architectural) |
