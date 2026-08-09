"""R13 — serving overhead dominates kernel time (probes: llama_bench + hyperfine).

Two-instrument triangulation (squeeze pass 2 #1): llama-bench measures
kernel-time tokens/sec and EXCLUDES tokenization + sampling (verified caveat,
the llama.cpp build docs); hyperfine measures end-to-end wall time of the
same workload.  If E2E exceeds reconstructed kernel time by > 15%, the
pipeline — not the kernels — is the bottleneck.

Data-integrity discipline (squeeze pass 2 #3): both instruments' self-reported
mean/stddev are cross-checked against their raw per-repetition samples via
``benchstats.crosscheck_stddev``; a mismatch means the bundle is corrupt or
tampered and the rule SKIPS instead of trusting either number.
"""

from __future__ import annotations

from pathlib import Path

from ... import benchstats
from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register, skipped

DIVERGENCE_THRESHOLD = 0.15   # >15% of E2E outside kernels fires the rule
_CONSISTENCY_SLACK = 1.05     # kernel "longer" than E2E beyond 5% = bad data


def _entry_kernel_seconds(entry: dict) -> tuple[float, str] | None:
    """Seconds of kernel time this llama-bench entry contributes per E2E run."""
    n_prompt = int(entry.get("n_prompt", 0))
    n_gen = int(entry.get("n_gen", 0))
    samples_ts = entry.get("samples_ts") or []
    if not samples_ts:
        return None
    tokens_per_s = benchstats.median(samples_ts)
    if tokens_per_s <= 0:
        return None
    if n_gen > 0 and n_prompt == 0:
        return n_gen / tokens_per_s, f"tg{n_gen}"
    if n_prompt > 0 and n_gen == 0:
        return n_prompt / tokens_per_s, f"pp{n_prompt}"
    if n_prompt > 0 and n_gen > 0:
        return (n_prompt + n_gen) / tokens_per_s, f"pg {n_prompt}+{n_gen}"
    return None


@register("R13")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert probe is not None
    lb = probe.json("llama_bench")
    hf = probe.json("hyperfine")

    if not isinstance(lb, list) or not lb:
        return skipped(spec, "llama_bench JSON is not a non-empty result array")
    results = (hf or {}).get("results") or []
    if not results:
        return skipped(spec, "hyperfine JSON carries no results[]")
    hf_res = results[0]
    times = hf_res.get("times") or []
    if len(times) < benchstats.MIN_SAMPLES_FOR_VERDICT:
        return skipped(spec, f"hyperfine has {len(times)} timing samples (< {benchstats.MIN_SAMPLES_FOR_VERDICT})")

    # --- instrument self-consistency gates -------------------------------
    for entry in lb:
        samples = entry.get("samples_ts") or []
        if not samples:
            continue
        cc = benchstats.crosscheck_stddev(
            samples, entry.get("avg_ts"), entry.get("stddev_ts")
        )
        if not cc.ok:
            return skipped(
                spec,
                f"llama-bench self-report disagrees with its samples "
                f"(n_prompt={entry.get('n_prompt')}, n_gen={entry.get('n_gen')}): {cc.notes[0]}",
            )
    cc_hf = benchstats.crosscheck_stddev(times, hf_res.get("mean"), hf_res.get("stddev"))
    if not cc_hf.ok:
        return skipped(spec, f"hyperfine self-report disagrees with its samples: {cc_hf.notes[0]}")

    # --- reconstruct kernel time vs end-to-end ---------------------------
    parts: list[str] = []
    kernel_s = 0.0
    for entry in lb:
        contrib = _entry_kernel_seconds(entry)
        if contrib is None:
            continue
        seconds, label = contrib
        kernel_s += seconds
        parts.append(f"{label}: {seconds:.3f}s")
    if kernel_s <= 0:
        return skipped(spec, "no usable pp/tg samples in llama-bench JSON")

    e2e_s = benchstats.median(times)
    if kernel_s > e2e_s * _CONSISTENCY_SLACK:
        return skipped(
            spec,
            f"kernel time {kernel_s:.3f}s exceeds end-to-end {e2e_s:.3f}s — instruments "
            "measured different workloads; refusing to diagnose",
        )

    overhead_s = max(0.0, e2e_s - kernel_s)
    ratio = overhead_s / e2e_s if e2e_s > 0 else 0.0
    split = (
        f"kernel {kernel_s:.3f}s ({', '.join(parts)}) vs end-to-end {e2e_s:.3f}s "
        f"→ {ratio * 100:.1f}% of wall time outside kernels"
    )

    if ratio <= DIVERGENCE_THRESHOLD:
        return clean(
            spec,
            [split, f"within {DIVERGENCE_THRESHOLD * 100:.0f}% divergence threshold — kernels dominate"],
        )

    fix = Fix(
        rule_id=spec.id,
        kind="code_suggestion",
        description=(
            f"{ratio * 100:.1f}% of end-to-end time is tokenization/sampling/serving, "
            "which llama-bench does not measure — kernel tuning cannot recover it. "
            "Redirect optimization to the pipeline: batch tokenization, prompt "
            "caching, streaming, persistent server instead of process-per-request."
        ),
        patch=None,
        commands=(
            "hyperfine --warmup 2 -r 7 '<end-to-end command>'  # E2E instrument",
            "llama-bench -m model.gguf -r 7 -o json            # kernel instrument",
        ),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=(
            split,
            "llama-bench timings exclude tokenization + sampling (documented caveat)",
            "instrument self-reports agree with raw samples (cross-checked)",
        ),
        locations=("probe:llama_bench", "probe:hyperfine"),
        fix=fix,
    )
