# 04 — Tooling and tool-call protocol

> **Spec (what/why).** Implementation lives in the plan files: tool-call parsers
> in [`../plan/04-layer-3-providers.md`](../plan/04-layer-3-providers.md), the
> registry + dispatch + approval flow in
> [`../plan/05-layer-4-agent-core.md`](../plan/05-layer-4-agent-core.md), refinements
> in [`../plan/00c-hermes-deepdive-addendum.md`](../plan/00c-hermes-deepdive-addendum.md)
> and [`../plan/00d-claude-code-deepdive-addendum.md`](../plan/00d-claude-code-deepdive-addendum.md),
> and MCP/ACP in [`../plan/09-layer-8-gateway-integrations.md`](../plan/09-layer-8-gateway-integrations.md).
>
> The machine-readable catalog is [`../../learning/data/tools.yaml`](../../learning/data/tools.yaml)
> — the single source of truth that generates both the table in this doc and the
> synthetic training data (`learning/scripts/generate_tool_dataset.py`).

## 1. Purpose & scope

Lamark's tool surface is based on **Hermes Agent's** tooling (re-implemented in
Rust — see [`01-scope-and-inheritance.md`](./01-scope-and-inheritance.md)), pared
to a v0.1 set and extended with Lamark-specific tools. This doc fixes the
**tool-call wire protocol**, the **registry/dispatch/policy architecture**, and
the **full catalog** with per-tool adopt/defer/drop status.

## 2. Tool-call protocol (base = Hermes / OpenAI)

The canonical wire format is the OpenAI function-calling shape Hermes uses. One
shape serves both the runtime and the training data (it is identical to
Nemotron-Agentic-v1, see [`06-trace-and-data-format.md`](./06-trace-and-data-format.md)).

- The model emits, on the assistant turn:
  ```json
  {"role": "assistant", "content": null,
   "tool_calls": [{"id": "call_1", "type": "function",
                   "function": {"name": "Read", "arguments": "{\"file_path\": \"/a.rs\"}"}}]}
  ```
- The runtime executes the call and feeds the result back as a `tool` message:
  ```json
  {"role": "tool", "tool_call_id": "call_1", "content": "<result text or JSON>"}
  ```
- Available tools are advertised in a top-level `tools[]` array of JSON-Schema
  function definitions (the `parameters` in `tools.yaml`).

`arguments` is a JSON **string** (streamed token-by-token); `tool_call_id`
correlates each result to its call.

## 3. Lamark parser normalization

Different model families emit tool calls differently. Lamark owns the parsing:
provider-specific parsers normalize every variant into one internal
`CompleteEvent` stream (`DeltaText` / `DeltaReasoning` / `DeltaToolCall{id,name,args_chunk}`
/ `ToolCallReady{id,name,args}` / `Finish` / `Error`). See
[`../plan/04-layer-3-providers.md`](../plan/04-layer-3-providers.md) §Tool-call parsers.

| Parser | Model family / wire format |
|---|---|
| `openai_json` | OpenAI, vLLM, Ollama, llama.cpp, SGLang, LM Studio, MLX — `choices[].delta.tool_calls[]` |
| `hermes_xml` | Hermes — `<tool_call>…</tool_call>` blocks in the text stream |
| `mistral` | Mistral function-calling format |
| `qwen3_moe` | Qwen3/3.5 — `<think>…</think>` reasoning + tool calls |
| `nano_v3` | Nemotron-3 nano reasoning channel |
| `gemma4` | Gemma4 reasoning channel |
| `claude_thinking` | Anthropic `tool_use` + thinking blocks |

Tool-call arguments are accumulated per `tool_call.id` and emitted as one
`ToolCallReady` at `finish_reason`. Where the engine supports constrained
decoding (vLLM/SGLang XGrammar), the tool's `parameters` schema is passed as
`guided_json` so arguments are syntactically valid by construction — which is
also how the synthetic trajectory data guarantees well-formed calls.

## 4. Registry, dispatch, policy, hooks

- **`Tool` trait** (`agent/crates/lamark-tools`): `name`, `schema`,
  `capabilities` (read-only / destructive / concurrency-safe), `category`,
  `toolset`, `invoke(args, ctx, cancel)`, optional `dynamic_schema_overrides`,
  `check()` usability probe, `max_result_chars`. Each tool module also exports
  its prompt-string and tests.
- **Registry**: explicit `register(...)`; a generation counter bumps on
  register/deregister so the prompt cache (memoized tool definitions) invalidates
  correctly on hot-reload.
- **Parallel dispatch with conflict groups**: tools whose `capabilities` mutate
  the same canonicalized `path` serialize; all other pairs run concurrently up to
  `agent.max_parallel_tool_calls`.
- **Approval flow**: `policy.evaluate(name, args, ctx) → Decision::{Allow|Prompt|Forbidden}`.
  Every shell-class / write-class tool (Bash, Write, Edit, MCPProxy, …) defaults
  to `Prompt`; declarative rules live in `agent/crates/lamark/policy.toml`. An
  optional auxiliary model can auto-approve before falling through to the user.
- **Hooks**: `PreToolUse` / `PostToolUse` / `PermissionRequest` fire on the hook
  bus; write-class tools surface LSP diagnostics in `PostToolUse` before the turn
  ends.
- **MCP wrapping**: external MCP servers are bridged via `MCPProxy`; their tools
  register as `mcp__<server>__<tool>` and are **destructive by default** unless
  their `inputSchema` marks them otherwise.

## 5. Tool catalog

Status legend: **adopt-v0.1** ships in v0.1 · **defer** = v0.2+ (browser,
multimodal, code-exec) · **drop** = not an agent tool in Lamark (messaging is the
gateway's job; platform integrations are out of scope). Generated from
`tools.yaml` (26 adopt / 22 defer / 15 drop = 63 catalog rows).

#### adopt-v0.1

| Lamark name | Hermes origin | toolset | flags | description |
|---|---|---|---|---|
| Read | read_file | file | read-only | Read a file from the workspace, optionally a line range. |
| Write | write_file | file | destructive | Create or overwrite a file; surfaces LSP/compiler diagnostics on completion. |
| Edit | patch | file | destructive | Exact-string replacement in a file; old_string must be unique. |
| Glob | search_files | file | read-only | Find files by glob pattern, sorted by modification time. |
| Grep | search_files | search | read-only | Search file contents with a regular expression (ripgrep-based). |
| Bash | terminal | shell | destructive | Execute a shell command in the sandbox; gated by the permission policy. |
| WebSearch | web_search | web | read-only | Search the web and return ranked results. |
| WebFetch | web_extract | web | read-only | Fetch a URL and extract its content as markdown. |
| MemorySearch | memory | memory | read-only | Search long-term memory in the knowledge-base over HTTP. |
| MemoryWrite | memory | memory | - | Write a memory fact to the knowledge-base. |
| UserProfileGet | memory | memory | read-only | Retrieve the current user profile snapshot. |
| TaskCreate | todo | task | - | Create a task in the structured task list. |
| TaskUpdate | todo | task | - | Update a task's status or fields. |
| TaskList | todo | task | read-only | List current tasks. |
| SkillView | skill_view | skills | read-only | Read a skill document by name. |
| SkillList | skills_list | skills | read-only | List available skills (name + description catalog). |
| SkillManage | skill_manage | skills | - | Create, update, or delete a skill document (validation-gated). |
| Agent | delegate_task | agents | - | Spawn a subagent with an isolated context; only its final summary returns. |
| Kanban | kanban_create | coordinator | - | Multi-agent Kanban coordination (create/show/list/complete/block/comment/heartbeat/link). |
| AskUserQuestion | clarify | interactive | - | Ask the user a multiple-choice or open-ended clarifying question. |
| ScheduleCron | cronjob | scheduling | - | Create or manage scheduled jobs (cron). |
| MCPProxy | - | mcp | destructive | Bridge to external MCP servers; their tools appear as mcp__<server>__<tool>. |
| ToolSearch | - | meta | read-only | Search for and load deferred tool schemas on demand. |
| WorktreeCreate | - | worktree | - | Create an isolated git worktree for the agent to work in. |
| AskApproval | - | interactive | - | Request explicit user approval for a gated action. |
| WorkflowPlan | - | workflow | - | Emit a dynamic orchestration plan executed by the workflow engine (see ../plan/05e). |

#### defer (v0.2+)

| Lamark name | Hermes origin | toolset | flags | description |
|---|---|---|---|---|
| ExecuteCode | execute_code | code_execution | destructive | Run code in a sandbox; overlaps Bash. |
| Process | process | shell | destructive | Manage background processes (list/kill/wait). |
| SessionSearch | session_search | search | read-only | Full-text search of past sessions (overlaps MemorySearch). |
| BrowserNavigate … BrowserCdp (12) | browser_* | browser | mixed | Headless browser automation — whole family deferred. |
| VisionAnalyze | vision_analyze | vision | read-only | Analyze an image with a vision model (multimodal v0.2). |
| VideoAnalyze | video_analyze | vision | read-only | Analyze video. |
| ImageGenerate | image_generate | image_gen | - | Generate images. |
| VideoGenerate | video_generate | video_gen | - | Generate video. |
| TextToSpeech | text_to_speech | tts | - | Convert text to audio. |
| ComputerUse | computer_use | computer_use | destructive | Desktop GUI control. |
| MixtureOfAgents | mixture_of_agents | moa | - | Multi-model reasoning aggregation. |

(The 12 `browser_*` tools are listed individually in `tools.yaml`; collapsed here.)

#### drop (not agent tools)

`x_search` (platform), `send_message` / `discord` / `discord_admin` (the gateway
handles messaging — [`../plan/09`](../plan/09-layer-8-gateway-integrations.md)),
`feishu_*` ×5, `ha_*` ×4, `spotify`, `yuanbao`. These are platform integrations,
not part of the agent tool registry.

## 6. Provenance

Tool names and shapes are documented for parity with Hermes Agent
(`learning/vendor/hermes/`, MIT). Lamark **re-implements** them in Rust; it does
not copy Hermes code. See [`01-scope-and-inheritance.md`](./01-scope-and-inheritance.md).
