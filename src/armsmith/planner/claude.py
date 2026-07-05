"""Claude tool-use planner — STUB. TODO(S1): wire the real tool-use loop.

No Anthropic API calls exist anywhere in this codebase in Phase 1; the
deterministic fallback (:mod:`armsmith.planner.fallback`) carries diagnose
end-to-end.  This module pins the S1 integration contract so wiring is a
drop-in:

* Model: ``claude-sonnet-5`` (frozen spec choice, ARCHITECTURE §6 — tool-use
  reliability for multi-step plan/patch loops; the planner needs
  instruction-following + code-diff quality, not creativity).
* Tools: :data:`armsmith.planner.interface.TOOL_SPECS`, passed verbatim as
  the Messages API ``tools`` parameter; the loop executes tool calls against
  the local scanner/knowledge/patch modules and feeds ``tool_result`` blocks
  back until the model stops calling tools.
* Authority bound: the planner ranks fixes and drafts patches; it cannot
  claim results, bypass the reproduce gate, or touch measurements.
* Budget: remaining USD is surfaced via the ``budget_remaining`` tool — a
  real budgeted-agent input, not prompt garnish.
"""

from __future__ import annotations

from ..rules import Finding, RuleSpec
from .interface import TOOL_SPECS, Plan

__all__ = ["ClaudePlanner", "PLANNER_MODEL"]

#: Frozen-spec model choice for S1 wiring (verified model id).
PLANNER_MODEL = "claude-sonnet-5"


class ClaudePlanner:
    """Stub — raises until S1 lands the tool-use loop."""

    name = "claude"
    tools = TOOL_SPECS

    def __init__(self, api_key: str | None = None):
        # TODO(S1): construct anthropic.Anthropic() here (key from env),
        # temperature/top_p untouched, tools=TOOL_SPECS, and run the manual
        # tool-use loop with human-auditable logging of every tool call.
        self._api_key = api_key

    def plan(
        self,
        findings: list[Finding],
        specs: dict[str, RuleSpec],
        budget_usd: float | None = None,
        max_fixes: int | None = None,
    ) -> Plan:
        raise NotImplementedError(
            "ClaudePlanner lands at S1 (no API calls in the Phase-1 core). "
            "Use armsmith.planner.DeterministicPlanner — same Plan contract, "
            "same gate downstream."
        )
