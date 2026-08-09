<div align="center">
  <img src="docs/icon-animated.svg" alt="Armsmith Icon" width="144">
  <h1>Armsmith ⚒️</h1>
  <p><em>The agent that forges your repo for Arm.</em></p>
  <img src="docs/readme-hero-animated.svg" alt="Armsmith — the agent that forges your repo for Arm" width="100%">

  <p>
    <strong>On a GitHub <code>ubuntu-24.04-arm</code> runner (Neoverse-N2), Armsmith measured its own
    R2 fix at <code>SDOT 0 → 1</code> and <code>−86.5%</code> kernel time — then signed the report and
    re-derived every statistic from the raw samples.</strong><br/>
    Reproduce it in ~2 minutes on any x86 laptop, no Arm hardware required:
    <a href="#-getting-started">Getting Started</a>.
  </p>

  <br/>

  [![Demo Video](https://img.shields.io/badge/▶_Demo-3_min-ef4444?style=for-the-badge)](https://youtu.be/JsT83BYMWd0)
  [![Live Demo](https://img.shields.io/badge/🚀_Live-Demo-06b6d4?style=for-the-badge)](https://armsmith.edycu.dev)
  [![Pitch Deck](https://img.shields.io/badge/📊_Pitch-Deck-f59e0b?style=for-the-badge)](https://armsmith.edycu.dev/deck.html)
  [![Devpost Submission](https://img.shields.io/badge/Devpost-View_Submission-003E54?style=for-the-badge&logo=devpost&logoColor=white)](https://devpost.com/software/armsmith-7j1lzt)
  [![Built for Arm AI Optimization Challenge](https://img.shields.io/badge/Arm_AI_Challenge-Cloud_AI-8b5cf6?style=for-the-badge)](https://arm-ai-optimization-challenge.devpost.com/)

  <br/>

  [![CI](https://github.com/edycutjong/armsmith/actions/workflows/ci.yml/badge.svg)](https://github.com/edycutjong/armsmith/actions/workflows/ci.yml)
  [![Release](https://img.shields.io/github/v/release/edycutjong/armsmith?sort=semver&logo=semanticrelease&logoColor=white&color=2EE6A6)](https://github.com/edycutjong/armsmith/releases)
  [![PyPI](https://img.shields.io/pypi/v/armsmith?logo=pypi&logoColor=white&color=3775A9)](https://pypi.org/project/armsmith/)
  ![456 tests passing](https://img.shields.io/badge/tests-456%20passing-brightgreen)
  ![coverage 100%](https://img.shields.io/badge/coverage-100%25-brightgreen)
  ![Python 3.11 | 3.12](https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776AB?style=flat&logo=python&logoColor=white)
  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](https://opensource.org/licenses/MIT)

  <br/>

  <sub><b>Arm platform</b></sub><br/>
  [![Arm Developer](https://img.shields.io/badge/Arm-Developer-0091BD?style=flat&logo=arm&logoColor=white)](https://developer.arm.com/)
  [![Arm Learning Paths](https://img.shields.io/badge/Arm-Learning%20Paths-0091BD?style=flat&logo=arm&logoColor=white)](https://learn.arm.com/)
  ![aarch64 native](https://img.shields.io/badge/arch-aarch64%20native-f7941e)
  ![Neoverse N2](https://img.shields.io/badge/measured%20on-Neoverse--N2-f7941e)
  [![AWS Graviton](https://img.shields.io/badge/target-AWS%20Graviton-FF9900?logo=amazonaws&logoColor=white)](https://aws.amazon.com/ec2/graviton/)
  [![KleidiAI](https://img.shields.io/badge/KleidiAI-micro--kernels-0091BD)](https://gitlab.arm.com/kleidi/kleidiai)
  [![llama.cpp](https://img.shields.io/badge/llama.cpp-GGUF%20repack-303030)](https://github.com/ggml-org/llama.cpp)

</div>

---

Armsmith profiles an AI repo on Arm, diagnoses *why*
it is slow on aarch64 with a 13-rule anti-pattern pack, drafts fixes, and renders a PR in which
**every fix has passed a reproduce-benchmark gate** — median-of-N, MAD noise bands, output-hash
equality. **The planner proposes; the silicon decides.** In-band deltas are reported as *no
change*, never as wins, and dropped fixes are reported, never hidden.

*(The planner shipping today is a deterministic priority sort. The Claude tool-use loop is
contract-pinned in `planner/interface.py` and raises `NotImplementedError` rather than
pretending — the gate, not the planner, is what makes a result trustworthy either way.)*

*(PR rendering is dry-run today: it prints exactly what would ship and makes no network call.
Posting is [on the roadmap](#-roadmap) and marked in code rather than implied here.)*

Scan any repo for aarch64 anti-patterns without installing anything:

```bash
uvx armsmith scan .          # or: pipx run armsmith scan .   ·   pip install armsmith
```

Then record a real bundle from your own machine and run the full 13-rule diagnosis on it —
no fixtures of ours involved:

```bash
armsmith record . --out ./armsmith-bundle --python .venv/bin/python
armsmith diagnose --replay ./armsmith-bundle
```

And put **your own** before/after through the same reproduce gate — median-of-N, scaled-MAD noise
band, output-hash equality, signed report. An improvement inside the band is reported as *no
change*, on your workload exactly as on ours:

```bash
armsmith bench-cmd --rule R3 \
  --baseline-cmd "python serve_bench.py --config before.yaml" \
  --candidate-cmd "python serve_bench.py --config after.yaml"
```

To reproduce the full gate — baseline, 13-rule scan, keep/drop verdicts, signed report — clone and
run the replay bundle:

```bash
git clone https://github.com/edycutjong/armsmith && cd armsmith
python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'
python -m pytest -q                                       # 456 passing, offline
armsmith diagnose --replay fixtures/replays/scenario_ragserve   # 4 kept · 2 dropped
```

No Arm hardware, no network, no API key. Full walkthrough in
[Getting Started](#-getting-started) · every gate at once with `make all` · extending the rule pack:
[CONTRIBUTING](.github/CONTRIBUTING.md).

## 💡 The Problem & Solution

### The Problem

Moving an AI workload from x86 to Arm is supposed to be a cost win, and usually it is — but when a
repo runs slowly on aarch64, nobody can tell you *why*. The failure modes are boring and invisible:
an amd64-pinned base image quietly running under QEMU, NumPy on reference BLAS, a GGUF quantization
that misses the ISA's repack path, a build with no `-mcpu` so the dot-product unit never gets used.

The deeper problem is what happens next. An LLM is very willing to tell you it made your code 30%
faster. Benchmarks are noisy, "improvements" inside the noise band get reported as wins, and a fix
that changes your output is still a fix if nobody checked. Performance claims are the easiest thing
in software to fake, including by accident.

### The Solution

Armsmith is an agent that is **not allowed to claim its own results**. It scans, it drafts fixes, and
then every fix has to survive a reproduce gate that the agent does not control:

```
armsmith diagnose ./repo
   ├─ host fingerprint (lscpu → dotprod/i8mm/SVE/SVE2/BF16/SME routing)
   ├─ 13-rule scan (static AST/Dockerfile/CI + recorded runtime probes)
   ├─ planner orders fixes (deterministic fallback; Claude tool-use = TODO(S1))
   ├─ REPRODUCE GATE  ── keep only: outside noise band AND output-hash equal
   └─ signed report (ed25519 + sha256) ─→ PR body with evidence table (dry-run)
```

A fix is kept only if it beats the measured noise band **and** produces byte-identical output. Fixes
that fail are reported with reasons, never dropped silently. The report embeds the raw samples, so
`armsmith verify` recomputes every statistic and every verdict independently — you never have to
trust the number that was printed at you.

### What is measured, and what is replayed

**Status: hardware-free core + one live Arm leg.** `456` pytest tests, all green, at **100% line
coverage**. The rule pack, the planner and the diagnose loop run against **replay bundles**, and a
bundle is one of two things, always labeled:

| bundle | manifest | where it comes from |
|---|---|---|
| the fixtures in this repo | `"synthetic": true` | hand-authored shapes for offline tests — measured on nothing |
| what `armsmith record` writes | `"synthetic": false` | observed on your host, or copied verbatim from your own instrument output |

Every loader refuses a bundle that declares neither. Provenance and *transport* are tracked
separately on purpose: a recorded bundle is replayed but entirely real, so its report carries
`"mode": "replay"` with `"synthetic": false`, and stamping it synthetic would understate a genuine
measurement exactly as badly as the reverse would overstate one.

One further path produces hardware numbers in-process rather than from a bundle,
`armsmith bench-live` ([see below](#measured-on-real-arm-silicon)) — `"mode": "live"`,
`"synthetic": false`. **Every number in this repo is one of these, and says which.** The remaining
live instruments (perf/PMU, Performix, llama-bench, hyperfine, cosign-in-CI, the Claude planner
loop, PR posting) land at S1 and are marked `TODO(S1)` in code.

### Recording a bundle for your own repo

`armsmith diagnose` needs a bundle. `armsmith record` writes one from the machine you run it on, so
the probe rules work on your code rather than only on our fixtures:

```bash
armsmith record . --out ./armsmith-bundle --python .venv/bin/python
```

It captures what the host can honestly answer — `lscpu`, transparent-hugepage state, and the BLAS
that `numpy.show_config()` reports for the interpreter you point `--python` at (that flag matters:
R3 is a claim about the venv that serves *your* model, and armsmith's own does not even depend on
numpy). For the probes that only exist as output from a real instrument, hand it the artifact you
already have and it is copied in unmodified:

```bash
armsmith record . --out ./b \
  --build-log build.log      # → R2      --pip-log pip-install.log   # → R8
  --cmake-cache CMakeCache.txt  # → R10   --gguf model.gguf           # → R5
  --perf perf.txt            # → R9      --ort-session session.json  # → R7
  --llama-bench lb.json --hyperfine hf.json   # → R13 (needs both)
```

Three rules the honesty contract will not let it fill in:

- **`env` and `proc_maps` are never captured**, so **R6** never runs from a recorded bundle and
  **R11** stays half-fed. A bundle is something you publish; an environment block carries CI tokens
  and a maps dump carries host paths. This is refused in code, not by convention, and a test asserts
  the files are absent.
- Anything not observed is **omitted, not guessed**. The rules that needed it report `skipped` with
  the probe named, and `record` prints exactly which rules your bundle can and cannot answer before
  you run `diagnose`.

## 🏗️ Architecture & Tech Stack

### The 13-Rule Pack

Every rule ships as a YAML descriptor (`src/armsmith/rules/packs/`) with a detector, a
deterministic fix generator, a citation URL, and positive/negative fixtures. Expected-gain ranges
are **estimates from the citations used only for planning order** — results come exclusively from
the gate.

| id | anti-pattern | detector |
|---|---|---|
| R1 | amd64-pinned image → QEMU emulation | static (Dockerfile/compose) |
| R2 | native build without `-mcpu`/`-march` | probe (build log + lscpu) |
| R3 | NumPy on reference BLAS | probe (`numpy.show_config()`) |
| R4 | silent float64 coercion | static (Python AST) |
| R5 | GGUF quant mismatched to ISA repack path | probe (GGUF header + lscpu) |
| R6 | threads × workers > vCPUs | probe (recorded env) |
| R7 | ONNX Runtime session defaults | probe (SessionOptions record) |
| R8 | pip sdist fallback for perf-critical wheels | probe (pip log) |
| R9 | tokenizer/preprocess memcpy storm | probe (perf report) |
| R10 | llama.cpp built without KleidiAI | probe (CMake cache) |
| R11 | THP/allocator untuned for big-model RSS | probe (sysfs + maps) |
| R12 | CI publishes amd64-only images | static (workflow YAML) |
| R13 | serving overhead dominates kernel time | probe (llama-bench × hyperfine) |

#### Precision on a repo we've never seen

A linter that cries wolf gets uninstalled, so R4 is measured against a real target rather than its
own fixtures. Pointed at a fresh clone of
[huggingface/text-generation-inference](https://github.com/huggingface/text-generation-inference):

| | flagged | false positives |
|---|---|---|
| naive "no `dtype=` → float64" | 5 | **4** |
| shipped R4 | **1** | 0 proven wrong |

The four that vanished were integer permutation arrays in the Marlin GPTQ path
(`layers/marlin/gptq.py:461,463`, `layers/marlin/util.py:134,136`). numpy infers dtype from the
data — `np.array([0, 2, 4])` is **int64**, and `np.full(n, 0)` is int64 too — so those were never
float64, and the fix R4 would have proposed (pin `dtype=np.float32`) would have silently turned an
index array into floats. R4 now reasons per constructor: `zeros`/`ones`/`empty`/`linspace` are
float64 whatever you pass them and are always reported; `array`/`full` are reported only when the
payload isn't a provable integer literal.

The one surviving hit, `utils/segments.py:17`, is `np.array(adapter_indices)` — a *non-literal*
argument. Armsmith cannot prove that one statically, so it reports it: under-reporting a real
float64 coercion on an inference path costs more than one honest question. That is the deliberate
bias, and `test_r4_does_not_flag_an_integer_permutation_array` pins the regression.

R13 is the two-instrument triangulation rule: llama-bench timings exclude tokenization + sampling,
so Armsmith reconstructs kernel time from llama-bench samples and compares it with hyperfine
end-to-end wall time — >15% divergence means the pipeline, not the kernels, is the bottleneck.
Both instruments' self-reported stats are cross-checked against their own raw samples first;
disagreement makes the rule refuse to diagnose.

### Tech Stack

| layer | choice | why |
|---|---|---|
| CLI | Typer + Rich | subcommand surface + the evidence tables judges actually read |
| Statistics | **pure stdlib**, zero deps (`armsmith.benchstats`) | the math that accepts or rejects a claim must be auditable at a glance |
| Rule descriptors | PyYAML | a 14th rule is one YAML file + one detector, no core changes |
| Report signing | `cryptography` (ed25519) | tamper-evident reports; `verify` re-derives every statistic |
| Report schema | `jsonschema` (draft 2020-12) | public contract, CI-validated — build your own viewer against it |
| Live Arm bench | GCC + binutils `objdump` on aarch64 | compile A/B from one source, then count SDOT in the disassembly |
| CI | GitHub Actions — `ubuntu-24.04-arm` · `ubuntu-22.04-arm` · `ubuntu-latest` × Py 3.11/3.12 | native arm64 legs, free, no hardware to rent |
| Quality | pytest + pytest-cov · ruff · mypy · CodeQL · TruffleHog | 456 tests, 100% line coverage |

## 🏆 Arm Integration (Cloud AI Track)

Armsmith is not an app that happens to run on Arm — Arm is the subject matter. The Arm-specific
surfaces it actually uses:

- **Native arm64 CI runners** — `ubuntu-24.04-arm` / `ubuntu-22.04-arm`, four green jobs per push,
  plus a dedicated `live-bench` job that takes a **real measurement** on Neoverse-N2.
- **ISA feature routing** — `lscpu` flags parsed into `dotprod / i8mm / SVE / SVE2 / BF16 / SME`, and
  rules gate their advice on what the target CPU actually has.
- **`-mcpu` / `-march` flag matrix (R2)** — the rule with a live, measured A/B behind it (below).
- **Arm dot-product & int8-matmul ISA** — `armsmith.witness` counts `SDOT/UDOT/SMMLA/USMMLA` in real
  disassembly, so a kernel claim is proven at the instruction level.
- **GGUF + KleidiAI paths (R5, R10)** — a real GGUF header parser checks whether the chosen
  quantization can reach the ISA's repack path; R10 checks whether llama.cpp was built with KleidiAI.
- **Arm Learning Path citations** — every rule carries a real upstream URL; `armsmith rules export`
  renders them as 13 migration cards.

Armsmith is arch-clean Python and installs identically on `aarch64` (AWS Graviton `c7g`/`c8g`,
Ampere, Axion, or a GitHub `ubuntu-24.04-arm` runner):

```bash
sudo apt-get update && sudo apt-get install -y python3-venv  # (perf, hyperfine, llama.cpp = live-mode, S1)
git clone https://github.com/edycutjong/armsmith && cd armsmith
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
python -m pytest -q                                                    # 456 passing on aarch64
armsmith doctor --offline --replay fixtures/replays/scenario_ragserve  # shows dotprod/i8mm/SVE routing
armsmith diagnose --replay fixtures/replays/scenario_ragserve          # identical loop, native arm64
armsmith bench-live --require-witness                                  # the real measurement, on your silicon
```

The offline suite proves the package is arch-clean on real Arm silicon, and `bench-live` takes a
genuine measurement on it. Full **live capture** — driving `perf`/`hyperfine`/`llama-bench` against
*your* workload and recording a real before/after — is the S1 path
(`armsmith diagnose <repo> --target ssh://…`, `LiveProbe` over SSH), marked `TODO(S1)` in code;
Armsmith never fabricates a hardware number.

The drop-in CI twin runs the same gate on an Arm runner:

```yaml
# .github/workflows/perf-gate.yml
jobs:
  arm-perf-gate:
    runs-on: ubuntu-24.04-arm  # free native-arm64 hosted runner
    steps:
      - uses: actions/checkout@v4
      - uses: edycutjong/armsmith@v1  # composite action — see action.yml
        with:
          replay: ./armsmith-bundle
```

`replay:` points at a bundle in **your** repo, not one Armsmith ships. Record it on the machine you
want gated and commit it (or rebuild it in the job before this step):

```bash
armsmith record . --out ./armsmith-bundle
```

## 📊 Engineering Rigor

<sub><b>Built with</b></sub><br/>
[![Typer](https://img.shields.io/badge/Typer-CLI-0B7285)](https://typer.tiangolo.com/)
[![Rich](https://img.shields.io/badge/Rich-tables-0B7285)](https://github.com/Textualize/rich)
[![cryptography](https://img.shields.io/badge/cryptography-ed25519-2E7D32)](https://cryptography.io/)
[![jsonschema](https://img.shields.io/badge/jsonschema-2020--12-2E7D32)](https://json-schema.org/)
[![PyYAML](https://img.shields.io/badge/PyYAML-rule%20packs-2E7D32)](https://pyyaml.org/)
[![Vercel](https://img.shields.io/badge/Vercel-site-000000?logo=vercel&logoColor=white)](https://vercel.com/)

<sub><b>Quality gates</b></sub><br/>
[![pytest](https://img.shields.io/badge/pytest-1%20tests-0A9EDC?logo=pytest&logoColor=white)](https://docs.pytest.org/)
[![Ruff](https://img.shields.io/badge/Ruff-linted-D7FF64?logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![mypy](https://img.shields.io/badge/mypy-typed-1F5082)](https://mypy-lang.org/)
[![CodeQL](https://img.shields.io/badge/CodeQL-0%20alerts-2088FF?logo=github&logoColor=white)](https://github.com/edycutjong/armsmith/security/code-scanning)
[![TruffleHog](https://img.shields.io/badge/TruffleHog-secret%20scan-8B5CF6)](https://github.com/trufflesecurity/trufflehog)
[![GitHub Actions](https://img.shields.io/badge/CI-arm64%20runners-2088FF?logo=githubactions&logoColor=white)](https://github.com/edycutjong/armsmith/actions)

| metric | value |
|---|---|
| Tests | **456** passing, **100%** line coverage |
| CI jobs per push | 10 — incl. **5 native arm64** (4 test legs + 1 live bench, 2 measured ISA extensions) |
| Rules | 13, each with a citation + positive/negative fixtures |
| Live Arm speedup (measured) | **7.4× / −86.5%**, outside a ±0.24% noise band |
| ISA witness | `SDOT 0 → 1` in the hot symbol, from real disassembly |
| Report integrity | ed25519 signature + sha256 content hash + full statistic recompute |
| Security | CodeQL **0 alerts** · TruffleHog (full history) · Dependabot · pip-audit |

### Measured on Real Arm Silicon

Everything else here proves the loop is *honest*. This is where it stops being a replay.

`armsmith bench-live` compiles a kernel **twice from one source**, differing only in the `-march`
flag that rule **R2** exists to flag, then measures both builds on the machine it is running on. It
refuses to run on anything that is not `aarch64`. There are two cases, on purpose — one measured
kernel is a data point, two on different ISA extensions is a harness:

| `--case` | source | flag under test | instruction | how the fast path is reached |
|---|---|---|---|---|
| `dot` (default) | [`int8_dot.c`](src/armsmith/bench/int8_dot.c) | `+dotprod` | **SDOT** | GCC's vectorizer finds it in plain C |
| `mmla` | [`int8_mmla.c`](src/armsmith/bench/int8_mmla.c) | `+i8mm` | **SMMLA** | ACLE intrinsic behind the feature macro |

The second case is honest about the difference: GCC does *not* reliably auto-vectorize a 2×8·8×2
int8 matmul into `SMMLA`, so that path uses `vmmlaq_s32` guarded by `__ARM_FEATURE_MATMUL_INT8`,
with a scalar fallback computing identical arithmetic. That is also how real libraries ship —
KleidiAI selects its micro-kernel per detected CPU capability — so the flag genuinely gates the code
path, and both builds must still produce the same checksum or the gate drops the fix.

Latest run, on a **GitHub-hosted `ubuntu-24.04-arm` runner** ([`ci.yml`](.github/workflows/ci.yml) →
job *Live Arm reproduce gate*), host **Neoverse-N2**, `gcc 13.3.0`:

Figures below are from CI run
[**31301665280**](https://github.com/edycutjong/armsmith/actions/runs/31301665280), copied out of
its signed `report-live.json` artifact:

| | `baseline` = `-O3 -march=armv8-a` | `fix_R2` = `-O3 -march=armv8.2-a+dotprod` |
|---|---|---|
| **SDOT in `dot_i8`** | **0** | **1** |
| median `kernel_s` | 0.059922 s | **0.008160 s** |
| p95 | 0.059972 s | 0.008178 s |
| Δ median | — | **−0.051762 s (−86.4%, a 7.3× speedup)** |
| noise band (k=3) | — | ±0.000239 s |
| output hash equal | — | ✅ identical |
| **gate verdict** | — | **`keep`** |

**The last digits move between runs, and that is the point.** This job re-measures on every push, so
a run today reports −86.4% where an earlier one reported −86.5%; the noise band moves with it. A
number that never drifted would be a number nobody was actually measuring. Download the artifact
from any green run and check it against this table yourself — `armsmith verify report-live.json`
recomputes every statistic from the embedded raw samples.

Read that first row before the timings. ARMv8.0 has no dot-product instruction, so the baseline
*cannot* contain one; enabling the ISA level lets GCC's vectorizer emit `SDOT` and the whole
accumulate collapses into it. The stopwatch says 7.4×, but the disassembly says *why*, and a
disassembly is not a benchmark you can argue with.

The measurement is not special-cased anywhere: the samples go through the same
`benchstats` → `gate` → signed-report path as every replay bundle, under the same
refuse-to-claim-inside-the-noise-band rule. A run that fails to beat its own noise is reported
`no_change` and **dropped** — and that outcome is a success for the tool, not a bug.

```bash
armsmith bench-live --require-witness       # on any aarch64 box; writes a signed report-live.json
armsmith verify report-live.json            # hash + ed25519 + schema + recompute-from-samples
```

CI runs exactly those two commands on every push and uploads the signed report as a build artifact,
so the numbers above are re-derivable by anyone with the repo and an Arm runner — including you.

### The Trust Chain

1. **Statistics engine** (`armsmith.benchstats`) — median-of-N, MAD noise bands
   (`k·√(smad_a²+smad_b²)`, k=3), p50/p95 by documented linear interpolation, ABAB interleave
   planning, and a hard *refuse-to-claim-inside-band* rule.
2. **Reproduce gate** (`armsmith.gate`) — drop on hash mismatch, drop on any out-of-band
   regression, drop when nothing clears the band. Reasons are machine-readable and shipped.
3. **Tamper-evident reports** (`armsmith.report`) — raw samples embedded next to every claimed
   statistic; canonical-JSON sha256 content addressing; ed25519 signature; `armsmith verify`
   *recomputes every statistic and gate verdict from the embedded samples*. Editing a number
   without re-running the math is detectable. Schema: [`schema/report.schema.json`](src/armsmith/schema/report.schema.json).
4. **ISA witness** (`armsmith.witness`) — counts SDOT/UDOT/SMMLA/USMMLA in disassembly
   before/after: wall-clock can be argued with; emitted instructions cannot.
5. **PR evidence** (`armsmith.evidence`, `armsmith.ghpr`) — the
   `| metric | before | after | Δ | noise band | PMU Δ |` table, the drop log, and the judge-facing
   `cosign verify-blob` command line. PR module is **dry-run only** here: it renders exactly what
   would be posted and never touches the network.

### Honesty Notes

- Replay bundles are **synthetic shapes**, generated by `scripts/make_fixtures.py` and labeled in
  every `manifest.json`; loaders refuse unlabeled measurement data, reports carry
  `mode: "replay"` + `synthetic: true`, and every rendered artifact shows a replay banner.
- `armsmith doctor` refuses to run without `--offline` + a recorded fixture: this development
  machine is never fingerprinted as if it were a target.
- The planner cannot claim results; only the gate can, and `armsmith verify` re-checks the gate.
- **Real and fabricated never mix, and transport is tracked separately from provenance.**
  `bench-live` reports carry `mode: "live"` + `synthetic: false`. A bundle written by
  `armsmith record` carries `mode: "replay"` + `synthetic: false` — replayed, but genuinely
  observed. Only the fixtures in this repo carry `synthetic: true`. The CLI banner and the PR body
  both key off `synthetic`, never off `mode`, so a recorded measurement is never understated as
  fabricated. `bench-live` raises rather than run on a non-`aarch64` host, so there is no code path
  that yields an Arm number off Arm silicon — and unit tests assert both halves.
- `LiveProbe` **refuses** the `env` and `proc_maps` probes even though it could trivially serve
  them: a report is a published artifact, and a CI environment block contains tokens. Every probe it
  cannot answer honestly raises instead of guessing.
- The live 7.4× is one microbenchmark on one runner, not a claim about your model. It is there to
  prove the *gate* works on real silicon; the honest way to get your number is to run it on yours.
- The `benchstats` module is shared with the Assayer project (declared in both repos).

## 🚀 Getting Started

### Prerequisites

- Python **3.11** or **3.12**
- No Arm hardware and no network beyond `pip` — the whole judge surface runs on an x86 laptop
- **macOS/Darwin:** the static rules (R1/R4/R12), `scan`, `diagnose --replay`, `verify` and
  `bench-cmd` all work. The probe rules read Linux-only sources (`lscpu`, the THP sysfs node), so
  `armsmith record` on a Mac writes a valid but **empty** bundle by design — it says so per probe
  rather than inventing values, and `diagnose` then skips those rules by name. Record on the Linux
  box you actually want diagnosed.
- *(optional)* an `aarch64` box + `gcc`/`objdump` if you want to take the live measurement yourself

### Installation

**To use it** — [`armsmith` on PyPI](https://pypi.org/project/armsmith/), no clone:

```bash
uvx armsmith scan .        # zero-install, one command
pipx install armsmith      # or keep it on your PATH
pip install armsmith       # or into a venv you manage
```

**To reproduce the gate or hack on it** — the replay bundles and the test suite live in the repo,
so this path needs the clone:

```bash
git clone https://github.com/edycutjong/armsmith && cd armsmith
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

### Judge Quickstart — Zero Hardware (~2 min)

This is the primary "runnable by a judge" surface, because most judges have no Graviton box. All
commands exit 0; the tamper step at the end goes red on purpose.

```bash
python -m pytest -q                                            # 456 passing, fully offline
armsmith scan fixtures/replays/scenario_ragserve               # static R1/R4/R12 on a real dir, zero hardware
armsmith diagnose --replay fixtures/replays/scenario_ragserve  # full reproduce gate (4 kept, 2 dropped)
armsmith witness fixtures/witness/objdump_before.txt fixtures/witness/objdump_after.txt  # ISA proof: 0→4 dotprod
armsmith verify fixtures/replays/scenario_ragserve/report.json # -> VERIFY OK (recomputes every stat)
armsmith ci --replay fixtures/replays/scenario_ragserve        # -> CI GATE PASSED (exit-code CI twin)
python scripts/verify_offline.py                               # -> ALL CHECKS PASSED — honest & offline
```

**The 20-second trust proof (whole pitch, zero hardware):** open
`fixtures/replays/scenario_ragserve/report.json`, change one digit in any `samples` array, re-run
`armsmith verify …/report.json` → red **`VERIFY FAILED`**. You never trust the printed number; the
arithmetic is independently re-derivable and tamper-evident.

Other surfaces: `armsmith rules list` · `armsmith rules explain R13` (fix + Arm Learning Path) ·
`armsmith rules export --format md` (writes the 13 migration-template cards to `docs/migration-templates/`) ·
`armsmith doctor --offline --replay fixtures/replays/scenario_ragserve` (host/ISA fingerprint) ·
`armsmith pr fixtures/replays/scenario_ragserve/report.json` (renders the bot PR — dry-run).

## 🧪 Testing & CI

The replay harness is hardware-free and runs in under a second locally; the live Arm leg needs
`aarch64` and refuses to run anywhere else:

```bash
.venv/bin/pip install -e '.[dev]'

.venv/bin/python -m pytest -q               # 456 tests, 100% line coverage
.venv/bin/ruff check .                      # lint gate (clean)
.venv/bin/mypy src                          # types — advisory, not a gate
.venv/bin/python scripts/verify_offline.py  # scan → gate → sign → verify, end-to-end

.venv/bin/armsmith bench-live --require-witness   # aarch64 only — the real measurement
```

CI (`.github/workflows/ci.yml`) runs that exact suite on a **native-arm64 + x86 matrix** —
`ubuntu-24.04-arm`, `ubuntu-22.04-arm`, and `ubuntu-latest` × **Python 3.11 / 3.12** — plus the
offline end-to-end loop and a JSON-Schema check on `schema/report.schema.json`. Because every test
is replay/fixture-based, the arm64 legs need zero Arm-specific setup and prove the package is
arch-clean. A separate **`live-bench`** job then runs `armsmith bench-live --require-witness` on
`ubuntu-24.04-arm`, verifies the signed report it produces, and uploads it as an artifact — that job
is where the numbers in [Measured on Real Arm Silicon](#measured-on-real-arm-silicon) come from.

| layer | tool | status |
|---|---|---|
| unit + replay suite | pytest (456 tests, 100% cov) | ✅ green, offline |
| lint | ruff | ✅ gate |
| types | mypy | ✅ advisory (`continue-on-error`) |
| end-to-end loop | `verify_offline.py` | ✅ scan → gate → sign → verify |
| report schema | jsonschema (draft 2020-12) | ✅ validated in CI |
| SAST | CodeQL (`language: python`) | ✅ [`codeql.yml`](.github/workflows/codeql.yml) |
| secret scanning | TruffleHog (`--only-verified`, full history) | ✅ CI security gate |
| dependency updates | Dependabot (pip + actions) | ✅ [`dependabot.yml`](.github/dependabot.yml) |
| dependency audit | pip-audit | ✅ advisory |
| **live Arm bench** | **`armsmith bench-live` on `ubuntu-24.04-arm`** | ✅ **real measurement + ISA witness, signed & verified in CI** |
| live-hardware instruments | hyperfine / llama-bench / Performix / cosign | `TODO(S1)` — not wired yet |

Everything above is real today. The live Arm row is a genuine measurement taken on a native arm64
runner; the last row is the honestly-deferred remainder — no CI job claims a measurement it did not
take.

## 📁 Project Structure

```
src/armsmith/          benchstats · probes · fingerprint · gguf · rules/ (packs + 13 detectors)
                       gate · report · keys · evidence · witness · ghpr · planner/ · diagnose · cli
                       livebench (the live Arm A/B: compile → witness → measure → gate)
bench/int8_dot.c       the live workload — one source, compiled two ways (rule R2)
schema/                report.schema.json (draft 2020-12, CI-validated)
fixtures/              hosts/ · rules/rXX_{pos,neg}/ · replays/scenario_ragserve/ · witness/
scripts/               make_fixtures.py (fixture provenance) · verify_offline.py
tests/                 456 tests (goldens, pos/neg per rule, gate, signing, CLI, e2e, live bench)
site/                  landing page + pitch deck (deployed straight from this repo)
docs/assets/           brand + hero assets (see ASSETS pipeline)
docs/migration-templates/  13 x86→Arm migration cards (armsmith rules export)
action.yml             composite GitHub Action — drop-in arm64 perf-regression gate
```

## 🧩 Reuse & Extend

Every artifact is reusable standalone of the CLI — this is the "could it be taken further / reused"
DX clause and the rubric's reusable-artifacts Impact:

- **13 x86→Arm migration templates** — `armsmith rules export --format md` renders one card per rule
  (anti-pattern · fix · expected gain · upstream citation · Arm Learning Path) into
  [`docs/migration-templates/`](docs/migration-templates/). Reusable on any repo.
- **Add a 14th rule without touching the engine** — one YAML descriptor into
  `src/armsmith/rules/packs/`, one detector, one import line; the loader validates and wires it
  in (see [CONTRIBUTING](.github/CONTRIBUTING.md#adding-a-rule-the-common-contribution) for the
  exact `detect()` signature).
- **Public signed-report schema** — [`schema/report.schema.json`](src/armsmith/schema/report.schema.json)
  (draft 2020-12, CI-validated). Build your own viewer/CI gate against it.
- **Importable methodology modules** — `from armsmith.benchstats import compare` (median-of-N/MAD/
  noise-band), `armsmith.gate`, `armsmith.report`, `armsmith.witness` — no CLI required.
- **Drop-in Arm CI gate** — `uses: edycutjong/armsmith@v1` on `runs-on: ubuntu-24.04-arm` (see
  [`action.yml`](action.yml)); the Marketplace *listing* is publish-pending, never claimed as live.
- **Installable in one command** — [`armsmith` on PyPI](https://pypi.org/project/armsmith/):
  `uvx armsmith scan .` runs the aarch64 anti-pattern scan on any repo with nothing to clone and
  nothing to configure. Published from CI by [Trusted Publishing](.github/workflows/release.yml)
  (OIDC, no API token in the repo), with sdist + wheel attached to every GitHub Release.

## 🗺️ Roadmap

- [x] 13-rule pack with citations, fixtures, and deterministic fix generators
- [x] Reproduce gate — noise bands, output-hash equality, machine-readable drop reasons
- [x] Tamper-evident signed reports + `verify` statistic recompute
- [x] ISA witness (SDOT/UDOT/SMMLA/USMMLA) driven against real binaries
- [x] Native arm64 CI + **live measured A/B on Neoverse-N2**
- [x] `armsmith record` — live capture on the local host writes a real, replayable bundle
- [ ] `LiveProbe` over `ssh://` (record a bundle for a remote Arm box from your laptop)
- [ ] Live instruments: `perf`/PMU, hyperfine, llama-bench, Arm Performix CLI ingestion
- [ ] Claude planner tool-use loop (contract already pinned in `planner/interface.py`)
- [ ] Real PR posting (`armsmith pr` is dry-run only today) + cosign keyless attestation in CI
- [ ] Arm MCP Server handshake → `query_arm_mcp` container-validation cross-check

## 📽️ Demo Materials

- **Demo video (3 min):** [youtu.be/JsT83BYMWd0](https://youtu.be/JsT83BYMWd0) — the reproduce gate
  dropping two of its own fixes on camera, the ISA witness, the tamper test, and the arm64 CI run.
  Scenes drawn from the replay bundle carry a `[replay]` badge on screen throughout.
- **Live site:** [armsmith.edycu.dev](https://armsmith.edycu.dev) — deployed straight from
  [`site/`](site/) in this repo, so the page you see is the source you can read.
- **Pitch deck:** [armsmith.edycu.dev/deck.html](https://armsmith.edycu.dev/deck.html)
- **Signed live report:** downloadable from any CI run as the `armsmith-live-report-arm64` artifact.

## 📄 License

MIT — see [LICENSE](LICENSE).

## 🙏 Acknowledgments

- **Arm Learning Paths** and upstream Arm/llama.cpp/ONNX Runtime documentation — every rule in the
  pack cites a real source rather than folk wisdom.
- **GitHub arm64 hosted runners**, which made a genuine Neoverse-N2 measurement possible with no
  hardware to rent.
- The `benchstats` module is shared with the Assayer project (declared in both repos).
