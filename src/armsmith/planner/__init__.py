"""armsmith.planner — fix planning.

Two implementations behind one protocol:

* :class:`~armsmith.planner.fallback.DeterministicPlanner` — no-LLM fallback,
  fully implemented (rule-priority ordering). Always available; used whenever
  the Claude planner is absent or disabled. The reproduce gate downstream is
  identical either way — planners only ORDER work, they never claim results.
* :class:`~armsmith.planner.claude.ClaudePlanner` — Claude tool-use planner,
  stub in this phase (TODO(S1); no API calls exist in this codebase).
"""

from .fallback import DeterministicPlanner
from .interface import TOOL_SPECS, Plan, PlanItem, PlannerProtocol

__all__ = ["PlanItem", "Plan", "PlannerProtocol", "TOOL_SPECS", "DeterministicPlanner"]
