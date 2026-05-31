# Private testing — install on a fresh machine while the repo is private

The `curl | bash` URL in the public README will return HTTP 404 until the
repository is flipped to public. Until then, testers need either:

- Read access to the repo (added as a collaborator), AND
- One of: a GitHub Personal Access Token (PAT), SSH key registered with
  their GitHub account, or `gh` CLI logged in.

This document is for the Lamark author and the small set of invited
testers running v0.1 alpha on hardware the author doesn't own. Three
paths in increasing UX cost.

## Path A — gh CLI (recommended for testers)

If the tester has `gh` installed and is logged in:

```bash
gh repo clone merocle/lamark-agent ~/lamark-agent
bash ~/lamark-agent/install.sh
```

`install.sh` is idempotent: when the repo is already cloned at
`~/lamark-agent` and contains `scripts/lamark`, it leaves the working
tree alone (no git pull, no overwrite). The rest of the install
(venv, deps, dispatcher symlink, PATH inject, hardware preview) runs
normally.

## Path B — git clone over HTTPS with a PAT

If the tester has only `git` and a GitHub PAT (classic, `repo` scope):

```bash
git clone https://<USER>:<PAT>@github.com/merocle/lamark-agent.git ~/lamark-agent
bash ~/lamark-agent/install.sh
```

The PAT goes into the remote URL in `.git/config` — the tester should
sanitize it later with:

```bash
cd ~/lamark-agent
git remote set-url origin https://github.com/merocle/lamark-agent.git
```

## Path C — author distributes a tarball

If a tester has no `gh`, no PAT, and you don't want to issue one,
ship them a tarball:

```bash
# On the author's machine:
gh release download v0.1.0-alpha.0 \
    --repo merocle/lamark-agent \
    --archive=tar.gz \
    --output lamark-agent.tar.gz

# Or just from a fresh checkout:
git archive --format=tar.gz --prefix=lamark-agent/ HEAD > lamark-agent.tar.gz
```

Then the tester:

```bash
tar -xzf lamark-agent.tar.gz -C ~/
bash ~/lamark-agent/install.sh
```

Loses git remote — they can't `git pull` for updates. Re-distribute a
new tarball when needed.

## What the tester does after install

The dispatcher is now at `~/.lamark/bin/lamark` and `~/.bashrc` (or
`~/.zshrc`) has the PATH export. Reload the shell, then:

```bash
lamark setup        # pick branch 2 (Existing endpoint) if you don't
                    # want to download a model — point at someone
                    # else's vLLM that's already running
lamark chat
```

## What we want from the test pass

- Real install on hardware the author doesn't own (4090, 5090, 3090, Mac
  with M-series, headless Linux box, etc.)
- One of: stuck installer, broken `lamark setup` wizard branch, broken
  `lamark serve start`, broken cross-session memory, surprising
  `lamark chat` UX. Screenshots/transcripts welcome.
- Tier S users on a non-author Spark would be the most valuable signal
  (validates that the v0.1 known-good path actually reproduces).

Report findings as issues in the GitHub repo (still private — testers
need repo access). Once the install path is validated on 2+
non-author machines, the repo flips public and `curl | bash` becomes
the standard advertised install command.
