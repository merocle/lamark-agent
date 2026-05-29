# 10c — Dataset from codebase (InferredBugs × OpenThoughts)

> Operational spec for building SFT and RL training datasets from any git repository
> (with optional merge-request enrichment).
>
> Methodology: Microsoft's InferredBugs static-analysis extraction (arXiv:2303.07263)
> crossed with the OpenThoughts-Agent-v1 teacher-trace + verifier pipeline.

**Status:** Draft v0.1 — 2026-05-29  
**See also:** [`plan/10`](./10-training-pipeline.md) for nightly orchestration; [`plan/10b`](./10b-dataset-catalog.md) for the full dataset catalog.

---

## Why this approach

The InferFix paper (ESEC/FSE 2023) showed that running static analysis on consecutive commit
pairs surfaces high-quality bug-fix pairs that keyword search on commit messages completely
misses — only 3–59% of fixes have relevant keywords in the commit message. The static
analyzer finds what text search can't.

OpenThoughts-Agent-v1 used InferredBugs directly in their SFT dataset and demonstrated that
converting those bug-fix pairs into teacher-generated agent traces produces state-of-the-art
performance for 8B models: 15.7% on SWE-Bench Verified vs 0.7% for the Qwen3-8B baseline.

Key empirical findings that constrain the design here:
- **Teacher model family matters more than model size.** GLM-4.6 as teacher gave ~2× downstream
  improvement on Terminal-Bench vs GPT-family teachers at comparable compute cost (OpenThoughts).
- **RL gives modest incremental gain over SFT.** SFT-only achieves 16.1% on TB-Dev;
  SFT+RL achieves 17.3% (+1.2 pp). Fix the data first, run RL second.
- **~15K SFT traces** (nl2bash + InferredBugs) was sufficient to fine-tune Qwen3-8B to
  top-of-class performance. More is better; more quality is more important than more quantity.

---

## Four-phase pipeline

```
Phase 1 — Extract       Phase 2 — Enrich        Phase 3 — Teach         Phase 4 — RL tasks
git history + static  →  MR/PR context (opt.) →  teacher traces (SFT)  →  task triplets (GRPO)
analyzer diffs            multi-turn iteration     Nemotron-Agentic-v1     instruction + Docker
                          history from reviews     format                  + pytest verifier
```

---

## Phase 1 — Bug-fix pair extraction from git history

### 1.1 Static analyzer matrix

| Language | Primary analyzer | Secondary | Output format |
|---|---|---|---|
| Rust | `cargo check --message-format=json` | `cargo clippy -- -D warnings` | JSON diagnostics |
| Python | `mypy --output=json` | `ruff check --output-format=json` | JSON |
| TypeScript / JS | `tsc --noEmit 2>&1` + `eslint --format=json` | — | mixed; parse both |
| Java | `infer run -- mvn compile` + `infer report` (AGPL: subprocess only) | `checkstyle` | JSON |
| C# | `dotnet build --verbosity normal 2>&1` + InferSharp | Roslyn analyzers | mixed |
| Go | `go vet -json ./...` | `staticcheck -f json ./...` | JSON |
| Kotlin | `./gradlew detekt --report xml` | Kotlin compiler warnings | XML / JSON |

> **AGPL note:** Infer (Meta) is AGPL-3.0. Run as subprocess only — never import as a library.
> This matches the existing TruffleHog constraint (load-bearing invariant 8 in CLAUDE.md).

### 1.2 Core extraction algorithm

```python
def extract_bug_fix_pairs(repo_path: Path, since: datetime) -> Iterable[BugFixPair]:
    for parent_sha, child_sha in iter_commit_pairs(repo_path, since):
        # Skip merge commits
        if is_merge_commit(repo_path, child_sha):
            continue

        with temp_worktree(repo_path, parent_sha) as parent_dir:
            parent_diag = run_analyzers(parent_dir, detect_language(repo_path))

        with temp_worktree(repo_path, child_sha) as child_dir:
            child_diag  = run_analyzers(child_dir,  detect_language(repo_path))

        fixed = diff_diagnostics(parent_diag, child_diag)   # in parent, absent in child
        if not fixed:
            continue

        diff         = get_diff(repo_path, parent_sha, child_sha)
        commit_msg   = get_commit_message(repo_path, child_sha)

        if not passes_quality_filter(diff, commit_msg):
            continue

        yield BugFixPair(
            pair_id       = sha256(f"{repo_path}:{parent_sha}:{child_sha}:{fixed[0].location}"),
            repo          = str(repo_path),
            language      = detect_language(repo_path),
            parent_sha    = parent_sha,
            child_sha     = child_sha,
            commit_message= commit_msg,
            fixed_diagnostics = fixed,
            buggy_context = extract_context(parent_dir, fixed, window_lines=20),
            diff          = diff,
            ewash_context = extract_ewash(parent_dir, fixed),  # extended syntax hierarchy
        )
```

### 1.3 Quality filter

```python
def passes_quality_filter(diff: str, commit_msg: str) -> bool:
    lines_changed = count_diff_lines(diff)
    files_changed = count_diff_files(diff)
    return (
        1     <= lines_changed   <= 200 and   # targeted fix, not a refactor
        1     <= files_changed   <= 5   and   # not a multi-file reorganization
        len(commit_msg.strip())  >= 20  and   # has a real commit message
        not is_generated_file(diff)           # skip auto-generated files
    )
```

Additional post-extraction filters:
- **Build reproducibility:** re-run analyzer on parent in a clean container; discard if the
  diagnostic doesn't appear (flaky environment).
- **Location precision:** the fixed diagnostic must map to a specific file+line; discard if
  only a module-level warning.
- **Dedup:** identical `(repo, parent_sha, diagnostic_code, file, line)` tuples are dropped.

### 1.4 Sample schema — `bug_fix_pairs.jsonl`

```json
{
  "pair_id": "a3f8c2d...",
  "repo": "https://github.com/org/repo",
  "language": "rust",
  "parent_sha": "abc123",
  "child_sha": "def456",
  "commit_message": "Fix borrow conflict when parsing empty input",
  "diagnostics_fixed": [
    {
      "analyzer": "cargo-check",
      "code": "E0502",
      "message": "cannot borrow `buf` as mutable because it is also borrowed as immutable",
      "file": "src/parser.rs",
      "line": 42,
      "column": 8,
      "severity": "error"
    }
  ],
  "buggy_context": {
    "file": "src/parser.rs",
    "content": "… full file content at parent commit …",
    "range": { "start_line": 22, "end_line": 62 }
  },
  "diff": "--- a/src/parser.rs\n+++ b/src/parser.rs\n@@ -42 +42 @@\n…",
  "ewash_context": {
    "file": "src/parser.rs",
    "module": "parser",
    "struct": null,
    "function": "parse_input",
    "imports": ["use std::io::{BufReader, Read};"]
  },
  "source": "git_history"
}
```

---

## Phase 2 — MR/PR enrichment (optional)

When merge-request / pull-request data is available, enrich each bug-fix pair with the
full review trajectory. This converts single-turn bug fixes into multi-turn agent episodes
that model the edit → review → revise loop.

### 2.1 Platform API mapping

| Platform | Commit → MR/PR API |
|---|---|
| GitHub | `GET /repos/{owner}/{repo}/commits/{sha}/pulls` |
| GitLab | `GET /projects/{id}/repository/commits/{sha}/merge_requests` |
| Bitbucket | `GET /repositories/{ws}/{slug}/commits/{sha}/pullrequests` |
| Azure DevOps | `GET /{org}/{project}/_apis/git/repositories/{repo}/commits/{sha}/pullRequests` |
| JetBrains Space | `GET /api/http/projects/{project}/code-reviews?commitId={sha}` |

Cache all API responses locally (`~/.lamark/training/mr_cache/`). Never re-request a
commit whose MR data is already cached.

### 2.2 Trajectory schema — `mr_enriched.jsonl`

```json
{
  "pair_id": "a3f8c2d...",
  "mr_id": "github:org/repo#123",
  "issue_text": "Memory leak when parse error occurs before file handle is closed",
  "pr_description": "Wrap handle in RAII guard so it is always closed on error path",
  "iterations": [
    {
      "iteration": 1,
      "diff": "…",
      "reviewer_comments": [
        {
          "author": "reviewer_a",
          "file": "src/io.rs",
          "line": 88,
          "body": "Should this use BufReader? The raw File handle leaks on panic."
        }
      ]
    },
    {
      "iteration": 2,
      "diff": "…",
      "reviewer_comments": []
    }
  ],
  "merged": true,
  "approvals": 2
}
```

Drop MR-enriched pairs where:
- MR was closed without merge (`merged: false`)
- MR body is empty and no linked issue exists
- All reviewer comments are style-only (heuristic: comment body < 15 words and no file/line ref)

---

## Phase 3 — SFT dataset: teacher trace generation

### 3.1 Task prompt

```python
TASK_TEMPLATE = """\
You are a senior {language} engineer. A static analysis tool reported:

**File:** `{file}`  **Line:** {line}  **Bug:** `{code}` — {message}

**Surrounding code (lines {start}–{end}):**
```{language}
{buggy_context}
```
{mr_context_block}
Identify the root cause, apply the minimal correct fix, and verify it with the available tools.
"""

MR_CONTEXT_BLOCK = """\

**PR description:** {pr_description}
**Reviewer feedback (iteration 1):** {reviewer_comments_text}
"""
```

For MR-enriched pairs, include the first-iteration reviewer comment as additional context.
For bare bug-fix pairs, omit the MR block entirely.

### 3.2 Teacher model selection

OpenThoughts-Agent finding: **teacher model family determines trace quality more than model
size**. GLM-4.6 gave ~2× downstream improvement on Terminal-Bench vs any GPT-family teacher.

| Priority | Teacher | Notes |
|---|---|---|
| **1 — default** | `THUDM/GLM-4.6-AWQ` (`QuantTrio/GLM-4.6-AWQ`) | Highest agentic trace quality per OpenThoughts; host via vLLM |
| 2 | `claude-opus-4-x` | Best reasoning traces; use via Anthropic API |
| 3 | `gpt-5` | Fallback; rotate monthly to prevent distribution collapse |

**Rotate teachers quarterly** — mixing distribution prevents the model from learning one
teacher's stylistic artifacts. Never use the same teacher for two consecutive monthly merge cycles.

### 3.3 Trace generation config

```python
TEACHER_CONFIG = {
    "model":              "glm-4.6",
    "provider":           "hosted_vllm",
    "base_url":           "http://localhost:8001/v1",
    "max_turns":          32,               # OpenThoughts-Agent-v1 default
    "max_context_length": 64_000,
    "temperature":        0.7,
    "top_p":              0.9,
    "tools": [                              # tools exposed to the teacher agent
        "read_file",
        "write_file",
        "patch",                            # apply a unified diff
        "run_terminal",                     # run tests / linter in repo container
        "search_codebase",                  # grep / ripgrep
        "view_diagnostics",                 # re-run analyzer on current workspace state
    ],
    "harness":  "terminus-2",              # OpenThoughts harness; Harbor as alternative
    "verifier": "re_run_analyzer",         # verifier used to label trace pass/fail
}
```

### 3.4 Three-stage trace quality filter (mandatory, from OpenThoughts-Agent)

```python
def filter_sft_traces(traces: list[Trace]) -> list[Trace]:
    # Stage 1: bad verifier → discard if non-deterministic across 3 runs
    traces = [t for t in traces if verifier_is_deterministic(t, runs=3, timeout_s=60)]

    # Stage 2: environment stability → discard if Docker is too slow
    traces = [t for t in traces if
              docker_build_time(t)    < 120 and   # seconds
              docker_teardown_time(t) < 30]

    # Stage 3: difficulty filter → keep only tasks a strong reference model can solve
    # (filters un-learnable tasks; OpenThoughts: use GPT-5 / Claude Opus as reference)
    traces = [t for t in traces if reference_model_can_solve(t, model="claude-opus-4")]

    return traces
```

Difficulty calibration (from OpenThoughts-TBLite methodology):
- **Easy** (reference pass rate ≥ 70%) — include in SFT
- **Medium** (40–69%) — include in both SFT and RL
- **Hard** (10–39%) — include in RL only
- **Extreme** (< 10%) — discard (no learning signal)

### 3.5 SFT trace format — Nemotron-Agentic-v1 extension

```json
{
  "uuid": "<sha256>",
  "source": "inferredbug_trace",
  "pair_id": "<from Phase 1>",
  "repo": "https://github.com/org/repo",
  "language": "rust",
  "bug_code": "E0502",
  "teacher_model": "glm-4.6",
  "teacher_version": "glm-4.6-awq-2025q2",
  "verifier_passed": true,
  "difficulty": "medium",
  "reference_pass_rate": 0.58,
  "messages": [
    {
      "role": "system",
      "content": "You are a senior Rust engineer. # Tools\n…tool schemas…"
    },
    {
      "role": "user",
      "content": "<rendered TASK_TEMPLATE>"
    },
    {
      "role": "assistant",
      "reasoning_content": "<think>The borrow checker error at line 42 means…</think>",
      "content": "Let me read the full file first to understand the context.",
      "tool_calls": [{"id": "call_1", "type": "function",
                      "function": {"name": "read_file", "arguments": "{\"path\":\"src/parser.rs\"}"}}]
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "…file content…"},
    {
      "role": "assistant",
      "content": "I see the conflicting borrows. Applying the fix.",
      "tool_calls": [{"id": "call_2", "type": "function",
                      "function": {"name": "patch", "arguments": "{…}"}}]
    },
    {"role": "tool", "tool_call_id": "call_2", "content": "Patch applied successfully."},
    {
      "role": "assistant",
      "content": "Fixed. Introduced a new block scope to end the immutable borrow before the mutable one begins."
    }
  ],
  "tools": ["read_file", "write_file", "patch", "run_terminal", "search_codebase", "view_diagnostics"],
  "reasoning": "on",
  "used_in": ["nemotron_v3", "qwen3_6"],
  "reducer_version": "nemotron-agentic-v1"
}
```

---

## Phase 4 — RL dataset: task triplets with verifiers

Each RL task is a triplet: `instruction.md` + `Dockerfile` + `verifier.py`.
This is the exact format used by OpenThoughts-Agent-v1-RL and is natively supported by
Harbor + SkyRL.

### 4.1 `instruction.md`

```markdown
# Fix: {bug_code} in {file}

A static analysis tool reported the following bug in this repository:

| Field | Value |
|-------|-------|
| File | `{file}` |
| Line | {line} |
| Bug type | `{bug_code}` |
| Message | {diagnostic_message} |

## Context

```{language}
{buggy_context_±20_lines}
```

## Your task

1. Identify the root cause of the bug.
2. Apply the minimal correct fix.
3. Verify your fix: run `cargo check` (or the equivalent for the language) and confirm
   the diagnostic is gone and no new errors were introduced.

Use available tools to read related files before editing.
```

### 4.2 `Dockerfile` template

```dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y git curl build-essential

# --- Language toolchain (Rust example; substitute per language) ---
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
ENV PATH="/root/.cargo/bin:${PATH}"

# --- Checkout repo at the BUGGY commit ---
RUN git clone {repo_url} /workspace
WORKDIR /workspace
RUN git checkout {parent_sha}

# --- Confirm the bug is reproducible (fail image build if it isn't) ---
RUN cargo check 2>&1 | grep -q "{diagnostic_message_fragment}" || \
    (echo "ERROR: bug not reproducible at {parent_sha}; discarding task" && exit 1)
```

Per-language toolchain substitutions:

| Language | Toolchain block |
|---|---|
| Python | `RUN pip install mypy ruff` |
| TypeScript | `RUN npm install -g typescript eslint` |
| Java | `RUN apt-get install -y default-jdk maven` (+ Infer via subprocess) |
| Go | `RUN curl -L go.dev/dl/go1.22.linux-amd64.tar.gz | tar -C /usr/local -xz; ENV PATH=$PATH:/usr/local/go/bin` |

### 4.3 `verifier.py` template

```python
"""
Pytest verifier for RL tasks. Runs static analysis on /workspace
and asserts the specific bug is gone without introducing new errors.
"""
import subprocess, json, pytest

BUG_CODE     = "{bug_code}"     # e.g. "E0502"
BUG_FILE     = "{file}"         # e.g. "src/parser.rs"
BUG_LINE_MIN = {line} - 5
BUG_LINE_MAX = {line} + 5
ANALYZER_CMD = {analyzer_cmd}   # e.g. ["cargo", "check", "--message-format=json"]

def run_analysis():
    r = subprocess.run(ANALYZER_CMD, capture_output=True, text=True, cwd="/workspace")
    return parse_diagnostics(r.stdout + r.stderr)   # list of {code, file, line, level}

def test_target_bug_fixed():
    """The specific diagnostic that was present in the buggy commit must be gone."""
    diags = run_analysis()
    still_present = any(
        d["code"] == BUG_CODE
        and d["file"].endswith(BUG_FILE)
        and BUG_LINE_MIN <= d.get("line", 0) <= BUG_LINE_MAX
        for d in diags
    )
    assert not still_present, f"Bug {BUG_CODE} still present in {BUG_FILE} near line {BUG_LINE_MIN}–{BUG_LINE_MAX}"

def test_no_new_errors():
    """The fix must not introduce new compiler errors."""
    errors = [d for d in run_analysis() if d.get("level") == "error"]
    assert len(errors) == 0, f"Fix introduced {len(errors)} new error(s): {errors[:3]}"
```

### 4.4 RL task filtration (three-stage, mandatory)

Same three-stage filter as Phase 3. Applied again here because the RL task set is built
independently of the SFT trace set and may include pairs that weren't teacher-traced.

```python
def filter_rl_tasks(tasks: list[RLTask]) -> list[RLTask]:
    # Stage 1: determinism — run verifier 3× on the ground-truth fix; must pass all 3
    tasks = [t for t in tasks if verifier_deterministic(t, runs=3, timeout_s=60)]

    # Stage 2: environment stability
    tasks = [t for t in tasks if
             docker_build_time(t)    < 120 and
             docker_teardown_time(t) < 30]

    # Stage 3: difficulty — discard extreme tasks (< 10% reference pass rate)
    # and trivial tasks (> 85% reference pass rate, too easy for RL signal)
    tasks = [t for t in tasks if 0.10 <= reference_pass_rate(t) <= 0.85]

    return tasks
```

### 4.5 RL task schema — `rl_tasks.jsonl`

```json
{
  "task_id": "<sha256>",
  "pair_id": "<from Phase 1>",
  "source": "inferredbug_rl",
  "repo": "https://github.com/org/repo",
  "language": "rust",
  "bug_code": "E0502",
  "instruction_path": "tasks/{task_id}/instruction.md",
  "dockerfile_path":  "tasks/{task_id}/Dockerfile",
  "verifier_path":    "tasks/{task_id}/verifier.py",
  "difficulty": "medium",
  "reference_pass_rate": 0.58,
  "verifier_deterministic": true,
  "docker_build_seconds": 42,
  "filter_stages_passed": [1, 2, 3]
}
```

### 4.6 GRPO group generation (Tier 3 activation)

For each RL task, generate N=8 agent trajectories from the current model checkpoint.
Requires at least 1 pass and 1 fail per group for a useful gradient signal.

```python
def generate_grpo_group(task: RLTask, model, n: int = 8) -> GRPOGroup | None:
    trajectories = [
        run_agent_on_task(model, task, temperature=0.8)
        for _ in range(n)
    ]
    rewards = [float(run_verifier(task, traj)) for traj in trajectories]  # 0.0 or 1.0

    # Skip homogeneous groups (all pass or all fail — no gradient signal)
    if all(r == rewards[0] for r in rewards):
        return None

    return GRPOGroup(
        task_id      = task.task_id,
        trajectories = trajectories,
        rewards      = rewards,
        group_pass_rate = sum(rewards) / n,
    )
```

GRPO hyperparameters (Tier 3):
- `lr = 1e-6` (much lower than SFT; forgetting is catastrophic at higher LR)
- `kl_coeff = 0.1`, `clip_ratio = 0.2`
- `n_generations = 8` per prompt
- Dataset: `nvidia/Nemotron-3-Nano-RL-Training-Blend` (Nemotron path)
- Framework: NeMo RL (Nemotron) or TRL GRPO (Qwen3.6)

---

## End-to-end runbook for any git + MR repo

### Prerequisites

```bash
# Python packages
pip install gitpython presidio-analyzer presidio-anonymizer spacy gliner datasketch
python -m spacy download en_core_web_lg

# Per-language toolchains on the extraction host or in Docker:
# Rust: rustup + cargo (stable)
# Python: mypy + ruff
# TypeScript: tsc + eslint
# Java: JDK + Maven + Infer (AGPL: subprocess only)
# Go: go + staticcheck
```

### Config — `dataset_config.yaml`

```yaml
repo:
  url:               "https://github.com/your-org/your-repo"
  local_path:        "/data/repos/your-repo"
  default_branch:    "main"
  mr_source:         "github"       # github | gitlab | bitbucket | azuredevops | space | none
  mr_token_env:      "GH_TOKEN"     # env var holding the API token

extraction:
  commit_lookback_days: 365
  max_diff_lines:       200
  max_files_changed:    5
  min_commit_message_length: 20
  language_override:    null        # null = auto-detect per file extension

analyzers:
  rust:   ["cargo-check", "cargo-clippy"]
  python: ["mypy", "ruff"]
  ts:     ["tsc", "eslint"]
  java:   ["infer"]                 # AGPL; subprocess only
  go:     ["go-vet", "staticcheck"]
  kotlin: ["detekt"]

teacher:
  model:            "glm-4.6"
  provider:         "hosted_vllm"
  base_url:         "http://localhost:8001/v1"
  max_turns:        32
  max_context:      64000
  rotate_quarterly: true            # switch teacher model each quarter

rl:
  n_generations:                8
  verifier_determinism_runs:    3
  docker_build_timeout_s:       120
  docker_teardown_timeout_s:    30
  difficulty_filter:            true
  difficulty_min_pass_rate:     0.10
  difficulty_max_pass_rate:     0.85
  reference_model:              "claude-opus-4"

output:
  sft_path:        "~/.lamark/training/raw/codebase_sft.jsonl"
  rl_tasks_path:   "~/.lamark/training/raw/codebase_rl_tasks.jsonl"
  grpo_groups_path:"~/.lamark/training/raw/codebase_grpo_groups.jsonl"

redaction:
  run_before_teacher: true    # always redact before any frontier model sees the data
  substitution_map:   "~/.lamark/training/redacted/substitution_map.json"
```

### CLI invocation

```bash
# Full run: all four phases on a single repo
lamark-train dataset from-codebase \
  --config dataset_config.yaml \
  --since 365d \
  --phases extract,enrich,teach,rl

# Nightly incremental (only commits since last run checkpoint)
lamark-train dataset from-codebase --config dataset_config.yaml --since 24h

# Multiple repos (pass multiple config files; runs phases in parallel per repo)
lamark-train dataset from-codebase \
  --config repo1.yaml --config repo2.yaml --config repo3.yaml \
  --since 365d

# Dry-run: show extraction counts without generating teacher traces
lamark-train dataset from-codebase --config dataset_config.yaml --dry-run

# RL tasks only (skip SFT trace generation)
lamark-train dataset from-codebase --config dataset_config.yaml --phases extract,enrich,rl

# Build GRPO groups from existing RL tasks (Tier 3 activation)
lamark-train dataset build-grpo-groups \
  --rl-tasks ~/.lamark/training/raw/codebase_rl_tasks.jsonl \
  --model /adapters/qwen36/coder-prod \
  --n-generations 8 \
  --out ~/.lamark/training/raw/codebase_grpo_groups.jsonl
```

### Integration with the nightly pipeline

```
connectors/
├── codebase_extract.py    # Phase 1: git history → bug-fix pairs
├── mr_enrich.py           # Phase 2: MR/PR context (optional)
├── teacher_trace.py       # Phase 3: teacher traces → SFT
└── rl_task_build.py       # Phase 4: RL task triplets + GRPO groups
```

`nightly.sh` orchestration:

```
T+0:00  codebase_extract.py --since=yesterday → /raw/codebase_pairs.jsonl
T+0:15  mr_enrich.py                           → /raw/codebase_enriched.jsonl
T+0:20  redact/stage1_secrets.py (mandatory before teacher sees data)
T+0:25  redact/stage2_pii.py
T+1:00  teacher_trace.py                       → /raw/codebase_sft.jsonl
         (feeds into existing T+1:30 curate / T+2:30 quality / T+2:55 blend steps)
```

RL tasks and GRPO groups are generated separately (weekly or on-demand, not nightly):

```bash
# Weekly (Saturdays, before Sunday DPO run)
lamark-train dataset from-codebase --phases extract,rl --since 7d
lamark-train dataset build-grpo-groups --rl-tasks /raw/codebase_rl_tasks.jsonl
```

### File layout in `lamark_trainer/connectors/`

```python
# codebase_extract.py
class CodebaseExtractor:
    def __init__(self, config: DatasetConfig): ...
    def run(self, since: datetime) -> Iterable[BugFixPair]: ...
    # Calls: clone_or_pull() → iter_commit_pairs() → run_analyzers() → passes_quality_filter()

# mr_enrich.py
class MREnricher:
    def __init__(self, config: DatasetConfig): ...
    def enrich(self, pairs: Iterable[BugFixPair]) -> Iterable[BugFixPair]: ...
    # Calls: link_commit_to_mr() → extract_mr_trajectory() → filter_mr()

# teacher_trace.py
class TeacherTracer:
    def __init__(self, config: DatasetConfig): ...
    def generate(self, pairs: Iterable[BugFixPair]) -> Iterable[SFTTrace]: ...
    # Calls: build_task_prompt() → run_teacher_agent() → filter_sft_traces()

# rl_task_build.py
class RLTaskBuilder:
    def __init__(self, config: DatasetConfig): ...
    def build(self, pairs: Iterable[BugFixPair]) -> Iterable[RLTask]: ...
    def generate_grpo_groups(self, tasks: Iterable[RLTask], model) -> Iterable[GRPOGroup]: ...
    # Calls: render_dockerfile() → render_verifier() → filter_rl_tasks() → generate_grpo_group()
```

---

## Expected dataset sizes

From InferFix paper and OpenThoughts-Agent experience:

| Repo size | Raw pairs | After quality filter | SFT traces | RL tasks (3-stage) |
|---|---|---|---|---|
| Small (< 50K LoC) | 50–200 | 20–80 | 20–80 | 10–40 |
| Medium (50K–500K LoC) | 500–3K | 200–1K | 200–1K | 100–500 |
| Large (500K+ LoC) | 3K–15K | 1K–5K | 1K–5K | 500–2.5K |
| Multi-repo fleet | 10K–50K+ | 5K–20K+ | 5K–20K+ | 2K–10K+ |

OpenThoughts-Agent-v1 SFT used ~15,209 samples total (nl2bash + InferredBugs). That is the
empirically validated sweet spot for an 8B model. Quality filter aggressively; don't pad.

---

## Known issues and mitigations

| Issue | Mitigation |
|---|---|
| Analyzer false positives | Run analyzer twice on same commit; include only reproducible diagnostics |
| Diff includes unrelated hunks | Clip to ±20 lines of diagnostic location; filter by `diagnostics_fixed` file list |
| MR API rate limits | Cache all responses; run enrichment asynchronously, separate from extraction |
| Teacher generates non-compiling fixes | Verifier catches these; label as `verifier_passed: false`; use as negative RL examples |
| Docker build failure for old commits | Skip pair if build fails in both parent and child (indicates broken repo state) |
| AGPL tooling (Infer, TruffleHog) | Subprocess only; never import; already covered by load-bearing invariant 4/8 in CLAUDE.md |
| Same pair mined from multiple branches | Dedup by `(repo, parent_sha, child_sha, diagnostic_code, file, line)` before teacher |

---

## References

- **InferFix:** Mathur et al., "InferFix: End-to-End Program Repair with LLMs over Retrieval-Augmented Prompts," ESEC/FSE 2023. arXiv:2303.07263.
- **InferredBugs dataset:** `github.com/microsoft/InferredBugs` — Java + C# bug-fix pairs used by InferFix.
- **OpenThoughts-Agent:** `openthoughts.ai/blog/agent` — blog post and datasets.
  - SFT dataset: `open-thoughts/OpenThoughts-Agent-v1-SFT` (HF, 15,209 rows, nl2bash + InferredBugs)
  - RL dataset: `open-thoughts/OpenThoughts-Agent-v1-RL` (HF, ~720 tasks)
  - Benchmark: `open-thoughts/OpenThoughts-TBLite` (HF, 100 tasks, r=0.911 with Terminal-Bench 2.0)
- **Harbor:** `github.com/harbor-framework/terminal-bench` — containerized agent execution harness.
- **SkyRL:** `github.com/NovaSky-AI/SkyRL` — RL orchestration; used by OpenThoughts-Agent.
- **OpenThinker-Agent-v1:** `open-thoughts/OpenThinker-Agent-v1` (HF) — Qwen3-8B fine-tuned;
  15.7% SWE-Bench Verified vs 0.7% baseline.
