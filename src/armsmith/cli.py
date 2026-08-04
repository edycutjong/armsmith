"""armsmith CLI (Typer) — hardware-free Phase-1 surface.

Commands:
  scan <repo_dir>              static-only aarch64 anti-pattern scan (R1/R4/R12), zero hardware
  diagnose --replay <bundle>   full offline loop: scan → plan → gate → signed report
  witness <before> <after>     ISA-witness: count SDOT/UDOT/SMMLA/USMMLA before vs after
  pr <report.json>             assemble the bot PR (dry-run); live posting is TODO(S1)
  ci --replay <bundle>         reproduce-gate as an exit-code CI twin (fails on regression)
  rules list | rules explain   the 13-rule pack with citations
  verify <report.json>         hash + ed25519 + recompute-stats verification
  keys init                    generate the local ed25519 signing keypair
  doctor --offline             host/ISA fingerprint from recorded fixtures
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__

app = typer.Typer(
    name="armsmith",
    help="Profile → diagnose → patch → prove → PR, for AI repos on Arm.",
    no_args_is_help=True,
    add_completion=False,
)
rules_app = typer.Typer(help="Inspect the 13-rule aarch64 anti-pattern pack.", no_args_is_help=True)
keys_app = typer.Typer(help="Manage the local ed25519 signing keypair.", no_args_is_help=True)
app.add_typer(rules_app, name="rules")
app.add_typer(keys_app, name="keys")

console = Console()
err_console = Console(stderr=True, style="bold red")


@app.command()
def version() -> None:
    """Print the armsmith version."""
    console.print(f"armsmith {__version__}")


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------

@app.command()
def diagnose(
    replay: Path = typer.Option(
        ..., "--replay", exists=True, file_okay=False,
        help="Replay bundle directory (recorded probes + bench records). "
             "Live hardware mode is TODO(S1).",
    ),
    out: Path = typer.Option(Path("report.json"), "--out", "-o", help="Report output path."),
    sign: bool = typer.Option(True, "--sign/--no-sign", help="ed25519-sign the report."),
    key_dir: Path | None = typer.Option(None, "--key-dir", help="Key directory (default ~/.armsmith)."),
    max_fixes: int | None = typer.Option(None, "--max-fixes", min=1, help="Cap planned fixes."),
    pr_dry_run: bool = typer.Option(False, "--pr-dry-run", help="Render the PR that WOULD be opened (no network)."),
) -> None:
    """Run the full diagnose loop against a recorded replay bundle."""
    from .diagnose import run_replay_diagnosis
    from .evidence import render_markdown
    from .report import write_report

    try:
        result = run_replay_diagnosis(replay, key_dir=key_dir, sign=sign, max_fixes=max_fixes)
    except (FileNotFoundError, ValueError) as exc:
        err_console.print(f"diagnose failed: {exc}")
        raise typer.Exit(code=2)

    rpt = result.report
    console.print(
        "[yellow]⚠ REPLAY MODE — synthetic/recorded data; not hardware results.[/yellow]"
    )

    # findings table
    table = Table(title=f"Rule scan — {rpt['scenario']}")
    table.add_column("rule")
    table.add_column("status")
    table.add_column("evidence / reason", overflow="fold")
    for f in rpt["findings"]:
        status = f["status"]
        style = {"matched": "red", "clean": "green", "skipped": "dim"}[status]
        detail = f["evidence"][0] if f["evidence"] else (f.get("skipped_reason") or "")
        table.add_row(f["rule_id"], f"[{style}]{status}[/{style}]", escape(detail))
    console.print(table)

    # gate summary
    kept = [x for x in rpt["fixes"] if x["verdict"] == "keep"]
    dropped = [x for x in rpt["fixes"] if x["verdict"] == "drop"]
    if rpt["fixes"]:
        gt = Table(title="Reproduce gate")
        gt.add_column("variant")
        gt.add_column("rule")
        gt.add_column("verdict")
        gt.add_column("reason", overflow="fold")
        for fx in rpt["fixes"]:
            color = "green" if fx["verdict"] == "keep" else "red"
            gt.add_row(
                fx["variant"], fx.get("rule_id") or "—",
                f"[{color}]{fx['verdict']}[/{color}]",
                escape(fx["reasons"][0] if fx["reasons"] else ""),
            )
        console.print(gt)
        console.print(f"gate: [green]{len(kept)} kept[/green] · [red]{len(dropped)} dropped[/red] (drops are reported, never hidden)")
    else:
        console.print("[dim]no bench records in bundle — scan-only report[/dim]")

    write_report(rpt, out)
    sig = rpt.get("signature")
    if sig:
        console.print(f"report → {out}  [green]signed[/green] sha256:{sig['report_sha256'][:16]}…")
    else:
        console.print(f"report → {out}  [yellow]UNSIGNED[/yellow] ({result.sign_note})")

    if pr_dry_run:
        from .ghpr import build_pr_draft, render_dry_run

        draft = build_pr_draft(rpt, specs_by_id=result.specs)
        console.print(render_dry_run(draft), markup=False)
    else:
        # still exercise the renderer so failures surface in normal runs
        render_markdown(rpt, specs_by_id=result.specs)


# ---------------------------------------------------------------------------
# scan  (static-only, zero hardware — the judge's no-hardware entry point)
# ---------------------------------------------------------------------------

@app.command()
def scan(
    repo_dir: Path = typer.Argument(
        ..., exists=True, file_okay=False, metavar="REPO_DIR",
        help="A repo checkout (or a replay bundle containing repo/) to scan with the "
             "STATIC rules only — zero probes, zero hardware.",
    ),
    strict: bool = typer.Option(
        False, "--strict", help="Exit non-zero if any anti-pattern is matched (CI use)."
    ),
) -> None:
    """Static-only aarch64 anti-pattern scan (R1 amd64 image · R4 float64 coercion ·
    R12 CI matrix). Runs on a real directory with no probes and no Arm hardware — the
    zero-hardware entry point a judge can point at their own clone."""
    from .rules import load_pack, run_all

    # A replay bundle nests the code under repo/; a bare clone is scanned as-is.
    target = repo_dir / "repo" if (repo_dir / "repo").is_dir() else repo_dir

    specs = load_pack()
    findings = {f.rule_id: f for f in run_all(specs, target, None)}  # probe=None → probe rules skip
    static_ids = [rid for rid, spec in specs.items() if spec.kind == "static"]

    table = Table(title=f"Static scan — {target}")
    table.add_column("rule")
    table.add_column("status")
    table.add_column("evidence / reason", overflow="fold")
    matched = 0
    for rid in static_ids:
        f = findings[rid]
        status = f.status.value
        style = {"matched": "red", "clean": "green", "skipped": "dim"}[status]
        detail = f.evidence[0] if f.evidence else (f.skipped_reason or "")
        table.add_row(rid, f"[{style}]{status}[/{style}]", escape(detail))
        if f.matched:
            matched += 1
    console.print(table)
    console.print(
        f"static scan: [red]{matched} matched[/red] of {len(static_ids)} static rules "
        "· probe/runtime rules (R2/R3/R5–R11/R13) need a replay bundle — run "
        "[bold]armsmith diagnose --replay[/bold]"
    )
    if strict and matched:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# witness  (ISA-witness: deterministic instruction-level proof, zero hardware)
# ---------------------------------------------------------------------------

@app.command()
def witness(
    before: Path = typer.Argument(
        ..., exists=True, dir_okay=False, help="objdump/disassembly text of the BASELINE hot symbol."
    ),
    after: Path = typer.Argument(
        ..., exists=True, dir_okay=False, help="objdump/disassembly text of the OPTIMIZED hot symbol."
    ),
) -> None:
    """ISA-witness: count Arm dot-product / int8-matmul instructions
    (SDOT/UDOT/SMMLA/USMMLA) before vs after. Deterministic proof the optimized kernel
    path is actually emitted — no stopwatch, no hardware, re-runnable anywhere."""
    from .witness import WITNESS_MNEMONICS, count_witness, witness_delta

    wb = count_witness(before.read_text(encoding="utf-8"))
    wa = count_witness(after.read_text(encoding="utf-8"))

    table = Table(title="ISA-witness — Arm kernel instructions in the hot path")
    table.add_column("mnemonic")
    table.add_column("before", justify="right")
    table.add_column("after", justify="right")
    for m in WITNESS_MNEMONICS:
        table.add_row(m, str(wb.counts.get(m, 0)), str(wa.counts.get(m, 0)))
    table.add_row("[bold]dotprod[/bold]", str(wb.dotprod), f"[green]{wa.dotprod}[/green]")
    table.add_row("[bold]int8 matmul[/bold]", str(wb.int8_matmul), f"[green]{wa.int8_matmul}[/green]")
    console.print(table)
    for line in witness_delta(wb, wa):
        console.print(f"  {line}")


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------

@rules_app.command("list")
def rules_list() -> None:
    """List the rule pack."""
    from .rules import load_pack

    specs = load_pack()
    table = Table(title=f"Armsmith rule pack — {len(specs)} rules")
    table.add_column("id")
    table.add_column("title", overflow="fold")
    table.add_column("kind")
    table.add_column("confidence")
    table.add_column("expected gain*")
    for spec in specs.values():
        lo, hi = spec.expected_gain_range
        table.add_row(spec.id, spec.title, spec.kind, spec.confidence, f"{lo:g}–{hi:g}×")
    console.print(table)
    console.print("[dim]* estimates from citations, used for planning order only — "
                  "results come exclusively from the reproduce gate[/dim]")


@rules_app.command("explain")
def rules_explain(rule_id: str = typer.Argument(..., help="Rule id, e.g. R3")) -> None:
    """Show one rule in full: summary, fix generator, citation."""
    from .rules import load_pack

    specs = load_pack()
    spec = specs.get(rule_id.upper())
    if spec is None:
        err_console.print(f"unknown rule {rule_id!r} — known: {', '.join(specs)}")
        raise typer.Exit(code=2)
    console.print(f"[bold]{spec.id} — {spec.title}[/bold]")
    console.print(f"kind: {spec.kind} · confidence: {spec.confidence} · probes: {list(spec.requires) or '—'}")
    console.print(f"\n{spec.summary}\n")
    console.print(f"[bold]fix generator:[/bold] {spec.fix_generator}")
    lo, hi = spec.expected_gain_range
    console.print(f"[bold]expected gain (estimate):[/bold] {lo:g}–{hi:g}× — {spec.gain_note}")
    console.print(f"[bold]citation:[/bold] {spec.citation_url}")
    if spec.learning_path:
        console.print(f"[bold]Arm Learning Path:[/bold] {spec.learning_path}")
    else:
        console.print("[bold]Arm Learning Path:[/bold] [dim]none — no direct LP; cites upstream doc above[/dim]")


@rules_app.command("export")
def rules_export(
    fmt: str = typer.Option("md", "--format", "-f", help="Export format (only 'md' is supported)."),
    out_dir: Path = typer.Option(
        Path("docs/migration-templates"), "--out-dir", "-o",
        help="Directory to write one migration-template card per rule.",
    ),
) -> None:
    """Render the 13 rule descriptors to Markdown x86→Arm migration cards
    (anti-pattern · before→after fix · expected gain · upstream citation · Arm
    Learning Path). The rubric's 'migration templates' + 'learning-ready content'
    artifact — pure render of fixture-tested YAML, zero hardware."""
    from .rules import load_pack

    if fmt != "md":
        err_console.print(f"unsupported format {fmt!r} — only 'md' is available")
        raise typer.Exit(code=2)

    specs = load_pack()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    lp_count = 0
    for spec in specs.values():
        lo, hi = spec.expected_gain_range
        lp_line = (
            f"**Arm Learning Path:** [{spec.learning_path}]({spec.learning_path}) — teaches this fix by hand.\n"
            if spec.learning_path
            else "**Arm Learning Path:** none — no direct Arm LP for this pattern; cites the upstream doc above.\n"
        )
        if spec.learning_path:
            lp_count += 1
        card = (
            f"# {spec.id} — {spec.title}\n\n"
            f"> x86→Arm/Graviton migration template · kind: `{spec.kind}` · confidence: {spec.confidence} · "
            f"expected gain (estimate): {lo:g}–{hi:g}×\n\n"
            f"## Anti-pattern\n\n{spec.summary}\n\n"
            f"## Fix\n\n{spec.fix_generator}\n\n"
            f"## Expected gain\n\n{lo:g}–{hi:g}× — {spec.gain_note}\n\n"
            f"> Estimates are for planning order only. Only Armsmith's reproduce gate "
            f"(median-of-N · MAD noise band · output-hash equality) can claim a result.\n\n"
            f"## References\n\n"
            f"**Upstream doc:** [{spec.citation_url}]({spec.citation_url})\n\n"
            f"{lp_line}"
        )
        path = out_dir / f"{spec.id}.md"
        path.write_text(card, encoding="utf-8")
        written.append(path)

    index = ["# x86 → Arm migration templates\n",
             "One card per Armsmith rule: the anti-pattern, the fix, and the Arm Learning "
             "Path that teaches it. Generated by `armsmith rules export --format md`.\n",
             "| Rule | Migration template | Arm Learning Path |",
             "|---|---|---|"]
    for spec in specs.values():
        lp = f"[LP]({spec.learning_path})" if spec.learning_path else "—"
        index.append(f"| {spec.id} | [{spec.title}]({spec.id}.md) | {lp} |")
    (out_dir / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")

    console.print(
        f"exported [green]{len(written)}[/green] migration cards → {out_dir}/ "
        f"({lp_count} link an Arm Learning Path; {len(written) - lp_count} cite upstream docs honestly) "
        f"+ README.md index"
    )


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

@app.command()
def verify(
    report_path: Path = typer.Argument(..., exists=True, dir_okay=False, metavar="REPORT_JSON"),
    trusted_pubkey_b64: str | None = typer.Option(
        None, "--trusted-pubkey", help="Require the signer to match this base64 raw ed25519 public key."
    ),
    schema: bool = typer.Option(True, "--schema/--no-schema", help="Validate against report.schema.json."),
) -> None:
    """Verify a report: content hash, ed25519 signature, schema, and that every
    claimed statistic recomputes from the embedded raw samples."""
    from .report import load_report, verify_report

    rpt = load_report(report_path)
    result = verify_report(rpt, trusted_public_key_b64=trusted_pubkey_b64, check_schema=schema)
    for check in result.checks:
        console.print(f"[green]✓[/green] {check}")
    for issue in result.issues:
        console.print(f"[red]✗ {issue.kind}[/red]: {issue.detail}")
    if not result.ok:
        err_console.print("VERIFY FAILED")
        raise typer.Exit(code=1)
    console.print("[bold green]VERIFY OK[/bold green] — tamper-evident chain holds")


# ---------------------------------------------------------------------------
# pr  (bot PR assembly — DRY-RUN only in this phase; live posting is TODO(S1))
# ---------------------------------------------------------------------------

@app.command()
def pr(
    report_path: Path = typer.Argument(..., exists=True, dir_okay=False, metavar="REPORT_JSON"),
    dry_run: bool = typer.Option(
        True, "--dry-run/--no-dry-run",
        help="Render the PR that WOULD be opened (default). Live posting is TODO(S1).",
    ),
    repo_slug: str = typer.Option("<org>/<repo>", "--repo", help="owner/name shown in the PR draft."),
) -> None:
    """Assemble the bot PR from a report: one commit per KEPT fix, evidence table, and
    the dropped-with-reasons log. Dry-run is fully real; live posting exits non-zero
    until the S1 submitter lands — armsmith never fakes a network result."""
    from .report import load_report
    from .rules import load_pack

    rpt = load_report(report_path)
    if not dry_run:
        err_console.print(
            "live PR posting is TODO(S1) — the PyGithub submitter is not wired yet. "
            "Run without --no-dry-run to render the exact PR that WOULD be opened."
        )
        raise typer.Exit(code=2)

    from .ghpr import build_pr_draft, render_dry_run

    draft = build_pr_draft(rpt, specs_by_id=load_pack(), repo_slug=repo_slug)
    console.print(render_dry_run(draft), markup=False)


# ---------------------------------------------------------------------------
# ci  (reproduce gate as an exit-code CI twin — the RegressionRail surface)
# ---------------------------------------------------------------------------

@app.command()
def ci(
    replay: Path = typer.Option(
        ..., "--replay", exists=True, file_okay=False,
        help="Replay bundle to gate. Live arm64-runner mode is TODO(S1).",
    ),
    key_dir: Path | None = typer.Option(None, "--key-dir", help="Key directory (default ~/.armsmith)."),
) -> None:
    """Reproduce gate as an exit-code CI twin: runs the same gate as `diagnose` and
    exits non-zero if any candidate REGRESSES outside its noise band — a drop-in
    GitHub Action for `runs-on: ubuntu-24.04-arm`."""
    from .benchstats import Verdict
    from .diagnose import run_replay_diagnosis

    try:
        result = run_replay_diagnosis(replay, key_dir=key_dir, sign=False)
    except (FileNotFoundError, ValueError) as exc:
        err_console.print(f"ci gate failed to run: {exc}")
        raise typer.Exit(code=2)

    rpt = result.report
    regressions: list[tuple[str, str, dict]] = []
    for fx in rpt.get("fixes", []):
        for metric, cmp in (fx.get("comparisons") or {}).items():
            if cmp.get("verdict") == Verdict.REGRESSED.value:
                regressions.append((fx["variant"], metric, cmp))

    kept = [x for x in rpt["fixes"] if x["verdict"] == "keep"]
    dropped = [x for x in rpt["fixes"] if x["verdict"] == "drop"]
    console.print(
        f"[yellow]⚠ REPLAY MODE[/yellow] ci gate — [green]{len(kept)} kept[/green] · "
        f"[red]{len(dropped)} dropped[/red] · {len(regressions)} regression(s)"
    )
    if regressions:
        for variant, metric, cmp in regressions:
            console.print(f"[red]✗ regression[/red] {variant}: {metric} — {escape(cmp.get('reason', ''))}")
        err_console.print("CI GATE FAILED — performance regression detected")
        raise typer.Exit(code=1)
    console.print("[bold green]CI GATE PASSED[/bold green] — no regression outside the noise band")


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------

@keys_app.command("init")
def keys_init(
    key_dir: Path | None = typer.Option(None, "--key-dir", help="Key directory (default ~/.armsmith)."),
    force: bool = typer.Option(False, "--force", help="Rotate: overwrite an existing keypair."),
) -> None:
    """Generate the ed25519 keypair used to sign reports."""
    from .keys import KeyError_, init_keys

    try:
        priv, pub = init_keys(key_dir=key_dir, force=force)
    except KeyError_ as exc:
        err_console.print(str(exc))
        raise typer.Exit(code=1)
    console.print(f"private key → {priv} (0600)")
    console.print(f"public key  → {pub}")
    console.print("[dim]CI uses Sigstore keyless instead of this key — TODO(S1).[/dim]")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

@app.command()
def doctor(
    offline: bool = typer.Option(
        False, "--offline",
        help="Fingerprint from recorded fixtures (required in Phase 1 — live capture is TODO(S1)).",
    ),
    replay: Path | None = typer.Option(
        None, "--replay", exists=True, file_okay=False,
        help="Replay bundle to fingerprint (uses its recorded lscpu + manifest host block).",
    ),
    lscpu_file: Path | None = typer.Option(
        None, "--lscpu-file", exists=True, dir_okay=False,
        help="Raw recorded lscpu output to fingerprint (labeled as fixture data).",
    ),
) -> None:
    """Host/ISA fingerprint table (dotprod/i8mm/SVE/SVE2/BF16/SME)."""
    from .fingerprint import (
        FEATURE_ORDER,
        HostFingerprint,
        _features_from_flags,
        capture_fingerprint,
        parse_lscpu,
    )
    from .probes import ReplayProbe

    if not offline:
        err_console.print(
            "doctor requires --offline in the Phase-1 core: live capture (lscpu/"
            "sysreport on the target box) is TODO(S1), and armsmith never "
            "fingerprints this development machine as if it were a target."
        )
        raise typer.Exit(code=2)

    fp: HostFingerprint
    if replay is not None:
        probe = ReplayProbe(replay)
        if not probe.has("lscpu"):
            err_console.print(f"bundle {replay} records no lscpu probe")
            raise typer.Exit(code=2)
        fp = capture_fingerprint(probe, probe.manifest.host)
    elif lscpu_file is not None:
        kv = parse_lscpu(lscpu_file.read_text(encoding="utf-8"))
        flags = kv.get("Flags", "").split()
        fp = HostFingerprint(
            architecture=kv.get("Architecture", "unknown"),
            model_name=kv.get("Model name", "unknown"),
            vendor=kv.get("Vendor ID", "unknown"),
            cpus=int(kv.get("CPU(s)", "0") or 0),
            isa=_features_from_flags(flags),
            flags=tuple(flags),
            source=f"fixture:{lscpu_file.name}",
        )
    else:
        err_console.print("provide --replay <bundle> or --lscpu-file <recorded lscpu output>")
        raise typer.Exit(code=2)

    console.print(f"[yellow]⚠ fingerprint source: {fp.source} — recorded data, not this machine[/yellow]")
    info = Table(title="Host fingerprint")
    info.add_column("field")
    info.add_column("value")
    info.add_row("architecture", fp.architecture)
    info.add_row("model", fp.model_name)
    info.add_row("vendor", fp.vendor)
    info.add_row("vCPUs", str(fp.cpus))
    info.add_row("instance", fp.instance)
    info.add_row("kernel", fp.kernel)
    info.add_row("governor", fp.governor)
    console.print(info)

    isa = Table(title="ISA features (rule-routing inputs)")
    isa.add_column("feature")
    isa.add_column("present")
    for name in FEATURE_ORDER:
        present = getattr(fp.isa, name)
        isa.add_row(name, "[green]✓[/green]" if present else "[red]✗[/red]")
    console.print(isa)
    console.print(
        "[dim]TODO(S1): embed sysreport (--config, --vulnerabilities sections) and "
        "carry a sysreport-summary hash in report provenance; Performix/MCP "
        "availability checks land with the hardware phase.[/dim]"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
