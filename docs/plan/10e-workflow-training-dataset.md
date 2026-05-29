# 10e — Workflow training dataset

> Training data to teach the model to write orchestration plans and reason
> about multi-agent task decomposition.
>
> Three sources: (1) synthetic plan generation, (2) real complex traces
> decomposed post-hoc, (3) SWE-Bench / InferredBugs multi-file tasks
> reframed as workflow problems.

**See also:** [`plan/05e`](./05e-dynamic-workflows.md) (engine spec), [`plan/10c`](./10c-dataset-from-codebase.md) (codebase dataset), [`plan/10`](./10-training-pipeline.md) (nightly pipeline).

---

## What the model needs to learn

The model must learn three distinct behaviours:

| Behaviour | Training signal |
|---|---|
| **Recognise** a task that benefits from parallel decomposition | Classification: "workflow-worthy" vs "single-agent" |
| **Write** a valid `WorkflowPlan` JSON (title, phases, tasks, modes, deps) | SFT on plan-generation examples |
| **Synthesise** results from parallel subagent outputs into a final answer | SFT on synthesis examples |

---

## Dataset 1 — Synthetic plan generation (teacher: gpt-5.4-mini, configurable)

### 1.1 Seed tasks

Collect task descriptions that are inherently parallel or decomposable:

| Seed source | How to extract | Example |
|---|---|---|
| SWE-Bench issues | Issues with `> 3` files changed | "Fix authentication across auth/, tests/, docs/" |
| InferredBugs multi-file | Pairs with `files_changed ≥ 3` | Bug spans 3 modules |
| Internal traces | Sessions with `> 8` tool calls and `> 3` distinct file paths | Complex refactor sessions |
| Hermes SkillsBench | Multi-domain tasks (Data Analysis, Doc Processing) | "Analyse 15 CSV files and summarise" |
| OpenThoughts-TBLite | Hard/Extreme tasks (10–39% / <10% reference pass rate) | Complex terminal tasks |

### 1.2 Teacher prompt (plan generation)

```
SYSTEM: You are an expert at decomposing complex software tasks into parallel
        execution plans. Output a valid WorkflowPlan JSON object.

USER:   Task: {seed_task_description}

        Available tools: {tool_list}
        Max concurrency: 16
        Max context per subagent: 32K tokens

        Write a WorkflowPlan JSON that parallelises this task efficiently.
        Rules:
        - Use parallel mode whenever subtasks are independent
        - Use pipeline mode when output A feeds input B
        - Use sequential only when strict ordering is required
        - Each task prompt must be self-contained (include all needed context)
        - Prefer 5-20 tasks total; more is fine for large-item-list tasks

ASSISTANT: <WorkflowPlan JSON>
```

### 1.3 Quality filter

- Valid JSON that parses against the `WorkflowPlan` schema: required
- At least 2 tasks: required
- At least one `parallel` phase: required (otherwise it's not a workflow)
- Two-judge consensus (gpt-5.4-mini + claude): both must rate the plan ≥ 4/5 on decomposition quality
- Dedup: embedding cosine < 0.90 against existing plans

### 1.4 Sample schema (one training example)

```json
{
  "uuid": "<sha256>",
  "source": "workflow_plan_synthetic",
  "seed_task_id": "<from InferredBugs / SWE-Bench / etc.>",
  "teacher_model": "gpt-5.4-mini",
  "messages": [
    { "role": "system", "content": "You are an expert at decomposing..." },
    { "role": "user",   "content": "Task: <seed task description>..." },
    { "role": "assistant", "content": null,
      "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": { "name": "workflow_plan",
                      "arguments": "<WorkflowPlan JSON>" }
      }]
    }
  ],
  "tools": [{ "type": "function", "function": { "name": "workflow_plan", ... }}],
  "reasoning": "on"
}
```

---

## Dataset 2 — Real trace decomposition (post-hoc)

For each complex existing trace (> 8 tool calls, > 3 distinct file paths),
reconstruct what a workflow plan COULD have looked like:

```python
def decompose_trace(trace: Conversation) -> WorkflowTrainingPair:
    """
    Given a real agent trace, generate a synthetic workflow plan that
    would have produced the same result with parallel execution.
    Steps:
    1. Extract tool call clusters (file reads, edits, searches by target)
    2. Identify parallel clusters (no data dep between them)
    3. Generate a WorkflowPlan that maps clusters to tasks
    4. Teacher model validates the plan makes sense
    """
```

This gives "grounded" examples where the correct answer is validated against
real execution outcomes.

---

## Dataset 3 — Synthesis training

Teach the model to synthesize results from parallel subagent outputs.

### 3.1 Positive examples

From completed workflow executions (after the engine is running):
- `workflow_manifest.json` gives the phase structure
- Each task's `ToolResult` is a "parallel input"
- The final synthesis agent's response is the label

### 3.2 Synthetic synthesis examples (cold-start)

Before the engine produces real data, generate synthetic examples:

```
SYSTEM: You are synthesising the results of {N} parallel sub-investigations.

USER:   Sub-task results:
        [1] {task_1_result}
        [2] {task_2_result}
        ...
        [N] {task_N_result}

        Synthesise these into a single coherent response for the user.

ASSISTANT: <synthesis>
```

Seed from SWE-Bench multi-file resolutions, TOUCAN multi-step traces,
and MUSE SkillsBench cross-domain results.

---

## Dataset 4 — Workflow-recognition (classification)

Fine-tune the model to recognise when a task is workflow-worthy vs single-agent.

| Label | Examples |
|---|---|
| `workflow` | "Audit all 47 crates", "Analyse every PR from last month", "Find all uses of X across the codebase" |
| `single_agent` | "Fix the null pointer in parser.rs", "Write a unit test for this function", "Summarise this PR" |

Binary classification; training examples balanced 50/50.
Generated by teacher model from diverse task descriptions; validated by hand on 200 examples.

---

## Data mixing (integration with nightly pipeline)

Workflow training data feeds into the nightly blend as a sub-bucket of the
"today's new task data" 65% slice. It does NOT need its own anchor — the
general anchor (Tulu 3 + OpenHermes) already covers instruction-following.

Recommended share of the new-data bucket: **10–15%** workflow examples.
Too much → model starts proposing workflows for trivial tasks.
Too little → model never learns the planning behaviour.

```toml
# blend_config.toml addition
[blend.workflow]
enabled = true
share_of_new_data_pct = 12   # 12% of the 65% new-data bucket
sources = [
  "~/.lamark/training/raw/workflow_plan_synthetic.jsonl",
  "~/.lamark/training/raw/workflow_decomposed.jsonl",
  "~/.lamark/training/raw/workflow_synthesis.jsonl",
  "~/.lamark/training/raw/workflow_recognition.jsonl",
]
```

---

## Evaluation

| Metric | Threshold | How measured |
|---|---|---|
| Plan schema validity | 100% | JSON schema validation |
| Phase utilisation | ≥ 1 parallel phase per plan | Structural check |
| Judge quality score | ≥ 4/5 (both judges) | Two-judge consensus |
| Workflow recognition accuracy | ≥ 90% | Held-out 200-example test set |
| Synthesis coherence | ≥ 4/5 MT-Bench judge | GPT-5 judge on held-out set |
| Regression: single-agent tasks | MMLU-Pro drop < 1 pp | Eval gate |

Add to `eval/thresholds.yaml`:
```yaml
workflow_recognition: { min_accuracy: 0.90 }
workflow_plan_validity: { min_rate: 1.0 }
```

---

## Connectors in `lamark_trainer/connectors/`

```
connectors/
└── workflow/
    ├── seed_collector.py       # collect workflow-worthy tasks from SWE-Bench/InferredBugs/traces
    ├── plan_generator.py       # teacher-generate WorkflowPlan JSON (gpt-5.4-mini default)
    ├── trace_decomposer.py     # post-hoc decompose complex existing traces
    ├── synthesis_generator.py  # generate synthesis training examples
    └── recognition_labeller.py # binary workflow vs single-agent labelling
```

---

## Dataset sizes (expected)

| Source | Raw | After filter | Training examples |
|---|---|---|---|
| Synthetic plan generation | 5K | 2–3K | 2–3K |
| Real trace decomposition | varies (nightly) | ~500/month | ~500/month |
| Synthesis synthetic | 2K | 1.5K | 1.5K |
| Recognition | 2K balanced | 2K | 2K |
| **Total initial** | ~9K | ~6K | **~6K** |

~6K workflow examples is sufficient for cold-start. After the engine is running
and producing real workflow traces, the real-decomposition source grows
automatically and the synthetic share can be reduced.
