# 08 — Multi-project isolation (one Lamark install, N projects)

> **Phase:** P3+ (cross-cutting — every layer feels it).
> **One-liner:** One Lamark binary on one machine serves projects
> `prod-infra`, `research-notebook`, and `personal-side-project` —
> traces, memory, skills, policy rules, and Curator state are all
> namespaced; nothing bleeds across project boundaries.

---

## North-star contribution

- **Domain quality.** A *junior engineer using Lamark for tutorials* and
  *a senior SRE doing prod ops* must not see each other's traces, secrets,
  or memory facts. Lamark has to be safe to install in shared homes /
  corporate machines / multi-tenant servers.
- **Agent-side self-improvement.** Memory facts, skills, prompt-section
  versions, policy rules all carry a `project_id` tag (or live in
  per-project files). Recall is filtered by project. Skills can be
  *promoted* from per-project to user-global if they prove valuable
  across projects.
- **Model-side self-improvement.** Each project becomes a **separable
  dataset** in KB. Training can run per-project adapters or one global
  adapter. Trace-to-adapter lineage is project-tagged.

### Signals produced / consumed

- **Produces:** per-project trace stores (`~/.lamark/traces/<project>/...`),
  per-project memory tags (`metadata.project_id`), per-project Kanban
  audit logs, per-project policy files (`./.lamark/policy.toml`).
- **Consumes:** `LAMARK.md` / `AGENTS.md` walk-up to detect the active
  project; KB project namespace; per-project config overrides.

---

## Idea

User opens three terminals, each in a different repo. Three `lamark
chat` sessions are running concurrently. They share the binary, the
provider router, and the model — but **nothing else**: separate memory,
separate skills, separate traces, separate Kanban boards, separate
policy rules. Closing all three and re-opening tomorrow, each session
recalls its own project facts.

## Actors

| Actor | Role |
|---|---|
| **`lamark-config`** | Layered config: env → CLI flags → `./.lamark/config.toml` (project) → `~/.lamark/config.toml` (user) → built-in defaults (`plan/03`). Project file wins. |
| **Project-id detector** | Function of `cwd` (or `--project` flag, or `LAMARK_PROJECT` env). Likely `hash(canonical_path)` for stability. |
| **`lamark-trace`** | Writes to `~/.lamark/traces/<project_id>/<rollout_id>/`. |
| **`lamark-memory`** | All entries tagged `metadata.project_id`. Queries filter by project default; explicit cross-project queries possible. |
| **`lamark-skills`** | Project skills at `./.lamark/skills/`; user at `~/.lamark/skills/`. Resolution per `plan/08:62–68`. |
| **`lamark-policy`** | Project policy at `./.lamark/policy.toml`; user at `~/.lamark/policy.toml`. Most-specific wins per rule. |
| **KB client** | Uses `kb_project_id` (`plan/06:275`) on all writes; reads scoped by default. |

## Trigger

Implicit: every `lamark chat` invocation detects its project on bootstrap (stage 4 — config load, `plan/02 §"8-stage bootstrap"`).

Explicit: `lamark --project <name> chat`, or `LAMARK_PROJECT=foo lamark chat`.

## Pipeline

1. **Bootstrap.** Project detected via cwd → `kb_project_id` computed (deterministic hash of canonical path OR user-pinned name from project config). Recorded in `manifest.json` (`plan/06:267, 275`).
2. **Config load.** Project file overrides user file overrides defaults. CLI flags override all.
3. **Trace recorder root.** Path becomes `~/.lamark/traces/<project_id>/<rollout_id>/` — *not* the flat `~/.lamark/traces/` shown in `plan/06:243`. (Gap G-040: scenario needs this; plan is currently flat.)
4. **Memory provider scope.** `MemoryQuery` defaults gain a `project_filter` field; `KnowledgeBaseMemory::search` adds `project_id` to the request. KB's hybrid retrieval already supports multi-tenant namespacing (`SPEC §2.4 — Multi-project isolation`).
5. **Skill resolution.** Three-tier search: project skills first, then user, then bundled. Already in `plan/08:62–68` — but project-scope semantics need pinning vs Curator (does the Curator run *per project* or globally? See OQ #1).
6. **Policy.** `policy.toml` loaded with project file overriding user file per rule.
7. **Kanban / coordinator state.** Coordinator state file at `~/.lamark/state/<project_id>/coordinator-<rollout_id>/`. KB Kanban mirror uses `coordinator_session_id` which is per-rollout — already isolated.
8. **Concurrent sessions, different projects.** Each session writes to its own subtree; no contention. Memory writes are independent KB POSTs with different `project_id` tags.
9. **Cross-project queries (explicit).** `lamark memory search --project all "X"` allowed but rare; UX makes the default narrow.
10. **Promotion path (skills + facts).** Curator may *propose* promoting a per-project skill to user-global if usage convergence happens across multiple projects (extends G-023). Same for memory facts that are truly generic.

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 1 (project detection) | `lamark-config` + `lamark::runtime` | 02, 03 |
| 3 (trace root) | `lamark-trace::Recorder::open` | 06 (gap G-040) |
| 4 (memory filter) | `lamark-memory` | 07a + SPEC §2.4 |
| 5 (skills) | `lamark-skills::loader` | 08 |
| 6 (policy) | `lamark-policy` | 05 |
| 7 (coordinator state) | `lamark-coordinator` | 05a + G-025 |
| 9 (cross-project queries) | `lamark::cli memory search` | 02 |
| 10 (promotion path) | Curator | 08 |

## Failure modes

| Failure | Expected behavior |
|---|---|
| **`cwd` outside any project** (e.g., `/tmp`) | Falls back to user-scope (`project_id = "_user_default"`). Banner in TUI. |
| **Two projects with same canonical path hash** (collision) | Astronomically unlikely with hash; if user-pinned name, validation rejects collision. |
| **Project-id rename** | Migrate command: `lamark project rename old new`. Moves trace dir + KB tag remap. Must be atomic. |
| **User reads cross-project by mistake** | `lamark memory search` default is `--project current`; cross-project requires explicit flag. |
| **Lossy migration (KB tag remap mid-flight)** | Pre-flight check + dry-run option. Aborts on any inconsistency. |
| **Concurrent sessions write same memory fact across projects** | Each gets its own tagged entry; not deduplicated (intentional). |
| **Symlinked project paths** | Canonicalize before hashing. Symlink + target hash the same. |

## Acceptance criteria

- [ ] `manifest.json` carries `project_id` and `kb_project_id`.
- [ ] Traces written under `~/.lamark/traces/<project_id>/<rollout_id>/`.
- [ ] `memory.search` with default scope returns only current-project facts.
- [ ] Skills resolution: project > user > bundled (already plan/08, verified working).
- [ ] Policy: project rules override user rules per `(tool, arg-shape)`.
- [ ] Concurrent sessions in two different projects write to disjoint paths and to disjoint KB namespaces — verified by running two `lamark exec` scripts in parallel and inspecting outputs.
- [ ] `lamark project list` enumerates known projects; `lamark project rename old new` works.
- [ ] Cross-project search requires explicit `--project all` or `--project <name>`.
- [ ] Curator runs are scoped to projects by default; cross-project promotion of skills/facts is explicit.

## Self-improvement assertions

1. **Recall stays in-project by default.** A memory fact from `prod-infra` never appears in a `personal-side-project` session unless explicitly cross-queried.
2. **Skill promotion across projects is observable.** If the same skill draft converges in ≥ 3 distinct projects, Curator proposes a user-global promotion (extends G-023 across projects).
3. **Per-project adapters are buildable.** Trainer can target `--project prod-infra` and produce a project-specific LoRA from that namespace's traces only.
4. **No bleed under concurrency.** Two sessions in different projects running in parallel produce disjoint trace bundles and disjoint memory writes.
5. **Migration safety.** `lamark project rename` is atomic; partial state on crash is recoverable.

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| `kb_project_id` in manifest | plan/06:275 | _audit_ |
| Multi-project isolation as KB capability | SPEC §2.4 | _audit_ |
| Project skills at `./.lamark/skills/` | plan/08:62–68 | _audit_ |
| Project config override | plan/03 | _audit_ |
| **Project-scoped trace directory** | (likely **gap G-040**) | _audit_ |
| **`project_id` detection function** (cwd hash / pinned name) | (likely **gap G-041**) | _audit_ |
| **Memory `project_filter` in `MemoryQuery`** | (likely **gap G-042**) | _audit_ |
| Curator scope (per-project vs global) | plan/08 implicit | _audit_ |
| `lamark project list / rename / migrate` CLI | (likely **gap G-043**) | _audit_ |
| Cross-project search UX | (likely **gap**) | _audit_ |
| Coordinator state per-project | plan/05a + G-025 | _audit_ |
| Concurrent two-project sessions don't collide | (depends on G-017) | _audit_ |
| Trainer `--project` filter | plan/10 | _audit_ |
| Symlink canonicalization | (likely **gap**) | _audit_ |
| Promotion path: per-project → user-global skills | extends G-023 | _audit_ |
