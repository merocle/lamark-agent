# 07 — Layer 6a: Hierarchical prompt composer + cache

> The agent's system prompt is not a string — it is a tree of sections with
> tiered cacheability. This crate owns assembly, ordering, and cache hints.

**Crates:** `crates/lamark-prompt/`, `crates/lamark-cache/`.
**Depends on:** `lamark-core` (types), `lamark-skills` (for the skills index), `lamark-memory` (for the volatile snapshot).
**References:**
- `~/.cache/lemark/vendor/hermes-agent/agent/prompt_builder.py` — section catalog.
- `~/.cache/lemark/vendor/hermes-agent/agent/system_prompt.py:90+` — three-tier stable/context/volatile split.
- `~/.cache/lemark/vendor/hermes-agent/agent/prompt_caching.py:49` — Anthropic `cache_control` `system_and_3` layout.
- `~/.cache/lemark/vendor/claude-code/utils/systemPrompt.ts:41-123` — override/coordinator/agent/custom/default/append layering.
- `~/.cache/lemark/vendor/claude-code/constants/systemPromptSections.ts` — memoized section model.

---

## Why hierarchical (not flat)

A single concatenated string can't satisfy three competing needs:

1. **Cache economics.** A 30KB prompt that's stable across 50 turns deserves a single cache write; the last 3 turns deserve their own. A flat string can't express that.
2. **Composition source.** Some sections come from SOUL.md, some from a coordinator role, some from a session-specific agent, some from project AGENTS.md, some from skills, some from memory. They have different ownership and different refresh rules.
3. **Auditing.** When a prompt regresses, you need to know which section caused the drift. A flat string is a needle in a haystack.

So a prompt is a **DAG of sections**, flattened at emit time, with cache hints attached.

## The section model

```rust
pub struct Section {
    pub id:        SectionId,                 // stable, e.g., "identity.soul"
    pub layer:     PromptLayer,               // Tier1Stable | Tier2Context | Tier3Volatile
    pub source:    SectionSource,             // Soul | Default | Project | User | Skill(name) | Memory | Custom
    pub content:   SectionContent,            // Text(String) | Template(...) | Lazy(fn)
    pub cacheable: bool,                      // hint to cache strategy
    pub depends_on: Vec<SectionId>,           // forces ordering + cache invalidation propagation
    pub fingerprint: u64,                     // xxhash of content; used for cache keys
    pub priority:  i32,                       // ordering within a layer
    pub metadata:  HashMap<String, Value>,
}

pub enum PromptLayer {
    Tier1Stable,     // identity, tool schemas, model-operational guidance — cached for the session
    Tier2Context,    // project AGENTS.md, role spec, --system-prompt flag — fixed per session
    Tier3Volatile,   // memory snapshot, USER.md, datetime, model line — rebuilt on compaction
}
```

A section is **content-addressed** by its fingerprint. The composer dedupes sections with the same fingerprint at emit time (e.g., if `AGENTS.md` content matches one already loaded from a higher layer).

## Composer

```rust
pub struct PromptComposer {
    layers: PromptLayers,                     // owns Tier1/2/3 collections
    skills: Arc<SkillIndex>,
    memory: Arc<dyn MemoryProvider>,
    config: Arc<Config>,
}

impl PromptComposer {
    pub async fn assemble(&self, ctx: &PromptContext) -> AssembledPrompt;
    pub fn invalidate(&self, layer: PromptLayer, predicate: impl Fn(&Section) -> bool);
}

pub struct AssembledPrompt {
    pub messages:  Vec<Message>,              // typically one system message + the conversation tail
    pub sections:  Vec<Section>,              // ordered list, for telemetry/audit
    pub cache_breakpoints: Vec<CacheBreakpoint>,
    pub stats: PromptStats,                   // total tokens, per-tier tokens
}
```

`assemble` is pure on its inputs; the same ctx → same output. Side effects (memory read, KB read) happen *before* assemble in the caller, who passes the read-out into `ctx`.

## Layered composition (claude-code-inspired)

```
Tier-1 (Stable, session-cached) — concatenated in this order:
  1. Identity            → SOUL.md or DEFAULT_AGENT_IDENTITY
  2. Operational         → per-model instructions (Qwen, Nemotron, Gemma4, Claude)
  3. Tool guidance       → MEMORY_GUIDANCE, SKILLS_GUIDANCE, KANBAN_GUIDANCE, PERMISSIONS_GUIDANCE
  4. Tool schemas        → JSON-schemas for currently-enabled tools (deferred-loadable)
  5. Skill index         → bundled + user skills (descriptions only; full bodies on demand)
  6. Coordinator         → multi-agent role (if /team mode)
  7. Agent role          → from agent-spec (subagent role override)

Tier-2 (Context, session-fixed):
  8. AGENTS.md           → walked up from cwd; first wins
  9. .lamark.md          → workspace-specific
 10. --system-prompt     → CLI flag, raw string
 11. Coordinator's "task brief"   → for subagents

Tier-3 (Volatile, rebuilt on compaction):
 12. Memory snapshot     → from memory provider
 13. USER.md             → user profile from KB
 14. External memory block → Honcho dialectic / Mem0
 15. Session metadata    → timestamp, session id, model, provider, rollout id

Append (per-message, not cached at Tier1):
 16. Last-turn nudges    → tool-use-enforcement reminders if model misbehaved
```

This mirrors Claude Code's `override > coordinator > agent > custom > default > append` priority while being more explicit about cache tiers.

## Override semantics

```rust
pub enum SectionOverride {
    /// Replace a section in-place by id.
    ReplaceById   { id: SectionId, with: Section },
    /// Skip a section.
    Skip          { id: SectionId },
    /// Inject a new section at a relative position.
    Insert        { after: SectionId, section: Section },
    /// Replace ALL of Tier-1 with a custom assembly.
    ReplaceTier1  { sections: Vec<Section> },
}
```

Use cases:
- `lamark chat --system-prompt "<text>"` → `Insert` after `Tier1.OperationalGuidance`.
- Subagent role spec → `ReplaceById("identity.soul")` + add `Insert("role.subagent.brief")`.
- `lamark exec`-driven batch run with deterministic prompt → `ReplaceTier1`.

## Lazy & deferred sections

Some sections are expensive (skill bodies, tool schemas with examples). Defer:

```rust
pub enum SectionContent {
    Text(String),
    Template { template: String, vars: HashMap<String, Value> },
    Lazy(Arc<dyn Fn(&PromptContext) -> BoxFuture<'static, String> + Send + Sync>),
}
```

Lazy sections are resolved in parallel by `assemble()` (`futures::future::join_all` over the lazy ones). This keeps prompt build under a few ms even with 50+ skills.

## Cache strategies (`lamark-cache`)

```rust
pub trait CachePolicy: Send + Sync {
    /// Look at the assembled prompt and decide where to place cache breakpoints.
    fn apply(&self, prompt: &mut AssembledPrompt, provider: &ProviderCapabilities);
}

pub enum CacheStrategyKind { Auto, CacheControl, PrefixHash, Off }
```

### `cache_control` strategy (Anthropic-compat)

Anthropic Messages API supports up to 4 cache breakpoints with TTL. We use the hermes-agent `system_and_3` layout:

- Breakpoint 1: end of Tier-1 (`identity + operational + tool guidance + tool schemas + skills`).
- Breakpoints 2/3/4: on the last three non-system messages.
- TTL: from config (`model.cache.ttl`, default 1h).

```rust
fn apply(&self, prompt: &mut AssembledPrompt, _caps: &ProviderCapabilities) {
    let tier1_end = prompt.last_index_of_layer(PromptLayer::Tier1Stable);
    prompt.set_cache_control(tier1_end, CacheControl::Ephemeral { ttl });
    for idx in prompt.last_n_assistant_user_indices(3) {
        prompt.set_cache_control(idx, CacheControl::Ephemeral { ttl });
    }
}
```

The provider crate (plan/04) sends `cache_control: {"type":"ephemeral", "ttl":"1h"}` on the chosen content blocks.

### `prefix_hash` strategy (vLLM, SGLang, llama.cpp APC)

Local backends with **automatic prefix caching** (vLLM ≥ 0.10, SGLang RadixAttention, llama.cpp slot-based) reuse KV cache across requests when the prompt has a common prefix. We don't need to send anything special — *but* we benefit from:

- **Stable section order** within Tier-1. We sort by `priority` deterministically and never reorder mid-session.
- **Stable rendering** of tool schemas (sort keys, no random IDs, no inline timestamps).
- **`X-Cache-Hint`** header on vLLM v0.10+ — we send a hash of Tier-1 so vLLM can scope its prefix-tree.

```rust
fn apply(&self, prompt: &mut AssembledPrompt, caps: &ProviderCapabilities) {
    if caps.prefix_hash {
        let tier1_hash = prompt.fingerprint_through(PromptLayer::Tier1Stable);
        prompt.extra_headers.insert("X-Cache-Hint".into(), format!("lamark-tier1-{:016x}", tier1_hash));
    }
    // no message-level breakpoints needed; backend infers from prefix
}
```

### `auto` strategy

The router resolves `Auto` based on provider capabilities:
- `cache_control` supported → use it.
- Else `prefix_hash` supported and Tier-1 ≥ `min_segment_tokens` → use it.
- Else `Off`.

### Cache report aggregation

When the provider returns `CacheReport { hit_tokens, miss_tokens, write_tokens }`, the composer records it under the session. The cost tracker (in `crates/lamark/src/observability.rs`) reports actual savings per turn.

## Determinism guards

CI tests assert:

1. **No floating-point in fingerprints.** Templates with floats are normalized to fixed-point.
2. **No timestamps in Tier-1.** Time is in Tier-3 (volatile) only.
3. **Sort-keys for HashMaps.** Tool schemas (`HashMap<String,Schema>`) serialize with BTreeMap for stable order.
4. **`assemble()` is deterministic.** Same `PromptContext` → same `AssembledPrompt` (verified by quickcheck-style test with 1000 random ctxs).

## Compaction interaction

When `ContextCompressor` triggers (plan/05 §"Compaction"):
- Tier-1 stays.
- Tier-2 stays.
- Tier-3 is **rebuilt from scratch** (memory snapshot is re-read; user profile re-fetched; KB context re-summarized).
- Old assistant/user pairs are replaced by a summary (recorded as a new section: `SectionSource::CompactionSummary(turn_range)`).

Cache breakpoints move accordingly; the next provider call usually sees a Tier-1 cache HIT and a Tier-3 cache MISS (expected).

## Slash command + skill influence

When the user runs a slash command:
- `Local` commands run a local function; no prompt change.
- `PromptInject` commands append a section to Tier-2 for this session ("the user invoked `/loop 5m /foo`, treat the rest of the conversation accordingly").
- `SkillBacked` commands have a skill body in Tier-1 (already loaded) and a one-shot instruction in Tier-2.

When the agent invokes a skill via `skill_invoke`:
- Skill body content is **already in Tier-1's skill index** (description) and is fetched-on-demand into the conversation as a user message (matches Claude Code's pattern — preserves prompt cache).

## Config

```yaml
prompt:
  identity_path: "~/.lamark/SOUL.md"        # optional override; falls back to default
  workspace_files:
    - "AGENTS.md"
    - ".lamark.md"
  skill_index_in_tier1: true                # default; toggles whether skills sit in Tier-1
  defer_tool_schemas: false                 # if true, tool schemas join Tier-2; smaller cache slot
  max_tier1_tokens: 8192
  max_tier3_tokens: 4096
  determinism_guards: strict                # strict | warn | off
```

## Tests

- **`tests/prompt/assemble_snapshot.rs`** — golden-file every layer for a canonical config; insta tracks drift.
- **`tests/prompt/cache_breakpoints_anthropic.rs`** — assert 4 breakpoints land in the right positions.
- **`tests/prompt/prefix_hint_vllm.rs`** — assert `X-Cache-Hint` present for vllm provider.
- **`tests/prompt/determinism.rs`** — random ctx 1000× → same fingerprint.
- **`tests/prompt/compaction.rs`** — compaction triggers; Tier-1 fingerprint unchanged; Tier-3 fingerprint changed.

## Cutover gate (P4 partial — prompt side)

- ✅ `AssembledPrompt::stats` shows Tier-1 ≥ X tokens, Tier-3 ≤ Y tokens, no overlap.
- ✅ Turn 2 in a session triggers a `CacheReport { hit_tokens > 0 }` on Anthropic.
- ✅ Turn 2 against vLLM shows a measurable latency improvement (KV cache reuse).
- ✅ Snapshot tests for the composer are green; CI fails on any unexpected drift.
