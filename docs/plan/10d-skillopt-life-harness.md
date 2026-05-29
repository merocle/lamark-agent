# 10d — Skill optimization + harness evolution (SkillOpt × LIFE-HARNESS)

> Spec for the skill adaptation layer that sits **before** SFT in the nightly pipeline.
> Skills first. Weights second. Harness always.
>
> Sources:
> - **SkillOpt** — arXiv:2605.23904 — text-space optimizer for agent skills; +23.5 pp avg on
>   GPT-5.5; tested on Qwen3.5-4B and Qwen3.6-35B-A3B (our exact stack).
> - **LIFE-HARNESS** — arXiv:2605.22166 — runtime harness adaptation; 88.5% avg relative
>   improvement; evolved from Qwen3-4B trajectories, transfers to 17 models including
>   Qwen3.5-4B, Qwen3.5-9B, Qwen3.5-27B, Qwen3.6-35B.

**Status:** Draft v0.1 — 2026-05-29  
**See also:** [`plan/08`](./08-layer-7-skills-plugins-curator.md) (Curator redesign), [`plan/10`](./10-training-pipeline.md) (nightly pipeline), [`plan/05`](./05-layer-4-agent-core.md) (turn loop).

---

## The core finding that changes the architecture

LIFE-HARNESS annotated 900 failure trajectories across seven deterministic environments and
found this failure distribution:

| Failure type | Share | What it means |
|---|---|---|
| **Trajectory degeneration** | 33.6% | Repetition, loops, oscillation, budget exhaustion |
| **Environment contract mismatch** | 33.3% | Wrong tool, wrong call order, semantic argument error |
| **Action realization** | 23.2% | Tool call not executable: bad args, JSON failure, wrong function name |
| **General reasoning** | 9.9% | Correct interface, wrong inference or computation |

**90% of failures are interface/harness failures, not reasoning failures.**

SFT and GRPO address the 9.9%. The harness addresses the 90%.
This inverts the training priority: fix the harness first, then use SFT only for the residual.

SkillOpt validates the complementary insight: a well-evolved skill file (+23.5 pp on GPT-5.5
without any weight changes) costs a fraction of nightly SFT and transfers across model
scales and harnesses.

The revised adaptation stack is:

```
1. LIFE-HARNESS evolution  → fixes 90% of failures (interface layer)
2. SkillOpt loop           → +23.5 pp from skills alone (procedure layer)
3. Nightly SFT             → residual reasoning failures (weight layer)
4. Weekly DPO              → alignment polish
5. GRPO (v0.2+)            → verifier-gated RL
```

---

## Part 1 — LIFE-HARNESS: four lifecycle layers

Every Lamark agent session runs through four harness layers that wrap the turn loop.
These layers are evolved from trace failures, not handcrafted once.

### 1.1 Architecture

```
            ┌─────────────────────────────────────────┐
            │            Lamark Turn Loop              │
            │                                          │
  task x ──▶│  ┌────────────────────────────────┐      │
            │  │  1. Environment Contract Layer  │      │
            │  │  (make constraints explicit)    │      │
            │  └────────────┬───────────────────┘      │
            │               ▼                          │
            │  ┌────────────────────────────────┐      │
            │  │  2. Procedural Skill Layer      │      │
            │  │  (inject retrieved skills)      │      │
            │  └────────────┬───────────────────┘      │
            │               ▼                          │
            │         LLM inference (frozen θ)         │
            │               │ action at                │
            │               ▼                          │
            │  ┌────────────────────────────────┐      │
            │  │  3. Action Realization Layer    │      │
            │  │  EXEC(at) or BLOCK(mt)          │      │
            │  └────────────┬───────────────────┘      │
            │               ▼                          │
            │         Execute tool / sandbox           │
            │               │ observation ot+1         │
            │               ▼                          │
            │  ┌────────────────────────────────┐      │
            │  │  4. Trajectory Regulation Layer │      │
            │  │  (detect + interrupt degenerate)│      │
            │  └────────────────────────────────┘      │
            └─────────────────────────────────────────┘
```

### 1.2 Layer 1 — Environment Contract

**What it does:** Makes stable environment constraints explicit before interaction begins.
Produces enhanced contract C' = C ⊕ ΔC inserted into the system prompt.

ΔC contains:
- Tool schemas with semantic constraints (not just JSON types)
- Admissible action sets and call-order rules
- Domain-specific policies (e.g. "never call finish before tool X")
- Recurring pitfalls from training traces

**Lamark mapping:** This is the environment-specific portion of the system prompt
assembled in `lamark-prompt`. Currently hardcoded. New: evolve ΔC from traces.

```rust
// lamark-harness/src/contract.rs (new crate)
pub struct EnvironmentContract {
    /// Base contract (tool schemas, stable rules) — never mutated
    pub base: String,
    /// Evolved delta — derived from trace failures; refreshed weekly
    pub delta: String,
}

impl EnvironmentContract {
    /// Render into the system prompt block
    pub fn render(&self) -> String { ... }
}
```

**Evolution trigger:** Weekly, from the past 7 days of traces. The coding agent (or teacher
model) reads failure traces + current delta, proposes additions, validates on held-out split.

### 1.3 Layer 2 — Procedural Skill Layer

**What it does:** BM25-retrieves relevant skills from skill memory based on the current task
description and injects them into the system prompt before the first LLM call.

```rust
// lamark-skills/src/retrieval.rs
pub fn retrieve_skills(
    query: &str,
    skill_store: &SkillStore,
    k: usize,             // default 3
) -> Vec<SkillDoc> {
    // BM25 or hybrid BM25+dense
    bm25_search(query, skill_store, k)
}
```

**This is what the current Curator produces.** The skills written by Curator are consumed
here at runtime. The LIFE-HARNESS finding confirms skills retrieved this way contribute to
the 88.5% improvement — the mechanism is already in `lamark-skills`.

### 1.4 Layer 3 — Action Realization

**What it does:** After LLM produces an action `at`, before the sandbox executes it, this
layer validates against environment-specific rules. Returns EXEC(at) or BLOCK(mt).

```rust
// lamark-harness/src/realization.rs
pub enum RealizationDecision {
    /// Execute as-is
    Exec,
    /// Block with a model-visible error message; agent retries within turn
    Block { message: String },
}

pub trait ActionRealizer: Send + Sync {
    fn realize(&self, action: &ToolCall, ctx: &TurnContext) -> RealizationDecision;
}
```

**CRITICAL distinction from `lamark-policy`:** Policy gates on user permission
(`Allow|Prompt|Forbidden`). Action Realization gates on **technical correctness** — silent,
immediate, model-visible blocking without user prompt. The two are orthogonal:

| Layer | Who decides | User sees | Purpose |
|---|---|---|---|
| `lamark-policy` | User/operator | Permission dialog | Safety/authorization |
| Action Realization | Environment rules | Nothing (agent retries silently) | Technical correctness |

**What it validates (evolved from traces):**
- Tool name exists in registry
- Required arguments present and correct type
- Semantic constraints (e.g. "args.date must be ISO-8601", "args.passenger_count ≤ 5")
- Domain-specific calling protocols (e.g. "must call search before book")
- Duplicate-call prevention (same exact call within same turn = block)

**Failure mode it prevents:** Action Realization failures (23.2% of all failures in LIFE-HARNESS). Currently Lamark has no such layer — tool errors propagate to the sandbox, produce cryptic error messages, and the agent often loops trying the same broken call.

### 1.5 Layer 4 — Trajectory Regulation

**What it does:** Monitors interaction patterns after each tool result and injects recovery
messages when degeneration is detected.

```rust
// lamark-harness/src/regulation.rs
pub struct TrajectoryRegulator {
    pub config: RegulationConfig,
}

pub struct RegulationConfig {
    pub repetition_threshold: usize,       // default 3 — same action N times → soft warning
    pub oscillation_window: usize,         // default 6 — ABAB... pattern in last N actions
    pub budget_warning_at: f32,            // default 0.3 — warn when 30% budget left
    pub budget_critical_at: f32,           // default 0.15 — hard directive when 15% left
}

pub enum RegulationOutput {
    /// No intervention
    None,
    /// Soft recovery hint — agent can ignore
    Hint { message: String },
    /// Warning with explicit retry suggestion
    Warning { message: String },
    /// Hard directive — agent must respond to this or turn is aborted
    Directive { message: String },
}
```

**What it detects (evolved from traces):**
- Same tool call repeated N times (repetition)
- Action sequence oscillating (ABAB pattern in last 6 actions)
- No state change after N steps (stagnation)
- Budget < 30% remaining without meaningful progress
- Self-contradictory corrections (agent arguing with itself)

**Failure mode it prevents:** Trajectory degeneration (33.6% — largest failure category). Currently Lamark's turn loop has no degeneration detection. Sessions spin until the context limit or max_iterations.

---

## Part 2 — SkillOpt: Curator as a text-space optimizer

The current Curator does ad-hoc skill consolidation. SkillOpt replaces it with a
**controlled optimization loop** — the same design discipline as weight training but applied
to text artifacts.

### 2.1 The optimization loop

```
for each epoch (default 4):
    1. ROLLOUT — run agent on batch of training tasks (B=40) with current skill
    2. REFLECT — split results into failure/success minibatches (size 8)
                 → optimizer generates add/delete/replace proposals for each
                 → hierarchical merge: failure edits > success edits
    3. RANK+CLIP — rank merged edits by expected utility, clip to Lt (default 4)
    4. CANDIDATE — apply top Lt edits → candidate skill
    5. VALIDATE — evaluate candidate on held-out split (Dsel)
                  → accept only if STRICTLY improves selection score
                  → rejected: add to epoch-local buffer (negative feedback)
    6. SLOW UPDATE (epoch boundary) — compare previous + current epoch skills
                  → write longitudinal guidance to protected section
                  → validate through gate
    7. META SKILL (epoch boundary) — summarize accepted/rejected patterns
                  → optimizer-side only; NOT deployed with agent
output: best_skill.md (300–2,000 tokens)
```

### 2.2 Implementation in `lamark-skills`

```rust
// lamark-skills/src/skillopt.rs

pub struct SkillOptConfig {
    pub epochs: usize,                    // default 4
    pub rollout_batch_size: usize,        // default 40
    pub reflection_minibatch_size: usize, // default 8
    pub edit_budget_initial: usize,       // default 4
    pub edit_budget_schedule: Schedule,   // Cosine | Linear | Constant | Autonomous
    pub edit_budget_floor: usize,         // default 2
    pub slow_update_sample_size: usize,   // default 20
    pub optimizer_model: String,          // teacher model; separate from target
}

pub struct SkillOptState {
    pub current_skill: SkillDoc,
    pub best_skill: SkillDoc,
    pub current_selection_score: f64,
    pub best_selection_score: f64,
    pub rejected_buffer: Vec<RejectedEdit>,  // epoch-local negative feedback
    pub meta_skill: Option<String>,           // optimizer-side only; NOT deployed
    pub skill_hash_cache: HashMap<SkillHash, f64>,
}

pub async fn run_skillopt(
    target_model: &dyn ModelProvider,
    optimizer_model: &dyn ModelProvider,
    harness: &dyn SkillHarness,
    initial_skill: SkillDoc,
    train_split: &[Task],
    selection_split: &[Task],
    config: SkillOptConfig,
) -> SkillDoc { /* ... */ }
```

### 2.3 Key design decisions (from SkillOpt ablations)

| Decision | Choice | Why |
|---|---|---|
| Edit budget (Lt) | 4, cosine decay to 2 | Bounded updates prevent erasing useful rules; ablation shows Lt=4 optimal |
| Validation gate | Strict `>` (ties rejected) | Ties mean no improvement; accepted skill must earn its place |
| Slow/meta update | Enabled | Removing it drops SpreadsheetBench 22.5 pp — biggest ablation effect |
| Rejected-edit buffer | Keep epoch-local | Provides negative feedback; removing drops 1.6–4.6 pp |
| Reflection minibatch | 8 (not 1, not 32) | Single-trajectory = anecdotal; minibatch = reusable procedural patterns |
| Evidence partition | Separate failure + success | Failure edits prioritized; success edits preserve working behavior |
| Meta skill | Optimizer-side ONLY | Deployed skill stays compact; training history not shipped |

### 2.4 What deployed skills look like

From SkillOpt experiments — final best_skill.md characteristics:
- **Size:** 379–1,995 tokens (median ~920)
- **Edits committed:** 1–4 per benchmark (median 2.5)
- **One accepted edit** produced the largest single gains (OfficeQA +39 pp, LiveMath +29 pp)
- Rules are **procedural, not instance-specific** — no names, no specific values, no task references
- Read as policies a thoughtful practitioner would write after extended domain exposure

Example learned rules (verbatim from best_skill.md files):
- *"Inspect workbook structure and formulas, then write evaluated static values across the full requested target range instead of relying on Excel recalculation."*
- *"Keep a horizon-aware visited/frontier ledger, diversify search after repeated same-type failures, and avoid revisiting the destination until holding the target."*

### 2.5 Transfer properties (important for Lamark)

| Transfer type | Result | Implication for Lamark |
|---|---|---|
| Cross-model (same harness) | Positive in 4/4 cells; sometimes exceeds in-domain | Optimize skill on Qwen3.5-9B; deploy on Qwen3.6-35B |
| Cross-harness (same model) | Codex→Claude Code: +59.7; Claude Code→Codex: +43.6 | Skills transfer across Lamark's CLI/gateway/MCP execution modes |
| Cross-benchmark (same domain) | Positive in 3/3 cells; smaller gains | Domain skill generalizes to nearby task families |

**Practical implication:** One skill optimization run on Qwen3.5-9B produces a portable artifact usable across all Lamark deployment modes without retraining.

---

## Part 3 — Harness evolution pipeline

How the harness layers (§1) are kept up-to-date from live traces.

### 3.1 Weekly harness evolution cycle

```python
# learning/scripts/harness_evolve.py

def evolve_harness(since_days=7):
    # 1. Collect traces from last N days
    traces = collect_traces(since=since_days)
    
    # 2. Annotate failures by type
    failures = annotate_failures(traces)
    # Categories: ACTION_REALIZATION | CONTRACT_MISMATCH | TRAJECTORY_DEGENERATION | REASONING
    
    # 3. Skip REASONING failures (→ SFT handles these)
    harness_failures = [f for f in failures if f.type != "REASONING"]
    
    # 4. For each failure type, propose harness updates
    contract_updates  = propose_contract_delta(harness_failures, filter="CONTRACT_MISMATCH")
    realization_rules = propose_realization_rules(harness_failures, filter="ACTION_REALIZATION")
    regulation_rules  = propose_regulation_rules(harness_failures, filter="TRAJECTORY_DEGENERATION")
    
    # 5. New skills from successful traces
    new_skills = extract_skills_from_successes(traces)
    
    # 6. Validate all proposals on held-out split
    proposals = [contract_updates, realization_rules, regulation_rules, new_skills]
    accepted  = [p for p in proposals if held_out_eval(p) > current_baseline]
    
    # 7. Apply accepted updates
    write_contract_delta(accepted.contract_updates)
    write_realization_rules(accepted.realization_rules)
    write_regulation_rules(accepted.regulation_rules)
    kb.post_skills(accepted.new_skills)
    
    return EvolutionReport(accepted=accepted, rejected=[p for p in proposals if p not in accepted])
```

### 3.2 Failure annotation protocol

From LIFE-HARNESS Appendix A.1 (adapted for Lamark):

```python
FAILURE_TAXONOMY = {
    "ACTION_REALIZATION": [
        "tool_call in content instead of tool_calls block",
        "invalid function name",
        "JSON parse failure",
        "missing required argument",
        "wrong argument type",
        "executable but semantically wrong format",
    ],
    "CONTRACT_MISMATCH": [
        "wrong tool for the step (finish before answer, search skipped)",
        "incorrect call order (book before search)",
        "premature submission",
        "tool used against stated policy",
    ],
    "TRAJECTORY_DEGENERATION": [
        "same action repeated ≥ 3 times",
        "oscillation ABAB in last 6 actions",
        "session ends at budget limit with clear repetition",
        "incorrect early commitment reinforced repeatedly",
        "environment returns repeated no-progress feedback",
    ],
    "REASONING": [
        "correct interface, wrong value or condition",
        "incorrect SQL / computation / retrieval",
        "wrong final decision despite following protocol",
    ],
}
```

### 3.3 Integration with nightly pipeline

Harness evolution runs **weekly, before the Sunday DPO run**:

```
Saturday T-2h:  harness_evolve.py --since 7d
                → updates contract delta, realization rules, regulation rules
                → new skills posted to KB
                → EvolutionReport committed to ~/.lamark/harness/

Sunday T+0:    DPO cycle begins (uses updated harness baseline)
```

The training tier ordering in the full weekly cycle:

```
Monday–Friday:  Nightly SFT (Tier 1) — residual REASONING failures only
Saturday:       SkillOpt loop (Tier 0.5) — skill optimization for current domain
Saturday:       Harness evolution — Contract/Realization/Regulation updates
Sunday:         DPO cycle (Tier 2)
```

---

## Part 4 — `lamark-harness` crate (new)

The four lifecycle layers live in a new crate sitting between `lamark-tools` and the turn loop.

```
lamark-harness/
├── src/
│   ├── lib.rs
│   ├── contract.rs       # EnvironmentContract: base + delta rendering
│   ├── skill_inject.rs   # BM25 skill retrieval + system prompt injection
│   ├── realization.rs    # ActionRealizer trait + evolved rule engine
│   ├── regulation.rs     # TrajectoryRegulator: degeneration detection
│   ├── evolved/          # persisted evolution artifacts (not compiled in)
│   │   ├── contract_delta.md
│   │   ├── realization_rules.toml
│   │   └── regulation_rules.toml
│   └── evolution.rs      # harness_evolve() entry point (called from trainer)
└── Cargo.toml
```

**Dependency direction:** `lamark-harness` depends on `lamark-core` + `lamark-tools` + `lamark-skills`. The turn loop in `lamark-core` gets a `HarnessStack` injected at construction. No circular deps.

```rust
// lamark-core/src/agent.rs
pub struct AIAgent {
    pub provider: Arc<dyn ModelProvider>,
    pub tools: Arc<ToolRegistry>,
    pub harness: Arc<HarnessStack>,   // NEW — inject at construction
    pub hooks: Arc<HookBus>,
    pub trace: Arc<TraceRecorder>,
    // ...
}
```

**Crate dependency rules:** `lamark-core` must NOT import `lamark-harness` directly
(violates no-upper-layer-peer rule). Instead: `lamark` binary wires `HarnessStack` into
`AIAgent` at startup, same as how it wires providers and tools.

---

## Part 5 — How this changes the nightly pipeline

### Before (current)

```
trace → SFT → DPO → GRPO (v0.2+)
90% of failures trained against with weights
Forgetting risk every night
```

### After

```
trace → harness_evolve → SkillOpt → SFT (residual) → DPO → GRPO (v0.2+)
90% of failures fixed at interface layer (no forgetting risk)
SFT treats only residual 10% (reasoning failures)
SFT training set is smaller → less forgetting risk → more stable nightly cycle
```

**SFT data filtering:** Before blending, classify failures from the night's traces:

```python
def filter_for_sft(traces):
    """Only reasoning failures go to SFT. Interface failures go to harness evolution."""
    annotated = annotate_failures(traces)
    sft_eligible = [
        t for t in annotated
        if t.failure_type == "REASONING" or t.success  # successes always eligible
    ]
    harness_eligible = [
        t for t in annotated
        if t.failure_type in ("ACTION_REALIZATION", "CONTRACT_MISMATCH", "TRAJECTORY_DEGENERATION")
    ]
    return sft_eligible, harness_eligible
```

This shrinks the nightly SFT training set by ~90% of failures → faster training, less forgetting.

---

## Part 6 — Crate layout additions to SPEC.md §6

```
agent/crates/
├── lamark-harness/          # NEW — four lifecycle layers (Contract/Skill/Realization/Regulation)
```

Add to SPEC.md §2.1 (Hermes inheritance):
- Action Realization Layer (LIFE-HARNESS §3.4)
- Trajectory Regulation Layer (LIFE-HARNESS §3.5)
- SkillOpt-style Curator (SkillOpt §3)

---

## Part 7 — Curator redesign (replaces plan/08 §Part B)

The current Curator does ad-hoc skill consolidation on a 7-day cycle.
Replace with SkillOpt-disciplined loop:

| Old Curator | New SkillOpt Curator |
|---|---|
| 7-day passive consolidation | Active optimization loop with training-style controls |
| Archive-not-delete | Same (preserved) |
| Judge model grades skills | Optimizer model proposes bounded edits |
| No validation gate | Strict held-out validation gate |
| No edit budget | Lt edits per step, cosine schedule |
| No negative feedback | Epoch-local rejected-edit buffer |
| No slow update | Epoch-wise slow/meta update (protected section) |
| Skill consolidation is destructive | Best-skill-only export; history preserved |

The 7-day Curator cycle maps to 1 SkillOpt training run (4 epochs × batch_size 40 = 160 task rollouts).

---

## References

- **SkillOpt** — arXiv:2605.23904. Benchmarks: SearchQA (+9.6), SpreadsheetBench (+38.9), OfficeQA (+39.0), DocVQA (+12.4), LiveMath (+29.3), ALFWorld (+11.9). Average +23.5 pp GPT-5.5. 52/52 cells best or tied.
- **LIFE-HARNESS** — arXiv:2605.22166. 116/126 model-env settings improved. 88.5% avg relative improvement. Failure distribution: Contract 33.3%, Degeneration 33.6%, Realization 23.2%, Reasoning 9.9%.
- **Key empirical result**: LIFE-HARNESS on Qwen2.5-32B beats specialized tool-use fine-tuned xLAM-2-32B by 7.5 pp in-domain; xLAM degrades out-of-domain. Harness and training are complementary, not competing.
