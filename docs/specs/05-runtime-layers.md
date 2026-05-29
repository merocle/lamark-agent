# 05 — Runtime layers (Rust)

The runtime is sliced into eight layers. This page is the **map**; the detailed
design of each layer lives in the linked plan file.

| # | Layer | Plan file | One-liner |
|---|---|---|---|
| 1 | **Entry point** — CLI, gateway-launcher, MCP-server-launcher | [`../plan/02-layer-1-entry-cli.md`](../plan/02-layer-1-entry-cli.md) | Single Rust binary `lamark`; subcommands `chat`, `gateway`, `mcp-serve`, `trace`, etc. |
| 2 | **Bootstrap & config** | [`../plan/03-layer-2-config-bootstrap.md`](../plan/03-layer-2-config-bootstrap.md) | Layered YAML + env + CLI flags; dependency-inject the runtime. |
| 3 | **Providers** | [`../plan/04-layer-3-providers.md`](../plan/04-layer-3-providers.md) | `ModelProvider` trait; OpenAI-compat, Anthropic-compat (with `cache_control`), Bedrock, local-streaming; tool-call parsers. |
| 4 | **Agent core** — turn loop, SQ/EQ, tools, environments | [`../plan/05-layer-4-agent-core.md`](../plan/05-layer-4-agent-core.md) | The heart. Codex-style event protocol. Tool registry/dispatch (see [`04-tooling-and-protocol.md`](./04-tooling-and-protocol.md)). |
| 5 | **Hooks + trace recorder** | [`../plan/06-layer-5-hooks-trace.md`](../plan/06-layer-5-hooks-trace.md) | Single bus drives recorder + gateway + UI + custom hooks. |
| 6 | **Prompt + cache** | [`../plan/07-layer-6-prompt-and-cache.md`](../plan/07-layer-6-prompt-and-cache.md) | Hierarchical prompt composer + cache strategies. |
| 6a | **Memory + knowledge-base** | [`../plan/07a-layer-6-memory-and-kb.md`](../plan/07a-layer-6-memory-and-kb.md) | Memory providers (KB default + Honcho/Mem0/Hindsight/SQLite); KB client. |
| 7 | **Skills + plugins + Curator** | [`../plan/08-layer-7-skills-plugins-curator.md`](../plan/08-layer-7-skills-plugins-curator.md) | Markdown skills, dynamic plugins (WASM + dylib), Curator background agent. |
| 8 | **Gateway + MCP + ACP + integrations** | [`../plan/09-layer-8-gateway-integrations.md`](../plan/09-layer-8-gateway-integrations.md) | Long-running messaging gateway; MCP client+server; ACP. |
| 9 | **Dynamic workflow engine** | [`../plan/05e-dynamic-workflows.md`](../plan/05e-dynamic-workflows.md) | Model writes an orchestration plan on the fly; engine executes with a parallel subagent fleet. |

Plus cross-cutting plan files:

| Plan file | Topic |
|---|---|
| [`../plan/10-training-pipeline.md`](../plan/10-training-pipeline.md) | Python training pipeline (nightly SFT, weekly DPO, monthly merge). |
| [`../plan/10c-dataset-from-codebase.md`](../plan/10c-dataset-from-codebase.md) | InferredBugs × paraphrase dataset from git history. |
| [`../plan/10d-skillopt-life-harness.md`](../plan/10d-skillopt-life-harness.md) | SkillOpt + LIFE-HARNESS + MUSE skill lifecycle spec. |
| [`../plan/10e-workflow-training-dataset.md`](../plan/10e-workflow-training-dataset.md) | Dataset to train dynamic workflow planning behaviour. |
| [`../plan/11-build-test-deploy.md`](../plan/11-build-test-deploy.md) | Cargo workspace, CI, packaging, docker images, release process. |

For the full ordered plan index (phase ordering, definition of done) see
[`../plan/00-overview.md`](../plan/00-overview.md).
