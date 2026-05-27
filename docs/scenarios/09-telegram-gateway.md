# 09 — Telegram gateway — phone-driven session

> **Phase:** P6.
> **One-liner:** User messages the Lamark Telegram bot from their phone:
> "what's the status of the prod deploy?" The gateway routes to a
> per-conversation `Session`, runs the agent, streams a response back —
> with full trace + memory continuity across messages and across
> reboots.

---

## North-star contribution

- **Domain quality.** Phone-driven, cross-device. Quality lever: voice-
  memo path (00c §5–§10) so the user can record a question in transit.
  Two-guard messaging model (adapter queue + runner control-command
  interception, 00c §6) prevents the agent from accidentally messaging
  the wrong chat.
- **Agent-side self-improvement.** Same memory + skills + Curator as
  scenario 01, *but conversation context is platform-aware* — user
  profile (Hermes-style `USER.md`) gets enriched with Telegram-side
  facts (timezone, preferred response length, language). KB stores the
  cross-platform identity.
- **Model-side self-improvement.** Gateway traces are SFT-quality and
  domain-tagged (`source: gateway:telegram`). Trainer can build adapters
  that are *messaging-style aware* (shorter, less code-heavy).

### Signals produced / consumed

- **Produces:** `GatewayMessageIn/Out` trace events; per-platform
  `UserProfile` memory entries; PII-safety tags on messages.
- **Consumes:** Telegram bot token; platform-specific session-key
  (chat_id × user_id); user profile from KB.

---

## Idea

User sends `"deploy status"`. Gateway maps `(telegram, chat_id, user_id)` → session. Spawns or resumes. Agent recalls the *Telegram-side* profile (terse, no emoji). Runs `kubectl rollout status`-equivalent tool. Streams summary back as a single Telegram message (split if > `max_message_length`).

## Actors

| Actor | Role |
|---|---|
| **User** | Sends text / voice messages from Telegram on a phone. |
| **Telegram adapter** | `crates/lamark-gateway/adapters/telegram.rs` — long-poll or webhook; normalizes to `IncomingMessage` (`plan/09 §"Adapter trait"`). |
| **Adapter facade** | Normalizes per-platform shapes to `IncomingMessage / OutgoingMessage`. |
| **Session router** | `conversation_key → Session::spawn_or_resume`. Persistence via trace bundles + KB. |
| **Two-guard interceptor** | (00c §6) Inbound: queue; outbound: control-command intercept to prevent leaks. |
| **STT engine** | For voice memos (00c §5–§10): audio file → cache → STT → text → normal pipeline. |

## Trigger

`lamark gateway run --adapters telegram` is already running. User sends a message. Webhook fires; adapter receives.

## Pipeline

1. **Receive.** Telegram webhook → adapter → `IncomingMessage { platform: Telegram, conversation_key, user_id, text|audio_ref, msg_id }`.
2. **Two-guard inbound queue.** Per-conversation FIFO queue ensures no out-of-order dispatch.
3. **Voice-memo path (if audio).** Download to cache → STT → text replaces the audio body; original audio_ref captured in trace.
4. **Session router.** `conversation_key = hash(platform, chat_id, user_id)`. Lookup in active sessions; if not present, query KB for last `rollout_id` of that key; resume from reduced state if within `gateway.session_idle_ttl` (config); else spawn fresh.
5. **Project detection.** Telegram conversations don't have a cwd. Use `gateway.<adapter>.<conversation_key>.project` config OR fall back to a `gateway:<adapter>` namespace per scenario 08.
6. **Agent runs the turn.** Same SQ/EQ as scenario 01. Memory provider returns the *Telegram-flavored* profile + project facts. Prompt composer keeps Tier-1 cached across turns.
7. **Outbound.** `AgentMessage` events → adapter `send()`. Adapter respects `max_message_length` (Telegram: 4096); paginates if needed. `supports_draft_streaming = true` for Telegram (00c §6) — adapter can edit a single message in place as the response grows.
8. **Two-guard outbound.** Outbound passes through the control-command interceptor: if a message body looks like a Telegram command (`/foo`), it's quoted to prevent platform-injection.
9. **Trace.** `GatewayMessageIn` and `GatewayMessageOut` events recorded with `payload_ref` to the original body. PII-safety tag from `PlatformEntry.pii_safe` flag.
10. **Profile maintenance.** After N messages, a Reflexion-style step updates the cross-platform `UserProfile` memory entry: language, preferred terseness, recurring topics.

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 1–2 (adapter + queue) | `lamark-gateway::adapters::telegram` | 09 §"Part A" |
| 3 (voice) | `lamark-gateway::voice` + STT provider | 00c §5–§10 |
| 4 (router) | `lamark-gateway::router` | 09 §"Architecture" |
| 5 (project resolution) | scenario 08 + 09 | 03 + 08 + 09 |
| 6 (agent loop) | `lamark-core` (unchanged) | 05 |
| 7 (outbound + streaming edit) | `lamark-gateway::adapter` | 09 + 00c §6 |
| 8 (two-guard) | `lamark-gateway::guard` | 00c §6 |
| 9 (trace) | `lamark-trace` | 06 §"Event variants" GatewayMessageIn/Out |
| 10 (profile) | `lamark-memory::UserProfile` kind | 07a |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Webhook delivery failure** | Telegram retries; adapter dedups by `msg_id`. |
| **Adapter queue overflow** | Per-conversation cap; oldest dropped with notification to user ("falling behind, simplifying…"). |
| **STT fails on voice** | User notified, asked to retype or rerecord. Trace records the failure. |
| **Session resume — KB unavailable** | Spawn fresh; trace records degraded resume. |
| **Permission prompt arrives but user not in chat** | Adapter sends prompt as a regular message; if `permission_prompt_timeout` exceeded → Deny per G-013. |
| **Outbound message too long** | Paginate; mark sequence with `(1/N)` markers. |
| **Concurrent messages from same chat** | Two-guard queue serializes; later messages wait. |
| **Token revoked mid-run** | Adapter detects auth failure; `GatewayDisconnected` event; reconnect with backoff; user-visible status banner via `lamark gateway list`. |

## Acceptance criteria

- [ ] `lamark gateway run --adapters telegram` boots and registers with Telegram (webhook or polling).
- [ ] Inbound text message routes to a session and produces a response.
- [ ] Voice memo path works end-to-end (audio → STT → response).
- [ ] Session resumes across gateway restart within `session_idle_ttl`.
- [ ] Outbound > 4096 chars is paginated; sequence markers visible.
- [ ] Permission prompts work via the gateway (modal becomes an inline message).
- [ ] `GatewayMessageIn/Out` events are in trace.jsonl with `payload_ref`.
- [ ] Cross-platform UserProfile updates after N messages.

## Self-improvement assertions

1. **Per-platform profile recall.** Same user on Telegram and CLI sees different prompt formatting (terse vs verbose) based on the `source` tag of their `UserProfile` memory.
2. **Adapter-tagged training data.** Trainer can filter trace samples by `source=gateway:telegram` to build a messaging-style adapter.
3. **Cross-session continuity.** Sending a message tonight that says "remember I asked about X earlier today" recalls today's earlier session's facts via memory.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| `lamark gateway run` CLI | plan/02 + plan/09 | _audit_ |
| Adapter trait + facade | plan/09 §"Adapter trait" | _audit_ |
| Session router (conversation_key) | plan/09 §"Architecture" | _audit_ |
| Telegram adapter specifics | plan/09 §"Part A" | _audit_ |
| Two-guard model | 00c §6 | _audit_ |
| PlatformEntry fields (max_message_length, pii_safe, supports_draft_streaming) | 00c §5 | _audit_ |
| Voice-memo path | 00c §5–§10 | _audit_ |
| Session resume + idle TTL | plan/09 + plan/06 KB reduce | _audit_ |
| Per-platform UserProfile memory kind | plan/07a `UserProfile` | _audit_ |
| Permission prompts via gateway | plan/05 + plan/09 — likely partial | _audit_ |
| Pagination + sequence markers for long messages | 00c §6 | _audit_ |
| Trace events for gateway | plan/06 GatewayMessageIn/Out | _audit_ |
| Conversation key construction | plan/09 | _audit_ |
| Webhook vs long-poll choice | plan/09 — likely partial | _audit_ |
| Gateway management CLI (list/stop/replace) | plan/02 | _audit_ |
| Project scoping for messaging (no cwd) | (likely **gap G-044**) | _audit_ |
