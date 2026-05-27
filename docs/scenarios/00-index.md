# Scenarios — index

> User-facing **use cases** and **working pipelines** for Lamark v0.1.
> Each scenario goes idea → actors → trigger → step pipeline → tools / layers
> touched → failure modes → **dual self-improvement contribution** →
> plan coverage matrix.

---

## North star

Lamark exists for two things, in this order:

1. **Deliver quality results in a chosen domain** (coding, ops, research,
   support, knowledge work — whichever domain the operator points it at).
2. **Continuously self-improve, on both sides of the loop:**
   - **Agent side** — skills are authored from successful traces, Curator
     consolidates weekly, prompts are refined via Reflexion/OPRO, memory
     reweights via reinforce signal. *No model retraining needed.*
   - **Model side** — every trace becomes a candidate SFT/DPO sample;
     nightly LoRA training, eval gate + forgetting probe, automatic
     promote/rollback. *The model itself gets better at the agent's job.*

> A feature only earns its place in v0.1 if it either (a) raises domain
> quality on day 1, or (b) feeds at least one of the two self-improvement
> loops with usable signal. Scenarios make this contribution explicit and
> testable.

### Quality signal vocabulary (used across all scenarios)

| Signal | Where it goes | Used by |
|---|---|---|
| **Trace bundle** (`rollout_id`) | `~/.lamark/traces/<id>/` → KB `POST /agents/{id}/traces` | Model-side trainer (`plan/10`). Reducer emits Nemotron-Agentic-v1. |
| **Skill draft** (markdown + YAML) | `.lamark/skills/` → Curator → KB `POST /knowledge/skills` | Agent-side: future sessions discover it. |
| **Memory fact** (`POST /memory/facts`) | KB | Agent-side prompt composer recalls next session. |
| **Reinforce signal** (success/fail tag on trace + memory) | KB | Both: reweights training samples; reranks memory retrieval. |
| **Eval probe outcome** (gold set + forgetting set) | KB `eval_sets` + adapter event | Model-side gate; can also reject a bad skill. |
| **Prompt-edit proposal** (OPRO/Reflexion candidate) | `.lamark/prompts/proposals/` → A/B → promote | Agent-side prompt evolution (`plan/07b`). |

Every scenario file MUST declare which signals it produces and which it consumes.

---

## Reading order

Scenarios are numbered **roughly by build phase** (P2 → P10) so earlier ones
unlock the later ones. Within a phase, scenarios that produce *signal* for
self-improvement come before scenarios that *consume* that signal.

| #   | Scenario                                          | Surface         | Phase  | Produces / Consumes | File |
|----:|---------------------------------------------------|-----------------|--------|---------------------|------|
| 01  | Local CLI coding session (golden path)            | TUI / CLI       | P2–P4  | P: trace, memory    | [01-local-cli-coding.md](./01-local-cli-coding.md) |
| 02  | Permission-gated dangerous command                | TUI / Hooks     | P3     | P: trace (denial samples) | 02-permission-gate.md |
| 03  | Trace bundle → KB upload → recall next session    | Trace + KB      | P3–P4  | P: trace, memory; C: memory | 03-trace-and-recall.md |
| 04  | Multi-agent Kanban + /goal Ralph loop             | Coordinator     | P5     | P: trace (multi-rollout), success/fail signal | 04-coordinator-kanban.md |
| 05  | Skill authored by agent + Curator weekly sweep    | Skills/Curator  | P5     | P: skill draft; C: trace | 05-skill-authoring-curator.md |
| 06  | Plugin host loads WASM + dylib plugin             | Plugin host     | P5     | P: trace (plugin tool calls) | 06-plugin-host.md |
| 07  | Prompt self-improvement (Reflexion + OPRO + A/B)  | Prompt evolve   | P5+    | P: prompt-edit; C: trace, eval | 07-prompt-self-improvement.md |
| 08  | Multi-project isolation (one Lamark, N projects)  | Cross-cutting   | P3+    | C: per-project trace/memory namespace | 08-multi-project-isolation.md |
| 09  | Telegram gateway — phone-driven session           | Gateway         | P6     | P: trace, memory (cross-device) | 09-telegram-gateway.md |
| 10  | MCP server — Claude Desktop uses Lamark tools     | MCP (server)    | P6     | P: trace from external client | 10-mcp-server.md |
| 11  | MCP-as-client — Lamark consumes third-party MCP   | MCP (client)    | P6     | C: external tool capability | 11-mcp-as-client.md |
| 12  | ACP registry — Lamark calls / is called by agents | ACP             | P6     | P: trace; C: peer agent results | 12-acp-registry.md |
| 13  | Sandbox: subagent in Kubernetes pod               | Sandbox (k8s)   | P5–P6  | P: isolated trace pull-back | 13-k8s-subagent.md |
| 14  | Batch runner — offline eval / mass trajectories   | Batch           | P7     | P: bulk trace, eval set | 14-batch-runner.md |
| 15  | Nightly training: trace → LoRA → promote          | Trainer         | P7–P8  | C: trace; P: adapter | 15-nightly-training.md |
| 16  | Forgetting probe → auto-rollback                  | Eval gate       | P8     | C: eval; P: rollback event | 16-forgetting-rollback.md |
| 17  | WebUI — live session in browser                   | WebUI           | P9     | (surface; reuses #01 signals) | 17-webui-live-session.md |
| 18  | Remote UI — control from another machine          | Remote UI       | P10    | (surface; reuses #01 signals) | 18-remote-ui.md |

---

## Template (each scenario file follows this)

```
# NN — <title>

## North-star contribution
- **Domain quality:** how this scenario produces a better outcome today.
- **Agent-side self-improvement:** which signals it emits (skill / memory /
  prompt-edit / reinforce). What gets better in the *next* session because
  of this one.
- **Model-side self-improvement:** what trace shape it produces and how the
  trainer consumes it (SFT-friendly / DPO pair / forgetting probe / etc.).

## Idea (1 paragraph)

## Actors

## Trigger

## Pipeline (numbered steps)
Each step cites a crate + a `plan/NN.md §section`.

## Layers / crates touched

## Failure modes

## Acceptance criteria
Testable; each ↔ one integration test.

## Self-improvement assertions (new)
- After this scenario runs, the trainer can build at least N samples of
  shape S from the bundle.
- After this scenario runs, a re-run of the same prompt observes ≥ X%
  cache hit / ≥ Y% latency drop / a skill recall / a memory hit.

## Plan coverage matrix
| Concern | Covered in | Status |

## Open questions
```

---

## Audit workflow

After each scenario file is approved:

1. Spawn a read-only sub-agent (Explore-type) with the scenario file + the
   relevant `plan/` files as input.
2. Agent fills the **Plan coverage matrix** with ✅ / ⚠️ / ❌ + 1-line
   evidence (`file:line`) for each row.
3. Agent **separately judges the self-improvement assertions**: does the
   trace shape support the claimed trainer use? does the skill-draft path
   exist? does the memory write/read round-trip exist?
4. Agent writes findings to `scenarios/_audit/NN-<slug>.md`.
5. Gaps (❌) accumulate in `scenarios/_audit/_gaps.md`. Each gap is
   classified as **blocker** (v0.1 doesn't ship without it) or **deferred**.

Sub-agents run **sequentially**, not in parallel — each audit's gap list
informs the framing of the next scenario.
