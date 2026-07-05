"""R6 — thread oversubscription (probe: env).

env.json shape (recorded at capture time)::

    {"env": {"OMP_NUM_THREADS": "8", ...}, "workers": 4, "nproc": 16,
     "worker_source": "gunicorn -w 4"}

When OMP_NUM_THREADS is unset, OpenMP runtimes default to one thread per
vCPU — each worker then spawns ``nproc`` threads.
"""

from __future__ import annotations

from pathlib import Path

from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register, skipped

_THREAD_KNOBS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "TORCH_NUM_THREADS",
)


@register("R6")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert probe is not None
    data = probe.json("env")
    env = {str(k): str(v) for k, v in (data.get("env") or {}).items()}
    workers = data.get("workers")
    nproc = data.get("nproc")
    if not isinstance(workers, int) or not isinstance(nproc, int) or workers < 1 or nproc < 1:
        return skipped(spec, "env probe lacks integer 'workers'/'nproc' fields")

    explicit: dict[str, int] = {}
    for knob in _THREAD_KNOBS:
        raw = env.get(knob)
        if raw is not None:
            try:
                explicit[knob] = int(raw)
            except ValueError:
                pass

    if explicit:
        threads_per_worker = max(explicit.values())
        thread_source = ", ".join(f"{k}={v}" for k, v in sorted(explicit.items()))
    else:
        threads_per_worker = nproc  # OpenMP default: one thread per vCPU
        thread_source = "no thread env set → OpenMP default = nproc per worker"

    total = workers * threads_per_worker
    if total <= nproc:
        return clean(
            spec,
            [f"workers({workers}) × threads({threads_per_worker}) = {total} ≤ nproc({nproc}) — no oversubscription"],
        )

    suggested = max(1, nproc // workers)
    evidence = [
        f"workers({workers}) × threads/worker({threads_per_worker}) = {total} runnable threads on {nproc} vCPUs "
        f"({total / nproc:.1f}× oversubscribed)",
        f"thread source: {thread_source}",
        f"worker source: {data.get('worker_source', 'unrecorded')}",
    ]
    fix = Fix(
        rule_id=spec.id,
        kind="env_change",
        description=(
            f"Cap per-worker threads so workers × threads matches the vCPU "
            f"budget: OMP_NUM_THREADS={suggested} (and mirror to "
            f"OPENBLAS_NUM_THREADS) with {workers} workers on {nproc} vCPUs."
        ),
        patch=(
            f"OMP_NUM_THREADS={suggested}\n"
            f"OPENBLAS_NUM_THREADS={suggested}"
        ),
        commands=(),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=tuple(evidence),
        locations=("probe:env",),
        fix=fix,
    )
