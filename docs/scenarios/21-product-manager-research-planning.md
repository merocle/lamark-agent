# 21 — Product Manager: market research, backlog review, and feature planning

> **Phase:** P6 (gateway, MCP connectors) + P5 (coordinator, skills) + P4 (memory + KB).
> **One-liner:** A Product Manager instructs Lamark to research the competitive
> landscape, compare market signals against the existing backlog, and produce
> a structured package of product documents — briefs, feature proposals,
> market research summaries, and roadmap drafts — then track their progress
> through planning and implementation, reacting to status changes in the
> issue tracker.

---

## North-star contribution

- **Domain quality (product management).** Compresses a research-and-synthesis
  cycle that takes a PM 1–2 weeks into a few hours. The agent cross-references
  multiple signal sources (web, competitor changelogs, customer feedback in
  YouTrack, internal backlog) and produces structured, version-tracked documents
  the PM can review and iterate on — not a wall of raw text.
- **Agent-side self-improvement.** Emits:
  - `memory_fact` writes: competitor product maps, user segment pain points,
    recurring customer request clusters. Recalled in every subsequent research
    session without re-crawling.
  - `skill_draft` candidates: reusable research templates (competitive matrix,
    JTBD interview synthesis, PRD skeleton) promoted by Curator after ≥ 3 uses.
  - `reinforce_signal=success` when the PM approves a document and the
    linked feature makes it into the roadmap; `fail` when a proposal is
    explicitly rejected or abandoned.
- **Model-side self-improvement.** Research + synthesis traces are rare and
  high-value SFT material: long-horizon web research, multi-source synthesis,
  structured document generation. DPO pairs arise from iterated document
  drafts (first draft vs PM-revised version).

---

## Idea

A Product Manager is responsible for the developer-tools vertical of a
software company. They need to: understand where the market is heading,
identify gaps between competitor offerings and their own roadmap, find
high-signal customer requests buried in the YouTrack backlog, and produce
a set of documents (competitive analysis, feature brief, PRD skeleton) that
the engineering and design teams can act on. They also want the agent to
watch the planning board and implementation tracker, nudging them when
feature specs haven't moved and updating documents as implementation
progresses. Lamark handles the research and writing; the PM reviews,
edits, and approves.

---

## Actors

| Actor | Role |
|---|---|
| **PM (Sofia)** | Product Manager. Defines the research brief, reviews drafts, approves documents, directs follow-up questions. Uses TUI or WebUI. |
| **Research coordinator** | `lamark-coordinator` sub-agent. Orchestrates parallel research sub-agents, merges findings, drives document drafting (`plan/05a`). |
| **Web research agent** | Sub-agent using `WebSearch` + `WebFetch` tools to gather market intelligence, competitor release notes, analyst reports, job postings. |
| **Backlog analysis agent** | Sub-agent querying YouTrack (`YouTrackTool`) for customer-reported issues, feature requests, vote counts, customer segments. |
| **Document authoring agent** | Sub-agent that synthesises research findings into structured product documents, stored in KB and/or the repo's `docs/product/` directory. |
| **Planning monitor agent** | Long-running sub-agent that watches the YouTrack roadmap board and development tracker; posts status updates and nudges to Sofia via gateway. |
| **Knowledge-base** | Stores all research findings, competitor profiles, customer segment models, and approved documents with version history. |
| **Gateway** | Slack adapter — delivers document-ready notifications, planning alerts, and weekly digests to Sofia's PM channel (`plan/09`). |
| **YouTrack MCP connector** | Exposes YouTrack search, vote counts, customer fields, roadmap board as tools. |

---

## Trigger

**Interactive brief:**
```
$ lamark chat "Research the AI coding assistant market for Q3 planning.
  Focus on: JetBrains AI Assistant competitors, new features shipped in
  the last 90 days, top 20 customer requests in AIASSIST project in YouTrack,
  gaps vs our roadmap. Produce a competitive analysis doc + 3 feature briefs."
```

**Recurring research pulse (cron-driven):**
```
$ lamark agent run --profile pm-research-pulse --schedule "0 9 * * MON"
```

---

## Pipeline

### Step 0 — Brief parsing + recall

1. Research coordinator parses the brief into a structured research plan:
   `{scope, competitors, signals, output_documents, deadline}`.
2. Queries `GET /memory/search?q=competitor_profile` — recalls existing
   competitor profiles (last crawl date, known features, pricing). Stale
   profiles (> 14 days) are flagged for refresh.
3. Queries `GET /memory/search?q=customer_segment` — recalls known user
   personas and pain-point clusters from prior research sessions.
4. Creates a research Kanban board: tasks for `[web_research, backlog_analysis,
   synthesis, document_drafting, review_loop]` (`plan/05b`).

### Step 1 — Parallel research

5. Coordinator spawns parallel sub-agents (concurrency bounded by config):
   - **Web research agent** (one per competitor or topic cluster):
     - `WebSearch("JetBrains AI Assistant competitor features 2026")`
     - `WebFetch(changelog_url)` for each known competitor release page
     - `WebSearch("AI coding assistant job postings 2026")` — proxy for R&D investment signals
     - Extracts structured signals: `{competitor, feature, date, source_url}`
     - Writes `memory_fact` for each new feature discovered.
   - **Backlog analysis agent**:
     - `YouTrackTool::Search(project="AIASSIST", type="feature-request", sort_by="votes")`
     - Clusters issues by theme using the model's synthesis capability
     - Computes: top N requested features, customer segments making the requests,
       issues that have been open > 180 days with > X votes (stalled high-demand items)
     - Writes `memory_fact: {cluster_label, top_issues, customer_segment, vote_count}`

### Step 2 — Synthesis

6. Coordinator collects all research sub-agent results.
7. Document authoring agent synthesises:
   - **Competitive gap analysis**: feature matrix (rows = features,
     columns = competitors + own product) derived from web research.
   - **Customer demand heat map**: vote-weighted cluster map from backlog analysis.
   - **Opportunity vectors**: features present in ≥ 2 competitors but absent
     from own roadmap AND requested by customers (overlap of gap + demand).
8. Cross-references opportunity vectors against the existing roadmap (fetched
   from YouTrack roadmap board) to classify each vector as:
   `planned | partially_planned | not_planned`.

### Step 3 — Document generation

9. Document authoring agent produces each requested output file:
   - `docs/product/2026-Q3-competitive-analysis.md` — structured competitive
     matrix + narrative summary.
   - `docs/product/feature-brief-<slug>.md` (one per top opportunity vector)
     — follows the product brief template (recalled from skills or generated
     from scratch with: problem statement, target customer, success metrics,
     out-of-scope, open questions).
   - `docs/product/market-research-2026-Q3.md` — synthesis of web research
     findings with cited sources.
10. Each document is written via `Write` tool (gated: `Prompt` for first write
    to a new document path; `Allow` for updates within `docs/product/`).
11. `POST /knowledge/skills` uploads each document to KB with version tag.
    Sofia is notified via Slack: "Research package ready for review — 1
    competitive analysis + 3 feature briefs."

### Step 4 — Review loop

12. Sofia opens the documents (TUI `lamark view docs/product/` or WebUI).
    She edits inline or leaves comments via `lamark comment <file> <line>`.
13. Each comment is surfaced to the document authoring agent as a
    `Op::ExternalEvent { type: "document_comment" }`. Agent interprets the
    comment and applies revisions.
14. Approved documents are tagged `status=approved` in KB.
    `YouTrackTool::CreateIssue` is called for each approved feature brief
    to seed the planning backlog with a pre-filled spec link.
15. `reinforce_signal=success` if the document is approved and linked issues
    are created; `fail` if the document is rejected outright.

### Step 5 — Planning and implementation tracking

16. Planning monitor agent (long-running, wakes on a schedule or YouTrack
    webhook) watches each linked issue:
    - **Stalled spec** (issue in `Spec Ready` > N days with no movement):
      Slack nudge to Sofia: "Feature brief X has had no engineering pick-up
      for 14 days."
    - **Implementation started**: agent updates the document's status section:
      "Implementation in progress — branch `feature/X` opened by Alex."
    - **Build green / deployed**: agent marks the document `implemented=true`;
      posts a digest to Sofia's PM channel.
17. Memory facts updated throughout: `{feature, decision_date, shipped_date,
    actual_scope_vs_brief}` — useful for retrospective accuracy analysis in
    future planning cycles.

---

## Layers / crates touched

| Step | Crate | Plan |
|---|---|---|
| 0 (brief, recall) | `lamark-coordinator`, `lamark-memory`, `lamark-kb-client` | 05a, 07a |
| 1 (web + backlog research) | `lamark-tools` (WebSearch, WebFetch, YouTrack MCP), `lamark-coordinator` | 05, 09, 11 |
| 2 (synthesis) | `lamark-core` (turn loop), `lamark-prompt` | 05, 07 |
| 3 (document generation) | `lamark-tools` (Write), `lamark-policy`, `lamark-kb-client` | 05, 06, 07a |
| 4 (review loop) | `lamark-gateway` (Slack), `lamark-hooks` (ExternalEvent) | 06, 09 |
| 5 (planning monitor) | `lamark-coordinator`, `lamark-gateway`, `lamark-kb-client` | 05a, 07a, 09 |

---

## Failure modes

| Failure | Expected behavior |
|---|---|
| **Competitor website blocks crawl** | Agent marks the source as `unavailable`; falls back to cached last-known profile from KB; notes the limitation in the generated document. |
| **YouTrack search returns > 1000 issues** | Agent paginates and clusters progressively; emits a partial analysis with a note on truncation. |
| **Context overrun during synthesis** | Compaction fires; coordinator serialises sub-agent results as structured JSON summaries rather than raw text, reducing token usage. |
| **Sofia rejects a document wholesale** | Agent writes a `reinforce_signal=fail` memory fact; prompts Sofia for the rejection reason; records the reason as a constraint for the next research cycle. |
| **Planning monitor misses a webhook** | Polling fallback every 4 hours ensures no state is missed, at the cost of slight delay. |
| **KB unavailable** | Research results are spooled to local `~/.lamark/outbox/`; documents are written locally; upload retried when KB recovers. |

---

## Acceptance criteria

- [ ] A research brief produces a competitive analysis + ≥ 1 feature brief
  within a single session, both written to `docs/product/`.
- [ ] Backlog analysis correctly identifies the top N voted feature requests
  and clusters them by theme without manual input.
- [ ] Opportunity vectors that are both in competitor offerings and customer-
  requested are correctly classified as `not_planned` vs `planned` by
  cross-referencing the YouTrack roadmap.
- [ ] Sofia's inline document comments trigger a revised draft within one
  agent turn.
- [ ] Approved feature briefs automatically create linked YouTrack issues with
  the spec document URL in the description.
- [ ] A second research session (same brief, 30 days later) uses recalled
  competitor profiles for known competitors and only re-crawls stale ones.
- [ ] Planning monitor posts a Slack nudge for a spec that has had no
  engineering activity for > N days (configurable).

---

## Self-improvement assertions

1. **Research SFT samples.** Each web research sub-agent bundle produces ≥ 1
   Nemotron-Agentic-v1 entry covering `WebSearch → WebFetch → synthesis` arcs.
2. **Document iteration DPO pairs.** An initial draft + PM-revised final
   version produce a `(draft_v1, approved_vN)` DPO pair the trainer can use
   to improve document generation quality.
3. **Memory recall reduces re-crawl.** After ≥ 2 research cycles, the web
   research agent issues fewer `WebFetch` calls for established competitors
   (verifiable by comparing tool call counts across trace bundles of the same
   research brief).
4. **Template skill reduces drafting time.** After Curator promotes a
   feature-brief template skill, the document authoring agent's first draft
   is generated in fewer turns (verifiable by trace length comparison).

---

## Plan coverage matrix

| Concern | Covered in | Status |
|---|---|---|
| Coordinator fan-out + Kanban | plan/05a, plan/05b | _audit_ |
| WebSearch + WebFetch tools | plan/05 §"Tool registry" | _audit_ |
| YouTrack MCP connector | plan/09 §"MCP client", plan/11 | _audit_ |
| Write tool + policy for `docs/product/` | plan/05, plan/06 | _audit_ |
| KB document storage + versioning | plan/07a §"Knowledge-base client" | _audit_ |
| Memory recall for competitor profiles | plan/07a | _audit_ |
| Slack gateway notifications | plan/09 §"Gateway" | _audit_ |
| ExternalEvent hook (document comment) | plan/06 §"Hook bus" | _audit_ |
| Skill promotion (brief template) | plan/08 §"Curator" | _audit_ |
| Long-running monitor agent | plan/05a §"Coordinator", plan/05c | _audit_ |
| Prompt compaction during synthesis | plan/07 §"Prompt cache" | _audit_ |

---

## Open questions

1. **Document storage location.** Should product documents live in the
   monorepo (`docs/product/`) or only in KB? For companies using Space/GitHub
   wikis, a push-to-wiki tool would be preferable. Config-driven output target.
2. **YouTrack MCP vs REST tool.** YouTrack has a REST API; wrapping it as an
   MCP server (reusable across projects) is cleaner than a bespoke tool.
   Decision feeds into the MCP connector design (`plan/11`).
3. **Comment intake protocol.** Sofia's inline document comments need a
   defined intake surface: a dedicated `lamark comment` CLI command, a WebUI
   annotation layer, or simply a structured Slack message? Each has different
   hook wiring.
4. **Roadmap source of truth.** If the YouTrack roadmap board is the canonical
   roadmap, the agent can read it directly. If roadmap lives in a separate
   tool (Productboard, Linear, Notion), a connector is needed. ADR required.
5. **Document versioning in KB.** How are document versions stored and
   compared? If KB stores only the latest, the DPO training pair (draft vs
   approved) may be lost. KB needs a version history endpoint or the raw
   trace bundle is the only source.
6. **Competitive research frequency.** How often should the monitoring agent
   re-crawl competitor changelogs? Too frequent = rate limits + noise; too
   infrequent = stale signals. Suggest: configurable per-competitor cadence
   stored as a memory fact.
