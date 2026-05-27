# 07a — Layer 6b: Memory + knowledge-base client

> Memory is the second pillar of Hermes-style persistence. We adopt the
> pluggable-provider design but route the default through `../knowledge-base`
> rather than local SQLite.

> 📎 **See also:** [00c addendum §4](./00c-hermes-deepdive-addendum.md) — adds
> `post_setup`, `sync_turn(turn_messages)`, `prefetch(query)`, `shutdown` to the
> `MemoryProvider` trait, and pins the `memory.skip_in_cron = true` default.

**Crates:** `crates/lamark-memory/`, `crates/lamark-kb-client/`.
**Depends on:** `lamark-core`, `reqwest`, `rusqlite` (fallback), `serde`.
**References:**
- `~/.cache/lemark/vendor/hermes-agent/agent/memory_manager.py` — provider trait + only-one-external-plugin invariant.
- `../knowledge-base/docs/02-system-specs.md` — Kotlin/Spring backend; `/memory /knowledge /search /graph /agents` endpoints.
- `../knowledge-base/docs/07b-public-api-rfc.md` — locked API contract.

**Key papers (see [`plan/00f-literature-survey.md`](./00f-literature-survey.md) §2 for full catalog):**
- **MemGPT** (arXiv 2310.08560) — OS-inspired tiered memory with LLM-driven paging; model emits `memory.search` / `memory.insert` tool calls autonomously.
- **Mem0** (arXiv 2504.19413) — production hybrid vector+graph; 91% lower p95 latency, 90%+ token cost reduction vs full-context.
- **Zep / Graphiti** (arXiv 2501.13956) — temporally-aware KG with `valid_from`/`valid_until` per fact; enables expiry without history deletion.
- **RAPTOR** (arXiv 2401.18059) — recursive abstractive retrieval tree; multi-level summaries for knowledge-base content.
- **Reflexion** (arXiv 2303.11366) — verbal reinforcement via episodic memory; lessons written to KB after failed rollouts.
- **AgentHER** (arXiv 2603.21357) — hindsight relabeling of failed trajectories; +7–12pp on WebArena/ToolBench vs success-only SFT.
- **ACON** (arXiv 2510.00615) — universal context compression for long-horizon agents; compress observations and history separately.

## What memory is for

Three jobs, often conflated:

1. **Durable facts.** "User prefers Rust over Go." "Project X uses Postgres 15." These live forever, indexed by topic.
2. **Episodic memory.** "Last Tuesday's bug in module Y was caused by Z; here's the fix that worked." Time-bound; useful for Reflexion-style learning.
3. **User profile.** "Alice is a senior backend engineer who works in JetBrains, prefers terse responses, hates emoji." Slow-changing.

Hermes splits these across `MEMORY.md` (1+2 mashed), `USER.md` (3), and external providers (1+2 advanced). We split them cleanly via knowledge-base's typed memory API.

## Memory trait

```rust
// crates/lamark-memory/src/trait.rs
#[async_trait]
pub trait MemoryProvider: Send + Sync {
    fn name(&self) -> &str;
    fn capabilities(&self) -> MemoryCapabilities;

    /// Write a fact / episode / observation.
    async fn write(&self, entry: MemoryEntry) -> Result<MemoryId, MemoryError>;

    /// Retrieve facts relevant to the current context.
    async fn search(&self, q: MemoryQuery, k: usize) -> Result<Vec<Recall>, MemoryError>;

    /// Build the prompt-time memory block (Tier-3 volatile, plan/07).
    async fn build_prompt_block(&self, ctx: &MemoryPromptContext) -> Result<MemoryBlock, MemoryError>;

    /// Mark an entry as confirmed-correct after a successful use.
    async fn reinforce(&self, id: MemoryId, signal: ReinforceSignal) -> Result<(), MemoryError>;

    /// Health check for `lamark doctor`.
    async fn health(&self) -> Result<HealthReport, MemoryError>;
}

pub struct MemoryEntry {
    pub kind: MemoryKind,                 // Fact | Episode | UserProfile | Skill | Strategy
    pub content: String,
    pub topic: Option<String>,
    pub source: MemorySource,             // user_explicit | agent_observation | tool_result | curator
    pub session_id: Option<SessionId>,
    pub turn_id:    Option<TurnId>,
    pub embeddings: Option<Vec<f32>>,     // optional; KB will embed if not provided
    // Temporal validity per Zep/Graphiti (arXiv 2501.13956): KB expires stale facts
    // without deleting history. Entities with changing state (e.g., "agent is on task X")
    // must set valid_until; permanent facts leave it None.
    pub valid_from:  Option<SystemTime>,
    pub valid_until: Option<SystemTime>,
    // Related memory IDs for inter-note linking (A-MEM, arXiv 2502.12110).
    // Written at insert time, not inferred at query time.
    pub related_ids: Vec<MemoryId>,
    pub metadata: HashMap<String, Value>,
}

pub struct MemoryQuery {
    pub text: Option<String>,
    pub embedding: Option<Vec<f32>>,
    pub topic: Option<String>,
    pub kinds: Vec<MemoryKind>,
    pub time_range: Option<TimeRange>,
    pub limit_chars: Option<usize>,
}

pub struct Recall {
    pub id: MemoryId,
    pub entry: MemoryEntry,
    pub score: f32,
    pub provenance: String,               // "kb:vector", "kb:graph", "honcho:dialectic", "local:fts5"
}
```

Capabilities:

```rust
pub struct MemoryCapabilities {
    pub supports_vector_search: bool,
    pub supports_graph: bool,
    pub supports_user_profile: bool,
    pub supports_reinforcement: bool,
    pub supports_time_range: bool,
}
```

## Implementations

```
crates/lamark-memory/
└── src/
    ├── trait.rs
    ├── compose.rs            # CompositeMemoryProvider — merges N providers per call
    ├── prompt_block.rs       # Build the Tier-3 prompt section from recalls
    └── providers/
        ├── kb.rs             # ← default. knowledge-base HTTP client
        ├── sqlite.rs         # local fallback; rusqlite with FTS5
        ├── honcho.rs         # Honcho dialectic user modeling (HTTP)
        ├── mem0.rs           # Mem0 API
        ├── hindsight.rs      # Hindsight episodic memory
        └── noop.rs           # for tests / `learning.enable_collection=false`
```

### The default: `KnowledgeBaseMemory`

```rust
pub struct KnowledgeBaseMemory {
    kb:        Arc<KbClient>,
    project_id: String,
    agent_id:   String,
    spool:     Arc<SqliteSpool>,           // local write spool when KB is unreachable
}
```

Writes are **fire-and-forget through a spool**:

```rust
async fn write(&self, entry: MemoryEntry) -> Result<MemoryId, MemoryError> {
    let id = MemoryId::new();
    self.spool.enqueue(id, &entry).await?;        // local persistent queue
    // best-effort immediate upload; if it fails, spool retries.
    let _ = tokio::spawn({
        let kb = self.kb.clone();
        let p = self.project_id.clone();
        let entry = entry.clone();
        async move {
            let _ = kb.post_memory_fact(&p, &entry).await;
        }
    });
    Ok(id)
}
```

### RAPTOR multi-level retrieval

The KB's `/search` endpoint is populated with multi-level RAPTOR-style summaries (arXiv 2401.18059):
- **Leaf level** — raw trace event chunks and raw document chunks.
- **Mid level** — cluster summaries produced by the trainer's `kb_client.py` after each nightly cycle.
- **Root level** — agent-level belief summaries produced by the Curator's weekly reflection pass.

At search time, query all levels and let the model synthesize. Pass an `abstraction_level` hint in `MemoryQuery.metadata` so the KB can scope results:

```rust
let q = MemoryQuery {
    text: Some("KV cache eviction strategy".to_string()),
    metadata: [("abstraction_level", "mid")].into(),
    ..Default::default()
};
```

This is more effective than single-level chunk retrieval for multi-step agent tasks — especially for tasks that require synthesizing information spread across multiple sessions.

### AgentHER: failed trajectory relabeling

Failed rollouts are not discarded. Per AgentHER (arXiv 2603.21357), the `lamark-trace` reducer tags failed rollouts for hindsight relabeling:

1. On `TurnEnded { status: Failed | Aborted }`, the reducer checks whether the rollout achieved any intermediate milestone (e.g., card moved to `in_progress`, file partially modified, partial test pass).
2. If a milestone was achieved, the reducer emits an `HindsightRelabeled` annotation in `reduced/conversation.jsonl` — the failed rollout is relabeled as "successfully achieving sub-goal X."
3. The trainer's `transform/trace_to_messages.py` consumes this annotation and generates a training sample for the relabeled sub-goal, rather than discarding the whole trajectory.

This +7–12 pp improvement on WebArena/ToolBench vs success-only SFT justifies the relabeling overhead.

Reads are **direct, but with a tight timeout and a fallback**:

```rust
async fn search(&self, q: MemoryQuery, k: usize) -> Result<Vec<Recall>, MemoryError> {
    let res = tokio::time::timeout(
        Duration::from_millis(self.kb.timeout_ms),
        self.kb.memory_search(&self.project_id, &q, k),
    ).await;
    match res {
        Ok(Ok(recalls)) => Ok(recalls),
        _ => {
            // fall back to local FTS5
            self.sqlite_fallback.search(q, k).await
        }
    }
}
```

The spool is **also** the local FTS5 fallback's source: as writes upload to KB, they go through both. So even if KB is down for days, local search still works.

### `CompositeMemoryProvider`

If `memory.external_provider == knowledge-base` AND `memory.honcho.enable == true`, a CompositeMemoryProvider routes:

- `search` → fan out to all configured providers in parallel; merge by score; cap by `limit_chars`.
- `write` → write to KB (primary); also notify Honcho (for dialectic).
- `build_prompt_block` → assemble a structured Tier-3 block with provenance:

```
<memory project="lamark-default" session="…">
  <facts>
    Rust uses cargo, not pip.                       [kb:graph 0.91]
    User prefers tests in tests/ over inline.       [kb:vector 0.83]
  </facts>
  <user_profile>
    Senior backend; JetBrains; Russian/English.     [kb:user_profile]
  </user_profile>
  <episodic>
    2026-05-22: Fixed PR #142 by rewriting the      [hindsight 0.77]
    pipeline to use bounded channels.
  </episodic>
  <strategies>
    For "scrape + summarize" prompts, spawning      [kb:strategy 0.81]
    3-4 subagents with budget=600s outperforms 1.
  </strategies>
</memory>
```

The trailing `[provenance score]` tags are stripped before sending to the model but kept in the trace recorder's payload for audit.

## Knowledge-base client (`lamark-kb-client`)

A thin HTTP wrapper. Public methods correspond 1:1 to `../knowledge-base/docs/07b-public-api-rfc.md`. Frozen surface (semver):

```rust
pub struct KbClient {
    http:    reqwest::Client,
    base_url: Url,
    token:   String,
    timeout_ms: u64,
}

impl KbClient {
    // Knowledge / documents
    pub async fn upsert_document(&self, p: &str, doc: &Document) -> Result<DocId>;
    pub async fn search(&self, p: &str, q: &SearchQuery) -> Result<Vec<SearchHit>>;
    pub async fn graph_query(&self, p: &str, q: &GraphQuery) -> Result<GraphResult>;

    // Memory
    pub async fn post_memory_fact(&self, p: &str, e: &MemoryEntry) -> Result<MemoryId>;
    pub async fn memory_search(&self, p: &str, q: &MemoryQuery, k: usize) -> Result<Vec<Recall>>;
    pub async fn upsert_user_profile(&self, p: &str, user_id: &str, profile: &UserProfile) -> Result<()>;
    pub async fn get_user_profile(&self, p: &str, user_id: &str) -> Result<Option<UserProfile>>;

    // Agent state
    pub async fn upsert_agent(&self, p: &str, agent: &AgentRegistration) -> Result<()>;
    pub async fn post_trace(&self, p: &str, bundle: &TraceUploadRequest) -> Result<TraceId>;
    pub async fn list_traces(&self, p: &str, q: &TraceListQuery) -> Result<Vec<TraceMetadata>>;
    pub async fn post_event(&self, p: &str, agent_id: &str, evt: &AgentEvent) -> Result<()>;

    // Adapters / training
    pub async fn upsert_adapter(&self, p: &str, agent_id: &str, a: &AdapterMetadata) -> Result<()>;
    pub async fn upsert_dataset(&self, p: &str, ds: &DatasetRecord) -> Result<DatasetId>;
    pub async fn upsert_eval_set(&self, p: &str, e: &EvalSet) -> Result<EvalSetId>;

    // Skills (mirror)
    pub async fn upsert_skill(&self, p: &str, agent_id: &str, s: &SkillRecord) -> Result<()>;
    pub async fn list_skills(&self, p: &str, q: &SkillQuery) -> Result<Vec<SkillRecord>>;

    // Reinforce
    pub async fn reinforce(&self, p: &str, target: &ReinforceTarget, signal: &ReinforceSignal) -> Result<()>;
}
```

All methods take `project_id` as first arg (project = KB's multi-tenant boundary; per SPEC §10).

### Auth

Bearer token from `KB_TOKEN` env. Token type per KB's auth/RBAC service. Refresh handling: tokens are long-lived (or rotated externally); the client doesn't auto-refresh in v0.1.

### Resilience

- Connection pooling via reqwest.
- Per-call timeout: 5s default.
- Retry: idempotent operations (`POST` with client-generated id) → 3 attempts, exponential backoff.
- Circuit breaker: if 10 consecutive calls fail, open the breaker for 30s and route to fallback. Trace event `KbCircuitOpened`.
- All metrics: `lamark_kb_calls_total{op,outcome}`, `lamark_kb_latency_seconds{op}`.

### Schema versioning

The KB API contract is **pinned per Lamark release**. `KbClient::new(...)` performs a `GET /version` on startup and refuses to operate if the KB's version is incompatible (compare against compiled-in `EXPECTED_KB_SEMVER`).

## On-denial memory write

**G-010.** When the permission bus resolves with `Decision::Deny`, the turn loop must call `memory.write(MemoryCandidate { kind: DeniedIntent, ... })` with the following fields:

- `intent`: the tool name + sanitized arg summary (no secrets — strip env values, tokens, passwords before recording).
- `policy_rule`: the policy rule that triggered the denial, if identifiable from the `PolicyViolation` event.
- `session_id` and `turn_id` of the denied call.

```rust
// In the turn loop, after receiving Decision::Deny:
let candidate = MemoryCandidate {
    kind: MemoryCandidateKind::DeniedIntent,
    intent: format!("{}: {}", tool_name, sanitize_args(&tool_args)),
    policy_rule: violation.rule_id.clone(),
    session_id: ctx.session_id,
    turn_id: ctx.turn_id,
    ..Default::default()
};
// fire-and-forget; denial must not block the turn loop
let _ = memory.write(candidate.into()).await;
```

This write is **fire-and-forget** — denial must not block the turn loop. If KB is down, the spool accepts it.

**Downstream consumers of `DeniedIntent` entries:**

- **Policy-rule-proposal subsystem (G-009):** clusters repeated denials to suggest new `Allow` rules when the user keeps approving overrides for the same pattern.
- **Training pipeline (DPO rejected-trajectory authoring, G-008):** denied tool calls become the "rejected" side of preference pairs, helping the model learn not to attempt disallowed actions.

---

## Memory candidate extraction (v0.1 heuristic)

**G-014.** The reducer (plan/06) calls `lamark-memory::extract::extract_candidates(turn: &ReducedTurn) -> Vec<MemoryCandidate>` **after** each turn. All rules are deterministic — no LLM calls, no added latency.

### Extraction rules

1. **Successful tool call → `Fact` candidate.** If the turn contains a `ToolCallEnded { ok: true }` event with a tool in `["write_file", "apply_patch", "run_shell"]` and the session's `reinforce_signal` is `Success`, emit a `Fact` candidate:
   - `body = "Successfully used {tool} in context: {short_summary}"`
   - `short_summary` = first 120 chars of the tool's output or the argument, whichever is shorter.

2. **Failed tool call → `Lesson` candidate.** If `reinforce_signal` is `Fail`, emit a `Lesson` candidate with the error message as the body.

3. **Code block in successful turn → `Fact` candidate.** If the assistant turn contains a code block (detected by ` ``` ` fence) and `reinforce_signal` is `Success`, emit a `Fact` with the file path (if extractable from adjacent context) and language tag.

4. **Cap at 3 candidates per turn.** If more than 3 candidates are produced, keep the longest/most specific (measured by `body.len()`). Ties broken by rule priority order (1 > 2 > 3).

### Module location

```
crates/lamark-memory/
└── src/
    └── extract.rs    # extract_candidates — pure fn, no async, no model calls
```

LLM-assisted extraction (Reflexion-style) belongs in Curator (plan/08), not here.

---

## `MemoryQuery` — `project_filter` field

**G-042.** The `MemoryQuery` struct gains a `project_filter` field to scope searches to a specific project, global memories only, or everything:

```rust
pub struct MemoryQuery {
    pub text: String,
    pub kinds: Vec<MemoryKind>,
    pub project_filter: Option<ProjectFilter>,  // NEW
    pub limit: usize,
    pub min_score: f32,
}

pub enum ProjectFilter {
    /// Only memories belonging to this project.
    Project(ProjectId),
    /// Only global (cross-project) memories.
    Global,
    /// No filter — all memories regardless of project scope (prior default).
    Any,
}
```

**Backward compatibility:** the field is `Option<ProjectFilter>`; existing callers that do not set it receive `None`, which the KB client maps to `ProjectFilter::Any` — identical to the prior behavior.

**Default for new sessions:** `MemoryPromptContext::new(...)` sets `project_filter = Some(ProjectFilter::Project(current_project_id))` so that session-start searches are scoped to the active project by default. Callers that want global recall must opt in explicitly.

**KB transport:** the `project_filter` is serialised as a `filter` query-param on `GET /memory/search`:
- `filter=project:<id>` → `ProjectFilter::Project`
- `filter=global` → `ProjectFilter::Global`
- absent → `ProjectFilter::Any`

---

## Bulk KB upload

**G-062.** The KB client must support batch trace upload with a sequential idempotent fallback.

### Primary path — batch endpoint

```rust
// lamark-kb-client/src/batch.rs
pub async fn upload_bundles(
    client: &KbClient,
    agent_id: &str,
    bundles: &[BundlePath],
) -> BatchUploadResult;
```

`POST /agents/{id}/traces/batch` accepts a JSON array of `TraceUploadRequest` objects; KB creates all in one transaction. The response shape is:

```json
{ "accepted": ["<trace_id>", ...], "rejected": [{ "index": 2, "reason": "..." }, ...] }
```

### Fallback — sequential idempotent calls

If the batch endpoint returns `404` or `405` (KB version predates batch support), the client falls back to sequential `POST /agents/{id}/traces` calls, one bundle per call, using the existing `KbClient::post_trace` method.

### Idempotency — `kb_upload_state.json`

Each bundle directory carries a `kb_upload_state.json` sidecar. States:

```
pending   → not yet attempted, or last attempt was transient failure
uploaded  → successfully accepted by KB; skip on retry
failed    → permanent failure (e.g. schema rejection); skip on retry after N attempts
```

State is written **atomically**: write to `kb_upload_state.json.tmp`, then `rename` over the target. This prevents corrupt state on crash mid-write.

On any retry run, bundles in `uploaded` state are skipped. Bundles in `failed` state are skipped after exceeding `batch.max_attempts` (default 3).

### `BatchUploadResult`

```rust
pub struct BatchUploadResult {
    pub accepted: Vec<TraceId>,
    pub rejected: Vec<BatchRejection>,
    pub skipped:  usize,           // already-uploaded bundles
}

pub struct BatchRejection {
    pub bundle_path: PathBuf,
    pub reason:      String,
}
```

### Usage

`upload_bundles` is called by:
- The batch runner CLI (`lamark upload-traces`).
- The nightly trainer script, via the KB HTTP API, which relies on the same idempotency file to avoid re-uploading across runs.

---

## "Reinforce" signal (Hermes ideology, hardened)

The user mentioned "reinforce" — this is the bridge between runtime and training:

```rust
pub struct ReinforceSignal {
    pub kind: ReinforceKind,             // PositiveOutcome | NegativeOutcome | UserCorrected | UserPraised
    pub session_id: SessionId,
    pub turn_id:    TurnId,
    pub reference:  ReinforceTarget,     // MemoryId | SkillId | StrategyId | PromptSectionId
    pub weight: f32,                     // 0..1; how strong the signal
    pub rationale: Option<String>,
}
```

When a session ends, the trace reducer infers an outcome (`success | tool_failed | user_aborted | iteration_limit`). For sessions that succeed:

- Memories that were recalled in the prompt → `Reinforce::PositiveOutcome`.
- Skills invoked successfully → `Reinforce::PositiveOutcome`.
- Strategies used (multi-agent fan-out, /goal loop) → `Reinforce::PositiveOutcome`.

For sessions that fail:

- Memories that were recalled but didn't help → `Reinforce::NegativeOutcome`.
- Skills invoked that errored → `Reinforce::NegativeOutcome`.

The KB stores these signals and exposes them to the training pipeline (plan/10): DPO preference pairs preferentially sample from reinforce-positive vs reinforce-negative.

This implements the "agent that learns from itself" loop *without* changing the runtime each night.

## Prompt-time integration

In `lamark-prompt::compose::assemble`, the volatile (Tier-3) section is built like:

```rust
let memory_ctx = MemoryPromptContext {
    cwd: ctx.cwd.clone(),
    last_user_message: ctx.last_user_message.clone(),
    conversation_summary: ctx.compaction_summary.clone(),
    topic_hints: ctx.tool_names_in_play.clone(),
    limit_chars: cfg.prompt.memory_block_max_chars,
};
let block = memory.build_prompt_block(&memory_ctx).await?;
sections.push(Section::tier3_memory(block));
```

`build_prompt_block` is the only memory call that's on the critical path; everyone else is async / off-thread. Budget: 200ms p99.

## Offline mode

When KB is unreachable AND `memory.required == false`:

- The agent logs `KbCircuitOpened` once.
- Memory search falls back to local SQLite (everything that was written via the spool is searchable).
- Memory writes still queue to the spool; uploaded when KB returns.
- Trace upload also queues.

When `memory.required == true`, the agent refuses to start (exit 66). Useful for production deployments where uncoordinated traces are worse than no traces.

## Tests

- **`tests/memory/kb_roundtrip.rs`** — write a fact → KB indexed → searching surfaces it within 500ms.
- **`tests/memory/spool_recovery.rs`** — kill KB → 100 writes spool → restart KB → spool drains within 30s.
- **`tests/memory/composite.rs`** — KB + Honcho both enabled; merged search returns both provenances with right scores.
- **`tests/memory/circuit_breaker.rs`** — 10 consecutive KB failures → breaker opens → SQLite path used → KB returns → breaker closes.
- **`tests/memory/reinforce.rs`** — synthesize a session with one recalled fact + success outcome → ReinforceSignal POSTed.

## Cutover gate (P4 done)

- ✅ `lamark memory search "hello"` against a running KB returns results.
- ✅ Trace upload of one rollout completes in <2s p50 against local KB.
- ✅ Offline mode survives a 5-minute KB outage; no agent errors, no lost writes.
- ✅ Composite memory provider returns merged results from KB + Honcho in one search.
- ✅ Reinforce signals from a synthetic positive-outcome session land in KB and are retrievable by the training pipeline.
