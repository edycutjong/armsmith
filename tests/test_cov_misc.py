"""Edge branches of the gate, the fingerprint, the stats and three detectors.

These cover the guard clauses the happy-path suites never reach: refusing a
malformed measurement record, refusing to claim a win with nothing comparable,
surviving an unparseable lscpu CPU count, ordering unattributed bench records
last, the zero-stddev cross-check branch, and the three detector early-outs
(no compiler invocations / no parseable perf rows / unparseable THP state).

All fixture data written here is synthetic and labeled as such.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from armsmith import benchstats, diagnose
from armsmith.benchstats import Direction, Verdict
from armsmith.fingerprint import capture_fingerprint
from armsmith.gate import GateConfig, MeasurementSet, evaluate_fix, load_measurement
from armsmith.probes import ReplayProbe
from armsmith.rules import FindingStatus, load_pack, run_rule

SYNTHETIC_PROVENANCE = (
    "hand-authored synthetic fixture for offline tests; values are illustrative "
    "shapes only and were NOT measured on any hardware"
)

LSCPU_NEOVERSE = """\
Architecture:            aarch64
CPU op-mode(s):          64-bit
CPU(s):                  16
Vendor ID:               ARM
Model name:              Neoverse-N2
Flags:                   fp asimd asimddp i8mm sve sve2 bf16
"""


@pytest.fixture()
def make_bundle(tmp_path: Path):
    """Write a minimal, provenance-labeled replay bundle and return its dir."""

    def _make(name: str, probes: dict[str, str]) -> Path:
        bundle = tmp_path / name
        (bundle / "probes").mkdir(parents=True)
        (bundle / "manifest.json").write_text(
            json.dumps(
                {
                    "synthetic": True,
                    "provenance": SYNTHETIC_PROVENANCE,
                    "scenario": f"cov fixture {name}",
                }
            ),
            encoding="utf-8",
        )
        for filename, text in probes.items():
            (bundle / "probes" / filename).write_text(text, encoding="utf-8")
        return bundle

    return _make


@pytest.fixture(scope="module")
def specs():
    return load_pack()


def _run_rule(specs, rule_id: str, bundle: Path):
    probe = ReplayProbe(bundle)
    return run_rule(specs[rule_id], probe.repo_dir, probe)


# --- gate: MeasurementSet.to_dict --------------------------------------------

def test_measurement_to_dict_round_trips_through_load_measurement(tmp_path: Path):
    """to_dict() must serialize a record load_measurement accepts unchanged."""
    ms = MeasurementSet(
        variant="fix_R9",
        instrument="hyperfine",
        metrics={"wall_s": [2.0, 1.9, 2.1], "rss_peak_mb": [510.0, 511.5, 509.0]},
        pmu={"cycles": 8.0e9, "ipc": 1.25},
        output_sha256="b" * 64,
        rule_id="R9",
        synthetic=True,
    )
    payload = ms.to_dict()
    # the provenance flag is what load_measurement refuses records without
    assert payload["synthetic"] is True
    assert payload["rule_id"] == "R9"
    assert payload["metrics"]["wall_s"] == [2.0, 1.9, 2.1]

    path = tmp_path / "fix_R9.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    reloaded = load_measurement(path)
    assert reloaded == ms


# --- gate: load_measurement sample-list guard ---------------------------------

@pytest.mark.parametrize(
    "samples",
    [[], 2.5, {"p50": 2.5}, None],
    ids=["empty-list", "bare-scalar", "dict", "null"],
)
def test_load_measurement_rejects_metric_without_sample_list(tmp_path: Path, samples):
    """A metric must carry a non-empty list of samples — no summaries, no nulls."""
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "synthetic": True,
                "variant": "fix_R2",
                "instrument": "hyperfine",
                "metrics": {"wall_s": samples},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        load_measurement(path)
    assert "'wall_s'" in str(exc.value)
    assert "non-empty sample list" in str(exc.value)


# --- gate: nothing comparable ------------------------------------------------

def test_gate_drops_when_no_metric_has_a_declared_direction():
    """Undeclared metrics are ignored, so the gate has nothing to judge → drop.

    The gate must never keep a fix by default: with zero comparisons it says so
    explicitly rather than falling through to the "in-band" wording.
    """
    same_hash = "c" * 64
    baseline = MeasurementSet(
        variant="baseline",
        instrument="custom",
        metrics={"widgets_per_fortnight": [10.0, 10.2, 9.8, 10.1, 10.0]},
        output_sha256=same_hash,
    )
    candidate = MeasurementSet(
        variant="fix_R9",
        instrument="custom",
        metrics={"widgets_per_fortnight": [99.0, 98.5, 99.5, 99.2, 98.8]},
        output_sha256=same_hash,
        rule_id="R9",
    )

    res = evaluate_fix(baseline, candidate)

    assert res.verdict == "drop"
    assert res.output_hash_equal is True          # correctness was fine…
    assert res.comparisons == {}                  # …but nothing was judgeable
    assert any("no declared direction" in r for r in res.reasons)
    assert "no shared, direction-declared metrics to compare" in res.reasons
    assert not any("noise band" in r for r in res.reasons)

    # declaring the direction turns the very same numbers into a keep
    cfg = GateConfig(directions={"widgets_per_fortnight": Direction.HIGHER_BETTER})
    kept = evaluate_fix(baseline, candidate, cfg)
    assert kept.verdict == "keep"
    assert kept.comparisons["widgets_per_fortnight"].verdict is Verdict.IMPROVED


def test_gate_drops_when_baseline_and_candidate_share_no_metrics():
    """Disjoint metric names leave nothing to compare — same explicit reason."""
    same_hash = "d" * 64
    baseline = MeasurementSet(
        variant="baseline",
        instrument="hyperfine",
        metrics={"wall_s": [2.0, 2.01, 1.99, 2.02, 1.98]},
        output_sha256=same_hash,
    )
    candidate = MeasurementSet(
        variant="fix_R6",
        instrument="llama-bench",
        metrics={"tokens_s_tg": [40.0, 41.0, 39.5, 40.5, 40.2]},
        output_sha256=same_hash,
        rule_id="R6",
    )

    res = evaluate_fix(baseline, candidate)

    assert res.verdict == "drop"
    assert res.comparisons == {}
    assert "no shared, direction-declared metrics to compare" in res.reasons


# --- fingerprint: unparseable CPU count ---------------------------------------

def test_capture_fingerprint_survives_unparseable_cpu_count(make_bundle):
    """A non-numeric CPU(s) value degrades to 0 without losing ISA routing."""
    lscpu = LSCPU_NEOVERSE.replace("CPU(s):                  16", "CPU(s):                  n/a")
    bundle = make_bundle("fp_bad_cpus", {"lscpu.txt": lscpu})

    fp = capture_fingerprint(ReplayProbe(bundle), {"instance": "c7g.4xlarge"})

    assert fp.cpus == 0                       # the guard clause, not a crash
    assert fp.model_name == "Neoverse-N2"     # everything else still parsed
    assert fp.architecture == "aarch64"
    assert fp.instance == "c7g.4xlarge"
    assert fp.isa.dotprod and fp.isa.i8mm and fp.isa.sve2 and fp.isa.bf16


# --- diagnose: unattributed bench records order last --------------------------

def test_unattributed_bench_record_is_ordered_after_planned_fixes(
    scenario_bundle: Path, tmp_path: Path
):
    """A bench record with rule_id=null sorts last, whatever its filename.

    Named "aaa_manual_tweak" so alphabetical order would put it first: only the
    plan-priority ordering can push it behind the rule-attributed fixes.
    """
    bundle = tmp_path / "ragserve_plus_manual"
    shutil.copytree(scenario_bundle, bundle)
    baseline = json.loads((bundle / "bench" / "baseline.json").read_text(encoding="utf-8"))
    manual = dict(baseline)
    manual["variant"] = "aaa_manual_tweak"
    manual["rule_id"] = None
    (bundle / "bench" / "aaa_manual_tweak.json").write_text(json.dumps(manual), encoding="utf-8")

    result = diagnose.run_replay_diagnosis(bundle, sign=False)

    variants = [f["variant"] for f in result.report["fixes"]]
    assert "aaa_manual_tweak" in variants
    assert variants[-1] == "aaa_manual_tweak"
    assert all(f["rule_id"] for f in result.report["fixes"][:-1])

    # planned rules keep their plan order ahead of the unattributed record
    planned = [rid for rid in result.plan.ordered_rule_ids()]
    seen = [f["rule_id"] for f in result.report["fixes"] if f["rule_id"] in planned]
    assert seen == [rid for rid in planned if rid in seen]

    # identical samples to the baseline → the gate must drop it, not keep it
    manual_fix = result.report["fixes"][-1]
    assert manual_fix["verdict"] == "drop"


# --- benchstats: zero-stddev cross-check --------------------------------------

def test_crosscheck_accepts_zero_stddev_when_samples_are_identical():
    """Both stddevs ~0 agree: the relative test would divide by zero."""
    cc = benchstats.crosscheck_stddev([1.5, 1.5, 1.5, 1.5, 1.5], 1.5, 0.0)

    assert cc.ok is True
    assert cc.computed_stddev == pytest.approx(0.0, abs=1e-12)
    assert cc.computed_mean == pytest.approx(1.5)
    assert cc.notes == ["instrument summary agrees with raw samples"]


def test_crosscheck_zero_branch_is_not_a_blanket_pass():
    """Constant samples with a non-zero reported stddev must still fail."""
    cc = benchstats.crosscheck_stddev([1.5, 1.5, 1.5, 1.5, 1.5], 1.5, 0.02)

    assert cc.ok is False
    assert any("reported stddev" in n for n in cc.notes)


# --- R2 — build log with no compiler invocations -------------------------------

def test_r2_clean_when_build_log_has_no_compiler_invocations(specs, make_bundle):
    bundle = make_bundle(
        "r02_no_compiles",
        {
            "lscpu.txt": LSCPU_NEOVERSE,
            "build_log.txt": (
                "make: Entering directory '/src/build'\n"
                "[ 25%] Built target support\n"
                "ld -shared -o libsupport.so support.o\n"
                "make: Nothing to be done for 'all'.\n"
                "make: Leaving directory '/src/build'\n"
            ),
        },
    )

    f = _run_rule(specs, "R2", bundle)

    assert f.status is FindingStatus.CLEAN
    assert f.fix is None
    assert f.evidence == ("build log contains no C/C++ compiler invocations",)


# --- R9 — perf report with no parseable overhead rows --------------------------

def test_r9_clean_when_perf_report_has_no_parseable_rows(specs, make_bundle):
    bundle = make_bundle(
        "r09_unparseable",
        {
            "perf_report.txt": (
                "# To display the perf.data header info, please use "
                "--header/--header-only options.\n"
                "#\n"
                "Error: The perf.data file has no samples!\n"
            ),
        },
    )

    f = _run_rule(specs, "R9", bundle)

    assert f.status is FindingStatus.CLEAN
    assert f.evidence == ("perf report contained no parseable overhead rows",)
    assert f.fix is None


def test_r9_unparseable_is_not_confused_with_a_zero_memcpy_report(specs, make_bundle):
    """A report that parses but has no memcpy rows gives the other clean reason."""
    bundle = make_bundle(
        "r09_no_memcpy",
        {
            "perf_report.txt": (
                "# Overhead  Command  Shared Object     Symbol\n"
                "    41.20%  python   _kernel.so        [.] gemm_tile\n"
                "     6.30%  python   libm.so.6         [.] expf\n"
            ),
        },
    )

    f = _run_rule(specs, "R9", bundle)

    assert f.status is FindingStatus.CLEAN
    assert any("0.0% of cycles" in e for e in f.evidence)


# --- R11 — unparseable THP state ------------------------------------------------

def test_r11_skips_when_thp_mode_cannot_be_parsed(specs, make_bundle):
    """No [bracketed] active mode → skip, don't guess (and don't claim a fix)."""
    bundle = make_bundle(
        "r11_no_brackets",
        {
            "thp.txt": "always madvise never\n",
            "proc_maps.txt": (
                "ffff8000-ffff9000 r-xp 00000000 fe:01 1234 "
                "/usr/lib/aarch64-linux-gnu/libc.so.6\n"
            ),
        },
    )

    f = _run_rule(specs, "R11", bundle)

    assert f.status is FindingStatus.SKIPPED
    assert "could not parse THP mode" in f.skipped_reason
    assert "always madvise never" in f.skipped_reason
    assert f.fix is None
