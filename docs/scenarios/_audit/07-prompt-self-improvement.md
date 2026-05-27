# Audit — scenario 07: Prompt self-improvement (Reflexion + OPRO + A/B)

**Verdict:** 🟡 Yellow. Design is comprehensive — three decoupled self-improvement loops with appropriate gates and safety guards. Loop B (Reflexion) and Loop C (OPRO + A/B + promote) are well-traced through `plan/07b`. **Seven new gaps surfaced; three blocker-level for v0.1.** The attribute-to-sections plumbing (G-033, G-034) is incomplete and prompt-adapter serialization (G-035) lacks enforcement logic.

---

## 1. Plan coverage matrix (14 rows)

| Concern | Covered in | Status | Notes |
|---|---|---|---|
| Loop A — Self-Refine (opt-in) | plan/07b §"Loop A" | ✅ | Slash command + auto-trigger on structural failure; default off |
| Loop B — Reflexion post-session hook | plan/07b §"Loop B" + plan/07a `Episode` | ✅ | Post-failure critique → KB episodic entry, topic-tagged |
| Reflexion lesson cap (≤ 500 / 1 KB) | plan/07b §"Loop B" | ✅ | Hard caps per project; decay when not reinforced |
| Loop C — OPRO/ProTeGi outline | plan/07b §"Loop C" | ✅ | Nightly in `plan/10`; K=8 variants; shadow + A/B |
| **`section_id` per-turn logging** | (G-033) | ❌ | `TraceEvent::TurnEnded` does not include active sections |
| **A/B traffic-split runner crate** | (G-034) | ❌ | Scenario assumes `crates/lamark-prompt::ab`; not in plan/07 |
| Variant safety eval (Constitutional filter) | plan/07b + plan/10 eval gate | ⚠️ | Constitutional rules location unspecified (G-037) |
| Promotion gate with statistical significance | plan/07b §"Loop C" | ✅ | Z-test α=0.05, Bonferroni-corrected |
| Held-out scoring uses deterministic replay (G-003) | plan/06 reducer + plan/10 | ✅ | Trainer calls frozen probe; determinism guard in place |
| **Section lineage in KB** | (G-036) | ⚠️ | Scenario references `SectionVersionEvent`; plan/10 lacks the KB endpoint |
| **Serialization with adapter rollout** | (G-035) | ⚠️ | Scenario calls it out as a failure mode; enforcement mechanism missing |
| Memory cap + decay for Reflexion | plan/07a (general) | ⚠️ | Decay schedule unspecified (G-038) |
| Prompt composer supports versioned sections | plan/07 §"layered composition" | ✅ | `Section.id` stable; `fingerprint` for cache keys |
| Reinforce-on-use detection for lessons | plan/07a + scenario 03 OQ#5 | ⚠️ | Inherits G-019 (deferred) |

---

## 2. Self-improvement assertions

| # | Assertion | Status | Evidence |
|---|---|---|---|
| 1 | Reflexion closes a runtime loop | ✅ | plan/07b Loop B + plan/07a Memory |
| 2 | OPRO produces measurable section-level uplift | ✅ | plan/07b §"Loop C" C1–C5 + plan/10 |
| 3 | Attribution is exact (`section_variant_id` per turn) | ⚠️ | Blocked on G-033 |
| 4 | Loops do not entangle with training (48h serialization) | ⚠️ | Listed as failure mode but enforcement missing (G-035) |
| 5 | Self-improvement is bounded | ✅ | plan/07b safety guards |

---

## 3. Gaps surfaced

### G-033. `section_id` per-turn logging in trace events — **blocker**
- **Owner:** `plan/06` recorder (`TraceEvent::TurnEnded`).
- **Resolution:** Add `active_sections: Vec<SectionId>` (or a new `SectionActive` event). Without it, per-section outcome aggregation is impossible; attribution assertion #3 collapses.

### G-034. A/B traffic-split runner crate — **blocker**
- **Owner:** `plan/07` prompt composer; new `crates/lamark-prompt::ab`.
- **Resolution:** Specify `ABRunner` trait + `DeterministicAssignment` impl. Same `(rollout_id, section_id)` → same variant across restarts (hash-based). Per-variant sample counter for statistical gating.

### G-035. Adapter-rollout serialization enforcement — **blocker**
- **Owner:** `plan/10` training pipeline + `plan/07b §"C5"`.
- **Resolution:** C5 gate queries KB for `AdapterPromoted` events on the same base model; if any in last 48h, block prompt promotion. Threshold tunable per model via `training.adapter_quiet_hours`. Without this, prompt A/B attribution contaminated by simultaneous LoRA rollouts.

### G-036. Section lineage in KB — **deferred**
- **Owner:** `plan/10` KB client + KB API contract.
- **Resolution:** Parallel to skill lineage (`plan/08:122`). New endpoint `POST /knowledge/prompt_sections/{id}/lineage_events`. Event types: `SectionProposed`, `SectionABStarted`, `SectionPromoted`, `SectionRolledBack`.

### G-037. Constitutional rules definition and location — **deferred**
- **Owner:** `plan/07b §"Verification gates"` + `plan/10`.
- **Resolution:** File format (YAML) at `~/.lamark/CONSTITUTION.yaml`. Schema: `core_constraints[]` with `id`, `description`, `forbidden_patterns[]`, `required_patterns[]`. C2 variant generation passes through the filter; constraint failure rejects the variant before scoring.

### G-038. Reflexion lesson expiration policy — **deferred**
- **Owner:** `plan/07a` + `plan/07b §"Loop B"`.
- **Resolution:** Pin: after 3 non-reinforce cycles, decay weight by half; after 6, archive. Hard TTL = 180 days regardless. Manual delete via `lamark memory forget <id>`. Surfaces in `lamark memory list --kind episode`.

### G-039. Section version promotion atomicity — **deferred**
- **Owner:** `plan/07` composer + `plan/10`.
- **Resolution:** Two-phase commit: write new section to a staging area; fence in composer (atomic pointer swap); rollback procedure on KB write failure. Prevents inconsistency where promotion's KB record exists but composer doesn't see the new version.

---

## 4. Cross-references to prior gaps

- **G-003** (reducer determinism) — directly required for C3 held-out scoring.
- **G-004 / G-008 / G-012** (`reinforce_signal` + DPO schema) — Loop B depends on clean `reinforce_signal` from reducer.
- **G-019** (Tier-3 cache invalidation when memory changes) — Reflexion lessons land in Tier-3; G-019 keeps it tractable.

## 5. Open-question resolutions

| # | Question | Resolution |
|---|---|---|
| 1 | `section_id` taxonomy | Unresolved → ADR-0035 with G-033. |
| 2 | Frontier-model dependency for OPRO | Deferred; explicit fallback not needed for v0.1. |
| 3 | A/B sample size (200 vs per-project) | Spec missing; recommend per-project override in `~/.lamark/config.toml`. |
| 4 | Loop C cadence | Nightly generation + weekly promotion is the recommended default; pin in ADR. |
| 5 | Constitutional rules location | → G-037. |
| 6 | Reflexion lesson lifecycle | → G-038. |

## 6. Critical insight

Scenario 07 is the **enabler of scenario-level self-improvement**, sitting at the intersection of three timescales: Loop A (per-turn), Loop B (per-session), Loop C (per-week). The design cleanly separates them and routes each through appropriate gates — Loop A opt-in, Loop B capped + decayed, Loop C shadow-tested + significance-gated + safety-filtered. The architecture is right. What's missing is **infrastructure to make the loops measurable**: without `section_id` per-turn logging (G-033) and an A/B runner (G-034), attribution collapses. Without adapter-rollout serialization (G-035), Loop C's measurements are contaminated by Scenario 15's training. **All three are blockers for a ship that claims "self-improving":** you cannot improve something you cannot measure, and you cannot measure sections you do not log.

## 7. Recommended ADRs

| ADR | Title | Severity |
|---|---|---|
| ADR-0035 | `section_id` + `section_variant_id` in trace events | **Blocker** (G-033) |
| ADR-0036 | A/B traffic-split runner (deterministic assignment) | **Blocker** (G-034) |
| ADR-0037 | Adapter rollout serialization gate (48h quiet hours) | **Blocker** (G-035) |
| ADR-0038 | Prompt-section lineage in KB | Deferred (G-036) |
| ADR-0039 | Constitutional rules — format, location, validator binding | Deferred (G-037) |
| ADR-0040 | Reflexion-lesson expiration policy | Deferred (G-038) |
| ADR-0041 | Section version promotion atomicity (2PC) | Deferred (G-039) |
