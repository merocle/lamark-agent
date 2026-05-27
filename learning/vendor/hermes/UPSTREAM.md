# Hermes Agent — Vendored Snapshot

This directory contains a vendored snapshot of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
at a pinned upstream SHA, used as the base of Lamark per the architecture in
`../../feasibility-report-v4.md`.

## Pinned upstream

- **Repository:** https://github.com/NousResearch/hermes-agent
- **SHA:** `874c2b1fe6ec185f9d1da17d31d2c7885d58c35c`
- **Captured:** 2026-05-23
- **License:** MIT — see `LICENSE` in this directory (preserved verbatim)

## What was vendored

Source code + skills + plugins + locales + docker config. Everything needed
at runtime to drive the agent loop.

## What was NOT vendored

- `website/` (16 MB marketing site)
- `tests/` (19 MB upstream test suite — we re-vet our patches with our own tests)
- `infographic/` (visual marketing assets)
- `RELEASE_*.md` (historical release notes)
- All `.git/` history
- All `__pycache__/` and `*.pyc`

## Modifications made by Lamark

See `MODIFICATIONS.md` in this directory for the diff stack applied on top
of the pinned SHA.

## License attribution

Original Hermes Agent code in this directory is © 2025 Nous Research under
the MIT License (see `./LICENSE`). Lamark modifications are © 2026 Lamark
contributors under the same MIT License. The combined work is therefore
MIT-licensed; both copyright lines must travel together in any redistribution.
