<!--
  PyPI long_description. Deliberately NOT the GitHub README.

  Three things break that one on PyPI:
    1. its hero/icon are relative paths (docs/*.svg) — PyPI resolves them
       against pypi.org and 404s;
    2. they are SVG, and GitHub raw serves .svg as text/plain, so an <img>
       will not render it even with an absolute URL;
    3. it carries 17 relative links (action.yml, LICENSE, .github/…, #anchors)
       that only resolve inside the repo.

  So this file uses absolute https URLs only, and a PNG served by the live
  site (correct Content-Type, already deployed, no new bytes in the repo).
-->

<div align="center">

<img src="https://armsmith.edycu.dev/assets/readme-hero.png" alt="Armsmith — the agent that forges your repo for Arm" width="100%">

# Armsmith

**The agent that forges your repo for Arm.**

[![PyPI](https://img.shields.io/pypi/v/armsmith?logo=pypi&logoColor=white&color=3775A9)](https://pypi.org/project/armsmith/)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](https://pypi.org/project/armsmith/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/edycutjong/armsmith/actions/workflows/ci.yml/badge.svg)](https://github.com/edycutjong/armsmith/actions/workflows/ci.yml)
[![aarch64 native](https://img.shields.io/badge/arch-aarch64%20native-f7941e)](https://github.com/edycutjong/armsmith)
[![measured on Neoverse-N2](https://img.shields.io/badge/measured%20on-Neoverse--N2-f7941e)](https://github.com/edycutjong/armsmith#measured-on-real-arm-silicon)

</div>

Armsmith profiles an AI repo on Arm, diagnoses **why** it is slow on aarch64 with a 13-rule
anti-pattern pack, drafts fixes, and renders a PR in which **every fix has passed a
reproduce-benchmark gate** — median-of-N, MAD noise bands, output-hash equality.

**The LLM plans; the silicon decides.** Deltas inside the noise band are reported as *no change*,
never as wins, and fixes that fail are reported rather than quietly dropped.

## Install

```bash
uvx armsmith scan .        # zero-install, one command
pipx install armsmith      # or keep it on your PATH
pip install armsmith
```

## Use it

**Scan any repo** for aarch64 anti-patterns — static rules, no hardware, no network:

```bash
armsmith scan .
```

**Record a bundle from your own machine, then diagnose your own repo.** The probe rules read
observations from a bundle; `record` captures what your host can honestly report and copies in any
real instrument output you already have:

```bash
armsmith record . --out ./bundle --python .venv/bin/python
armsmith diagnose --replay ./bundle
```

The manifest it writes declares `"synthetic": false`, because none of it is invented. Anything the
host cannot observe is **omitted, not guessed** — the rules that needed it report `skipped` with the
probe named. `env` and `proc_maps` are never captured at all: a bundle is a published artifact, and
those carry CI tokens and host paths.

**Gate your own change.** Give it your before and after command and it runs the same statistics —
ABAB interleaving, median-of-N, a scaled-MAD noise band, output-hash equality, a signed report. A
delta inside the band is reported as *no change*, never as a win:

```bash
armsmith bench-cmd --rule R3 \
  --baseline-cmd "python serve_bench.py --config before.yaml" \
  --candidate-cmd "python serve_bench.py --config after.yaml"
```

It refuses to run off `aarch64`, and it carries no ISA witness — it is a stopwatch, and says so.

**Take the measurement yourself**, on any `aarch64` box (a free GitHub `ubuntu-24.04-arm` runner is
enough). It refuses to run anywhere else rather than produce an Arm number off Arm hardware:

```bash
armsmith bench-live --require-witness
armsmith verify report-live.json
```

## The number, and why you don't have to trust it

On a GitHub-hosted `ubuntu-24.04-arm` runner (**Neoverse-N2**, gcc 13.3.0), Armsmith compiles one
int8 dot-product source **twice** — differing only in the `-march` flag that rule R2 exists to
flag — disassembles both, and measures them:

| | `-O3 -march=armv8-a` | `-O3 -march=armv8.2-a+dotprod` |
|---|---|---|
| **SDOT in the hot symbol** | **0** | **1** |
| median kernel time | 0.059975 s | **0.008123 s** |
| Δ | — | **−86.5% — a 7.4× speedup** |
| noise band (k=3) | — | ±0.000144 s |
| output hash | — | identical |
| **gate verdict** | — | **keep** |

Read the first row before the timings: ARMv8.0 has no dot-product instruction, so the baseline
*cannot* contain one. The stopwatch says 7.4×; the disassembly says **why**.

Every report is ed25519-signed and carries its own raw samples, so `armsmith verify` recomputes
every statistic and every gate verdict independently — change one digit and it prints
`VERIFY FAILED`.

## What it looks for

13 aarch64 anti-patterns, 10 of them citing the Arm Learning Path that teaches the fix by hand:
amd64-pinned images running under QEMU · builds with no `-mcpu`/`-march` · NumPy on reference
BLAS · silent float64 coercion · GGUF quant mismatched to the ISA repack path · threads × workers
over vCPU count · ONNX Runtime session defaults · pip sdist fallback · preprocess memcpy storms ·
llama.cpp built without KleidiAI · THP/allocator untuned · CI publishing amd64-only images ·
serving overhead dominating kernel time.

`armsmith rules export` renders them as x86→Arm migration cards, 11 with a paste-able before→after
diff.

## Honest scope

- **`armsmith pr` is dry-run today.** It renders exactly what would ship and makes no network call.
- The measured **−86.5%** is one int8 microkernel on one runner. It proves the gate works on real
  silicon; it is never scaled into a claim about your workload. The honest number for your model is
  the one you measure on your own hardware.
- Live `perf`/PMU capture, llama-bench, the Claude planner loop and real PR posting are marked
  `TODO(S1)` in code and exit non-zero rather than fabricate output.

## Links

- **Source:** https://github.com/edycutjong/armsmith
- **Docs & demo:** https://armsmith.edycu.dev
- **Releases (signed sdist + wheel):** https://github.com/edycutjong/armsmith/releases
- **Report JSON Schema:** https://armsmith.edycu.dev/schema/report.schema.json

MIT licensed.
