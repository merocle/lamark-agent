# 00b — Ideology comparison & feature matrix

> The two reference agents in our cloned corpus represent two genuinely
> different visions of what an "AI agent" is. This file makes the choice
> explicit: which DNA we adopt from each, and where Lamark goes its own way.

## The two ideologies

### Hermes-Agent — "the agent that grows with you"

| Trait | Hermes's bet |
|---|---|
| **Primary surface** | Messaging gateways. The agent is reached from Telegram/Slack/Discord/WhatsApp, not the terminal. |
| **Persistence** | Always-on. Sessions outlive the user's interaction. Memory is the point. |
| **Learning** | The agent rewrites itself. Skills authored autonomously; Curator consolidates weekly; trajectories feed Atropos RL → new model checkpoints. |
| **Tool philosophy** | Broad. 70+ tools across 28 toolsets covering files, shell, web, browser, vision, voice, video, MCP, kanban, schedule, finance. |
| **Multi-agent** | Kanban with heartbeat + zombie detection. Parent posts cards; children claim, work, complete. /goal Ralph loop. |
| **Memory** | First-class. MEMORY.md (2,200 char snapshot), USER.md, FTS5 SQLite, plus pluggable Honcho/Mem0/Hindsight. |
| **Security default** | ALLOW-ALL command approval (publicly criticized; see Hermes Issue #7826). |
| **Audience** | Individuals; small ops automation; "the worker you reach from your phone." |
| **Coding work** | A use case, not THE use case. Not optimized to beat Claude Code at coding. |

### Claude Code — "the coding pair in your terminal"

| Trait | Claude Code's bet |
|---|---|
| **Primary surface** | Terminal (Ink/React TUI) + IDE extensions. You sit next to it; you watch each edit. |
| **Persistence** | Per-session. Each invocation is fresh. CLAUDE.md per repo gives durable context, but no cross-session memory store. |
| **Learning** | None inside the runtime. Claude the model improves, the CLI does not. |
| **Tool philosophy** | Focused. ~40 tools concentrated on code ops: Read, Write, Edit, Patch, Grep, Glob, Bash, TaskCreate, Agent, plus MCP. |
| **Multi-agent** | Coordinator + team mode + Agent tool. Less elaborate than Hermes's Kanban; more disciplined per-subagent isolation. |
| **Memory** | Light. CLAUDE.md per repo + session-history file. Plays well with prompt cache. |
| **Security default** | Permission-first. Every shell/write/edit gates on approval; explicit decision DSL. |
| **Audience** | Developers; engineering teams; "the partner in your IDE." |
| **Coding work** | THE point. Patch atomicity, LSP diagnostics on every write, diff-aware Edit, undo trail. |

## What each one teaches Lamark

The user explicitly asked: "compare ideology … take best ideas to make all features." So we take the load-bearing ideas from each, not the shrugs.

### Hermes ideas we adopt

1. **Cross-session persistence is the differentiator.** A coding session that ends with the conversation thrown away is a wasted training sample. Lamark keeps every trace, every memory write, every skill creation event — long-term in knowledge-base.
2. **The agent authors its own skills.** A markdown skill written by the agent after a successful 5-tool-call session is more durable than a one-shot result. Curator consolidates weekly. (Skills + Curator: plan/08.)
3. **Atropos-style training flywheel.** Traces are the dataset. Without that, we're just another agent shell. (Training pipeline: plan/10.)
4. **Multi-platform reach via the gateway.** Personal automation lives in messaging, not terminals. (Gateway: plan/09.)
5. **External-memory pluggability.** Honcho's dialectic user modeling, Mem0's structured fact extraction, Hindsight's episodic memory — they all have niches. We expose the trait, ship multiple impls. (Memory: plan/07a.)
6. **Kanban multi-agent with heartbeat.** When parallel work fans out, you need progress visibility and zombie detection. Not just "spawn and pray." (Coordinator: plan/05a.)
7. **/goal Ralph loop.** The "keep refining until done" primitive that turns long objectives into managed loops. (Coordinator: plan/05a.)

### Claude Code ideas we adopt

1. **Permission-first by default.** Hermes's ALLOW-ALL is the wrong default; the security audit confirmed it. Lamark's policy is `prompt` by default with explicit `Allow|Prompt|Forbidden` rules. (Policy: plan/05 + plan/06.)
2. **Hooks as a first-class extensibility surface.** The `PreToolUse/PostToolUse/UserPromptSubmit/PermissionRequest/...` taxonomy is the cleanest design in the space. (Hooks: plan/06.)
3. **Layered, cacheable system-prompt composition.** Override > coordinator > agent > custom > default > append. Memoized sections; explicit cache breakpoints. (Prompt: plan/07.)
4. **Tight file ops with diff awareness.** Edit's old_string/new_string contract; Patch atomicity; LSP-driven post-write diagnostics. Coding rigor that Hermes never aimed at. (Tools: plan/05.)
5. **Slash command UX inside the chat.** 100+ commands feels like a lot, but each removes friction from "I need to do X right now." (Slash commands: plan/02.)
6. **Skill discovery convention.** YAML frontmatter (name, description, whenToUse, aliases) + filesystem layout (`.claude/skills/` project → `~/.claude/skills/` user → bundled). Lamark uses the same convention with `.lamark/`. (Skills: plan/08.)
7. **MCP everywhere — client AND server.** Claude Code is bidirectional MCP. Lamark must be too, or it can't slot into engineers' existing setups (Cursor, Windsurf, Claude Desktop, VS Code). (MCP: plan/09.)
8. **Cost-tracker built in.** Engineers want to know "what did this session cost?" before they start the next one. We track inputs/outputs/cache reads/cache writes per turn. (Observability: plan/02 + plan/11.)
9. **Subprocess hooks for user customization.** `~/.lamark/hooks.toml` with shell-command callbacks. Lets users extend without writing Rust. (Hooks: plan/06.)
10. **Codebase-aware context loading.** `AGENTS.md` walked up from cwd; first wins. (Prompt: plan/07.)

## Where Lamark deliberately differs from both

Some choices fit neither ideology — they are ours.

| Choice | Why |
|---|---|
| **Pure Rust runtime.** | Performance + safety + no Python startup cost in the gateway path. Both originals are interpreted. |
| **knowledge-base as canonical store.** | Hermes uses local SQLite; Claude Code uses session files. We delegate persistence to a separate Kotlin/Spring backend with hybrid RAG + KG + RAPTOR. (Memory + KB: plan/07a.) |
| **Codex-style trace bundles, not flat JSONL.** | The reduced graph (state.json + conversation.jsonl) captures causality. Hermes's flat trajectory loses it. (Trace: plan/06.) |
| **Prompt self-improvement as an explicit pipeline.** | Hermes does this implicitly via skills + Curator + Atropos. We make it explicit with prompt-gradient-descent + Reflexion + A/B + KB-as-memory-of-strategies. (Self-improvement: plan/07b.) |
| **Open training pipeline.** | Hermes's Atropos is Nous-internal. Ours runs on the same box. Anyone can fork the trainer. (Training: plan/10.) |
| **Two-tier sandbox default: `local` for dev, `kubernetes` for production.** | Fixes Hermes's actual failure mode at the policy layer (ALLOW-ALL → permission-first prompt), not by forcing every user into a container. `local` is the dev / try-it path (zero infra, fastest iteration); `kubernetes` is the recommended production path (Pod-per-subagent isolation, NetworkPolicy egress, namespace-scoped RBAC). `docker` covers single-host prod / air-gapped; `ssh` for build-server ops. Modal / Daytona / Singularity / Vercel-Sandbox are plugin candidates. See [`plan/05d`](./05d-sandbox-config-examples.md). |
| **Gateway is opt-in.** | Hermes installs all 20+ gateways. We ship 3 (Telegram, Slack, Discord) at v0.1; others come as plugins. Reduces binary size and attack surface. |

## The synthesis: what Lamark IS

Lamark is, in one paragraph: **a Rust agent runtime that runs locally (CLI + ratatui TUI), reaches users through messaging gateways (Telegram/Slack/Discord), captures every interaction as a Codex-style trace bundle, stores everything durably in a sibling knowledge-base service, authors and consolidates skills autonomously, runs multi-agent coordinator/Kanban flows for parallel work, gates dangerous tool use behind a permission-first policy DSL, exposes its tools to other agents via MCP, and feeds a nightly/weekly/monthly fine-tuning pipeline that closes the learning loop into the very LLM the runtime uses.**

Each clause in that sentence is owned by exactly one plan file:

| Clause | Plan file |
|---|---|
| Rust agent runtime, CLI + ratatui TUI | 02 |
| Reaches users through messaging gateways | 09 |
| Captures every interaction as a Codex-style trace bundle | 06 |
| Stores everything durably in a sibling knowledge-base service | 07a |
| Authors and consolidates skills autonomously | 08 |
| Multi-agent coordinator/Kanban flows for parallel work | 05a |
| Gates dangerous tool use behind a permission-first policy DSL | 05 |
| Exposes its tools to other agents via MCP | 09 |
| Feeds a nightly/weekly/monthly fine-tuning pipeline | 10 |
| Closes the learning loop into the very LLM the runtime uses | 10 + 07b |

## Feature parity matrix

What hermes ships, what Claude Code ships, what Lamark ships — at v0.1.

| Feature | Hermes | Claude Code | Lamark v0.1 |
|---|---|---|---|
| Cross-session memory | ✅ (MEMORY/USER/FTS5/external) | ⚠ (CLAUDE.md only) | ✅ (KB + local SQLite fallback) |
| Autonomous skill creation | ✅ | ⚠ (user-authored) | ✅ |
| Curator background consolidation | ✅ | ❌ | ✅ |
| Multi-agent Kanban | ✅ | ⚠ (no Kanban; just `Agent` tool) | ✅ |
| Coordinator role + team mode | ⚠ | ✅ | ✅ |
| /goal Ralph loop | ✅ | ❌ | ✅ |
| Subagent isolation w/ heartbeat | ✅ | ⚠ | ✅ |
| Sibling messaging | ⚠ | ❌ | ✅ |
| Hooks system | ❌ | ✅ | ✅ |
| Subprocess hooks for user ext. | ❌ | ✅ | ✅ |
| Permission-first default | ❌ (ALLOW-ALL) | ✅ | ✅ |
| Allow/Prompt/Forbid policy DSL | ⚠ | ✅ | ✅ |
| Hierarchical prompt composition | ✅ | ✅ | ✅ |
| Anthropic cache_control | ✅ | ✅ | ✅ |
| Local prefix-cache hints | ⚠ | ⚠ | ✅ |
| Slash commands (100+) | ⚠ (~30) | ✅ (100+) | ✅ (~60 at v0.1) |
| Sandbox backends | ✅ (7 envs, command-only) | ⚠ (Docker only) | ✅ (3 in-tree — Local/Docker/SSH — host **agents** not just commands; 4 more as plugin candidates; see plan/05c) |
| LSP semantic diagnostics on write | ❌ | ✅ (v0.14.0) | ⚠ (v0.2) |
| Diff-aware Edit/Patch | ⚠ | ✅ | ✅ |
| MCP client | ✅ | ✅ | ✅ |
| MCP server | ✅ | ✅ | ✅ |
| ACP | ✅ | ❌ | ✅ |
| Messaging gateways | ✅ (20+) | ❌ | ✅ (3: TG/Slack/Discord) |
| Worktree-per-session | ✅ | ✅ | ✅ |
| Trace bundle (Codex format) | ❌ | ❌ | ✅ |
| Reducer → Nemotron-Agentic-v1 | ❌ | ❌ | ✅ |
| Knowledge-base integration | ❌ | ❌ | ✅ |
| Nightly SFT loop | ⚠ (Atropos, Nous-internal) | ❌ | ✅ (open) |
| Weekly DPO loop | ❌ | ❌ | ✅ |
| Monthly merge + requantize | ❌ | ❌ | ✅ |
| Eval gate w/ forgetting probe | ❌ | ❌ | ✅ |
| Cost tracker | ⚠ | ✅ | ✅ |
| Plugin host (WASM + dylib) | ⚠ (Python plugins) | ❌ | ✅ |
| Vision/voice/video tools | ✅ | ⚠ | ⚠ (v0.2) |
| Browser automation | ✅ (Camofox) | ❌ | ⚠ (v0.2) |
| Self-improvement pipeline | implicit | ❌ | explicit (plan/07b) |
| Open-source license | MIT | proprietary | MIT |

Legend: ✅ shipped at parity; ⚠ partial / opt-in; ❌ absent.

## Anti-features (we explicitly do NOT ship at v0.1)

Even when both originals ship something, we may skip it:

| Anti-feature | Why |
|---|---|
| 20+ messaging gateways out of the box | Binary bloat + attack surface. 3 in v0.1; the rest are plugins. |
| Hermes 4 reasoning model defaults | Per Nous's own guidance, not a good fit inside an agent loop. We default to tool-tuned Qwen3 / Nemotron-3-Nano / Gemma4. |
| Anthropic OAuth + Claude Code credential auto-discovery | Optional plugin; not in the core. |
| Browser automation (Camofox/Browserbase) | v0.2. Coding agent first. |
| Voice memo TTS/STT | v0.2. |
| Native Windows | macOS + Linux only at v0.1. |
| Multi-tenant SaaS | Knowledge-base owns multi-project namespacing; the runtime is per-user. |

## What this means for the engineering team

When you make a design decision that doesn't have a clear plan-file precedent, fall back to:

1. **Does this feature exist in Hermes?** If yes and it's in the "Hermes ideas we adopt" list above, ship it the Hermes way.
2. **Does this feature exist in Claude Code?** If yes and it's in the "Claude Code ideas we adopt" list, ship it the Claude Code way.
3. **Is it Lamark-unique?** Document it in `docs/decisions/` as an ADR.
4. **Is it on the anti-features list?** Decline it.

When in doubt, follow Claude Code's design for *coding/safety/UX* details, and Hermes's design for *persistence/learning/gateway* details. That's the synthesis in one rule.
