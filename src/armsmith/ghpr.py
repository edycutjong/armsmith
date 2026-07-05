"""armsmith.ghpr — GitHub PR assembly, DRY-RUN ONLY in this phase.

Renders exactly what WOULD be posted: PR title, body (evidence markdown),
branch name, and one commit per surviving fix.  No network calls exist in
this module; the PyGithub-backed submitter lands at S1 (`armsmith pr`).
"""

from __future__ import annotations

from dataclasses import dataclass

from .evidence import render_markdown

__all__ = ["CommitPlan", "PrDraft", "build_pr_draft", "render_dry_run"]


@dataclass(frozen=True)
class CommitPlan:
    message: str
    rule_id: str | None
    files_touched: tuple[str, ...]   # descriptive in replay mode
    patch_preview: str | None


@dataclass(frozen=True)
class PrDraft:
    repo_slug: str
    branch: str
    title: str
    body: str
    commits: tuple[CommitPlan, ...]
    labels: tuple[str, ...] = ("armsmith", "performance", "arm64")


def build_pr_draft(report: dict, specs_by_id=None, repo_slug: str = "<org>/<repo>") -> PrDraft:
    """One commit per KEPT fix; dropped fixes appear only in the body's drop log."""
    kept = [f for f in (report.get("fixes") or []) if f.get("verdict") == "keep"]
    findings_by_rule = {f.get("rule_id"): f for f in (report.get("findings") or [])}

    commits: list[CommitPlan] = []
    for fix in kept:
        rule_id = fix.get("rule_id")
        finding = findings_by_rule.get(rule_id) or {}
        proposal = finding.get("fix") or {}
        headline = ""
        for metric, cmp in sorted((fix.get("comparisons") or {}).items()):
            if cmp.get("verdict") == "improved" and cmp.get("delta_pct") is not None:
                headline = f" ({metric} {cmp['delta_pct']:+.1f}%)"
                break
        commits.append(
            CommitPlan(
                message=f"perf(arm64): {rule_id} {proposal.get('kind', 'fix')}{headline} [armsmith]",
                rule_id=rule_id,
                files_touched=tuple(finding.get("locations") or ()),
                patch_preview=proposal.get("patch"),
            )
        )

    run_id = str(report.get("run_id", ""))[:8]
    n = len(commits)
    title = f"[armsmith] {n} reproduce-gated Arm optimization{'s' if n != 1 else ''} for {report.get('scenario', 'repo')}"
    return PrDraft(
        repo_slug=repo_slug,
        branch=f"armsmith/fixes-{run_id or 'run'}",
        title=title,
        body=render_markdown(report, specs_by_id=specs_by_id, repo_slug=repo_slug),
        commits=tuple(commits),
    )


def render_dry_run(draft: PrDraft) -> str:
    """Terminal-friendly rendering of what WOULD be posted (no network)."""
    out = [
        "════════ DRY RUN — nothing was sent to GitHub ════════",
        f"repo:    {draft.repo_slug}",
        f"branch:  {draft.branch}",
        f"title:   {draft.title}",
        f"labels:  {', '.join(draft.labels)}",
        f"commits: {len(draft.commits)}",
    ]
    for i, c in enumerate(draft.commits, 1):
        out.append(f"  [{i}] {c.message}")
        if c.files_touched:
            out.append(f"      files: {', '.join(c.files_touched[:6])}")
        if c.patch_preview:
            first = c.patch_preview.strip().splitlines()[0]
            out.append(f"      patch: {first[:100]}")
    out.append("──────── PR body (markdown) ────────")
    out.append(draft.body)
    out.append("════════ END DRY RUN ════════")
    return "\n".join(out)
