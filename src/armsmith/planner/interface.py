"""Planner interface + the tool-use surface the Claude planner will bind to.

The tool definitions below follow the Claude Messages API tool shape
(name / description / input_schema with JSON Schema) so S1 wiring is a
drop-in: they are passed verbatim as the ``tools`` parameter of the
tool-use loop.  Tool NAMES come from BUILD_PLAN Phase 2:
``scan_repo``, ``query_knowledge``, ``query_arm_mcp``, ``propose_patch``,
``budget_remaining``.

The planner's authority is strictly bounded (PRD trust story): it ranks
applicable fixes and drafts patches — it can never claim results, skip the
reproduce gate, or edit measurements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..rules import Finding, RuleSpec

__all__ = ["PlanItem", "Plan", "PlannerProtocol", "TOOL_SPECS"]


@dataclass(frozen=True)
class PlanItem:
    rule_id: str
    rationale: str
    priority: int              # 0 = first

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "rationale": self.rationale, "priority": self.priority}


@dataclass(frozen=True)
class Plan:
    items: tuple[PlanItem, ...]
    planner: str               # "deterministic-fallback" | "claude" (S1)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def ordered_rule_ids(self) -> list[str]:
        return [i.rule_id for i in sorted(self.items, key=lambda x: x.priority)]

    def to_dicts(self) -> list[dict]:
        return [i.to_dict() for i in sorted(self.items, key=lambda x: x.priority)]


class PlannerProtocol(Protocol):
    name: str

    def plan(
        self,
        findings: list[Finding],
        specs: dict[str, RuleSpec],
        budget_usd: float | None = None,
        max_fixes: int | None = None,
    ) -> Plan: ...


#: Claude tool-use tool definitions (Messages API `tools` shape). S1 passes
#: these verbatim; Phase 1 ships them so tests can pin the contract.
TOOL_SPECS: list[dict] = [
    {
        "name": "scan_repo",
        "description": (
            "Run the deterministic 13-rule aarch64 anti-pattern scan against the "
            "target repo + recorded probes. Returns findings with evidence and "
            "citations. Call this first; never guess at findings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "checkout path"},
                "replay_bundle": {"type": "string", "description": "replay bundle dir (offline mode)"},
            },
            "required": ["repo_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "query_knowledge",
        "description": (
            "Search the attributed Arm Learning Paths corpus (CC BY-SA) for flag "
            "semantics, wheel gotchas, and kernel-path lore. Returns cited excerpts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "query_arm_mcp",
        "description": (
            "Query the Arm MCP Server's migration-analysis tools (exact tool names "
            "enumerate at the S1 handshake). Garnish, not load-bearing: the corpus "
            "fallback answers the same questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "propose_patch",
        "description": (
            "Draft a patch for ONE matched rule on the fix branch. Containment "
            "rules apply (<=5 files per fix, no major dependency bumps, no "
            "auto-merge). The patch goes to the reproduce gate; the gate's verdict "
            "is final and cannot be appealed by the planner."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "pattern": "^R\\d+$"},
                "description": {"type": "string"},
                "diff": {"type": "string", "description": "unified diff, <=5 files"},
            },
            "required": ["rule_id", "description", "diff"],
            "additionalProperties": False,
        },
    },
    {
        "name": "budget_remaining",
        "description": (
            "Read the remaining diagnosis budget in USD (--budget guard). The "
            "planner must plan within budget; re-benching a fix costs money."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
