"""Deterministic no-LLM fallback planner.

Ordering is a documented, stable total order over MATCHED findings:

1. rule confidence (high → medium → low);
2. expected-gain-range midpoint, descending (an ESTIMATE used only for
   ordering — never reported as a result);
3. rule id, ascending numeric (R1 before R2 …) — the final tie-break makes
   the plan fully deterministic.

Budget/max_fixes semantics: in replay mode diagnosis is free, but the cap
logic is real — ``max_fixes`` truncates the plan after ordering, and a
``budget_usd`` of 0 plans nothing (the budgeted-agent mechanic the Claude
planner will consume as a tool input at S1).
"""

from __future__ import annotations

from ..rules import Finding, RuleSpec
from ..rules.base import CONFIDENCE_RANK
from .interface import Plan, PlanItem

__all__ = ["DeterministicPlanner"]


def _rule_number(rule_id: str) -> int:
    digits = "".join(ch for ch in rule_id if ch.isdigit())
    return int(digits) if digits else 1_000_000


class DeterministicPlanner:
    name = "deterministic-fallback"

    def plan(
        self,
        findings: list[Finding],
        specs: dict[str, RuleSpec],
        budget_usd: float | None = None,
        max_fixes: int | None = None,
    ) -> Plan:
        notes: list[str] = []
        matched = [f for f in findings if f.matched and f.rule_id in specs]

        def sort_key(f: Finding):
            spec = specs[f.rule_id]
            return (
                CONFIDENCE_RANK.get(spec.confidence, 99),
                -spec.gain_midpoint(),
                _rule_number(f.rule_id),
            )

        ordered = sorted(matched, key=sort_key)

        if budget_usd is not None and budget_usd <= 0:
            notes.append("budget exhausted (<= $0) — planning no fixes")
            ordered = []
        if max_fixes is not None and len(ordered) > max_fixes:
            dropped = [f.rule_id for f in ordered[max_fixes:]]
            notes.append(f"max_fixes={max_fixes} cap — deferred {dropped}")
            ordered = ordered[:max_fixes]

        items = tuple(
            PlanItem(
                rule_id=f.rule_id,
                priority=i,
                rationale=(
                    f"confidence={specs[f.rule_id].confidence}, "
                    f"expected-gain midpoint {specs[f.rule_id].gain_midpoint():.2f}x "
                    "(estimate for ordering only; the reproduce gate decides)"
                ),
            )
            for i, f in enumerate(ordered)
        )
        return Plan(items=items, planner=self.name, notes=tuple(notes))
