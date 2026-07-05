"""Evidence renderer: PR-body markdown table + cosign footer + honesty banners."""

import pytest

from armsmith.evidence import cosign_verify_line, render_fix_table, render_markdown
from armsmith.rules import load_pack


@pytest.fixture(scope="module")
def report():
    from tests.conftest import FIXTURES

    from armsmith.diagnose import run_replay_diagnosis

    return run_replay_diagnosis(FIXTURES / "replays" / "scenario_ragserve", sign=False).report


def test_table_header_matches_spec(report):
    kept = [f for f in report["fixes"] if f["verdict"] == "keep"][0]
    table = render_fix_table(kept)
    assert table.splitlines()[0] == "| metric | before | after | Δ | noise band | PMU Δ |"


def test_table_rows_carry_band_and_pmu(report):
    kept = [f for f in report["fixes"] if f["verdict"] == "keep"][0]
    table = render_fix_table(kept)
    assert "outside band" in table
    assert "ipc" in table  # PMU Δ cell


def test_markdown_replay_banner_present(report):
    md = render_markdown(report)
    assert "REPLAY MODE" in md
    assert "NOT hardware results" in md


def test_markdown_dropped_section_reports_reasons(report):
    md = render_markdown(report)
    assert "dropped by the gate" in md
    assert "reported, not hidden" in md
    assert "noise band" in md          # in-band drop reason
    assert "output hash mismatch" in md  # hash-mismatch drop reason


def test_markdown_gate_line_states_refusal_rule(report):
    md = render_markdown(report)
    assert "In-band deltas are reported as **no change**" in md


def test_markdown_citations_from_specs(report):
    specs = load_pack()
    md = render_markdown(report, specs_by_id=specs)
    assert "https://docs.docker.com/build/building/multi-platform/" in md


def test_cosign_footer_line_shape(report):
    md = render_markdown(report, repo_slug="edycu/ragserve")
    line = cosign_verify_line(repo_slug="edycu/ragserve")
    assert line in md
    assert line.startswith("cosign verify-blob report.json --bundle report.sigstore.json")
    assert '--certificate-identity "https://github.com/edycu/ragserve/.github/workflows/ci.yml@refs/heads/main"' in line
    assert '--certificate-oidc-issuer "https://token.actions.githubusercontent.com"' in line


def test_markdown_unsigned_note_when_no_signature(report):
    md = render_markdown(report)
    assert "unsigned" in md.lower()


def test_markdown_host_line(report):
    md = render_markdown(report)
    assert "Neoverse-V2" in md
    assert "synthetic-c8g.4xlarge" in md
