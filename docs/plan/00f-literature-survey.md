# 00f — LLM Agent Literature Survey

> Living reference of key papers across agentic reasoning, tool calling, multi-agent
> coordination, memory, and self-improvement. Each entry includes an ArXiv ID and a
> Lamark-specific design takeaway.
>
> **Last updated:** 2026-05-25. Covers 2022–2026.

**Cross-reference index** (plan → papers):
- `plan/05` agent core → §1 (reasoning/planning), §3 (tool calling, §3.6 parallel)
- `plan/05a` coordinator → §4 (multi-agent)
- `plan/07a` memory → §2 (memory)
- `plan/07b` self-improvement → §5 (self-improvement)
- `plan/10` training pipeline → §5.10–§5.11 (STaR/ReST/GRPO), §3.9 (Fission-GRPO)
- `plan/04` providers → §3.15 (XGrammar), §3.7 (async calling)

---

## §1 Agentic Reasoning & Planning

### §1.1 ReAct family

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **ReAct: Synergizing Reasoning and Acting in LLMs** (Yao et al., ICLR 2023) | 2210.03629 | Interleaves chain-of-thought traces with tool actions in one generation loop — think-act-observe cycle | Foundation of our turn loop. Every `TurnStarted` / `ToolCallStarted` / `ToolCallEnded` event in `trace.jsonl` is one ReAct step |
| **Pre-Act: Multi-Step Planning Improves Acting** (Microsoft, May 2025) | 2505.09970 | Explicit multi-step plan *before* first action reduces error accumulation in 10+-step tasks | Coordinator's planning pass (plan/05a) should emit a plan artifact before dispatching subagents; plan revision on failure is first-class |

### §1.2 Thought structures

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Chain-of-Thought Prompting** (Wei et al., NeurIPS 2022) | 2201.11903 | Step-by-step reasoning dramatically improves multi-step tasks — works above ~100B params | Bedrock of all deliberative reasoning; `reasoning_content` in trace bundles captures CoT for training |
| **Tree of Thoughts** (Yao et al., NeurIPS 2023) | 2305.10601 | BFS/DFS tree over partial solutions with self-evaluation enables backtracking | Use ToT-style beam search when coordinator plans have many branching options (code repair, multi-file changes) |
| **Graph of Thoughts** (Besta et al., ETH Zürich, Aug 2023) | 2308.09687 | Arbitrary DAG of thoughts: merge (aggregation), split (decomposition), feedback — 62% better sorting at 31% lower cost than ToT | GoT aggregation maps to `SubagentCompleted` → coordinator merge step; natural fit for parallel subagent synthesis |

### §1.3 Extended thinking / inference-time scaling

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **DeepSeek-R1: RL-driven reasoning** (DeepSeek, Jan 2025) | 2501.12948 | GRPO + binary verifiable rewards produces o1-level reasoning without human annotation | `reasoning_content` in traces is high-value training signal; GRPO is our primary post-SFT RL algorithm |
| **OpenAI o1 System Card** (OpenAI, Dec 2024) | 2412.16720 | Inference-time compute scaling: more thinking = better answers, orthogonal to model size | Configure per-task "thinking budget" in `lamark-providers`; route complex planning to think-mode |
| **Qwen3 Technical Report** (Alibaba, May 2025) | 2505.09388 | Switchable thinking/non-thinking mode via budget — unified dense+MoE family 0.6B–235B | Emit thinking budget as an inference parameter in `LocalOpenAICompat`; route simple tool calls to non-thinking |
| **Scaling LLM Test-Time Compute Optimally** (Snell et al., UC Berkeley / Google, Aug 2024) | 2408.03314 | PRM-guided best-of-N sampling can match a larger model; budget should be dynamically allocated per prompt difficulty | Wire PRM scoring into the nightly trainer as a step-level reward signal; use it for inference-time selection in high-stakes tool chains |
| **From System 1 to System 2: Survey** (Feb 2025) | 2502.17419 | Full landscape of deliberative-reasoning techniques; finding: System 2 is not always better — need a S1/S2 router | Implement a difficulty classifier in the provider layer to decide when to engage think-mode |
| **Dualformer** (Oct 2024) | 2410.09918 | Train a single model for both fast (no scratchpad) and slow (full CoT) outputs via randomized trace dropout | Fine-tuning technique: randomly drop `reasoning_content` from some training samples so the model learns both modes |
| **Slow Thinking Survey** (May 2025) | 2505.02665 | Empirical inference-time scaling curves across o1/R1/QwQ/Qwen3; thinking budget has diminishing returns | Use this to calibrate max thinking tokens per task type in `lamark-config` |

### §1.4 Planning & decomposition

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Least-to-Most Prompting** (Zhou et al., ICLR 2023) | 2205.10625 | Sequential subgoal conditioning: each answer feeds the next; 99.7% on SCAN with 14 examples | Prerequisite-ordered tasks (multi-file changes, DB migrations) should use sequential conditioning in the coordinator |
| **Plan-and-Solve Prompting** (Wang et al., ACL 2023) | 2305.04091 | Two-prompt strategy: generate plan → execute plan; fewer calculation and missing-step errors than zero-shot CoT | Coordinator planning pass and executor action pass should use separate prompts (already in plan/05a) |
| **Hierarchical Planning + Execution** (Apr 2025) | 2504.16563 | Two-tier: planner owns global task graph; executors own individual tool calls; planner revises graph on partial results | Validates Lamark's coordinator + subagent decomposition; add plan-revision event to Kanban FSM |

### §1.5 MCTS for LLM reasoning

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **MCTS Boosts Reasoning via Iterative Preference Learning** (Xie et al., May 2024) | 2405.00451 | MCTS rollouts → step-level DPO preference pairs → train → repeat (AlphaZero-style) | Direct blueprint for self-improvement: run MCTS over tool/reasoning space, store (chosen, rejected) pairs in `dpo_pairs.jsonl` |
| **rStar: Mutual Reasoning for Small LLMs** (Microsoft, Aug 2024) | 2408.06195 | Two SLMs of equal size: one generates, one discriminates — strong results without a teacher model | Discriminator model pattern → quality gate for trace data before KB upload; no larger teacher needed |
| **Cost-Augmented MCTS** (May 2025) | 2505.14656 | MCTS node scoring includes latency + API cost; balances quality vs compute envelope | Apply cost-aware search in the coordinator when operating under a hard token budget |

---

## §2 Memory & Context Management

### §2.1 Memory architectures

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **MemGPT: LLMs as Operating Systems** (Packer et al., UC Berkeley, Oct 2023) | 2310.08560 | OS-inspired tiered memory: in-context → recall DB → archive; LLM itself issues paging tool calls | `lamark-memory` tiering mirrors this directly; model should emit `memory.search` / `memory.insert` tool calls autonomously |
| **A-MEM: Agentic Memory — Zettelkasten-style** (Xu et al., Feb 2025) | 2502.12110 | Memory notes enriched with keywords + inter-note links at write time; flexible retrieval without fixed schema | KB entries via `lamark-kb-client` must carry rich metadata at write time; `/graph` for inter-note links |
| **H-MEM: Hierarchical Memory** (2025) | 2507.22925 | Multi-level semantic abstraction hierarchy with positional encodings; coarse-to-fine retrieval | KB memory store should be layered: raw events → entity summaries → agent beliefs; `/search` query should include abstraction-level hint |
| **Hindsight is 20/20** (Dec 2025) | 2512.12818 | Four memory networks: world facts, agent experiences, entity summaries, evolving beliefs | Structure KB memory entries by type with separate namespaces; selective retrieval by type per reasoning stage |

### §2.2 Episodic memory & reflection

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Reflexion: Verbal RL for Agents** (Shinn et al., NeurIPS 2023) | 2303.11366 | Self-reflections stored in episodic buffer after each trial; 130/134 tasks vs ReAct baseline | After failed rollout, write structured reflection to KB `/memory`; available as retrieval for next run — no weight update needed |
| **Generative Agents** (Park et al., Stanford, Apr 2023) | 2304.03442 | Three-tier: stream of experience → reflection synthesis → retrieval-based planning; periodic generalization into higher-level abstractions | Scheduled reflection pass in `lamark-memory`: condense recent trace events into KB summaries; `/graph` for entity relationships |

### §2.3 Long context & compaction

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Retrieval meets Long Context LLMs** (NVIDIA, Oct 2023) | 2310.03025 | 4K-context LLM + retrieval matches 16K fine-tuned LLM; retrieval improves even long-context models — complementary, not alternatives | Wire `lamark-kb-client` retrieval as a first-class step before every agent turn even when using a long-context model |
| **ACON: Context Compression for Long-Horizon Agents** (Kang et al., Oct 2025) | 2510.00615 | Universal learnable compression for agent observation history and reasoning; agent-type-agnostic | `lamark-prompt` composer should compress old tool outputs before eviction; compression applied separately to history vs. observations |
| **Recursive Summarization for Dialogue Memory** (Wang et al., Aug 2023) | 2308.15022 | Rolling recursive summary (summarize → compress → re-summarize) preserves coherence far better than flat summarization | `lamark-memory` rolling summary should be recursively updated, not regenerated from scratch; each version written to KB |

### §2.4 Production memory systems

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Mem0: Scalable Long-Term Memory** (Chhikara et al., Apr 2025) | 2504.19413 | Hybrid vector + graph; 26% better than OpenAI memory, 91% lower p95 latency, 90%+ token cost reduction | `lamark-memory` Mem0 impl: vector index for semantic recall + KB `/graph` for entity relationships |
| **Zep: Temporal Knowledge Graph for Agent Memory** (Rasmussen et al., Jan 2025) | 2501.13956 | Temporally-aware KG (Graphiti) with validity windows for each fact; 94.8% on Deep Memory Retrieval | KB entity facts must include `valid_from` / `valid_until`; enables expiry without deletion — critical for multi-session correctness |

### §2.5 KV cache management

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **SideQuest: Model-Driven KV Cache for ReAct** (Feb 2026) | 2602.22603 | Turn-aware eviction: distinguish planning context from tool-output context; standard cache compression ill-suited to agentic loops | `lamark-cache` prefix-hash strategy should tag KV segments by turn type; evict old tool outputs first, preserve system/planning prefix |
| **ARKV: Adaptive KV Cache Management** (Mar 2026) | 2603.08727 | Dynamic KV budget redistribution across layers by attention entropy importance | `lamark-providers` `LocalOpenAICompat` should surface per-layer KV budget hints to vLLM/SGLang |

### §2.6 Hierarchical retrieval

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **RAPTOR: Recursive Abstractive Retrieval** (Sarthi et al., Stanford, Jan 2024) | 2401.18059 | Bottom-up cluster + summarize tree; retrieval across all levels; +20% on QuALITY vs flat chunked RAG | KB `/knowledge` store: populate with multi-level RAPTOR summaries (raw chunks → cluster summaries); query all levels at retrieval time |

### §2.7 Catastrophic forgetting

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Empirical Study of Catastrophic Forgetting in LLMs** (Si et al., Aug 2023) | 2308.08747 | Forgetting is universal in 1B–7B LLMs during continual fine-tuning; general instruction tuning as regularizer only partially helps | Nightly SFT must apply replay-based regularization; externalizing facts to KB is a complementary forgetting mitigation |
| **Catastrophic Forgetting: Comparative Analysis** (Apr 2025) | 2504.01241 | Smaller well-regularized models (Phi-3.5-mini) show minimal forgetting while maintaining adaptation | Consider Phi-3.5 / Phi-4 family as a continual-training base; document as ADR in `docs/decisions/` |

### §2.8 Hindsight experience replay

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **AgentHER: Hindsight Experience Replay for Trajectory Relabeling** (2026) | 2603.21357 | Failed trajectories relabeled with achievable sub-goals → high-quality synthetic training data; +7–12pp on WebArena and ToolBench | `lamark-trace` reducer should tag failed rollouts with relabeled sub-goal annotations rather than discarding them |

---

## §3 Tool Calling & Function Calling

### §3.1–§3.2 Foundational work

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **ToolLLM / ToolBench** (Qin et al., Jul 2023) | 2307.16789 | 16K real-world RapidAPI calls + DFSDT tree search at inference; outperforms greedy decoding for multi-step tool use | Use tree-search at inference time for long/ambiguous tool sequences; pre-filter candidate tools with a retriever |
| **Gorilla: LLM for API Calling** (Patil et al., UC Berkeley, May 2023) | 2305.15334 | Retrieval-aware fine-tuning dramatically reduces hallucinated API arguments | Attach live documentation retrieval to every tool call at inference time; put current schema in context |

### §3.3–§3.5 Tool use advances

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **AnyTool: Self-Reflective Hierarchical API Agent** (Du et al., ICML 2024) | 2402.04253 | Hierarchical API retriever (category → tool → endpoint) + self-reflection loop that re-narrows candidates on failure | Hierarchical retrieval essential for tool libraries >100 tools; self-reflection re-narrows, not blind retry |
| **ToolACE: Synthetic Data for Function Calling** (Alibaba, Sep 2024) | 2409.00920 | Auto-generate 26K diverse APIs via speciation-adaptation-evolution; 8B model trained on this matches GPT-4 on BFCL | Invest in synthetic tool-use data diversity over raw volume; use ToolACE methodology for `lamark-tools` synthetic training data |
| **ToolPlanner: Multi-Granularity Instructions + RL** (XiaoMi, Sep 2024) | 2409.14826 | Trains on statement-level and category-level instructions (no explicit API names); RL with path planning | Train on instruction granularities matching how real users speak — no explicit tool names in training prompts |

### §3.6 Parallel tool calling

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **LLMCompiler: Parallel Function Calling via DAG Planning** (Kim et al., Dec 2023) | 2312.04511 | Planner LLM decomposes request into dependency DAG; independent branches execute concurrently; 3.7× latency speedup, 6.7× cost reduction | Before executing tool calls, emit a DAG plan; dispatch independent branches concurrently — highest-leverage latency optimization |
| **Asynchronous LLM Function Calling** (Gim et al., Dec 2024) | 2412.07017 | In-context interrupt protocol: continue generating tokens for other subtasks while a function call executes in background; 1.6×–5.4× latency reduction | For agents with many short predictable tool calls, async dispatch eliminates blocking penalty; expose in `lamark-providers` |

### §3.7 Tool selection

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **AutoTool: Graph-Based Tool Selection** (Nov 2025) | 2511.14650 | Tool-transition graph from historical trajectories exploits "tool usage inertia"; graph traversal replaces LLM inference for 30% of tool selections | Mine `trace.jsonl` tool-call sequences to build a transition graph in the KB; use it as a routing hint before invoking the model |

### §3.8–§3.9 Error recovery

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Fission-GRPO: Learning to Recover from Tool Errors** (Zhang et al., Jan 2026) | 2601.15625 | "Fissions" failed RL trajectories into corrective training instances via Error Simulator; +4pp absolute on BFCL v4 multi-turn | Generate on-policy error-correction pairs for the nightly DPO; Error Simulator is the `rejected` trajectory in `dpo_pairs.jsonl` |
| **ERR: Recoverability Law for Tool Agents** (Jan 2026) | 2601.22352 | Expected Recovery Regret (ERR) formalizes recoverability; falsifiable relationship with Efficiency Score | Use ERR/Efficiency Score as a diagnostic in the eval gate to distinguish recovery deficits from accuracy deficits |

### §3.10–§3.12 Evaluation

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **BFCL: Berkeley Function-Calling Leaderboard** (Patil et al., Jun 2024) | 2407.00121 | 4,951+ test cases: single-turn, parallel, multiple, multi-turn; AST-match + live execution metrics | Eval against parallel and multi-turn splits specifically — single-turn benchmarks hide dependency confusion and state drift |
| **τ-bench (TAU-Bench)** (Yao et al., Jun 2024) | 2406.12045 | Dynamic multi-turn customer-service with user simulator + policy-following agent; pass^k metric; even GPT-4o <50% on tasks | Measure pass^k (probability of k consecutive successes), not single-trial rate; policy-compliance is the dominant failure mode |
| **ToolSandbox: Stateful Interactive Evaluation** (Lu et al., Aug 2024) | 2408.04682 | Stateful environment where tool availability is conditional (shared world state); GPT-4o user simulator | Evaluate in stateful environments where tool preconditions change mid-conversation; surface `ToolCallEnded { ok: false }` when precondition violated |
| **StableToolBench** (Guo et al., ACL 2024) | 2403.07714 | Virtual API server (cache + simulator) makes ToolBench results reproducible | Never eval tool-calling against live production APIs in CI; use cached responses or simulators |

### §3.13–§3.14 Constrained decoding

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **XGrammar: Flexible Structured Generation** (Dong et al., Nov 2024) | 2411.15100 | Context-free grammar constrained decoding at up to 100× speedup; pre-checks context-independent tokens offline | Use XGrammar (via vLLM/SGLang) to guarantee syntactically valid JSON tool call arguments at the token level — eliminates parse errors |
| **JSONSchemaBench: Structured Output Evaluation** (Guidance-AI / Microsoft, Jan 2025) | 2501.10868 | 10K real-world JSON schemas; schema complexity (not validity) drives constrained-decoding failures | Test tool schemas against JSONSchemaBench; simplify deeply nested schemas to reduce failure rates |

---

## §4 Multi-Agent Coordination

### §4.1–§4.2 Orchestration frameworks

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **AutoGen** (Wu et al., Microsoft, Aug 2023) | 2308.08155 | Conversable agents in flexible topologies (two-agent, group, nested); GroupChatManager dispatches replies | GroupChatManager ≈ `lamark-coordinator`; reply-function dispatch is an alternative to Kanban pull — worth noting for async task routing |
| **Magentic-One** (Fourney et al., Microsoft, Nov 2024) | 2411.04468 | Orchestrator + specialist agents (WebSurfer, FileSurfer, Coder, Terminal) with an explicit ledger; SOTA on GAIA/WebArena | Coordinator ledger — structured scratchpad of goals, subgoal status, error notes — should be a first-class Kanban FSM state |
| **MetaGPT** (Hong et al., Aug 2023) | 2308.00352 | SOPs encoded in multi-agent workflows; agents produce typed structured artifacts (spec → design → code → tests) | Subagents should emit schema-validated outputs (not raw prose); `lamark-protocol` should define per-skill output schemas |
| **CAMEL: Role-Playing via Inception Prompting** (Li et al., KAUST, Mar 2023) | 2303.17760 | AI user + AI assistant given complementary system prompts; autonomous task completion without human intervention | Two cooperating subagents can be bootstrapped from a single coordinator prompt injecting complementary role descriptions |

### §4.3 Skill-growing agents

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Voyager: Open-Ended Embodied Agent** (Wang et al., NVIDIA, May 2023) | 2305.16291 | Ever-growing skill library of executable code + automatic curriculum + iterative prompting with self-verification | `lamark-skills`: only persist a skill after self-verification passes — adopt this gate in the Curator |

### §4.4 Hierarchical multi-agent

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **HALO: Hierarchical Orchestration** (Hou et al., May 2025) | 2505.13516 | Three layers: planner → role-design → executor; mid-level instantiates task-specific agents dynamically | Coordinator must not have a fixed subagent roster; dynamic instantiation at runtime keeps the system extensible |
| **AgentOrchestra: TEA Protocol** (Zhang et al., Jun 2025) | 2506.12508 | Tool-Environment-Agent (TEA) protocol: agents exposed as typed tools to coordinators — unifies tool invocation and agent delegation | `lamark-coordinator` should treat subagent invocation and tool invocation identically through the same `PermissionRequest` / `Decision` path |
| **MultiAgentBench** (Zhu et al., Mar 2025) | 2503.01935 | Milestone-based KPIs for collaboration; graph topology + cognitive planning improve milestone achievement by 3% vs star topology | Kanban should expose milestone events (not just done/failed); consider peer-to-peer delegation for parallelizable subtasks |

### §4.5 Interoperability protocols

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Survey: MCP, ACP, A2A, ANP** (Ehtesham et al., May 2025) | 2505.02279 | Systematically compares 4 live standards: MCP (JSON-RPC, tool-level), ACP (RESTful HTTP), A2A (Agent Cards + skills directory), ANP (P2P) | Prioritize MCP interoperability before implementing ACP/ANP; adoption roadmap: MCP first for tool integration, A2A for cross-org delegation |
| **Mesh Memory Protocol (MMP)** (Apr 2026) | 2604.19540 | Semantic memory layer for agent teams to share, evaluate, and merge cognitive state across sessions | `lamark-memory` trait should expose `merge_from_agent(agent_id, items)` for multi-session coordinator handoffs |

### §4.6 Trust & security

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Trust Paradox in Multi-Agent LLMs** (Xu et al., Oct 2025) | 2510.18563 | More inter-agent trust = better task success BUT proportionally higher Over-Exposure Rate and Authorization Drift; Minimum Necessary Information (MNI) as safety baseline | Default: each subagent receives only context required for its subtask — not the full session. Coordinator must strip context before delegation |
| **SoK: Trust-Authorization Mismatch** (Shi et al., Dec 2025) | 2512.06914 | 200+ papers under Belief-Intention-Permission framework; root cause of prompt injection / over-authorization: static RBAC decoupled from dynamic trust | `lamark-policy` `Allow|Prompt|Forbidden` DSL must be evaluated dynamically per-request with a runtime trust-level context variable |

### §4.7 Decentralized / emergent

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **SwarmSys: Pheromone-Inspired Swarm Agents** (Li et al., Oct 2025) | 2510.10047 | Agents leave contextual trace-tags influencing task routing; three roles: Explorer, Worker, Validator; no central controller | For long-horizon tasks, "trace tag" on completed subtasks in KB biases future subagent selection — minimal pheromone mechanism |
| **AgentNet: Decentralized Evolutionary Coordination** (Yang et al., Apr 2025) | 2504.00587 | RAG-based DAG; agents self-select collaborators by local expertise without global coordinator | Expertise-based routing via KB agents index can be layered on Kanban as a routing hint |
| **Dynamic Ad-Hoc Networking for LLM Agents** (Feb 2026) | 2602.08009 | MANET-era routing algorithms applied to LLM agents; dynamic coalition formation and dissolution | `lamark-coordinator` should expose a service-discovery endpoint so agents locate each other without hard-coded URLs |

---

## §5 Self-Improvement & Evaluation

### §5.1 In-session refinement

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Self-Refine: Iterative Refinement with Self-Feedback** (Madaan et al., May 2023) | 2303.17651 | Single LLM generates → critiques → refines in a closed loop; +20% avg across 7 tasks | Implement as a built-in skill; `lamark-prompt` section model injects prior draft + critique as structured section |
| **Reflexion: Verbal Reinforcement Learning** (Shinn et al., NeurIPS 2023) | 2303.11366 | Verbal self-reflections stored in episodic buffer; enables improvement without weight updates | Curator writes reflection entries to KB after failed rollouts; available via `/search` for next run |

### §5.2 Prompt optimization

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **OPRO: LLMs as Optimizers** (Yang et al., Google, Sep 2023) | 2309.03409 | Feed (solution, score) pairs back to LLM; it proposes better solutions; up to 50% improvement on Big-Bench | Algorithmic blueprint for our OPRO cycle: reducer emits `(prompt_version, rollout_score)` → meta-prompt proposes next section revision |
| **ProTeGi: Prompt Optimization via Textual Gradients** (Pryzant et al., Sep 2023) | 2305.03495 | LLM-generated critiques of prompt errors + beam search over candidates | Curator pass: for each failing trace cluster, generate critique string → rank candidate prompt edits |
| **PromptBreeder: Self-Referential Prompt Evolution** (Fernando et al., DeepMind, Sep 2023) | 2309.16797 | Evolves both task-prompts AND the mutation-prompts that modify them | Meta-skill: store mutation-prompt variants in KB as skills; Curator scores by rollout fitness → replace across training epochs |

### §5.3 Constitutional AI / RLAIF

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Constitutional AI** (Bai et al., Anthropic, Dec 2022) | 2212.08073 | Critique-revision SFT + AI-generated preference labels from a written constitution; removes need for human preference annotation | `lamark-policy` DSL (`Allow|Prompt|Forbidden`) is the "constitution"; nightly pipeline auto-generates DPO preference pairs by judging which trajectory better follows `policy.toml` |

### §5.4 Agent evaluation

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **SWE-bench: Real-World GitHub Issues** (Jimenez et al., Oct 2023) | 2310.06770 | 2,294 issue→PR pairs with execution-based pass/fail verification | Canonical verifier for software-engineering tool use; unit-test pass/fail = cheap binary verifiable reward for RLVR |
| **WebArena: Realistic Web Environment** (Zhou et al., NAACL 2024) | 2307.13854 | 4 self-hosted web apps with functional-correctness validators; best GPT-4 agent 14.41% vs 78.24% human | Programmatic functional-correctness checks in `lamark-sandbox` are the model for environment verifiers; deterministic reward, no learned RM needed |
| **OSWorld: Desktop Multimodal Agent Evaluation** (Xie et al., NeurIPS 2024) | 2404.07972 | 369 desktop tasks; execution-script post-action state checking; best model 12.24% vs 72.36% human | Post-action state checking informs how `lamark-sandbox` backends emit `VerifiedReward` events beyond stdout matching |
| **AgentBench: LLMs as Agents** (Liu et al., Tsinghua, Aug 2023) | 2308.03688 | 8 interactive environments (OS, web, DB, KG, game); primary bottlenecks: long-term reasoning + instruction-following | `lamark-test-utils` eval harness should measure multi-turn completion rate per environment category |
| **GAIA: General AI Assistant Evaluation** (Mialon et al., Nov 2023) | 2311.12983 | 466 real-world tasks requiring reasoning + multimodality + web + tool use; humans 92% vs AI 15% | Use GAIA pass rate as a release gate criterion in the nightly trainer |
| **LLM-as-Judge / MT-Bench** (Zheng et al., NeurIPS 2023) | 2306.05685 | GPT-4 as pairwise judge achieves >80% agreement with human raters | Implement LLM-as-judge as a reducer step: judge each rollout pair, store preference label in trace bundle as DPO signal |

### §5.5 PRM vs ORM

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **Let's Verify Step by Step (PRM)** (Lightman et al., OpenAI, May 2023) | 2305.20050 | PRMs scoring each reasoning step outperform ORMs and majority voting: 78.2% vs 72.4% vs 69.6% on MATH | `trace.jsonl` step-per-line format is aligned with PRM training; fine-tune a lightweight PRM on traces where step outcomes are labeled by test results |

### §5.6 Self-training loops

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **STaR: Bootstrapping Reasoning With Reasoning** (Zelikman et al., May 2022) | 2203.14465 | Generate rationale → filter correct → fine-tune → repeat; 30× larger model performance from small seed | This IS the nightly SFT → filter → retrain loop; "rationalization" fallback = reducer generating synthetic CoT from successful rollouts |
| **ReST: Reinforced Self-Training** (Gulcehre et al., Google DeepMind, Aug 2023) | 2308.08998 | Alternates Grow (sample to dataset) and Improve (offline RL / supervised filter) phases; better sample efficiency than online RLHF | Grow = accumulate `~/.lamark/traces/` from production; Improve = train on filtered high-reward trajectories in nightly cycle |
| **ReST meets ReAct** (Aksitov et al., Google, Dec 2023) | 2312.10003 | Applies ReST to a ReAct-style tool-using agent; 2 iterations enough for meaningful gains; small fine-tuned model matches large prompted model | Direct validation that our SFT loop works for multi-step tool-using agents; keep nightly cycles short and frequent |
| **V-STaR: Training Verifiers for Self-Taught Reasoners** (Hosseini et al., Feb 2024) | 2402.06457 | Jointly train generator (SFT on correct traces) + verifier (DPO on correct+incorrect traces); verifier-guided selection +4–17% | Failed rollouts are not waste — they are the verifier's negative examples; verifier trained via DPO on `dpo_pairs.jsonl` |

### §5.7 RLVR and GRPO

| Paper | ArXiv | Key contribution | Lamark takeaway |
|---|---|---|---|
| **DeepSeek-R1: GRPO + Verifiable Rewards** (DeepSeek, Jan 2025) | 2501.12948 | Group Relative Policy Optimization + binary rewards trains o1-level reasoning without human annotation | GRPO is our post-SFT RL phase: sample K rollouts per task from `ModelProvider`, score by test pass/fail, update with GRPO loss — no reward model crate needed |
| **Agent-RLVR: SE Agents via Guidance + Environment Rewards** (Pan et al., Jun 2025) | 2506.11425 | Adds "agent guidance" (strategic plans + dynamic error feedback) to steer agents before GRPO updates; solves sparse-reward failure of vanilla RLVR in agentic settings | Closest existing work to Lamark's full loop: guidance = `lamark-skills` injection + KB retrieval; unit-test reward = `VerifiedReward` from `lamark-sandbox`. Adopt guided-then-GRPO as primary RL algorithm once SFT bootstrapping is done |

---

## Design Decision Cross-Reference

Papers that directly informed or should update specific Lamark architectural decisions:

| Decision | Paper(s) | Plan section |
|---|---|---|
| ReAct as the turn loop primitive | 2210.03629 | plan/05 §Agent loop |
| Parallel tool calls via DAG planning | 2312.04511 | plan/05 §Tool dispatch |
| XGrammar for valid JSON tool calls | 2411.15100 | plan/04 §LocalOpenAICompat |
| DFSDT tree-search for long tool chains | 2307.16789 | plan/05 §Tool planning |
| Coordinator ledger / Kanban FSM | 2411.04468 | plan/05a §Coordinator FSM |
| Agent-as-tool unification (TEA) | 2506.12508 | plan/05a §Subagent delegation |
| Dynamic subagent instantiation | 2505.13516 | plan/05a §Subagent roster |
| MNI trust baseline for subagent context | 2510.18563 | plan/05a §Context delegation |
| Dynamic trust-level in policy evaluation | 2512.06914 | plan/lamark-policy §DSL |
| MemGPT tiered memory + LLM-driven paging | 2310.08560 | plan/07a §Memory tiers |
| RAPTOR multi-level KB retrieval | 2401.18059 | plan/07a §KB retrieval |
| Zep temporal validity windows on KB facts | 2501.13956 | plan/07a §KB write format |
| ACON context compression (obs vs history) | 2510.00615 | plan/06 §CompactionMarker |
| AgentHER failed rollout relabeling | 2603.21357 | plan/06 §Reducer / DPO pairs |
| OPRO as prompt section evolution | 2309.03409 | plan/07b §Loop C |
| PromptBreeder meta-skill mutation | 2309.16797 | plan/08 §Curator |
| Constitutional AI / RLAIF from policy.toml | 2212.08073 | plan/10 §Weekly DPO |
| STaR / ReST as the nightly SFT loop | 2203.14465, 2308.08998 | plan/10 §Train |
| V-STaR verifier from dpo_pairs.jsonl | 2402.06457 | plan/10 §Weekly DPO |
| GRPO (DeepSeek-R1) as post-SFT RL phase | 2501.12948 | plan/10 §Train |
| Agent-RLVR as guided-then-GRPO primary RL | 2506.11425 | plan/10 §Train |
| Fission-GRPO error correction training | 2601.15625 | plan/10 §Weekly DPO |
| PRM for step-level reward from trace events | 2305.20050 | plan/10 §Eval gate |
| SWE-bench as VerifiedReward source | 2310.06770 | plan/11 §Sandbox eval |
| pass^k metric for tool-call reliability | 2406.12045 | plan/11 §Eval gate |
