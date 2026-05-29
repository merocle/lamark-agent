# 02 — knowledge-base integration

`knowledge-base` is the system of record for **all persistent data** that outlives a single Lamark process:

| Lamark data | knowledge-base endpoint |
|---|---|
| Trace bundles (post-reduction) | `POST /agents/{id}/traces` |
| Long-term memory facts | `POST /memory/facts`, `GET /memory/search` |
| User profile (`USER.md` snapshot) | `POST /memory/user_profiles` |
| Skills (markdown + version) | `POST /knowledge/skills` |
| Training datasets (Nemotron-Agentic-v1 JSONL) | `POST /knowledge/datasets` |
| Eval gold sets + forgetting probes | `POST /knowledge/eval_sets` |
| Adapter metadata + lineage | `POST /agents/{id}/adapters` |
| Promotion / rollback events | `POST /agents/{id}/events` |

knowledge-base then enables:

- **Hybrid retrieval** for memory (dense + sparse + KG + RAPTOR hierarchical) — replaces the SQLite FTS5 in Hermes-Agent.
- **Knowledge graph** for skills + entities the agent learns about — drives recommendation in Curator.
- **Reinforce** — RL-flavored re-weighting of memory & dataset samples based on which sessions actually succeeded.
- **Multi-project isolation** — one Lamark install can serve multiple project namespaces with separate trace stores.

Lamark talks to knowledge-base only over HTTP; we don't share a DB. The knowledge-base API contract is in `../../knowledge-base/docs/07b-public-api-rfc.md`.
