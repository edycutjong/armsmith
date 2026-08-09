# Armsmith as a library

The CLI is one consumer of these modules; nothing here needs it. Everything below
is importable from a plain `pip install armsmith`, has no network access, and no
Arm hardware requirement unless stated.

```bash
pip install armsmith
```

---

## `armsmith.benchstats` — the statistics the gate runs on

The part most worth stealing. Median-of-N with a scaled-MAD noise band, and a
refuse-to-claim rule: a delta inside the band is **`no_change`**, never a win.

```python
from armsmith.benchstats import compare, summarize, plan_interleaved
```

### `summarize(samples: Sequence[float]) -> SampleStats`

| field | meaning |
|---|---|
| `n` | sample count |
| `median`, `p50`, `p95` | order statistics |
| `mad` | median absolute deviation |
| `smad` | MAD scaled by 1.4826 — a robust σ estimate |
| `mean`, `stddev`, `min`, `max` | reported, never used for verdicts |

### `compare(baseline, candidate, direction=Direction.LOWER_BETTER, k=3.0, min_samples=3) -> Comparison`

```python
from armsmith.benchstats import compare, Direction

c = compare([1.00, 1.02, 0.99], [0.60, 0.62, 0.59])
c.verdict      # Verdict.IMPROVED
c.delta        # -0.40  (median difference)
c.delta_pct    # -40.0
c.band         # k * smad(baseline) — the noise floor
c.reason       # human-readable, quoted verbatim into reports
```

`verdict` is `IMPROVED`, `REGRESSED`, `NO_CHANGE`, or `INSUFFICIENT_DATA`.
**`NO_CHANGE` is returned whenever `abs(delta) <= band`**, regardless of sign —
that single line is the whole honesty thesis.

### `plan_interleaved(variants, reps, warmup) -> list[Slot]`

ABAB scheduling, so machine drift lands on both sides instead of on whichever
ran second. Each `Slot` has `.variant` and `.warmup`.

---

## `armsmith.gate` — keep/drop decisions

```python
from armsmith.gate import GateConfig, MeasurementSet, run_gate

outcome = run_gate(baseline_set, [candidate_set], GateConfig(primary_metrics=("wall_s",)))
outcome.results[0].verdict   # "keep" | "drop"
outcome.results[0].reasons   # why — always populated, including for drops
```

A candidate is **kept only if** at least one primary metric improved outside its
noise band **and** the output hash matches. `MeasurementSet` carries
`variant`, `instrument`, `metrics` (name → samples), `output_sha256`,
`rule_id`, and a required boolean `synthetic` — loaders raise on records that
omit it, so unlabelled measurement data cannot enter a report.

---

## `armsmith.report` — signed, self-verifying reports

```python
from armsmith.report import build_report, sign_report, verify_report, schema_path
```

- `build_report(*, mode, scenario, repo, host, findings, outcome, ..., synthetic=None)`
  — `mode` is transport (`"live"`/`"replay"`), `synthetic` is provenance. They are
  **separate axes**: a bundle from `armsmith record` is replayed but real.
- `sign_report(report, key_dir=None)` — ed25519 over canonical JSON.
- `verify_report(report)` — content hash, signature, JSON Schema, **and** a full
  recompute of every claimed statistic from the embedded raw samples.
- `schema_path()` — the packaged `report.schema.json`, also served at its `$id`:
  <https://armsmith.edycu.dev/schema/report.schema.json>

---

## `armsmith.witness` — instruction-level proof

```python
from armsmith.witness import count_witness, witness_delta

w = count_witness(objdump_text)     # any objdump output, any host
w.dotprod, w.int8_matmul, w.total, w.counts
```

Counts `SDOT`/`UDOT`/`SMMLA`/`USMMLA` in disassembly. Pure text analysis — it
needs no Arm hardware and no binary, only the objdump text.

---

## `armsmith.rules` — the rule pack as data

```python
from armsmith.rules import load_pack, run_rule, run_all

specs = load_pack()                       # {"R1": RuleSpec, ...}
finding = run_rule(specs["R4"], repo_path, probe=None)
finding.status        # FindingStatus.MATCHED | CLEAN | SKIPPED
finding.evidence      # every match, with file:line
finding.fix.kind      # "code_suggestion" | "advisory" | "ci_patch" | ...
```

`RuleSpec` carries `title`, `kind`, `requires` (probe kinds), `summary`,
`fix_generator`, `expected_gain_range` (an **estimate**, never a result),
`citation_url`, `learning_path`, and optional `before`/`after` snippets.

Pass `probe=None` to run static rules only — that is exactly what
`armsmith scan` does, and it needs no bundle.

---

## `armsmith.benchcmd` — gate your own commands

```python
from armsmith.benchcmd import run_command_bench

res = run_command_bench("./bench_before.sh", "./bench_after.sh", measured_rounds=7)
res.baseline.to_measurement()    # feed straight into run_gate
```

Refuses non-`aarch64` hosts, refuses non-deterministic workloads, and reports no
ISA witness because there is no binary to disassemble.

---

## Stability

Everything above is exercised by the test suite at 100% line coverage. The report
JSON Schema is the most stable contract here — build against that if you only need
the data. Module APIs may change before 2.0; the schema will not without a
`schema_version` bump.
