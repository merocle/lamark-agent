# 04 — Layer 3: Providers

> Where the LLM lives. One trait, many backends. Owns retries, streaming,
> cache decisions, and tool-call parsing. Upper layers see only `dyn
> ModelProvider`.

**Crate:** `crates/lamark-providers/`
**Depends on:** `lamark-core` (types), `lamark-cache` (cache decisions), `reqwest`, `eventsource-stream`, `serde_json`.
**Replaces:** hermes-agent's `agent/anthropic_adapter.py`, `bedrock_adapter.py`, `gemini_*adapter.py`, `codex_responses_adapter.py`, `azure_identity_adapter.py`.
**Reference:** `~/.cache/lemark/vendor/codex/codex-rs/model-provider/src/provider.rs:83` — port the `ModelProvider` trait verbatim in spirit, simplified for our needs.

## The trait

```rust
#[async_trait]
pub trait ModelProvider: Send + Sync {
    /// Provider info (name, capabilities, model catalog).
    fn info(&self) -> &ProviderInfo;

    /// Run one completion. Returns a stream of events.
    /// The stream MUST end with either FinishReason or Error.
    async fn complete(
        &self,
        req: CompleteRequest,
        cancel: CancellationToken,
    ) -> Result<BoxStream<'static, CompleteEvent>, ProviderError>;

    /// List models available right now (some providers cache; this can be the cache hit).
    async fn list_models(&self) -> Result<Vec<ModelInfo>, ProviderError>;

    /// Reach-check for `lamark doctor`.
    async fn health_check(&self) -> Result<HealthReport, ProviderError>;
}
```

## Request shape

```rust
pub struct CompleteRequest {
    pub model:    String,
    pub messages: Vec<Message>,            // role + content blocks
    pub tools:    Vec<ToolSchema>,
    pub tool_choice: ToolChoice,           // Auto | Required | None | Specific(name)
    pub max_tokens:  Option<u32>,
    pub temperature: Option<f32>,
    pub top_p:       Option<f32>,
    pub stop:        Vec<String>,
    pub seed:        Option<u64>,
    pub reasoning:   ReasoningRequest,
    pub cache:       CacheStrategy,
    pub metadata:    Metadata,             // session_id, turn_id, rollout_id — set by core
}
```

`Message::content` is **content blocks** (text, image, tool_use, tool_result, thinking), not a string. This is what Anthropic expects natively and what we synthesize for OpenAI-format providers.

## Event stream

```rust
pub enum CompleteEvent {
    /// Provider acknowledged the request (received first byte).
    Started   { upstream_id: Option<String> },

    /// Streaming text content.
    DeltaText { content: String },

    /// Streaming reasoning content (Anthropic thinking, Qwen <think>, Nemotron nano_v3).
    DeltaReasoning { content: String },

    /// Tool call accumulating. id is local to this call.
    DeltaToolCall { id: String, name: Option<String>, args_chunk: String },

    /// Tool call done; complete and parsable.
    ToolCallReady { id: String, name: String, args: serde_json::Value },

    /// Usage stats (sent at end by most providers; we accumulate cache_read/write).
    Usage(UsageStats),

    /// The model finished generating. Reason is one of stop/length/tool_use/etc.
    Finish(FinishReason),

    /// Cache hit/miss report (if provider sends it; some do, some inferred).
    CacheReport(CacheReport),

    /// Stream-terminating error.
    Error(ProviderError),
}
```

The hooks bus emits `InferenceStarted` on `Started` and `InferenceCompleted` on `Finish | Error`. See plan 06.

## Implementations

```
crates/lamark-providers/
└── src/
    ├── lib.rs
    ├── trait.rs                  # ModelProvider, types, errors
    ├── router.rs                 # multi-provider routing + fallback
    ├── cache.rs                  # CacheStrategy resolution
    ├── retry.rs                  # exponential backoff + jitter
    ├── parsers/                  # tool-call and reasoning parsers
    │   ├── mod.rs
    │   ├── openai_json.rs        # standard OpenAI JSON tool-call
    │   ├── hermes_xml.rs         # <tool_call>...</tool_call>
    │   ├── mistral.rs            # mistral function-calling
    │   ├── qwen3_moe.rs          # <think>...</think> reasoning
    │   ├── nano_v3.rs            # Nemotron-3 nano reasoning splitter
    │   ├── gemma4.rs             # Gemma4 reasoning channel
    │   └── claude_thinking.rs    # Anthropic thinking_blocks
    └── impls/
        ├── openai_compat.rs      # vLLM, Ollama, llama.cpp, SGLang, LM Studio, MLX, OpenAI
        ├── anthropic.rs          # Anthropic Messages API, cache_control, thinking
        ├── bedrock.rs            # AWS Bedrock Converse API
        ├── gemini_native.rs      # Google Gemini direct
        └── codex_responses.rs    # OpenAI Responses API (for codex-flavored servers)
```

## OpenAI-compat impl (the workhorse)

Covers vLLM, Ollama, llama.cpp, SGLang, LM Studio, MLX, plus OpenAI itself.

```rust
pub struct OpenAICompatProvider {
    info:    ProviderInfo,
    client:  reqwest::Client,
    base:    Url,
    key:     Option<String>,
    parsers: ParserSet,
    cache:   Arc<dyn CachePolicy>,
    retry:   RetryConfig,
}

#[async_trait]
impl ModelProvider for OpenAICompatProvider {
    async fn complete(
        &self,
        req: CompleteRequest,
        cancel: CancellationToken,
    ) -> Result<BoxStream<'static, CompleteEvent>, ProviderError> {
        let body = self.build_chat_body(&req)?;          // map content-blocks → OAI shape
        let mut res = self.client
            .post(self.base.join("chat/completions")?)
            .bearer_auth(self.key.as_deref().unwrap_or("EMPTY"))
            .json(&body)
            .send()
            .await
            .map_err(ProviderError::from)?
            .error_for_status()?;

        let sse = eventsource_stream::EventSource::new(res);
        let parser = self.parsers.for_model(&req.model);

        let stream = sse.flat_map(move |evt| match evt {
            Ok(e) => parser.parse_sse_chunk(&e.data),     // → Vec<CompleteEvent>
            Err(e) => vec![CompleteEvent::Error(e.into())],
        });

        Ok(stream.boxed())
    }
    /* ... */
}
```

Key details:
- **Reasoning extraction** happens in the parser: `<think>...</think>` is stripped from `DeltaText` and re-emitted as `DeltaReasoning`. The reducer (plan 06) then drops these blocks from prior turns to match Nemotron's multi-turn splitting rule.
- **Thinking budget**: `CompleteRequest.reasoning` carries a `ReasoningRequest::Budget { max_tokens: u32 }` variant. The provider maps this to `enable_thinking=true` + `thinking_budget_tokens` for Qwen3/vLLM, or `thinking: {type: "enabled", budget_tokens: N}` for Anthropic. A `ReasoningRequest::None` disables think-mode entirely. This enables per-task routing between System 1 (fast) and System 2 (deliberative) as described in arXiv 2502.17419 and Qwen3 (arXiv 2505.09388).
- **Tool-call streaming**: OpenAI streams tool-call arguments token-by-token. We accumulate per tool_call.id and emit one `ToolCallReady` when finish_reason fires.
- **Constrained decoding (XGrammar)**: For `LocalOpenAICompat` targets that support it (vLLM v0.10+, SGLang), pass `guided_json: <tool_schema>` in the chat completion body when `tool_choice` is `Specific(name)` or `Required`. This routes through XGrammar (arXiv 2411.15100) in the server and guarantees syntactically valid JSON arguments at the token level — eliminates JSON parse errors with near-zero latency overhead. Opt-in via `provider.constrained_decoding = true` in config.
- **Cache hint**: OpenAI-compat providers don't honor `cache_control`. We compute prefix-hash IDs of the stable section and pass them in `X-Cache-Hint` headers (vLLM v0.10+ honors this; others ignore). See plan 07.

## Anthropic impl

The tricky one. Worth the work because Claude Opus is the strongest tool-calling backend.

```rust
pub struct AnthropicProvider {
    info:   ProviderInfo,
    client: reqwest::Client,
    base:   Url,
    key:    String,
    /* ... */
}
```

Specifics:
- Uses `/v1/messages` (not `/v1/chat/completions`).
- Tool calls are returned as `content` blocks of type `tool_use`; we map to `DeltaToolCall`.
- `thinking` blocks are extended thinking — emit as `DeltaReasoning`.
- `cache_control` breakpoints are applied to **system** message and the **last 3 non-system** messages, all at 1h TTL. See plan 07 for the strategy; the provider trusts the strategy decision.
- `prompt_caching` beta header set.

## Bedrock / Gemini / Codex Responses

Same trait, different wire format. Bedrock uses Converse API; Gemini uses the v1 native API; Codex Responses uses the OpenAI Responses shape (event-typed streaming closer to ours).

Each gets its own integration test (recorded with `wiremock` + golden tape).

## Router & fallback

`ProviderRouter` is one indirection layer up: it owns multiple `Arc<dyn ModelProvider>` and routes by model alias. Fallback chain is configured per `model.fallback_chain`. On `ProviderError::Transient`, the router retries; on `ProviderError::Permanent`, it moves to the next provider; on exhaustion it fails the turn.

```rust
pub struct ProviderRouter {
    routes:    HashMap<String, Vec<Arc<dyn ModelProvider>>>,
    config:    RoutingConfig,
}

impl ProviderRouter {
    pub async fn complete(&self, req: CompleteRequest, cancel: CancellationToken)
        -> Result<BoxStream<'static, CompleteEvent>, ProviderError>
    { ... }
}
```

## Retry semantics

Per provider, configurable:

```rust
pub struct RetryConfig {
    pub max_attempts: u8,
    pub base_delay_ms: u64,
    pub max_delay_ms: u64,
    pub jitter: bool,
    pub retry_on: HashSet<ErrorClass>,   // Transient, RateLimit, 5xx
}
```

Default: 3 attempts, 200ms base, 5s max, jitter on, `{Transient, RateLimit, GatewayTimeout, ServerError}`. **No retries on `Permanent` or `BadRequest`** — those mean the request itself is wrong.

## Tool-call parsers

Provider sends raw chunks; we own the parsing. Parsers live in `parsers/` and implement:

```rust
pub trait ToolCallParser: Send + Sync {
    fn parse_sse_chunk(&self, chunk: &str) -> Vec<CompleteEvent>;
}
```

For OpenAI JSON: split by SSE `data:` lines; parse `choices[0].delta.tool_calls[]`; track per-id buffer for `function.arguments`.

For Hermes XML: scan for `<tool_call>...</tool_call>` boundaries in the text stream; emit `DeltaText` for surrounding text and `ToolCallReady` for each closed XML block.

The parser is selected by `model.tool_call.parser`, with sensible defaults per model family.

## Cache strategy negotiation

```rust
pub enum CacheStrategy {
    Auto,
    CacheControl { ttl: Duration },
    PrefixHash   { min_segment_tokens: u32 },
    Off,
}
```

The provider implements support flags:

```rust
pub struct ProviderCapabilities {
    pub cache_control: bool,
    pub prefix_hash:   bool,
    pub reasoning:     bool,
    pub parallel_tool_calls: bool,
    pub max_context:   u32,
}
```

Strategy `Auto` resolves to:
- `CacheControl` if `cache_control && messages have ≥1 cacheable breakpoint`.
- `PrefixHash` if `prefix_hash && stable section ≥ min_segment_tokens`.
- `Off` otherwise.

This is the only place strategy decisions are made. Upper layers pass `Auto`.

## Errors

```rust
pub enum ProviderError {
    Transient(String),         // network blip, retry
    RateLimit { retry_after: Option<Duration> },
    Cancelled,
    BadRequest(String),
    AuthFailed(String),
    NotFound(String),
    ContextLengthExceeded { used: u32, limit: u32 },
    ServerError(u16, String),
    ProtocolError(String),
    Other(String),
}
```

The crate maps every HTTP / SSE / stream error into one of these variants. Upper layers never see `reqwest::Error`.

## Observability

Every `complete()` call:
- `tracing::info_span!("inference", model = ..., provider = ..., turn_id = ..., session_id = ...)`.
- `prometheus::histogram!("lamark_inference_seconds")` per provider+model.
- `prometheus::counter!("lamark_inference_tokens_total", "kind" => "input/output/cache_read/cache_write")`.
- `prometheus::counter!("lamark_inference_errors_total", "class" => "<kind>")`.

## § Rate limiter (G-061)

The `lamark-providers::rate_limiter` module implements a per-provider token bucket rate limiter shared across all sessions that use the same provider.

### Configuration

```yaml
providers:
  anthropic:
    rate_limit:
      requests_per_minute: 60
      tokens_per_minute: 100000
```

Defaults: no limit (unlimited) when the `rate_limit` block is absent.

### Implementation

```rust
pub struct TokenBucket {
    capacity:    f64,   // = requests_per_minute
    tokens:      f64,   // current available tokens
    refill_rate: f64,   // = capacity / 60.0  (tokens per second)
    last_refill: Instant,
}

impl TokenBucket {
    /// Try to consume one token. Returns the wait duration if the bucket is
    /// empty; returns `None` if a token was consumed immediately.
    pub fn try_consume(&mut self) -> Option<Duration>;
}
```

The bucket is wrapped in `Arc<tokio::sync::Mutex<TokenBucket>>` and stored in the provider struct, initialized once at startup. All sessions sharing the same provider share the same `Arc`.

### HTTP 429 handling

When the provider returns an HTTP 429:

- Read the `Retry-After` header. Accept both the seconds form (`Retry-After: 30`) and the HTTP-date form (`Retry-After: Wed, 21 Oct 2015 07:28:00 GMT`).
- Wait **exactly** that duration before retrying.
- If no `Retry-After` header is present, use exponential backoff: start at 2 s, double on each attempt, cap at 60 s.

This logic lives in `retry.rs` alongside the existing `RetryConfig`, keyed on `ProviderError::RateLimit { retry_after }`.

### Batch runner integration

Before dispatching each prompt, the pool worker calls `try_consume()` on the provider's bucket. If the bucket is empty, the worker `tokio::time::sleep`s until the next token is available rather than spinning. This throttles dispatch naturally without busy-waiting and without needing a separate coordination mechanism.

### Observability

The following are emitted as `tracing` spans so they appear in the existing observability pipeline:

- `rate_limiter.wait_total_ms` — cumulative milliseconds spent waiting for a token across the session.
- `rate_limiter.retry_count` — number of times a 429 triggered a wait-and-retry cycle.

```rust
tracing::info!(
    wait_ms = wait.as_millis(),
    provider = %self.info().name,
    "rate_limiter: sleeping for token"
);
```

## Tests

- **`tests/openai_compat_record.rs`** — record real responses from vLLM into `tests/tapes/`; replay with `wiremock`.
- **`tests/anthropic_cache_control.rs`** — verify the cache_control breakpoints land on the right messages.
- **`tests/router_fallback.rs`** — first provider returns Transient → second succeeds → final stream contains both providers' events with provider tag.
- **`tests/parser_qwen3_think.rs`** — `<think>…</think>` blocks emerge as DeltaReasoning, not DeltaText.
- **`tests/parser_hermes_xml.rs`** — XML tool-call boundaries.
- **`tests/error_mapping.rs`** — 429, 500, 503, broken JSON, partial SSE.

## Cutover gate (P2 done)

- ✅ `OpenAICompatProvider` against a local vLLM produces a streamed reply with at least one tool call.
- ✅ `AnthropicProvider` returns a `CacheReport { hit_tokens: > 0 }` on the second turn of a 2-turn session.
- ✅ Router fallback test passes.
- ✅ All four reasoning parsers (`qwen3_moe`, `nano_v3`, `gemma4`, `claude_thinking`) round-trip a recorded sample.
- ✅ The provider crate has zero dependencies on `lamark-core` impl details — only on its types and traits.
