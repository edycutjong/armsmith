"""R7 — ONNX Runtime session defaults (probe: ort_session).

ort_session.json shape (recorded from the app's SessionOptions)::

    {"intra_op_num_threads": 0, "inter_op_num_threads": 0,
     "graph_optimization_level": "ORT_ENABLE_BASIC",
     "execution_mode": "ORT_SEQUENTIAL", "workers": 4, "nproc": 16}

Cross-multiplies intra-op defaults with worker count (squeeze pass 2 #5).
"""

from __future__ import annotations

from pathlib import Path

from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register, skipped

_FULL_OPT = "ORT_ENABLE_ALL"


@register("R7")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert probe is not None
    data = probe.json("ort_session")
    try:
        intra = int(data["intra_op_num_threads"])
        inter = int(data["inter_op_num_threads"])
        opt_level = str(data["graph_optimization_level"])
    except (KeyError, TypeError, ValueError) as exc:
        return skipped(spec, f"ort_session probe missing session-option fields: {exc}")
    workers = data.get("workers", 1)
    nproc = data.get("nproc", 0)
    workers = workers if isinstance(workers, int) and workers >= 1 else 1
    nproc = nproc if isinstance(nproc, int) and nproc >= 1 else 0

    evidence: list[str] = []
    if opt_level != _FULL_OPT:
        evidence.append(
            f"graph_optimization_level={opt_level} — fusions/layout optimizations "
            f"below {_FULL_OPT}"
        )
    if intra == 0 and workers > 1:
        per_worker = nproc if nproc else "nproc"
        evidence.append(
            f"intra_op_num_threads=0 (default = all cores) with {workers} workers → "
            f"{workers} × {per_worker} threads oversubscribe the box"
        )
    if inter not in (0, 1) and workers > 1:
        evidence.append(
            f"inter_op_num_threads={inter} with {workers} workers multiplies thread pools"
        )

    if not evidence:
        return clean(
            spec,
            [f"session options tuned: intra={intra}, inter={inter}, opt={opt_level}, workers={workers}"],
        )

    suggested_intra = max(1, nproc // workers) if nproc else 1
    fix = Fix(
        rule_id=spec.id,
        kind="config_patch",
        description=(
            "Pin ONNX Runtime session options for multi-worker aarch64 serving: "
            f"intra_op={suggested_intra} (nproc/workers), inter_op=1, graph "
            f"optimization {_FULL_OPT} (default CPU EP; ACL EP is community-"
            "maintained — mention-only)."
        ),
        patch=(
            "so = onnxruntime.SessionOptions()\n"
            f"so.intra_op_num_threads = {suggested_intra}  # nproc({nproc}) // workers({workers})\n"
            "so.inter_op_num_threads = 1\n"
            "so.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL\n"
            "session = onnxruntime.InferenceSession(model_path, sess_options=so)"
        ),
        commands=(),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=tuple(evidence),
        locations=("probe:ort_session",),
        fix=fix,
    )
