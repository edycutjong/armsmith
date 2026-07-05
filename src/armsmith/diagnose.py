"""armsmith.diagnose — the diagnose flow (replay mode), CLI-independent.

profile → diagnose → (plan) → reproduce-gate → report, driven entirely from a
replay bundle so the full loop runs with zero Arm hardware (COMPLEXITY §5
`verify_offline` requirement). Live mode lands at S1 with the same code path
fed by LiveProbe + real instruments.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import report as report_mod
from .fingerprint import capture_fingerprint
from .gate import GateConfig, MeasurementSet, load_measurement, run_gate
from .keys import KeyError_
from .planner import DeterministicPlanner, Plan
from .probes import ReplayProbe
from .rules import Finding, RuleSpec, load_pack, run_all

__all__ = ["DiagnosisResult", "run_replay_diagnosis"]


@dataclass
class DiagnosisResult:
    report: dict
    specs: dict[str, RuleSpec]
    findings: list[Finding]
    plan: Plan
    signed: bool
    sign_note: str | None


def _ordered_candidates(
    records: dict[str, MeasurementSet],
    plan: Plan,
) -> list[MeasurementSet]:
    """Order fix measurements by plan priority; unplanned variants follow."""
    by_rule: dict[str, list[MeasurementSet]] = {}
    rest: list[MeasurementSet] = []
    for name in sorted(records):
        ms = records[name]
        if ms.rule_id:
            by_rule.setdefault(ms.rule_id, []).append(ms)
        else:
            rest.append(ms)
    ordered: list[MeasurementSet] = []
    for rid in plan.ordered_rule_ids():
        ordered.extend(by_rule.pop(rid, []))
    for rid in sorted(by_rule):
        ordered.extend(by_rule[rid])
    ordered.extend(rest)
    return ordered


def run_replay_diagnosis(
    bundle_dir: Path,
    key_dir: Path | None = None,
    sign: bool = True,
    gate_config: GateConfig | None = None,
    max_fixes: int | None = None,
) -> DiagnosisResult:
    bundle_dir = Path(bundle_dir)
    probe = ReplayProbe(bundle_dir)
    manifest = probe.manifest

    # 1. host fingerprint — from recorded lscpu + manifest host block only
    host = capture_fingerprint(probe, manifest.host) if probe.has("lscpu") else None

    # 2. rule scan (static rules against bundle repo/, probe rules against probes/)
    specs = load_pack()
    findings = run_all(specs, probe.repo_dir, probe)

    # 3. deterministic plan (Claude planner = TODO(S1))
    planner = DeterministicPlanner()
    plan = planner.plan(findings, specs, max_fixes=max_fixes)

    # 4. reproduce gate over recorded measurements
    cfg = gate_config or GateConfig()
    outcome = None
    bench = probe.bench_records()
    if "baseline" in bench:
        baseline = load_measurement(bench.pop("baseline"))
        candidates = _ordered_candidates(
            {name: load_measurement(path) for name, path in bench.items()},
            plan,
        )
        outcome = run_gate(baseline, candidates, cfg)

    # 5. report (+ optional signature)
    repo_meta = dict(manifest.extra.get("repo", {})) if isinstance(manifest.extra.get("repo"), dict) else {}
    rpt = report_mod.build_report(
        mode="replay",
        scenario=manifest.scenario,
        repo={"url": repo_meta.get("url", f"replay:{bundle_dir.name}"), "sha": repo_meta.get("sha", "n/a")},
        host=host,
        findings=[f.to_dict() for f in findings],
        outcome=outcome,
        gate_config=cfg,
        plan=plan.to_dicts(),
        cost={"cost_usd": 0.0, "note": "replay mode — no hardware spend"},
    )

    signed = False
    sign_note: str | None = None
    if sign:
        try:
            rpt = report_mod.sign_report(rpt, key_dir=key_dir)
            signed = True
        except KeyError_ as exc:
            sign_note = f"report left unsigned: {exc}"
    else:
        sign_note = "signing disabled (--no-sign)"

    return DiagnosisResult(
        report=rpt,
        specs=specs,
        findings=findings,
        plan=plan,
        signed=signed,
        sign_note=sign_note,
    )
