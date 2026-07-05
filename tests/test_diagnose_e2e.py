"""Replay-mode end-to-end: scan → plan → gate → signed report → verify."""

import pytest

from armsmith.diagnose import run_replay_diagnosis
from armsmith.report import verify_report


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    from tests.conftest import FIXTURES

    from armsmith.keys import init_keys

    kd = tmp_path_factory.mktemp("e2e-keys")
    init_keys(key_dir=kd)
    return run_replay_diagnosis(FIXTURES / "replays" / "scenario_ragserve", key_dir=kd, sign=True)


def test_scan_statuses_match_scenario_design(result):
    statuses = {f.rule_id: f.status.value for f in result.findings}
    assert statuses == {
        "R1": "matched", "R2": "skipped", "R3": "matched", "R4": "matched",
        "R5": "skipped", "R6": "matched", "R7": "skipped", "R8": "matched",
        "R9": "clean", "R10": "skipped", "R11": "matched", "R12": "matched",
        "R13": "skipped",
    }


def test_plan_orders_matched_rules_only(result):
    planned = result.plan.ordered_rule_ids()
    assert set(planned) == {"R1", "R3", "R4", "R6", "R8", "R11", "R12"}
    assert planned[0] == "R1"  # high confidence + largest estimated midpoint


def test_gate_verdicts_expected(result):
    verdicts = {f["variant"]: f["verdict"] for f in result.report["fixes"]}
    assert verdicts == {
        "fix_R1": "keep", "fix_R3": "keep", "fix_R4": "keep", "fix_R6": "keep",
        "fix_R11": "drop", "fix_R8": "drop",
    }


def test_drop_reasons_are_specific(result):
    by_variant = {f["variant"]: f for f in result.report["fixes"]}
    assert any("noise band" in r for r in by_variant["fix_R11"]["reasons"])
    assert any("output hash mismatch" in r for r in by_variant["fix_R8"]["reasons"])


def test_report_marked_replay_and_synthetic(result):
    assert result.report["mode"] == "replay"
    assert result.report["synthetic"] is True
    assert result.report["cost"]["cost_usd"] == 0.0


def test_report_carries_host_fingerprint(result):
    host = result.report["host"]
    assert host["model_name"] == "Neoverse-V2"
    assert host["source"].startswith("replay[SYNTHETIC]")


def test_report_signed_and_verifies(result):
    assert result.signed
    check = verify_report(result.report)
    assert check.ok, [i.detail for i in check.issues]


def test_unsigned_flow_reports_reason(tmp_path):
    from tests.conftest import FIXTURES

    res = run_replay_diagnosis(
        FIXTURES / "replays" / "scenario_ragserve",
        key_dir=tmp_path / "empty-keys",  # no keypair here
        sign=True,
    )
    assert not res.signed
    assert "keys init" in res.sign_note
    assert "signature" not in res.report


def test_max_fixes_flows_into_plan(result):
    from tests.conftest import FIXTURES

    res = run_replay_diagnosis(FIXTURES / "replays" / "scenario_ragserve", sign=False, max_fixes=2)
    assert len(res.plan.items) == 2


def test_kept_fix_medians_match_synthetic_design(result):
    by_variant = {f["variant"]: f for f in result.report["fixes"]}
    cmp = by_variant["fix_R1"]["comparisons"]["wall_s"]
    assert cmp["baseline"]["median"] == pytest.approx(2.00)
    assert cmp["candidate"]["median"] == pytest.approx(0.70)
    assert cmp["delta_pct"] == pytest.approx(-65.0)
