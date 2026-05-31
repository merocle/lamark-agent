---
name: Bug report
about: Something broke during install or use
title: "[bug] "
labels: bug
---

<!--
Lamark is verified on DGX Spark (tier S); consumer GPUs (tier M/L/XS) are
wired but not yet validated on real hardware. The details below let us tell
a tier-specific issue apart from a real bug — please fill them in.
-->

## What happened

<!-- What you expected vs what actually happened. -->

## Hardware / tier

- GPU (and VRAM):
- Tier picked by `lamark setup` (S / M / L / XS):
- OS + arch (`uname -a`):

## `lamark status` output

```
# paste the full output of: lamark status
```

## Relevant logs

```
# e.g. `lamark logs vllm` (serving issues) or
#      ~/.lamark/logs/nightly-train-*.log (training issues)
```

## Steps to reproduce

1.
2.
3.

## Install method

<!-- curl|bash, git clone, tarball, ... and the commit/tag if you know it. -->
