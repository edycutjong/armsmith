# Armsmith — PROGRESS (hardware-free core + the first live Arm leg)

Session: 2026-07-04, extended 2026-08-08 · build target `build/` · specs FROZEN
(deviations → DEVIATIONS.md).
Suite: **456 pytest tests, 456 passing, 0 failing, 100% line coverage**
(`.venv/bin/python -m pytest`) plus
`scripts/verify_offline.py` end-to-end green. Every measurement-shaped value is either a labeled
synthetic replay fixture (`synthetic: true`) or a real live measurement (`mode: "live"`,
`synthetic: false`) — nothing is unlabeled, and nothing is fabricated.

## ✅ Live Arm leg — added 2026-08-08

`armsmith bench-live` (`src/armsmith/livebench.py` + `bench/int8_dot.c`) is the first code path in
this repo that produces a hardware number. It compiles one source twice — `-O3 -march=armv8-a` vs
`-O3 -march=armv8.2-a+dotprod`, the exact flag R2 flags — then on the host it runs on: reads a real
`lscpu` for the ISA table, disassembles the `dot_i8` symbol in **both** binaries and counts
SDOT/UDOT/SMMLA/USMMLA, ABAB-interleaves the timed runs through `benchstats.plan_interleaved`, and
feeds the samples to the ordinary `gate` — same refuse-to-claim-inside-the-band rule, no special case.

Measured on a GitHub-hosted `ubuntu-24.04-arm` runner (**Neoverse-N2**, gcc 13.3.0), CI run
[31247155528](https://github.com/edycutjong/armsmith/actions/runs/31247155528):

| | baseline | fix_R2 |
|---|---|---|
| SDOT in `dot_i8` | 0 | **1** |
| median `kernel_s` | 0.059975 s | **0.008123 s** |
| Δ median | — | **−86.5% (7.4×)**, band ±0.000144 s (k=3) |
| output hash | — | identical |
| gate | — | **keep** |

`LiveProbe` is no longer a stub for what it can observe honestly (`lscpu`, THP, harness-captured
disassembly). It refuses `env` and `proc_maps` **on purpose** — a report is published and a CI
environment block carries tokens — and raises for every kind it cannot answer. Remote (`ssh://`)
targets and the remaining instruments stay TODO(S1).

## ✅ Done (hardware-free core)

- **benchstats engine** — median/MAD/scaled-MAD, p50/p95 (documented linear interpolation),
  combined noise band `k·√(smad²+smad²)` (k=3 default), refuse-to-claim-inside-band verdicts,
  insufficient-samples refusal (n<3), ABAB interleave planner (warmups first, strict alternation,
  N-variant round-robin), instrument self-report cross-check (squeeze pass 2 #3). 35 golden tests.
- **Probe layer** — `Probe` interface; `ReplayProbe` bundle backend (manifest with mandatory
  `synthetic` + provenance labels; loaders refuse unlabeled data); `LiveProbe` implemented for
  local exec (see the live-Arm section above). 16 probe kinds mapped.
- **Host fingerprint** — lscpu parser + ISA feature table (dotprod/i8mm/sve/sve2/bf16/sme),
  fixture-only capture (never fingerprints this machine), provenance label carried into reports.
- **Rule pack: all 13 rules** — YAML descriptors (id/kind/requires/summary/fix_generator/
  expected_gain_range/gain_note/citation_url/confidence, all citations real https URLs) + loader
  with 1:1 registry validation. Static detectors fully real: R1 (Dockerfile/compose platform pins,
  patch generated), R4 (numpy AST scan w/ alias handling, file:line sites), R12 (workflow YAML
  build matrix, buildx/native-runner fix). Probe detectors real against replay fixtures: R2, R3,
  R5 (real GGUF header parser + stub writer), R6, R7 (squeeze #5 cross-multiply), R8, R9
  (threshold 15%, LLM-fix marked TODO(S1)), R10 (KleidiAI + SME sweep + `--device none` note),
  R11, R13 (two-instrument divergence, >15% threshold, crosscheck-refusal on corrupt data).
  Positive + negative fixture test per rule (plus extra variants: r05 no-dotprod, r10 non-ggml,
  r13 corrupt), skip-not-guess tests for missing probes.
- **Reproduce gate** — output-hash equality (required by default), per-metric direction registry
  (refuses unknown metrics rather than guessing), regression veto, in-band drop with reasons,
  primary-metric scoping, advisory PMU deltas, provenance-labeled measurement loader.
- **Report model** — canonical-JSON sha256 content addressing; ed25519 sign (`keys init`,
  0600 PEM, rotate with --force) / `verify` re-hashes, checks signature (+ optional trusted key),
  validates `schema/report.schema.json` (draft 2020-12), and **recomputes every summary statistic
  and gate verdict from embedded raw samples** (tamper tests prove detection of metric, verdict,
  comparison, and body tampering).
- **Evidence renderer** — `| metric | before | after | Δ | noise band | PMU Δ |` table, replay
  banner, kept + dropped (with reasons) sections, rule citations, ed25519 footer, judge-facing
  `cosign verify-blob` command string (sdk_cosign.md shape; command only, TODO(S1) wiring).
- **GitHub PR module — dry-run only** — one commit per KEPT fix, branch/title/labels/body
  rendered, `render_dry_run` explicitly states nothing was sent; test asserts no network modules load.
- **Planner** — tool-use interface pinned (`scan_repo`, `query_knowledge`, `query_arm_mcp`,
  `propose_patch`, `budget_remaining` with JSON schemas); DeterministicPlanner fallback
  (confidence → gain-midpoint → numeric-id total order; max_fixes cap; zero-budget refusal);
  ClaudePlanner stub (model `claude-sonnet-5` per frozen spec) raising TODO(S1) — zero API code.
- **CLI (Typer)** — `diagnose --replay` (Rich tables, signed report, `--pr-dry-run`),
  `rules list|explain`, `verify`, `keys init`, `doctor --offline` (refuses non-offline;
  fixture/bundle fingerprint + ISA table + sysreport TODO(S1) note), `version`.
- **ISA-witness (Tier A #1)** — objdump text parser counting SDOT/UDOT/SMMLA/USMMLA + before/after
  delta narrative, fixture-tested AND driven against real binaries by `bench-live` (0→1 SDOT
  measured on Neoverse-N2). Hottest-symbol selection via perf on the target stays TODO(S1).
- **Fixtures** — 94 files, ALL generated by `scripts/make_fixtures.py` (auditable provenance):
  3 lscpu host shapes, 29 rule bundles, witness disassembly pair, `scenario_ragserve` demo bundle
  (7 matched rules; gate = 4 keeps + 1 in-band drop + 1 hash-mismatch drop).
- **Repo scaffold** — pyproject (console script), MIT LICENSE, .gitignore, README with
  docs/assets/readme-hero.svg embed + honest scope banner, assets copied from spec `assets/`
  (node_modules excluded), CI workflow on verified `ubuntu-24.04-arm`/`ubuntu-22.04-arm` +
  `ubuntu-latest` matrix (suite is replay-based → runs on all three), `scripts/verify_offline.py`.

## Test count vs COMPLEXITY target

**456 green now** vs blueprint target **150** (which included hardware-phase tests), at 100% line
coverage. The 2026-08-09 additions cover `armsmith record` (the honesty contract: manifest declares
`synthetic: false`, refused probes are never written, unobservable probes are omitted rather than
guessed) and R4's dtype-inference guard. Of the earlier 234, 208 are
hardware-free (rule pack 49 · benchstats 39 · gate/report/keys 37 · probes/fingerprint/gguf 26 ·
CLI/e2e 24 · evidence/witness/ghpr 22 · planner 11); the 2026-08-08 additions cover the live Arm
leg (livebench honesty invariants + workload-contract parsing + LiveProbe refusals), one of which
only executes on aarch64 Linux and is skipped elsewhere. Hardware-phase tests still OWED at S1+:
live-instrument adapters (hyperfine/llama-bench/perf exec), Performix JSON ingestion, sysreport
parsing, cosign attest/verify execution, noise-floor study assertions, live PR posting, MCP
handshake — the categories COMPLEXITY counted toward its 25 replay-integration/10 nightly split.

## ▶ Next (S1 spike, per BUILD_PLAN Phase 0 — needs hardware/accounts)

1. Graviton boxes (c8g.4xlarge spot + t4g.small), AMI + setup script, cost receipts.
2. Performix CLI install + one real capture; record exact verbs + machine-readable schema.
3. Arm MCP Server handshake; enumerate tool names; wire `query_arm_mcp` + R1/R12 validator
   cross-check (Tier A #2).
4. arm64 runner smoke on a scratch public repo (labels already wired in ci.yml).
5. ~~LiveProbe~~ **done for local exec** (lscpu/THP/captured disassembly, 2026-08-08), and
   ~~bundle recording~~ **done 2026-08-09**: `armsmith record` writes the bundle layout
   ReplayProbe reads, with `"synthetic": false`, so probe rules run on a stranger's repo rather
   than only on our fixtures. Still owed: `ssh://` targets, to record a bundle for a remote Arm
   box from a laptop.
6. ClaudePlanner tool-use loop (contract pinned in planner/interface.py; model per spec).
7. `armsmith pr` real posting (PyGithub), cosign keyless in CI, sysreport in `doctor`.

## ⚠ Blockers / risks

- Performix CLI is account-gated — exact verbs unverifiable until S1 (fallback: perf + flamegraph).
- Arm MCP tool names only enumerable at handshake — `query_arm_mcp` is garnish until then.
- Replay fixtures are synthetic shapes; any drift between them and real instrument output
  (esp. llama-bench JSON field names on current master) must be reconciled at S1 recording time.
- arm64 hosted runners are public-preview — queue-time spikes feed the noise-floor study, but CI
  latency may vary.

## TODO(S1) inventory (grep `TODO(S1)`)

`probes.LiveProbe` · `planner/claude.py` (tool-use loop) · `cli.doctor` (sysreport embed,
live capture) · `evidence` (cosign wiring beyond command string) · `ghpr` (real submitter) ·
`keys` (OS keychain / ARMSMITH_KEY env path) · `witness` (objdump-on-target + hottest-symbol
selection) · R9 fix drafting via planner · ci.yml live-bench job.

## Tier-A COMPLEXITY coverage (pass 3)

| item | status |
|---|---|
| 1. ISA-witness | DONE — parser + delta + fixtures, and objdump now runs on real binaries in CI (0→1 SDOT on Neoverse-N2); perf-hottest-symbol selection at S1 |
| 2. MCP container-validation cross-check | deferred to S1 handshake (interface note in planner tools) |
| 3. Formal report schema, CI-validated | DONE (`schema/report.schema.json` + verify + ci.yml check) |
| 4. SLSA provenance on releases | deferred to S1/Phase-3 (release-time attestations) |
| 5. benchstats as own PyPI package | module isolated + dependency-free, ready to split; publish deferred |

Squeeze pass 2 items landed: #1 R13 rule · #3 llama-bench per-rep ingestion + stddev crosscheck ·
#5 R7 cross-multiply · #6 CI pinned to verified arm labels · #7 cosign footer line. (#2 R10 SME
sweep encoded in fix commands; #4 doctor sysreport depth = S1.)
