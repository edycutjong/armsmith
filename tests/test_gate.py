"""Reproduce-gate engine: keep/drop semantics with reasons."""

import pytest

from armsmith.benchstats import Direction, Verdict
from armsmith.gate import (
    GateConfig,
    MeasurementSet,
    evaluate_fix,
    load_measurement,
    run_gate,
)

BASE_WALL = [2.01, 1.98, 2.03, 2.00, 1.99, 2.02, 2.00]
FAST_WALL = [0.71, 0.69, 0.72, 0.70, 0.70, 0.71, 0.69]
INBAND_WALL = [2.00, 1.97, 2.02, 2.00, 1.98, 2.01, 2.00]
SLOW_WALL = [3.01, 2.98, 3.03, 3.00, 2.99, 3.02, 3.00]
HASH_A = "aa" * 32
HASH_B = "bb" * 32


def ms(variant, wall, out=HASH_A, rule_id=None, pmu=None, extra_metrics=None):
    metrics = {"wall_s": list(wall)}
    if extra_metrics:
        metrics.update(extra_metrics)
    return MeasurementSet(
        variant=variant, instrument="hyperfine", metrics=metrics,
        pmu=pmu or {}, output_sha256=out, rule_id=rule_id, synthetic=True,
    )


def test_keep_on_clear_win_with_hash_equal():
    res = evaluate_fix(ms("baseline", BASE_WALL), ms("fix_R1", FAST_WALL, rule_id="R1"))
    assert res.verdict == "keep"
    assert res.output_hash_equal is True
    assert res.comparisons["wall_s"].verdict is Verdict.IMPROVED
    assert any("improved outside the noise band" in r for r in res.reasons)


def test_drop_inside_noise_band_with_reason():
    res = evaluate_fix(ms("baseline", BASE_WALL), ms("fix_R11", INBAND_WALL, rule_id="R11"))
    assert res.verdict == "drop"
    assert any("noise band" in r for r in res.reasons)
    assert res.comparisons["wall_s"].verdict is Verdict.NO_CHANGE


def test_drop_on_output_hash_mismatch_even_if_faster():
    res = evaluate_fix(ms("baseline", BASE_WALL), ms("fix_R8", FAST_WALL, out=HASH_B, rule_id="R8"))
    assert res.verdict == "drop"
    assert res.output_hash_equal is False
    assert any("changes behavior" in r for r in res.reasons)


def test_drop_on_regression():
    res = evaluate_fix(ms("baseline", BASE_WALL), ms("fix_bad", SLOW_WALL))
    assert res.verdict == "drop"
    assert any("regressed outside the noise band" in r for r in res.reasons)


def test_drop_when_hash_missing_and_required():
    res = evaluate_fix(ms("baseline", BASE_WALL), ms("fix", FAST_WALL, out=None))
    assert res.verdict == "drop"
    assert res.output_hash_equal is None
    assert any("cannot prove behavior unchanged" in r for r in res.reasons)


def test_keep_when_hash_missing_but_not_required():
    cfg = GateConfig(require_output_hash=False)
    res = evaluate_fix(ms("baseline", BASE_WALL), ms("fix", FAST_WALL, out=None), cfg)
    assert res.verdict == "keep"


def test_mixed_metrics_regression_vetoes_win():
    base = ms("baseline", BASE_WALL, extra_metrics={"rss_peak_mb": [500.0] * 7})
    cand = ms("fix", FAST_WALL, extra_metrics={"rss_peak_mb": [900.0] * 7})
    res = evaluate_fix(base, cand)
    assert res.verdict == "drop"
    assert res.comparisons["wall_s"].verdict is Verdict.IMPROVED
    assert res.comparisons["rss_peak_mb"].verdict is Verdict.REGRESSED


def test_primary_metrics_scope_wins():
    base = ms("baseline", BASE_WALL, extra_metrics={"rss_peak_mb": [500.0] * 7})
    cand = ms("fix", list(BASE_WALL), extra_metrics={"rss_peak_mb": [300.0] * 7})
    # wall unchanged (identical), rss improved — but primary is wall_s only → drop
    cfg = GateConfig(primary_metrics=("wall_s",))
    res = evaluate_fix(base, cand, cfg)
    assert res.verdict == "drop"
    assert any("no primary metric improved" in r for r in res.reasons)


def test_unknown_metric_direction_is_refused_not_guessed():
    base = ms("baseline", BASE_WALL, extra_metrics={"mystery_units": [1.0] * 7})
    cand = ms("fix", FAST_WALL, extra_metrics={"mystery_units": [2.0] * 7})
    res = evaluate_fix(base, cand)
    assert "mystery_units" not in res.comparisons
    assert any("no declared direction" in r for r in res.reasons)
    # declared via config → now compared
    cfg = GateConfig(directions={"mystery_units": Direction.HIGHER_BETTER})
    res2 = evaluate_fix(base, cand, cfg)
    assert res2.comparisons["mystery_units"].verdict is Verdict.IMPROVED


def test_pmu_delta_is_advisory_evidence():
    base = ms("baseline", BASE_WALL, pmu={"ipc": 1.25, "cycles": 8.0e9})
    cand = ms("fix", FAST_WALL, pmu={"ipc": 2.21, "cycles": 2.9e9})
    res = evaluate_fix(base, cand)
    assert res.pmu_delta["ipc"]["delta_pct"] == pytest.approx(76.8)
    assert res.pmu_delta["cycles"]["after"] == pytest.approx(2.9e9)


def test_run_gate_outcome_partitions():
    baseline = ms("baseline", BASE_WALL)
    outcome = run_gate(baseline, [
        ms("fix_R1", FAST_WALL, rule_id="R1"),
        ms("fix_R11", INBAND_WALL, rule_id="R11"),
    ])
    assert [r.verdict for r in outcome.results] == ["keep", "drop"]
    assert len(outcome.kept) == 1 and len(outcome.dropped) == 1
    assert outcome.candidates[0].variant == "fix_R1"


def test_load_measurement_requires_synthetic_flag(tmp_path):
    p = tmp_path / "m.json"
    p.write_text('{"variant": "x", "metrics": {"wall_s": [1, 2, 3]}}')
    with pytest.raises(ValueError, match="synthetic"):
        load_measurement(p)


def test_load_measurement_requires_metrics(tmp_path):
    p = tmp_path / "m.json"
    p.write_text('{"synthetic": true, "variant": "x", "metrics": {}}')
    with pytest.raises(ValueError, match="no metrics"):
        load_measurement(p)


def test_load_measurement_roundtrip(scenario_bundle):
    ms_ = load_measurement(scenario_bundle / "bench" / "fix_R1.json")
    assert ms_.variant == "fix_R1"
    assert ms_.rule_id == "R1"
    assert ms_.synthetic is True
    assert len(ms_.metrics["wall_s"]) == 7
