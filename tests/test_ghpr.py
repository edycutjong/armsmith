"""GitHub PR module — dry-run only (renders, never posts)."""

import pytest

from armsmith.ghpr import build_pr_draft, render_dry_run
from armsmith.rules import load_pack


@pytest.fixture(scope="module")
def report():
    from tests.conftest import FIXTURES

    from armsmith.diagnose import run_replay_diagnosis

    return run_replay_diagnosis(FIXTURES / "replays" / "scenario_ragserve", sign=False).report


def test_one_commit_per_kept_fix(report):
    draft = build_pr_draft(report, repo_slug="edycu/ragserve")
    kept = [f for f in report["fixes"] if f["verdict"] == "keep"]
    assert len(draft.commits) == len(kept) == 4
    assert all(c.message.startswith("perf(arm64):") for c in draft.commits)
    assert all("[armsmith]" in c.message for c in draft.commits)


def test_dropped_fixes_get_no_commits(report):
    draft = build_pr_draft(report)
    commit_rules = {c.rule_id for c in draft.commits}
    assert "R11" not in commit_rules and "R8" not in commit_rules


def test_title_and_branch(report):
    draft = build_pr_draft(report, repo_slug="edycu/ragserve")
    assert draft.title.startswith("[armsmith] 4 reproduce-gated Arm optimization")
    assert draft.branch.startswith("armsmith/fixes-")
    assert draft.repo_slug == "edycu/ragserve"


def test_body_is_the_evidence_markdown(report):
    specs = load_pack()
    draft = build_pr_draft(report, specs_by_id=specs)
    assert "| metric | before | after | Δ | noise band | PMU Δ |" in draft.body
    assert "REPLAY MODE" in draft.body


def test_dry_run_render_never_claims_posting(report):
    draft = build_pr_draft(report)
    out = render_dry_run(draft)
    assert "DRY RUN — nothing was sent to GitHub" in out
    assert draft.title in out


def test_no_network_modules_imported():
    import sys

    import armsmith.ghpr  # noqa: F401

    for mod in ("github", "requests", "httpx", "urllib3"):
        assert mod not in sys.modules, f"{mod} must not load in dry-run PR module"
