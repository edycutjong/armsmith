"""armsmith.gate — the reproduce gate.

Consumes baseline + candidate measurement records (replay bundles in this
phase; live runs at S1 — same shapes), applies benchstats verdicts and
output-hash equality, and emits a keep/drop per fix with machine-checkable
reasons.

Gate policy (RULEPACK math, COMPLEXITY §5):
  DROP if outputs are not hash-equal (or a hash is missing while required);
  DROP if any metric REGRESSES outside its noise band;
  DROP if no primary metric IMPROVES outside its noise band
       ("in-band = no change" — never claimed as a win);
  otherwise KEEP, carrying per-metric comparisons + advisory PMU deltas.

Measurement record shape (bench/*.json in a replay bundle)::

    {"synthetic": true,               # provenance flag — REQUIRED
     "variant": "baseline" | "fix_R3",
     "rule_id": null | "R3",
     "instrument": "hyperfine",
     "metrics": {"wall_s": [...], "rss_peak_mb": [...]},
     "pmu": {"cycles": ..., "instructions": ..., "ipc": ..., "cache_miss_pct": ...},
     "output_sha256": "..."}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import benchstats
from .benchstats import Comparison, Direction, Verdict

__all__ = [
    "METRIC_DIRECTIONS",
    "MeasurementSet",
    "GateConfig",
    "FixResult",
    "GateOutcome",
    "load_measurement",
    "evaluate_fix",
    "run_gate",
]

#: Which way is better, per metric name. Unknown metrics require an explicit
#: direction in GateConfig.directions — the gate refuses to guess.
METRIC_DIRECTIONS: dict[str, Direction] = {
    "wall_s": Direction.LOWER_BETTER,
    "wall_p50_s": Direction.LOWER_BETTER,
    "wall_p95_s": Direction.LOWER_BETTER,
    "latency_ms": Direction.LOWER_BETTER,
    "rss_peak_mb": Direction.LOWER_BETTER,
    "cache_miss_pct": Direction.LOWER_BETTER,
    "tokens_s_pp": Direction.HIGHER_BETTER,
    "tokens_s_tg": Direction.HIGHER_BETTER,
    "throughput_rps": Direction.HIGHER_BETTER,
    "ipc": Direction.HIGHER_BETTER,
}


@dataclass(frozen=True)
class MeasurementSet:
    variant: str
    instrument: str
    metrics: dict[str, list[float]]
    pmu: dict[str, float] = field(default_factory=dict)
    output_sha256: str | None = None
    rule_id: str | None = None
    synthetic: bool = True

    def to_dict(self) -> dict:
        return {
            "variant": self.variant,
            "instrument": self.instrument,
            "rule_id": self.rule_id,
            "synthetic": self.synthetic,
            "metrics": self.metrics,
            "pmu": self.pmu,
            "output_sha256": self.output_sha256,
        }


def load_measurement(path: Path) -> MeasurementSet:
    """Load one bench record; refuses records without a provenance flag."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "synthetic" not in data or not isinstance(data["synthetic"], bool):
        raise ValueError(
            f"{path}: measurement record must declare boolean 'synthetic' "
            "provenance — unlabeled numbers never enter the gate"
        )
    metrics = data.get("metrics") or {}
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError(f"{path}: record has no metrics")
    clean_metrics: dict[str, list[float]] = {}
    for name, samples in metrics.items():
        if not isinstance(samples, list) or not samples:
            raise ValueError(f"{path}: metric {name!r} must be a non-empty sample list")
        clean_metrics[str(name)] = [float(s) for s in samples]
    return MeasurementSet(
        variant=str(data.get("variant") or Path(path).stem),
        instrument=str(data.get("instrument", "unknown")),
        metrics=clean_metrics,
        pmu={str(k): float(v) for k, v in (data.get("pmu") or {}).items()},
        output_sha256=data.get("output_sha256"),
        rule_id=data.get("rule_id"),
        synthetic=data["synthetic"],
    )


@dataclass(frozen=True)
class GateConfig:
    band_k: float = benchstats.DEFAULT_BAND_K
    min_samples: int = benchstats.MIN_SAMPLES_FOR_VERDICT
    require_output_hash: bool = True
    #: metrics that count as wins; None → every shared non-PMU metric counts.
    primary_metrics: tuple[str, ...] | None = None
    #: direction overrides / additions for metrics not in METRIC_DIRECTIONS.
    directions: dict[str, Direction] = field(default_factory=dict)

    def direction_for(self, metric: str) -> Direction | None:
        if metric in self.directions:
            return self.directions[metric]
        return METRIC_DIRECTIONS.get(metric)


@dataclass(frozen=True)
class FixResult:
    variant: str
    rule_id: str | None
    verdict: str                      # "keep" | "drop"
    reasons: tuple[str, ...]
    comparisons: dict[str, Comparison]
    pmu_delta: dict[str, dict[str, float | None]]
    output_hash_equal: bool | None    # None = not checkable

    def to_dict(self) -> dict:
        return {
            "variant": self.variant,
            "rule_id": self.rule_id,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "comparisons": {m: c.to_dict() for m, c in self.comparisons.items()},
            "pmu_delta": self.pmu_delta,
            "output_hash_equal": self.output_hash_equal,
        }


def _pmu_delta(base: dict[str, float], cand: dict[str, float]) -> dict[str, dict[str, float | None]]:
    out: dict[str, dict[str, float | None]] = {}
    for counter in sorted(set(base) & set(cand)):
        b, c = base[counter], cand[counter]
        out[counter] = {
            "before": b,
            "after": c,
            "delta": c - b,
            "delta_pct": ((c - b) / b * 100.0) if b != 0 else None,
        }
    return out


def evaluate_fix(
    baseline: MeasurementSet,
    candidate: MeasurementSet,
    config: GateConfig | None = None,
) -> FixResult:
    cfg = config or GateConfig()
    reasons: list[str] = []
    comparisons: dict[str, Comparison] = {}

    # --- output-hash equality (correctness before speed) -----------------
    hash_equal: bool | None
    if baseline.output_sha256 and candidate.output_sha256:
        hash_equal = baseline.output_sha256 == candidate.output_sha256
        if not hash_equal:
            reasons.append(
                f"output hash mismatch: baseline {baseline.output_sha256[:12]}… != "
                f"candidate {candidate.output_sha256[:12]}… — fix changes behavior"
            )
    else:
        hash_equal = None
        if cfg.require_output_hash:
            missing = [
                name for name, ms in (("baseline", baseline), ("candidate", candidate))
                if not ms.output_sha256
            ]
            reasons.append(
                f"output hash missing for {missing} — cannot prove behavior "
                "unchanged (gate requires hash equality)"
            )

    # --- per-metric statistics -------------------------------------------
    shared = sorted(set(baseline.metrics) & set(candidate.metrics))
    improved: list[str] = []
    regressed: list[str] = []
    for metric in shared:
        direction = cfg.direction_for(metric)
        if direction is None:
            reasons.append(f"metric {metric!r} has no declared direction — ignored (declare in GateConfig)")
            continue
        cmp = benchstats.compare(
            baseline.metrics[metric],
            candidate.metrics[metric],
            direction=direction,
            k=cfg.band_k,
            min_samples=cfg.min_samples,
        )
        comparisons[metric] = cmp
        if cmp.verdict is Verdict.IMPROVED:
            improved.append(metric)
        elif cmp.verdict is Verdict.REGRESSED:
            regressed.append(metric)

    primaries = list(cfg.primary_metrics) if cfg.primary_metrics else [m for m in comparisons]
    primary_wins = [m for m in improved if m in primaries]

    # --- verdict -----------------------------------------------------------
    drop = False
    if hash_equal is False or (hash_equal is None and cfg.require_output_hash):
        drop = True
    if regressed:
        drop = True
        for m in regressed:
            reasons.append(f"{m} regressed outside the noise band: {comparisons[m].reason}")
    if not primary_wins:
        drop = True
        if not comparisons:
            reasons.append("no shared, direction-declared metrics to compare")
        else:
            reasons.append(
                "no primary metric improved outside the noise band — in-band "
                f"deltas are reported as 'no change' (checked: {primaries})"
            )
    if not drop:
        for m in primary_wins:
            reasons.append(f"{m} improved outside the noise band: {comparisons[m].reason}")

    return FixResult(
        variant=candidate.variant,
        rule_id=candidate.rule_id,
        verdict="drop" if drop else "keep",
        reasons=tuple(reasons),
        comparisons=comparisons,
        pmu_delta=_pmu_delta(baseline.pmu, candidate.pmu),
        output_hash_equal=hash_equal,
    )


@dataclass(frozen=True)
class GateOutcome:
    baseline: MeasurementSet
    candidates: tuple[MeasurementSet, ...]
    results: tuple[FixResult, ...]

    @property
    def kept(self) -> list[FixResult]:
        return [r for r in self.results if r.verdict == "keep"]

    @property
    def dropped(self) -> list[FixResult]:
        return [r for r in self.results if r.verdict == "drop"]


def run_gate(
    baseline: MeasurementSet,
    candidates: list[MeasurementSet],
    config: GateConfig | None = None,
) -> GateOutcome:
    results = tuple(evaluate_fix(baseline, cand, config) for cand in candidates)
    return GateOutcome(baseline=baseline, candidates=tuple(candidates), results=results)
