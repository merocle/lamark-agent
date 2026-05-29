# 10b — Dataset catalog

> Reference list of open datasets used in the Lamark self-learning pipeline.
> Covers: model-specific training data (what Nemotron / Qwen3 / Gemma4 were trained on),
> anchor datasets (frozen in every nightly blend), and candidate enrichment datasets.

**See also:** [`plan/10`](./10-training-pipeline.md) for the pipeline mechanics and blend ratios.

---

## Training data used in target base models

Understanding what the base models already saw guides anchor selection: we should **anchor on data the base model already knows** (to preserve it) rather than data it has never seen (which belongs in new training).

### NVIDIA Nemotron-3-Nano (30B-A3B, released Dec 2025)

Post-training pipeline: SFT → multi-environment synchronous GRPO → RLHF (Qwen3-Nemotron-235B GenRM) → DPO (tool-hallucination).

**Pre-training (25 T tokens, unique ~13.3 T, two-phase WSD schedule):**

| Dataset | Tokens | HF path | License |
|---|---|---|---|
| Nemotron-CC-v2 | 3.46 T (English) + 1.74 T (multilingual) | `nvidia/Nemotron-CC-v2` | NVIDIA DAAMT |
| Nemotron-CC-v2.1 | +2.5 T (CC 2024–25 + 15-lang synthetic via Qwen3-30B-A3B) | `nvidia/Nemotron-CC-v2.1` | NVIDIA DAAMT |
| Nemotron-CC-Code-v1 | 427.9 B (code from CC, Phi-4 cleaned) | `nvidia/Nemotron-CC-Code-v1` | NVIDIA DAAMT |
| Nemotron-Pretraining-Code-v2 | ~340 B (GitHub + synthetic Q&A, review, transpilation) | `nvidia/Nemotron-Pretraining-Code-v2` | NVIDIA DAAMT |
| Nemotron-Pretraining-Specialized-v1 | ~270 B (RQA, InfiniByte, Wiki-Rewrite, Sci-Coding, Math-Textbooks, STEM-SFT) | `nvidia/Nemotron-Pretraining-Specialized-v1` | CC BY/SA 4.0 |
| Nemotron-CC-Math-v1 | 133 B (Phi-4 pipeline) | `nvidia/Nemotron-CC-Math-v1` | CC BY 4.0 |
| Nemotron-PrismMath | 4.6 B (QwQ-32B + DeepSeek-R1 from Big-Math + OpenR1) | `nvidia/Nemotron-PrismMath` | open |
| Nemotron-MIND (refreshed) | 73 B (CC math conversations, Phi-4) | `nvidia/Nemotron-MIND` | open |
| Synthetic Tool Calling | 26.2 B (Qwen3-235B-A22B-2507 + gpt-oss-120b) | part of Post-Training-v3 | — |

**Post-training SFT (13 M new samples; Nemotron-Post-Training-v3 collection):**

| Dataset | HF path | Notes |
|---|---|---|
| OpenCodeReasoning / OpenCodeReasoning-2 | `nvidia/OpenCodeReasoning-2` | DeepSeek-R1-0528; competition code |
| OpenMathReasoning | `nvidia/OpenMathReasoning` | gpt-oss-120b + Qwen2.5-32B; math with reasoning traces |
| HelpSteer3 | `nvidia/HelpSteer3` | Preference + helpfulness |
| Nemotron-Personas-USA | `nvidia/Nemotron-Personas-USA` | Persona seeds for diverse chat/RL synthesis |
| Nemotron Aegis v2 | `nvidia/Aegis-AI-Content-Safety-Dataset-2.0` | Safety refusals |
| NeMo Gym RL collection | `nvidia/` (various) | Minesweeper, Sudoku, Typewriter, SWE-Gym, R2E-Gym, tool-use, structured-output |
| Nemotron-SFT-Math-v3 | `nvidia/Nemotron-SFT-Math-v3` | 3.6 M samples, 144 GB; DeepSeek-V3 + DeepSeek-V3.2; CoT + Python TIR |
| Nemotron-SFT-Instruction-Following-Chat-v2 | `nvidia/Nemotron-SFT-Instruction-Following-Chat-v2` | 2 M samples; Kimi-K2, GLM-4.6, Qwen3-235B, gpt-oss-120b |
| Nemotron-SFT-Agentic-v2 | `nvidia/Nemotron-SFT-Agentic-v2` | Multi-turn agent SFT |
| Nemotron-SFT-Competitive-Programming-v2 | `nvidia/Nemotron-SFT-Competitive-Programming-v2` | Competitive code SFT |
| Nemotron-SFT-SWE-v2 | `nvidia/Nemotron-SFT-SWE-v2` | Software engineering tasks |
| Nemotron-SFT-Multilingual-v1 | `nvidia/Nemotron-SFT-Multilingual-v1` | 15-language SFT |
| Nemotron-SpecializedDomains-Finance-v1 | `nvidia/Nemotron-SpecializedDomains-Finance-v1` | Finance domain SFT |

**Post-training RL (37 datasets, 21 env configs, ~1.2 M rollouts for Super; ~similar for Nano):**

| Dataset | HF path | Notes |
|---|---|---|
| Nemotron-RL-math-OpenMathReasoning | `nvidia/Nemotron-RL-math-OpenMathReasoning` | GRPO math environments |
| Nemotron-RL-coding-competitive_coding | `nvidia/Nemotron-RL-coding-competitive_coding` | Code execution reward |
| Nemotron-RL-instruction_following | `nvidia/Nemotron-RL-instruction_following` | IF verifier environments |
| Nemotron-RL-Agentic-Function-Calling-Pivot-v1 | `nvidia/Nemotron-RL-Agentic-Function-Calling-Pivot-v1` | Tool-call RL tasks |
| Nemotron-RL-bixbench_hypothesis | `nvidia/Nemotron-RL-bixbench_hypothesis` | 250 bioinformatics hypothesis tasks; Docker + pytest verifiers |
| Nemotron-3-Nano-RL-Training-Blend | `nvidia/Nemotron-3-Nano-RL-Training-Blend` | Compiled RL blend used for Nano |

**Reward modeling:**

| Dataset | HF path | Notes |
|---|---|---|
| HelpSteer3 | `nvidia/HelpSteer3` | Pairwise + pointwise; used to train Qwen3-Nemotron-235B GenRM |
| HelpSteer2 | `nvidia/HelpSteer2` | Earlier preference data |
| Nemotron-RLHF-GenRM-v1 | `nvidia/Nemotron-RLHF-GenRM-v1` | Generative reward model training data |

**Third-party seeds used (from model card):** GSM8K, OpenCodeReasoning(-2), HelpSteer3, opc-sft-stage2, Big-Math-RL-Verified, MetaMathQA, Skywork-OR1-RL-Data, SWE-Gym, R2E-Gym-Subset, WildChat-1M, LMSYS-Chat-1M, KernelBook (GPUMODE), SCP-116K, LIMO, arena-human-preference-140k, SWE-Smith, PRM800K, SciBench, FineWeb-2, peS2o, OpenWebMath, BioRxiv, PMC Open Access, arXiv, FinQA, MedMCQA, The Common Pile v0.1, FineMath, MegaMath, FLAN, WikiTableQuestions, Hendrycks MATH, MathPile, NuminaMath-CoT, ARC, OpenBookQA, MMLU Auxiliary Train, glaive-function-calling-v2, APIGen/xlam-60k, EleutherAI arithmetic, CDQuestions, JailbreakV-28k (RedTeam-2K), Gretel safety alignment.

---

### NVIDIA Nemotron-3-Super (120B-A12B, released 2026)

Larger sibling of Nano. 120B total / 12B active. Key architectural differences from Nano:
- **NVFP4 pre-training** (unlike Nano which used BF16); 4× inference speedup on B200 vs FP8 on H100.
- **Multi-Token Prediction (MTP)**: predicts 3 future tokens simultaneously; up to 3× structured-generation speedup.
- **Latent MoE**: tokens projected into compressed low-rank latent space (4× more effective experts).
- 25T tokens, 10T unique curated; 15M coding problems; 7M SFT samples from 40M pool; 21 RL env configs, 37 RL datasets, ~1.2M env rollouts.
- HF: `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-FP8`

---

### NVIDIA Llama-Nemotron Post-Training Dataset

Used for Llama-Nemotron family; publicly released and directly usable for Lamark SFT:

| Dataset | HF path | Size | License | Notes |
|---|---|---|---|---|
| Llama-Nemotron-Post-Training-Dataset | `nvidia/Llama-Nemotron-Post-Training-Dataset` | 35.1 M samples / 130 GB | CC-BY-4.0 | Math 22M + Code 10M + Science 709K + IF 56K + Chat 40K + Safety 31K; SFT + RL splits |
| Llama-Nemotron-Post-Training-Dataset-v2 | `nvidia/Nemotron-Post-Training-Dataset-v2` | 5.22 M samples | CC-BY-4.0 | 6 languages (EN/DE/IT/ES/FR/JA); math+code+STEM+chat+multilingual |
| Llama-Nemotron-VLM-Dataset-v1 | `nvidia/Llama-Nemotron-VLM-Dataset-v1` | — | CC-BY-4.0 | Vision-language model training |

---

### NVIDIA Safety & Privacy Datasets

| Dataset | HF path | Size | License | Notes |
|---|---|---|---|---|
| Aegis 2.0 | `nvidia/Aegis-AI-Content-Safety-Dataset-2.0` | — | CC-BY-4.0 | 12 hazard categories; Aegis 2.0 taxonomy |
| Nemotron-Safety-Guard-Dataset-v3 | `nvidia/Nemotron-Safety-Guard-Dataset-v3` | 514 K (626 MB) | CC-BY-4.0 | 12 languages; jailbreak + adversarial; CultureGuard pipeline; arXiv:2508.01710 |
| NemoGuard 1.0 | `nvidia/NemoGuard-1.0-ContentSafety` | — | Apache 2.0 | On-topic refusal patterns |
| Nemotron-PII | `nvidia/Nemotron-PII` | — | — | PII detection and redaction training |

---

### NVIDIA Persona Seeds (for synthetic data diversity)

| Dataset | HF path | Notes |
|---|---|---|
| Nemotron-Personas-USA | `nvidia/Nemotron-Personas-USA` | US demographic persona seeds |
| Nemotron-Personas-India | `nvidia/Nemotron-Personas-India` | India persona seeds |
| Nemotron-Personas-Japan | `nvidia/Nemotron-Personas-Japan` | Japan persona seeds |
| Nemotron-Personas-Brazil | `nvidia/Nemotron-Personas-Brazil` | Brazil persona seeds |
| Nemotron-Personas-France | `nvidia/Nemotron-Personas-France` | France persona seeds |
| Nemotron-Personas-Korea | `nvidia/Nemotron-Personas-Korea` | Korea persona seeds |
| Nemotron-Personas-Singapore | `nvidia/Nemotron-Personas-Singapore` | Singapore persona seeds |

Use these to diversify OSS-Instruct seeding (plan/10 §curate) — inject a random persona seed
into the teacher prompt to prevent distribution collapse from homogeneous synthetic data.

---

### NVIDIA Evaluation Datasets (do not train on these)

| Dataset | HF path | Notes |
|---|---|---|
| SPEED-Bench | `nvidia/SPEED-Bench` | Speed + quality combined eval |
| ComputeEval | `nvidia/compute-eval` | 566 CUDA kernel correctness + perf problems; Eval-only license |
| AceReason-Math | `nvidia/AceReason-Math` | Math reasoning eval |

---

### Qwen3.5 family (Alibaba, Feb 2026; "Towards Native Multimodal Agents")

Qwen3.5 is a **new architecture family**, not Qwen3 + increment. Architecture: hybrid Gated DeltaNet (SSM) + Gated Attention + sparse MoE. Native multimodal, 262K context (1M extensible), 201 languages, RL-trained across million-agent environments. Apache 2.0.

**Dense branch:** 0.8B (`Qwen3.5-0.8B-Base`), 2B, 4B, 9B — the four small models requested.  
**MoE branch:** `Qwen3.6-35B-A3B` (`qwen3_5_moe` arch ID), 122B, 397B.

Pre-training data composition not publicly detailed. Training approach inherited from Qwen3 + multimodal early fusion + million-agent RL environments.

| Stage | Tokens | Content mix (inferred from model card) |
|---|---|---|
| Pre-training | Large | Web, code, math, multimodal, 201 languages |
| Post-training | — | RL across million-agent environments; unified vision-language |
| Synthetic | — | Similar to Qwen3 synthetic pipeline |

> **Training data for Qwen3 (predecessor, arXiv:2505.09388):** Stage 1 = 30+ T (web, PDF, books, code, STEM; 119 languages); Stage 2 = 5 T (rebalanced, STEM/math/code emphasis); Synthetic = Qwen2.5-Math + Qwen2.5-Coder generated. Qwen3.5 likely extends this with multimodal data.

**Qwen-released datasets directly usable for Lamark:**

| Dataset | HF path | Size | License | Notes |
|---|---|---|---|---|
| DeepPlanning | `Qwen/DeepPlanning` | 1K–10K / 104 MB | Apache 2.0 | Multi-step planning with API calls; travel + shopping; proactive info acquisition; arXiv:2601.18137 |
| RationaleRM | `Qwen/RationaleRM` | 22 K train + 1 K test | CC BY 4.0 | Preference data with atomic rationales; GPT-5 generated rationales; 3 domains (general/stem/code); RM-Bench 87.1%; arXiv:2602.04649 |

**DeepPlanning** is directly relevant for Lamark's tool-use trajectory training (the
"proactive information acquisition" skill — making API calls to discover hidden state —
maps directly to Lamark's agent loop). Ingest as multi-turn Nemotron-Agentic-v1 conversations.

**RationaleRM** is useful for training a reward model or as a preference signal in DPO.
The `individual_preference.reasoning` field provides rationale-grounded labels that are
higher quality than simple preference rankings.

---

### Gemma 4 (Google DeepMind)

- Web documents, code, mathematics, images, audio; 140+ languages.
- Knowledge cutoff: January 2025.
- No public compositional breakdown. Open weights, Apache 2 license.
- HF: `google/gemma-4-27b-it`, `google/gemma-4-31b-it`.

---

## Anchor datasets (frozen, quarterly rebuild)

These are the datasets that stay fixed in every nightly blend to prevent forgetting. See plan/10 §Buffer + blend for ratios.

### General anchor

| Dataset | Size | HF path | License | Role |
|---|---|---|---|---|
| Tulu 3 SFT | ~10K sampled | `allenai/tulu-3-sft-mixture` | Apache 2.0 | Diverse instruction (IFEval, FLAN, code, math, multilingual) |
| OpenHermes 2.5 | 10K sampled | `teknium/OpenHermes-2.5` | MIT | General chat + instruction diversity |
| IFEval train | ~500 | `google/IFEval` | Apache 2.0 | Instruction-following compliance lock |
| NuminaMath-CoT | ~2K | `AI-MO/NuminaMath-CoT` | Apache 2.0 | Competition math reasoning |
| CodeFeedback-Filtered | ~2K | `m-a-p/CodeFeedback-Filtered-Instruction` | Apache 2.0 | Code instruction tuning |
| Internal gold set | ~1K | `GET /knowledge/eval_sets?tag=gold` | Proprietary | Project-specific task quality lock |

### Safety anchor

| Dataset | Size | HF path | License | Role |
|---|---|---|---|---|
| Aegis 2.0 | ~1.5K | `nvidia/Aegis-AI-Content-Safety-Dataset-2.0` | CC-BY-4.0 | Content safety refusals |
| HelpSteer3 | ~1K | `nvidia/HelpSteer3` | CC-BY-4.0 | Helpfulness + harmlessness preference |
| NemoGuard 1.0 | ~500 | `nvidia/NemoGuard-1.0-ContentSafety` | Apache 2.0 | On-topic refusal patterns |

---

## Instruction tuning (general)

For OSS-Instruct seeding and initial SFT if local traces are sparse.

| Dataset | Size | HF path | License | Format | Notes |
|---|---|---|---|---|---|
| ShareGPT | 112K convos | `anon8231489123/ShareGPT_Vicuna_unfiltered` | CC-BY-NC-4.0 | JSONL | Multi-turn human-ChatGPT |
| OpenHermes 2.5 | 1M | `teknium/OpenHermes-2.5` | MIT | JSONL | Diverse open-source mix |
| Tulu 3 SFT mix | ~326K | `allenai/tulu-3-sft-mixture` | Apache 2.0 | Parquet | Multi-source; IFEval + FLAN + code + math |
| UltraFeedback | 64K instr / 250K+ completions | `openbmb/UltraFeedback` | CC-BY-NC-4.0 | Parquet | Multi-dimensional RLHF; 6 source datasets |
| WizardLM Evol-Instruct | 196K+ | `WizardLMTeam/WizardLM_evol_instruct_V2_196k` | CC-BY-NC-4.0 | JSONL | Iterative instruction evolution |
| LIMA | 1K | `GAIR/lima` | CC-BY-NC-4.0 | JSONL | High-curation; quality > quantity reference |
| FLAN Collection | Large | `Muennighoff/flan` | Varies | JSONL | Zero-shot / few-shot / CoT task collection |

---

## Math & reasoning

For math anchor seeds and OSS-Instruct math seeding.

| Dataset | Size | HF path | License | Notes |
|---|---|---|---|---|
| GSM8K | ~8K | `openai/gsm8k` | MIT | Grade school word problems |
| MATH | ~7K | `lighteval/MATH` | MIT | Competition math |
| OpenMathInstruct-1 | 1.8M pairs | `nvidia/OpenMathInstruct-1` | CC-BY-4.0 | MATH + GSM8K solutions via Llama |
| OpenMathInstruct-2 | 14M pairs | `nvidia/OpenMathInstruct-2` | CC-BY-4.0 | Llama 3.1-405B-Instruct generated |
| NuminaMath-CoT | 860K pairs | `AI-MO/NuminaMath-CoT` | Apache 2.0 | Competition math + chain-of-thought |
| MetaMathQA | 395K | `meta-math/MetaMathQA` | MIT | CoT-augmented GSM8K + MATH |
| DeepMind Math | Large | `deepmind/math_dataset` | Apache 2.0 | Wide math domain coverage |

---

## Code

| Dataset | Size | HF path | License | Notes |
|---|---|---|---|---|
| CodeFeedback-Filtered | 156K (from 287K) | `m-a-p/CodeFeedback-Filtered-Instruction` | Apache 2.0 | High-quality code instruction |
| Magicoder-OSS-Instruct-75K | 75K | `ise-uiuc/Magicoder-OSS-Instruct-75K` | MIT | OSS reference-grounded code |
| Magicoder-Evol-Instruct-110K | 110K | `ise-uiuc/Magicoder-Evol-Instruct-110K` | MIT | Evolved code instruction diversity |
| CodeContests | ~13K problems | `deepmind/code_contests` | Apache 2.0 | AtCoder / Codeforces / CodeChef |
| APPS | ~10K problems | `codeparrot/apps` | MIT | LeetCode-style application problems |
| TACO | Large | `BAAI/TACO` | Apache 2.0 | Topic-organised algorithmic code |
| EvolveCoder | ~1.5M test cases | `MathCoderForge/EvolveCoder` | Apache 2.0 | TACO + APPS + Codeforces; RL-oriented |

---

## Bug-fix & program repair (InferredBugs-style)

Static-analysis-grounded bug-fix pairs and teacher-generated agent traces. Primary source
for the codebase extraction pipeline in [`plan/10c`](./10c-dataset-from-codebase.md).

| Dataset | Size | Source / HF path | License | Notes |
|---|---|---|---|---|
| **OpenThoughts-Agent-v1-SFT** | 15,209 rows / 110 MB | `open-thoughts/OpenThoughts-Agent-v1-SFT` | Apache 2.0 | nl2bash + InferredBugs teacher traces (GLM-4.6); Nemotron-Agentic-v1 format; 32-turn max, 64K context |
| **OpenThoughts-Agent-v1-RL** | ~720 tasks / 10 MB | `open-thoughts/OpenThoughts-Agent-v1-RL` | Apache 2.0 | nl2bash verified tasks; instruction+Dockerfile+pytest verifier triplets |
| **InferredBugs (Java)** | ~6,248 patches | `github.com/microsoft/InferredBugs/java/` | MIT | NPD + RL + TSV bugs from 6,200+ repos; Infer static analysis; pairs with `bug.json` + `commit_info.json` |
| **InferredBugs (C#)** | ~2,032 patches | `github.com/microsoft/InferredBugs/csharp/` | MIT | Same methodology; InferSharp |
| **SWE-Bench Verified** | 500 tasks | `princeton-nlp/SWE-bench_Verified` | MIT | GitHub issue → repo fix; evaluation target (decontaminate against this) |
| **SWE-Smith** | Large | `SWE-bench/SWE-smith` | Apache 2.0 | Synthetic SWE-bench-style tasks from CI/CD issue logs |

**Integration note:** `OpenThoughts-Agent-v1-SFT` can be used as a cold-start SFT corpus
when local Lamark traces are sparse. Ingest via `transform/trace_to_messages.py` (fields
map directly to Nemotron-Agentic-v1).

---

## Tool-use, function-calling & agentic trajectories

Most directly relevant to Lamark's trace format. Useful for cold-start when local traces are sparse, and for evaluating `tool_call_compliance`.

### NVIDIA agentic datasets

| Dataset | Size | HF path | License | Notes |
|---|---|---|---|---|
| Nemotron-SFT-Agentic-v2 | — | `nvidia/Nemotron-SFT-Agentic-v2` | CC-BY-4.0 | Multi-turn agent SFT; Nemotron-Agentic-v1 format; direct cold-start candidate |
| Nemotron-RL-Agentic-Function-Calling-Pivot-v1 | — | `nvidia/Nemotron-RL-Agentic-Function-Calling-Pivot-v1` | CC-BY-4.0 | Function-calling RL tasks with verifiers |
| Synthetic Tool Calling | 26.2 B tokens | part of `nvidia/Nemotron-Post-Training-v3` | — | Qwen3-235B + gpt-oss-120b generated; Hermes-format tool calls |

### Function-calling datasets

| Dataset | Size | HF path | License | Format | Notes |
|---|---|---|---|---|---|
| xLAM-function-calling-60k | 60K | `Salesforce/xlam-function-calling-60k` | CC-BY-4.0 | JSONL | De-facto standard for Qwen3/Hermes tool-call format; used by OpenThoughts + setup-guide |
| Hermes-Function-Calling-Thinking-V1 | — | `NousResearch/hermes-function-calling-thinking-v1` | Apache 2.0 | JSONL | Reasoning + tool-calling for thinking-mode models |
| glaive-function-calling-v2 | — | `glaiveai/glaive-function-calling-v2` | CC-BY-4.0 | JSONL | Multi-turn function-calling dialogues |

### General agentic trajectories

| Dataset | Size | Source / HF path | License | Format | Notes |
|---|---|---|---|---|---|
| ToolBench | 16,464 APIs / 49 cats | `ToolBench/ToolBench` | Apache 2.0 | JSONL | RapidAPI auto-generated; OpenAI tool-call format |
| ToolBench-R | Enhanced | `ToolBench/ToolBench` (R split) | Apache 2.0 | JSONL | Error feedback + self-reflection |
| StableToolBench | Simulated | `zhicheng-ye/StableToolBench` | Apache 2.0 | JSONL | Reproducible API simulation for eval |
| AgentInstruct | Growing | `microsoft/ToolBench` family | MIT | JSONL | Tool-use instruction generation |
| TOUCAN | 1.5M | `ServiceNow-AI/TOUCAN` | Apache 2.0 | JSONL | Real-world MCP environment trajectories — closest to our format |
| OSWorld | 369 desktop tasks | `xlang-ai/OSWorld` | Apache 2.0 | JSONL | Real computer environment trajectories (NeurIPS 2024) |
| τ-bench (TAU-Bench) | Dynamic | `sierra-research/tau-bench` | MIT | JSONL | Airline/retail tool-agent-user dialogue; eval + data |
| APIBench | OOD test | `ShishirPatil/gorilla` | Apache 2.0 | JSONL | ToolBench-derived generalisation eval |
| Qwen/DeepPlanning | 1K–10K / 104 MB | `Qwen/DeepPlanning` | Apache 2.0 | WebDataset | Multi-step planning with proactive API calls; travel + shopping; arXiv:2601.18137 |

**Integration note:** TOUCAN, OSWorld, and Nemotron-SFT-Agentic-v2 use OpenAI function-calling format compatible with Nemotron-Agentic-v1. Ingest via `transform/trace_to_messages.py`.

---

## RLHF & preference

For the weekly DPO cycle and reward model training.

| Dataset | Size | HF path | License | Notes |
|---|---|---|---|---|
| HelpSteer3 | — | `nvidia/HelpSteer3` | CC-BY-4.0 | NVIDIA pairwise + pointwise; used to train Qwen3-Nemotron-235B GenRM; best NVIDIA preference dataset |
| HelpSteer2 | — | `nvidia/HelpSteer2` | CC-BY-4.0 | Earlier NVIDIA preference data; multi-attribute scoring |
| Nemotron-RLHF-GenRM-v1 | — | `nvidia/Nemotron-RLHF-GenRM-v1` | CC-BY-4.0 | GenRM training data; generative reward model |
| Qwen/RationaleRM | 22K train + 1K test | `Qwen/RationaleRM` | CC BY 4.0 | Preference with atomic rationales (GPT-5 annotated); 3 domains; RM-Bench 87.1%; arXiv:2602.04649 |
| Anthropic HH-RLHF | 170K comparisons | `Anthropic/hh-rlhf` | CC-BY-NC-4.0 | Helpfulness + harmlessness; chosen/rejected pairs |
| Nectar | 183K prompts / 3.8M pairs | `berkeley-nest/Nectar` | CC-BY-NC-4.0 | 7-wise GPT-4 ranked; lmsys + ShareGPT + HH-RLHF + UltraFeedback + Evol |
| UltraFeedback (binarised) | 64K | `HuggingFaceH4/ultrafeedback_binarized` | CC-BY-NC-4.0 | Ready-to-use chosen/rejected; used by Tulu 3 |
| Capybara | Multi-turn 3+ | `LDJnr/Capybara` | CC-BY-4.0 | Long-context multi-turn; 1000+ token context |
| RewardBench | Eval only | `allenai/reward-bench` | Apache 2.0 | Reward model evaluation harness |

---

## Format quick-reference

### ChatML / Nemotron-Agentic-v1 (our canonical format)

```jsonl
{
  "uuid": "<sha256>",
  "messages": [
    {"role": "system",    "content": "…"},
    {"role": "user",      "content": "…"},
    {"role": "assistant", "content": "…", "reasoning_content": "…only on final turn…",
     "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "…", "arguments": "…"}}]},
    {"role": "tool",      "tool_call_id": "call_1", "content": "…"},
    {"role": "assistant", "content": "…"}
  ],
  "tools": [{"type": "function", "function": {"name": "…", "description": "…", "parameters": {…}}}],
  "reasoning": "on",
  "source": "cli",
  "rollout_id": "…",
  "reducer_version": "nemotron-agentic-v1"
}
```

### DPO pair format (dpo_pairs.jsonl)

```jsonl
{
  "pair_id": "<sha256>",
  "chosen":   {"trajectory_id": "<pair_id>_c", "messages": […]},
  "rejected": {"trajectory_id": "<pair_id>_r", "messages": […], "projection_method": "replay_without_recovery"},
  "admissibility": "grounded",
  "reducer_version": "nemotron-agentic-v1"
}
```

### Preference pair format (HH-RLHF / Nectar / UltraFeedback)

```jsonl
{"chosen": [{"role": "user", "content": "…"}, {"role": "assistant", "content": "…"}],
 "rejected": [{"role": "user", "content": "…"}, {"role": "assistant", "content": "…"}]}
```

Most preference datasets ship as Parquet on HuggingFace. The `quality/` module normalises all into the chosen/rejected schema before the DPO trainer sees them.

---

## Evaluation benchmarks

Used for eval-gate and ongoing monitoring. Do NOT train on these.

### NVIDIA Nemotron evaluation suite

Official benchmarks from the Nemotron-3-Nano evaluation recipe (HF blog, NeMo Evaluator):

| Benchmark | Score (Nano 30B-A3B) | Category | Notes |
|---|---|---|---|
| BFCL v4 | 53.8% | Function Calling | Primary tool-call quality gate; HF `gorilla-llm/Berkeley-Function-Calling-Leaderboard` |
| LiveCodeBench v6 | 68.3% | Coding | Aug 2024–May 2025 window; HF `livecodebench/code_generation_lite` |
| MMLU-Pro | 78.3% | Knowledge | Multi-domain knowledge; HF `TIGER-Lab/MMLU-Pro` |
| GPQA Diamond | 73.0% | Science | Graduate-level science Q&A; HF `Idavidrein/gpqa` |
| AIME 2025 | 89.1% | Mathematics | AMC/AIME competition math |
| SciCode | 33.3% | Scientific Coding | End-to-end scientific code generation |
| IFBench | 71.5% | Instruction Following | Format + instruction compliance |
| HLE (Humanity's Last Exam) | 10.6% | Frontier Knowledge | Extremely hard multi-domain; near-human expert level |

**Eval tooling:** NeMo Evaluator SDK (`github.com/NVIDIA-NeMo/Evaluator`) + NeMo Skills + LM Evaluation Harness. Config via YAML; results in `results.json`; judge-based scoring requires `JUDGE_API_KEY`. See HF blog `nvidia/nemotron-3-nano-evaluation-recipe` for the full reproducibility recipe.

### Agent capability benchmarks

| Benchmark | Tasks | HF path | Notes |
|---|---|---|---|
| **OpenThoughts-TBLite** | 100 | `open-thoughts/OpenThoughts-TBLite` | Terminal agent; Pearson r=0.911 with TB2; 2.6–8× faster than TB2; calibrated with Claude Haiku 4.5; 9 domains; Easy/Med/Hard/Extreme tiers |
| **OpenThoughts-TB-Dev** | 70 | `open-thoughts/OpenThoughts-TB-dev` | Dev proxy; correlates with TBLite + TB2; used for ablation (15 instruction sources, teacher model study) |
| Terminal-Bench 2.0 | Full | `terminal-bench/terminal-bench` | Gold standard for terminal agents; slow (~10 h for 8B) |
| SWE-Bench Verified | 500 | `princeton-nlp/SWE-bench_Verified` | Primary SWE quality gate; Nano baseline 0.7%, OpenThinker-Agent-v1 15.7% |
| SPEED-Bench | — | `nvidia/SPEED-Bench` | Speed + quality combined; NVIDIA official |
| AceReason-Math | — | `nvidia/AceReason-Math` | Math reasoning eval |

### Standard capability benchmarks

| Benchmark | HF path | Notes |
|---|---|---|
| MMLU | `cais/mmlu` | General knowledge; primary forgetting-probe benchmark |
| MMLU-Pro | `TIGER-Lab/MMLU-Pro` | Harder multi-domain; Nemotron eval suite |
| GSM8K | `openai/gsm8k` | Grade school math |
| HumanEval | `openai/openai_humaneval` | Code generation pass@1 |
| MBPP | `google-research-datasets/mbpp` | Python programming |
| IFEval | `google/IFEval` | Instruction following compliance |
| MT-Bench | — | Multi-turn quality; GPT-4 judge |
| Arena-Hard | — | Hard instruction following |
| BFCL v3/v4 | `gorilla-llm/Berkeley-Function-Calling-Leaderboard` | Tool calling |
| LiveCodeBench v5/v6 | `livecodebench/code_generation_lite` | Live competitive coding |
| GPQA Diamond | `Idavidrein/gpqa` | Science; graduate-level |
| AIME 2025 | — | Competition math |
| HLE | — | Frontier multi-domain |
| SciCode | — | Scientific code generation |
| SWE-Bench Lite-50 | `princeton-nlp/SWE-bench` | Faster SWE eval (50-task subset) |
| ComputeEval | `nvidia/compute-eval` | CUDA kernel correctness + perf (566 problems; eval-only license — no training) |

**Recommendation:** Use OpenThoughts-TBLite as the fast nightly eval proxy for agent capability. Add `tblite_100: { drop_pct_max: 3 }` to `eval/thresholds.yaml` once a baseline is established (currently commented-out in SPEC.md §9).

---

## Dataset decontamination targets

Any sample with ≥ 13-gram overlap against these evaluation sets is dropped before blending.

| Eval set | HF path |
|---|---|
| MMLU | `cais/mmlu` |
| MMLU-Pro | `TIGER-Lab/MMLU-Pro` |
| GSM8K | `openai/gsm8k` |
| HumanEval | `openai/openai_humaneval` |
| MBPP | `google-research-datasets/mbpp` |
| LiveCodeBench | `livecodebench/code_generation_lite` |
| SWE-Bench Verified | `princeton-nlp/SWE-bench_Verified` |
| BFCL v4 | `gorilla-llm/Berkeley-Function-Calling-Leaderboard` |
| IFEval | `google/IFEval` |
| GPQA | `Idavidrein/gpqa` |
| OpenThoughts-TBLite | `open-thoughts/OpenThoughts-TBLite` |
| OpenThoughts-TB-Dev | `open-thoughts/OpenThoughts-TB-dev` |
| IFBench | — |
| AceReason-Math | `nvidia/AceReason-Math` |
