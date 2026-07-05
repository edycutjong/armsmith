"""Golden tests for armsmith.benchstats on synthetic distributions with known answers."""

import math
import random

import pytest

from armsmith import benchstats as bs
from armsmith.benchstats import Direction, Verdict

# ---------------------------------------------------------------------------
# median / MAD / percentiles — hand-computed goldens
# ---------------------------------------------------------------------------

def test_median_odd():
    assert bs.median([5, 1, 3]) == 3


def test_median_even_is_mean_of_middles():
    assert bs.median([1, 2, 3, 4]) == 2.5


def test_median_single():
    assert bs.median([7.5]) == 7.5


def test_median_unsorted_input():
    assert bs.median([9, 2, 7, 4, 6]) == 6


def test_mad_golden():
    # samples 1..5: median 3, |dev| = [2,1,0,1,2] → median 1
    assert bs.mad([1, 2, 3, 4, 5]) == 1.0


def test_mad_constant_series_is_zero():
    assert bs.mad([4.2, 4.2, 4.2, 4.2]) == 0.0


def test_scaled_mad_applies_consistency_constant():
    assert bs.scaled_mad([1, 2, 3, 4, 5]) == pytest.approx(1.4826)


def test_percentile_p50_matches_median():
    xs = [10, 20, 30, 40, 50]
    assert bs.percentile(xs, 50) == bs.median(xs) == 30


def test_percentile_p95_linear_interpolation_golden():
    # rank = 0.95 * 4 = 3.8 → 4 + 0.8*(5-4) = 4.8
    assert bs.percentile([1, 2, 3, 4, 5], 95) == pytest.approx(4.8)


def test_percentile_p0_p100_are_min_max():
    xs = [3.5, 1.2, 9.9, 4.4]
    assert bs.percentile(xs, 0) == 1.2
    assert bs.percentile(xs, 100) == 9.9


def test_percentile_single_sample():
    assert bs.percentile([42.0], 95) == 42.0


def test_percentile_rejects_out_of_range_q():
    with pytest.raises(ValueError):
        bs.percentile([1, 2], 101)


def test_empty_samples_rejected():
    with pytest.raises(ValueError):
        bs.median([])


def test_nan_rejected():
    with pytest.raises(ValueError):
        bs.mad([1.0, float("nan")])


def test_inf_rejected():
    with pytest.raises(ValueError):
        bs.summarize([1.0, float("inf")])


def test_sample_stddev_golden():
    # [2,4,4,4,5,5,7,9]: mean 5, ddof=1 variance 32/7
    assert bs.sample_stddev([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(math.sqrt(32 / 7))


def test_sample_stddev_n1_is_zero():
    assert bs.sample_stddev([3.3]) == 0.0


def test_summarize_fields_consistent():
    stats = bs.summarize([1, 2, 3, 4, 5, 6, 7])
    assert stats.n == 7
    assert stats.median == 4
    assert stats.p50 == 4
    assert stats.min == 1 and stats.max == 7
    assert stats.mean == pytest.approx(4.0)
    d = stats.to_dict()
    assert set(d) == {"n", "median", "mad", "smad", "p50", "p95", "mean", "stddev", "min", "max"}


# ---------------------------------------------------------------------------
# noise band + refuse-to-claim verdicts
# ---------------------------------------------------------------------------

def test_noise_band_rss_combination_golden():
    base = [1, 2, 3, 4, 5]      # smad = 1.4826
    cand = [1, 2, 3, 4, 5]
    expected = 3.0 * math.sqrt(2 * 1.4826**2)
    assert bs.noise_band(base, cand) == pytest.approx(expected)


def test_noise_band_requires_positive_k():
    with pytest.raises(ValueError):
        bs.noise_band([1, 2, 3], [1, 2, 3], k=0)


def test_compare_improved_lower_better():
    base = [2.00, 2.01, 1.99, 2.02, 1.98, 2.00, 2.01]
    cand = [1.00, 1.01, 0.99, 1.02, 0.98, 1.00, 1.01]
    cmp = bs.compare(base, cand, direction=Direction.LOWER_BETTER)
    assert cmp.verdict is Verdict.IMPROVED
    assert cmp.delta == pytest.approx(-1.0)
    assert cmp.delta_pct == pytest.approx(-50.0)


def test_compare_regressed_lower_better():
    base = [1.00, 1.01, 0.99, 1.02, 0.98, 1.00, 1.01]
    cand = [2.00, 2.01, 1.99, 2.02, 1.98, 2.00, 2.01]
    cmp = bs.compare(base, cand, direction=Direction.LOWER_BETTER)
    assert cmp.verdict is Verdict.REGRESSED


def test_compare_higher_better_direction_flips_meaning():
    base = [100.0, 101.0, 99.0, 100.5, 99.5, 100.0, 100.2]
    cand = [150.0, 151.0, 149.0, 150.5, 149.5, 150.0, 150.2]
    assert bs.compare(base, cand, direction=Direction.HIGHER_BETTER).verdict is Verdict.IMPROVED
    assert bs.compare(cand, base, direction=Direction.HIGHER_BETTER).verdict is Verdict.REGRESSED


def test_compare_refuses_to_claim_inside_band():
    # medians differ by 0.005; smad≈0.0222 each side → band ≈ 0.094 ≫ 0.005
    base = [2.01, 1.98, 2.03, 2.00, 1.99, 2.02, 2.00]
    cand = [2.00, 1.97, 2.02, 2.00, 1.98, 2.01, 2.00]
    cmp = bs.compare(base, cand, direction=Direction.LOWER_BETTER)
    assert cmp.verdict is Verdict.NO_CHANGE
    assert "refusing to claim" in cmp.reason


def test_compare_exact_band_edge_is_no_change():
    # |delta| == band must NOT be claimed (<= rule)
    base = [10.0, 10.0, 10.0, 10.0, 10.0]
    cand = [10.0, 10.0, 10.0, 10.0, 10.0]
    cmp = bs.compare(base, cand)
    assert cmp.band == 0.0
    assert cmp.verdict is Verdict.NO_CHANGE


def test_compare_insufficient_samples():
    cmp = bs.compare([1.0, 1.1], [0.5, 0.6, 0.7], min_samples=3)
    assert cmp.verdict is Verdict.INSUFFICIENT_SAMPLES
    assert cmp.delta is None and cmp.band is None


def test_compare_zero_baseline_median_delta_pct_none():
    cmp = bs.compare([0.0, 0.0, 0.0], [1.0, 1.0, 1.0])
    assert cmp.delta_pct is None  # never inf — reports stay JSON-strict


def test_compare_seeded_normal_distribution():
    rng = random.Random(1234)
    base = [rng.gauss(2.0, 0.02) for _ in range(21)]
    cand = [rng.gauss(1.0, 0.02) for _ in range(21)]
    cmp = bs.compare(base, cand, direction=Direction.LOWER_BETTER)
    assert cmp.verdict is Verdict.IMPROVED
    assert abs(cmp.delta + 1.0) < 0.05


def test_comparison_to_dict_json_shape():
    cmp = bs.compare([1, 1, 1], [2, 2, 2])
    d = cmp.to_dict()
    assert d["verdict"] == "regressed"
    assert d["direction"] == "lower_better"
    assert d["baseline"]["median"] == 1


# ---------------------------------------------------------------------------
# ABAB interleave planner
# ---------------------------------------------------------------------------

def test_plan_interleaved_counts_and_indices():
    slots = bs.plan_interleaved(["A", "B"], reps=7, warmup=2)
    assert len(slots) == (7 + 2) * 2
    measured_a = [s for s in slots if s.variant == "A" and not s.warmup]
    assert [s.rep_index for s in measured_a] == list(range(7))
    assert sum(1 for s in slots if s.warmup) == 4


def test_plan_interleaved_alternates_strictly():
    slots = bs.plan_interleaved(["A", "B"], reps=5, warmup=1)
    for prev, cur in zip(slots, slots[1:]):
        assert prev.variant != cur.variant  # ABAB…, never AA/BB adjacency


def test_plan_interleaved_warmups_first():
    slots = bs.plan_interleaved(["A", "B"], reps=3, warmup=2)
    assert all(s.warmup for s in slots[:4])
    assert not any(s.warmup for s in slots[4:])


def test_plan_interleaved_three_variants_round_robin():
    slots = bs.plan_interleaved(["base", "fix1", "fix2"], reps=2, warmup=0)
    assert [s.variant for s in slots] == ["base", "fix1", "fix2", "base", "fix1", "fix2"]


def test_plan_interleaved_global_order_is_sequential():
    slots = bs.plan_interleaved(["A", "B"], reps=2, warmup=1)
    assert [s.order for s in slots] == list(range(len(slots)))


def test_plan_interleaved_validates_inputs():
    with pytest.raises(ValueError):
        bs.plan_interleaved([], reps=3)
    with pytest.raises(ValueError):
        bs.plan_interleaved(["A", "A"], reps=3)
    with pytest.raises(ValueError):
        bs.plan_interleaved(["A"], reps=0)
    with pytest.raises(ValueError):
        bs.plan_interleaved(["A"], reps=1, warmup=-1)


# ---------------------------------------------------------------------------
# instrument cross-check
# ---------------------------------------------------------------------------

def test_crosscheck_agreement():
    samples = [50.0, 49.6, 50.5, 49.9, 50.3, 49.7, 50.1]
    cc = bs.crosscheck_stddev(samples, reported_mean=50.0, reported_stddev=bs.sample_stddev(samples))
    assert cc.ok


def test_crosscheck_mean_mismatch_flags():
    cc = bs.crosscheck_stddev([50.0, 50.1, 49.9], reported_mean=90.0, reported_stddev=None)
    assert not cc.ok
    assert "mean" in cc.notes[0]


def test_crosscheck_stddev_mismatch_flags():
    cc = bs.crosscheck_stddev([50.0, 50.1, 49.9], reported_mean=None, reported_stddev=5.0)
    assert not cc.ok


def test_crosscheck_none_reports_skip_checks():
    cc = bs.crosscheck_stddev([1.0, 2.0, 3.0], reported_mean=None, reported_stddev=None)
    assert cc.ok
