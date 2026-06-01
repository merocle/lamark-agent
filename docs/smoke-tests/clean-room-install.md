# Clean-room install smoke test (P1-1)

Validates the author's release criterion — *someone other than the author can
install and run it* — for the install + dispatch + agent-import layer, on a
machine the author doesn't special-case.

## Method

A throwaway `ubuntu:24.04` container (only `python3 python3-venv git curl`
apt-installed — nothing else), no GPU, the repo seeded from disk so the test
exercises the installer's dependency closure rather than the (private) clone:

```bash
docker run --rm -v <repo>:/src:ro ubuntu:24.04 bash -c '
  apt-get update -qq && apt-get install -y -qq python3 python3-venv git curl
  cp -r /src /root/lamark-agent && rm -rf /root/lamark-agent/.git
  LAMARK_SKIP_DEP_CHECK=1 LAMARK_REPO=/root/lamark-agent LAMARK_HOME=/root/.lamark \
    bash /root/lamark-agent/install.sh
  # the import lamark chat actually runs:
  PYTHONPATH=/root/lamark-agent/vendor/hermes:/root/lamark-agent/src \
    /root/.lamark/venv/bin/python -c "from hermes_cli.main import main"
  /root/.lamark/bin/lamark status
'
```

## Result — PASS (2026-06-01)

- `install.sh` exits 0 on a clean box (venv + the thin core dep set).
- `from hermes_cli.main import main` (the exact import `lamark chat` runs)
  succeeds with **only** the installer's dependency set — the agent loop is
  importable for a fresh non-owner; no missing-dependency `ImportError`.
- `lamark status` exits 0 with a clean health overview ("setup not complete",
  hardware detected, model server not running, no adapters yet, timer not
  installed) — the expected pre-`setup` state.

## Bugs this test caught (and fixed)

Both were invisible to the unit suite — only a clean, login-less environment
surfaced them:

1. **`status.sh` crashed under `set -u`** — `${SUDO_USER:-$USER}` with `$USER`
   unbound (containers / cron / login-less shells) aborted `lamark status`
   with exit 1. Fixed to `${SUDO_USER:-$(id -un)}`.

## Scope / what this does NOT cover

- Local model **serving** (vLLM + GPU) is tier-S (DGX Spark) and verified
  separately — not exercised here (the clean-room box has no GPU).
- The real `curl | bash` clone path is only reachable once the repo is public;
  this test seeds the repo from disk to validate everything downstream of the
  clone.
