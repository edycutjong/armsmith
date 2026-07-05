"""armsmith.benchstats — the statistics engine behind the reproduce gate.

Design contract (see COMPLEXITY.md §5 and RULEPACK math):

* Robust center = **median**; robust spread = **MAD** (median absolute deviation),
  scaled by the normal-consistency constant 1.4826 so it estimates sigma under
  approximately-normal noise.
* **Noise band** between a baseline and a candidate sample set is
  ``k * sqrt(smad_base^2 + smad_cand^2)`` (root-sum-square of the two scaled
  MADs, i.e. each median's dispersion treated as independent), with ``k = 3.0``
  by default.
* **Refuse-to-claim rule**: if ``|median_cand - median_base| <= band`` the
  verdict is ``no_change`` — Armsmith never claims a win (or a loss) inside
  the noise band.
* Percentiles use linear interpolation between closest ranks
  (``rank = q/100 * (n - 1)``), the same method as ``numpy.percentile``'s
  default — documented so third parties can reproduce every number.
* ABAB interleaving: measurement runs of baseline/candidate are interleaved
  (A B A B ...) rather than blocked (AAAA BBBB) so slow drift (thermal,
  frequency governor, noisy neighbors) hits both variants equally.

Everything here is pure Python and dependency-free on purpose: the math that
accepts or rejects a performance claim must be auditable at a glance.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "MAD_NORMAL_CONSISTENCY",
    "DEFAULT_BAND_K",
    "MIN_SAMPLES_FOR_VERDICT",
    "Direction",
    "Verdict",
    "SampleStats",
    "Comparison",
    "RunSlot",
    "median",
    "mad",
    "scaled_mad",
    "percentile",
    "sample_stddev",
    "summarize",
    "noise_band",
    "compare",
    "plan_interleaved",
    "crosscheck_stddev",
    "CrossCheck",
]

#: 1 / Phi^-1(3/4): makes MAD a consistent estimator of sigma for normal noise.
MAD_NORMAL_CONSISTENCY = 1.4826

#: Default band multiplier: a claim must clear 3x the combined scaled MAD.
DEFAULT_BAND_K = 3.0

#: Below this many measured samples per side we refuse to issue any verdict.
MIN_SAMPLES_FOR_VERDICT = 3


class Direction(str, Enum):
    """Which way is better for a metric."""

    LOWER_BETTER = "lower_better"    # wall time, latency, RSS, cache-miss %
    HIGHER_BETTER = "higher_better"  # tokens/sec, IPC, throughput


class Verdict(str, Enum):
    IMPROVED = "improved"
    REGRESSED = "regressed"
    NO_CHANGE = "no_change"                      # inside the noise band
    INSUFFICIENT_SAMPLES = "insufficient_samples"  # refuse to judge


def _check_samples(xs: Sequence[float], name: str = "samples") -> list[float]:
    vals = [float(x) for x in xs]
    if not vals:
        raise ValueError(f"{name} must be non-empty")
    for v in vals:
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"{name} contains non-finite value {v!r}")
    return vals


def median(xs: Sequence[float]) -> float:
    """Standard median: middle element, or mean of the two middle elements."""
    vals = sorted(_check_samples(xs))
    n = len(vals)
    mid = n // 2
    if n % 2 == 1:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def mad(xs: Sequence[float]) -> float:
    """Raw median absolute deviation: median(|x - median(x)|)."""
    vals = _check_samples(xs)
    m = median(vals)
    return median([abs(v - m) for v in vals])


def scaled_mad(xs: Sequence[float]) -> float:
    """MAD scaled by 1.4826 — a robust sigma estimate under normal noise."""
    return mad(xs) * MAD_NORMAL_CONSISTENCY


def percentile(xs: Sequence[float], q: float) -> float:
    """Percentile via linear interpolation between closest ranks.

    ``rank = q/100 * (n - 1)``; interpolate between floor(rank) and
    ceil(rank).  Matches ``numpy.percentile(..., method="linear")``.
    """
    if not 0.0 <= q <= 100.0:
        raise ValueError(f"q must be in [0, 100], got {q}")
    vals = sorted(_check_samples(xs))
    n = len(vals)
    if n == 1:
        return vals[0]
    rank = (q / 100.0) * (n - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return vals[int(rank)]
    frac = rank - lo
    return vals[lo] + frac * (vals[hi] - vals[lo])


def sample_stddev(xs: Sequence[float]) -> float:
    """Sample standard deviation (ddof=1). 0.0 for n == 1."""
    vals = _check_samples(xs)
    n = len(vals)
    if n == 1:
        return 0.0
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    return math.sqrt(var)


@dataclass(frozen=True)
class SampleStats:
    """Summary statistics for one metric's sample set."""

    n: int
    median: float
    mad: float
    smad: float          # scaled MAD (sigma-consistent)
    p50: float
    p95: float
    mean: float
    stddev: float        # sample stddev, ddof=1
    min: float
    max: float

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "median": self.median,
            "mad": self.mad,
            "smad": self.smad,
            "p50": self.p50,
            "p95": self.p95,
            "mean": self.mean,
            "stddev": self.stddev,
            "min": self.min,
            "max": self.max,
        }


def summarize(xs: Sequence[float]) -> SampleStats:
    vals = _check_samples(xs)
    return SampleStats(
        n=len(vals),
        median=median(vals),
        mad=mad(vals),
        smad=scaled_mad(vals),
        p50=percentile(vals, 50),
        p95=percentile(vals, 95),
        mean=sum(vals) / len(vals),
        stddev=sample_stddev(vals),
        min=min(vals),
        max=max(vals),
    )


def noise_band(
    baseline: Sequence[float],
    candidate: Sequence[float],
    k: float = DEFAULT_BAND_K,
) -> float:
    """Combined noise band: ``k * sqrt(smad_base^2 + smad_cand^2)``."""
    if k <= 0:
        raise ValueError(f"band multiplier k must be > 0, got {k}")
    sb = scaled_mad(baseline)
    sc = scaled_mad(candidate)
    return k * math.sqrt(sb * sb + sc * sc)


@dataclass(frozen=True)
class Comparison:
    """Outcome of comparing candidate samples against baseline samples."""

    verdict: Verdict
    direction: Direction
    baseline: SampleStats | None
    candidate: SampleStats | None
    delta: float | None            # candidate.median - baseline.median
    delta_pct: float | None        # delta / baseline.median * 100
    band: float | None             # combined noise band (absolute units)
    band_k: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "direction": self.direction.value,
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "delta": self.delta,
            "delta_pct": self.delta_pct,
            "band": self.band,
            "band_k": self.band_k,
            "reason": self.reason,
        }


def compare(
    baseline: Sequence[float],
    candidate: Sequence[float],
    direction: Direction = Direction.LOWER_BETTER,
    k: float = DEFAULT_BAND_K,
    min_samples: int = MIN_SAMPLES_FOR_VERDICT,
) -> Comparison:
    """Compare candidate vs baseline with the refuse-to-claim-inside-band rule.

    Verdict logic:
      * fewer than ``min_samples`` on either side → ``insufficient_samples``;
      * ``|delta| <= band``                       → ``no_change``;
      * otherwise improved/regressed according to ``direction``.
    """
    base_vals = _check_samples(baseline, "baseline")
    cand_vals = _check_samples(candidate, "candidate")

    if len(base_vals) < min_samples or len(cand_vals) < min_samples:
        return Comparison(
            verdict=Verdict.INSUFFICIENT_SAMPLES,
            direction=direction,
            baseline=summarize(base_vals),
            candidate=summarize(cand_vals),
            delta=None,
            delta_pct=None,
            band=None,
            band_k=k,
            reason=(
                f"need >= {min_samples} samples per side for a verdict "
                f"(got baseline n={len(base_vals)}, candidate n={len(cand_vals)})"
            ),
        )

    bs = summarize(base_vals)
    cs = summarize(cand_vals)
    band = noise_band(base_vals, cand_vals, k=k)
    delta = cs.median - bs.median
    # delta_pct is None when the baseline median is 0 (undefined ratio);
    # never emit +/-inf — reports must stay strict-JSON serializable.
    delta_pct = (delta / bs.median * 100.0) if bs.median != 0 else None

    if abs(delta) <= band:
        verdict = Verdict.NO_CHANGE
        reason = (
            f"|Δmedian| = {abs(delta):.6g} inside noise band ±{band:.6g} "
            f"(k={k:g}) — refusing to claim a change"
        )
    else:
        better = delta < 0 if direction is Direction.LOWER_BETTER else delta > 0
        verdict = Verdict.IMPROVED if better else Verdict.REGRESSED
        reason = (
            f"Δmedian = {delta:+.6g} clears noise band ±{band:.6g} (k={k:g}), "
            f"direction={direction.value}"
        )

    return Comparison(
        verdict=verdict,
        direction=direction,
        baseline=bs,
        candidate=cs,
        delta=delta,
        delta_pct=delta_pct,
        band=band,
        band_k=k,
        reason=reason,
    )


# --------------------------------------------------------------------------
# ABAB interleave planner
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RunSlot:
    """One scheduled benchmark execution."""

    order: int          # global execution order, 0-based
    variant: str        # e.g. "baseline" or "fix_R3"
    rep_index: int      # per-variant repetition counter (warmups count separately)
    warmup: bool        # warmup runs are executed then discarded


def plan_interleaved(
    variants: Sequence[str],
    reps: int = 7,
    warmup: int = 2,
) -> list[RunSlot]:
    """Interleaved run schedule: warmup rounds first, then measured rounds.

    For variants ["A", "B"], reps=2, warmup=1 the schedule is:
        A(w) B(w) A0 B0 A1 B1

    Properties (unit-tested):
      * every variant gets exactly ``warmup`` warmup slots and ``reps``
        measured slots;
      * consecutive slots never run the same variant (when >1 variant);
      * measured rep indices are 0..reps-1 in order for each variant.
    """
    names = list(variants)
    if not names:
        raise ValueError("need at least one variant")
    if len(set(names)) != len(names):
        raise ValueError(f"variant names must be unique, got {names}")
    if reps < 1:
        raise ValueError(f"reps must be >= 1, got {reps}")
    if warmup < 0:
        raise ValueError(f"warmup must be >= 0, got {warmup}")

    slots: list[RunSlot] = []
    order = 0
    for w in range(warmup):
        for name in names:
            slots.append(RunSlot(order=order, variant=name, rep_index=w, warmup=True))
            order += 1
    for r in range(reps):
        for name in names:
            slots.append(RunSlot(order=order, variant=name, rep_index=r, warmup=False))
            order += 1
    return slots


# --------------------------------------------------------------------------
# Instrument cross-check (squeeze pass 2 #3)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CrossCheck:
    """Result of checking an instrument's self-reported stats vs raw samples."""

    ok: bool
    computed_mean: float
    computed_stddev: float
    reported_mean: float | None
    reported_stddev: float | None
    rel_tol: float
    notes: list[str] = field(default_factory=list)


def crosscheck_stddev(
    samples: Sequence[float],
    reported_mean: float | None,
    reported_stddev: float | None,
    rel_tol: float = 0.10,
) -> CrossCheck:
    """Cross-check an instrument's reported mean/stddev against its own samples.

    Used when ingesting llama-bench JSON (which reports avg/stddev alongside
    per-repetition samples): if the summary does not match the samples the
    bundle was transcribed wrong or tampered with — the gate must not trust it.

    A reported value of None skips that check. stddev comparison tolerates
    ``rel_tol`` relative error against max(|computed|, |reported|); when the
    computed stddev is ~0, absolute agreement within 1e-9 is required.
    """
    vals = _check_samples(samples)
    computed_mean = sum(vals) / len(vals)
    computed_std = sample_stddev(vals)
    notes: list[str] = []
    ok = True

    if reported_mean is not None:
        scale = max(abs(computed_mean), abs(reported_mean), 1e-12)
        if abs(computed_mean - reported_mean) / scale > rel_tol:
            ok = False
            notes.append(
                f"reported mean {reported_mean:.6g} disagrees with samples "
                f"mean {computed_mean:.6g} beyond rel_tol={rel_tol:g}"
            )
    if reported_stddev is not None:
        scale = max(abs(computed_std), abs(reported_stddev))
        if scale < 1e-9:
            pass  # both ~zero: agree
        elif abs(computed_std - reported_stddev) / scale > rel_tol:
            ok = False
            notes.append(
                f"reported stddev {reported_stddev:.6g} disagrees with samples "
                f"stddev {computed_std:.6g} beyond rel_tol={rel_tol:g}"
            )
    if ok:
        notes.append("instrument summary agrees with raw samples")
    return CrossCheck(
        ok=ok,
        computed_mean=computed_mean,
        computed_stddev=computed_std,
        reported_mean=reported_mean,
        reported_stddev=reported_stddev,
        rel_tol=rel_tol,
        notes=notes,
    )
