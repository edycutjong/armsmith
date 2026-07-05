"""Deterministic fallback planner + Claude tool-use interface contract."""

import pytest

from armsmith.planner import TOOL_SPECS, DeterministicPlanner
from armsmith.planner.claude import PLANNER_MODEL, ClaudePlanner
from armsmith.rules import Finding, FindingStatus, load_pack


def finding(rid, status=FindingStatus.MATCHED):
    return Finding(rule_id=rid, status=status)


@pytest.fixture(scope="module")
def specs():
    return load_pack()


def test_orders_by_confidence_then_gain_then_id(specs):
    # R3 high/3.25x mid > R1 high/6.0x mid — R1 first (higher midpoint);
    # R2 medium sorts after all high rules regardless of gain.
    plan = DeterministicPlanner().plan(
        [finding("R2"), finding("R3"), finding("R1")], specs
    )
    assert plan.ordered_rule_ids() == ["R1", "R3", "R2"]


def test_rule_id_final_tiebreak_is_numeric(specs):
    # R6 and R1: both high confidence; midpoints R1=6.0, R6=2.1 → R1 first.
    # R3 (3.25) vs R6 (2.1): R3 before R6. Deterministic across runs.
    plan_a = DeterministicPlanner().plan([finding("R6"), finding("R3"), finding("R1")], specs)
    plan_b = DeterministicPlanner().plan([finding("R1"), finding("R6"), finding("R3")], specs)
    assert plan_a.ordered_rule_ids() == plan_b.ordered_rule_ids() == ["R1", "R3", "R6"]


def test_only_matched_findings_planned(specs):
    plan = DeterministicPlanner().plan(
        [finding("R1"), finding("R3", FindingStatus.CLEAN), finding("R5", FindingStatus.SKIPPED)],
        specs,
    )
    assert plan.ordered_rule_ids() == ["R1"]


def test_max_fixes_cap_records_deferral(specs):
    plan = DeterministicPlanner().plan(
        [finding("R1"), finding("R3"), finding("R6")], specs, max_fixes=2
    )
    assert len(plan.items) == 2
    assert any("max_fixes=2" in n for n in plan.notes)


def test_zero_budget_plans_nothing(specs):
    plan = DeterministicPlanner().plan([finding("R1")], specs, budget_usd=0.0)
    assert plan.items == ()
    assert any("budget exhausted" in n for n in plan.notes)


def test_rationale_labels_gain_as_estimate(specs):
    plan = DeterministicPlanner().plan([finding("R1")], specs)
    assert "estimate for ordering only" in plan.items[0].rationale
    assert "gate decides" in plan.items[0].rationale


def test_priorities_sequential_from_zero(specs):
    plan = DeterministicPlanner().plan([finding("R1"), finding("R3")], specs)
    assert [i.priority for i in plan.items] == [0, 1]


def test_tool_specs_pin_the_s1_contract():
    names = [t["name"] for t in TOOL_SPECS]
    assert names == ["scan_repo", "query_knowledge", "query_arm_mcp", "propose_patch", "budget_remaining"]
    for tool in TOOL_SPECS:
        assert tool["description"]
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_propose_patch_tool_states_containment():
    tool = next(t for t in TOOL_SPECS if t["name"] == "propose_patch")
    assert "reproduce gate" in tool["description"]
    assert "<=5 files" in tool["description"] or "≤5" in tool["description"]


def test_claude_planner_is_a_stub_no_api(specs):
    planner = ClaudePlanner()
    assert planner.tools is TOOL_SPECS
    assert PLANNER_MODEL == "claude-sonnet-5"
    with pytest.raises(NotImplementedError, match="S1"):
        planner.plan([finding("R1")], specs)


def test_no_anthropic_import_in_phase1():
    """House rule: zero API-call code paths in the Phase-1 core."""
    import sys

    assert "anthropic" not in sys.modules
    import armsmith.planner.claude  # noqa: F401 — importing the stub stays offline

    assert "anthropic" not in sys.modules
