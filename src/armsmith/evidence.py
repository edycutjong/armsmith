"""armsmith.evidence — renders the PR-body evidence markdown from a report.

The table schema is identical in CLI, PR body, and viewer (COMPLEXITY §5):

    | metric | before | after | Δ | noise band | PMU Δ |

plus: replay/synthetic banner, kept and dropped fix sections (drops carry
their reasons — honesty discipline), rule citations, host fingerprint, and
the cosign verify-blob footer (COMMAND STRING ONLY — running it is a CI/S1
concern; see the cosign docs for the verified invocation shape).
"""

from __future__ import annotations

from typing import Any

__all__ = ["render_markdown", "cosign_verify_line", "render_fix_table"]

_PMU_PRIMARY = ("ipc", "cache_miss_pct", "cycles", "instructions")


def _fmt(v: float | None, digits: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{digits}g}"
    return str(v)


def _delta_cell(cmp: dict) -> str:
    delta = cmp.get("delta")
    pct = cmp.get("delta_pct")
    if delta is None:
        return "—"
    arrow = "↓" if delta < 0 else ("↑" if delta > 0 else "→")
    pct_txt = f" ({pct:+.1f}%)" if pct is not None else ""
    return f"{arrow} {delta:+.4g}{pct_txt}"


def _band_cell(cmp: dict) -> str:
    band = cmp.get("band")
    if band is None:
        return "—"
    verdict = cmp.get("verdict")
    marker = "outside band" if verdict in ("improved", "regressed") else "inside band"
    return f"±{band:.4g} ({marker})"


def _pmu_cell(pmu_delta: dict[str, Any]) -> str:
    parts = []
    for counter in _PMU_PRIMARY:
        entry = pmu_delta.get(counter)
        if not entry:
            continue
        pct = entry.get("delta_pct")
        if pct is None:
            continue
        parts.append(f"{counter} {pct:+.1f}%")
    return "; ".join(parts) if parts else "—"


def render_fix_table(fix: dict) -> str:
    """One fix's evidence table: metric | before | after | Δ | noise band | PMU Δ."""
    rows = [
        "| metric | before | after | Δ | noise band | PMU Δ |",
        "|---|---|---|---|---|---|",
    ]
    pmu_cell = _pmu_cell(fix.get("pmu_delta") or {})
    comparisons = fix.get("comparisons") or {}
    first = True
    for metric in sorted(comparisons):
        cmp = comparisons[metric]
        before = (cmp.get("baseline") or {}).get("median")
        after = (cmp.get("candidate") or {}).get("median")
        rows.append(
            f"| {metric} | {_fmt(before)} | {_fmt(after)} | {_delta_cell(cmp)} "
            f"| {_band_cell(cmp)} | {pmu_cell if first else '〃'} |"
        )
        first = False
    if len(rows) == 2:
        rows.append("| _no shared metrics_ | — | — | — | — | — |")
    return "\n".join(rows)


def cosign_verify_line(
    report_name: str = "report.json",
    repo_slug: str = "<org>/<repo>",
    workflow: str = "ci.yml",
) -> str:
    """The judge-facing cosign verify-blob command (string only, not executed)."""
    return (
        f"cosign verify-blob {report_name} --bundle {report_name.replace('.json', '')}.sigstore.json "
        f'--certificate-identity "https://github.com/{repo_slug}/.github/workflows/{workflow}@refs/heads/main" '
        f'--certificate-oidc-issuer "https://token.actions.githubusercontent.com"'
    )


def _finding_lines(findings: list[dict], specs_by_id: dict[str, Any] | None) -> list[str]:
    lines: list[str] = []
    for f in findings:
        status = f.get("status")
        rid = f.get("rule_id")
        icon = {"matched": "🔧", "clean": "✅", "skipped": "⏭"}.get(status, "•")
        title = ""
        cite = ""
        if specs_by_id and rid in specs_by_id:
            spec = specs_by_id[rid]
            title = f" — {spec.title}"
            cite = f" ([source]({spec.citation_url}))"
            if getattr(spec, "learning_path", None):
                cite += f" · [Arm Learning Path]({spec.learning_path})"
        lines.append(f"- {icon} **{rid}**{title}: {status}{cite}")
        if status == "matched":
            for ev in (f.get("evidence") or [])[:3]:
                lines.append(f"  - {ev}")
        elif status == "skipped" and f.get("skipped_reason"):
            lines.append(f"  - reason: {f['skipped_reason']}")
    return lines


def render_markdown(
    report: dict,
    specs_by_id: dict[str, Any] | None = None,
    repo_slug: str = "<org>/<repo>",
) -> str:
    """Full PR-body / report markdown."""
    out: list[str] = []
    mode = report.get("mode", "replay")
    synthetic = report.get("synthetic", True)

    out.append(f"## Armsmith diagnosis — `{report.get('scenario', 'unknown')}`")
    out.append("")
    # Provenance and transport are separate axes. `mode` says how the numbers
    # reached the report; `synthetic` says whether they were ever real. A bundle
    # from `armsmith record` is replayed but genuine, and stamping it SYNTHETIC
    # would understate a real measurement — the mirror image of the overclaim
    # this whole tool exists to prevent.
    if synthetic:
        out.append(
            "> ⚠️ **REPLAY MODE — SYNTHETIC DATA.** Measurements below come from a "
            "synthetic replay bundle used to exercise the pipeline. They are "
            "NOT hardware results and must not be quoted as such."
        )
        out.append("")
    elif mode == "replay":
        out.append(
            "> ● **RECORDED — REAL OBSERVATIONS.** Measurements below were captured on a "
            "host by `armsmith record` and replayed through the gate; the bundle manifest "
            "declares `\"synthetic\": false`. Probes that could not be observed were "
            "omitted, never invented."
        )
        out.append("")

    host = report.get("host") or {}
    if host:
        feats = ", ".join(host.get("isa_feats") or []) or "none reported"
        out.append(
            f"**Host:** {host.get('model_name', '?')} · {host.get('cpus', '?')} vCPU · "
            f"instance `{host.get('instance', '?')}` · kernel `{host.get('kernel', '?')}` · "
            f"ISA: {feats} · source: {host.get('source', '?')}"
        )
        out.append("")

    gate_cfg = report.get("gate_config") or {}
    out.append(
        f"**Gate:** median-of-N, MAD noise band (k={gate_cfg.get('band_k', '?')}), "
        f"min {gate_cfg.get('min_samples', '?')} samples/side, output-hash equality "
        f"{'required' if gate_cfg.get('require_output_hash') else 'optional'}. "
        "In-band deltas are reported as **no change** — never as wins."
    )
    out.append("")

    findings = report.get("findings") or []
    if findings:
        out.append(f"### Rule scan ({sum(1 for f in findings if f.get('status') == 'matched')} matched)")
        out.extend(_finding_lines(findings, specs_by_id))
        out.append("")

    fixes = report.get("fixes") or []
    kept = [f for f in fixes if f.get("verdict") == "keep"]
    dropped = [f for f in fixes if f.get("verdict") == "drop"]

    if kept:
        out.append(f"### ✅ Fixes kept by the reproduce gate ({len(kept)})")
        for fix in kept:
            out.append("")
            out.append(f"#### `{fix.get('variant')}` (rule {fix.get('rule_id') or '—'})")
            out.append("")
            out.append(render_fix_table(fix))
            out.append("")
            hash_eq = fix.get("output_hash_equal")
            out.append(
                f"- output hash: {'equal ✓' if hash_eq else ('MISMATCH ✗' if hash_eq is False else 'not checked')}"
            )
            for reason in fix.get("reasons") or []:
                out.append(f"- {reason}")
    if dropped:
        out.append("")
        out.append(f"### 🗑 Fixes dropped by the gate ({len(dropped)}) — reported, not hidden")
        for fix in dropped:
            out.append(f"- `{fix.get('variant')}` (rule {fix.get('rule_id') or '—'}):")
            for reason in fix.get("reasons") or []:
                out.append(f"  - {reason}")
    if fixes:
        out.append("")

    sig = report.get("signature")
    out.append("---")
    if sig:
        out.append(
            f"Report `sha256:{sig.get('report_sha256', '')[:16]}…` · ed25519-signed "
            f"(key `{sig.get('public_key_b64', '')[:12]}…`) · verify locally: "
            f"`armsmith verify report.json`"
        )
    else:
        out.append("Report unsigned (run `armsmith keys init` and re-run with --sign).")
    out.append("")
    out.append("CI attestation verify (keyless, GitHub OIDC — command for judges; TODO(S1) wiring):")
    out.append("```")
    out.append(cosign_verify_line(repo_slug=repo_slug))
    out.append("```")
    out.append("")
    out.append("_Generated by [Armsmith](https://armsmith.edycu.dev) — the agent proposes, the silicon disposes._")
    return "\n".join(out)
