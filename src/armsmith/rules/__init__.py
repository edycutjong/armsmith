"""armsmith.rules — YAML rule pack loader + detector registry.

``load_pack()`` reads every ``packs/*.yaml`` descriptor, validates required
fields, imports the detector modules (which self-register), and verifies the
pack and the registry agree 1:1.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import yaml

from .base import (
    CONFIDENCE_RANK,
    DETECTORS,
    Finding,
    FindingStatus,
    Fix,
    RuleSpec,
    run_rule,
)

__all__ = [
    "load_pack",
    "run_all",
    "RuleSpec",
    "Fix",
    "Finding",
    "FindingStatus",
    "DETECTORS",
    "run_rule",
    "PACK_DIR",
]

PACK_DIR = Path(__file__).parent / "packs"

_REQUIRED_FIELDS = (
    "id",
    "title",
    "kind",
    "summary",
    "fix_generator",
    "expected_gain_range",
    "gain_note",
    "citation_url",
    "confidence",
)

_VALID_KINDS = {"static", "probe", "hybrid"}


def _parse_descriptor(path: Path) -> RuleSpec:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: descriptor must be a mapping")
    missing = [f for f in _REQUIRED_FIELDS if f not in data]
    if missing:
        raise ValueError(f"{path}: missing required fields {missing}")
    if data["kind"] not in _VALID_KINDS:
        raise ValueError(f"{path}: kind must be one of {sorted(_VALID_KINDS)}")
    if data["confidence"] not in CONFIDENCE_RANK:
        raise ValueError(f"{path}: confidence must be one of {list(CONFIDENCE_RANK)}")
    gain = data["expected_gain_range"]
    if (
        not isinstance(gain, (list, tuple))
        or len(gain) != 2
        or not all(isinstance(g, (int, float)) for g in gain)
        or not (0 < float(gain[0]) <= float(gain[1]))
    ):
        raise ValueError(f"{path}: expected_gain_range must be [lo, hi] with 0 < lo <= hi")
    url = str(data["citation_url"])
    if not url.startswith("https://"):
        raise ValueError(f"{path}: citation_url must be https")
    lp = data.get("learning_path")
    if lp is not None:
        lp = str(lp)
        if not lp.startswith("https://"):
            raise ValueError(f"{path}: learning_path must be https when present")
    return RuleSpec(
        id=str(data["id"]),
        title=str(data["title"]).strip(),
        kind=str(data["kind"]),
        requires=tuple(data.get("requires") or ()),
        summary=str(data["summary"]).strip(),
        fix_generator=str(data["fix_generator"]).strip(),
        expected_gain_range=(float(gain[0]), float(gain[1])),
        gain_note=str(data["gain_note"]).strip(),
        citation_url=url,
        learning_path=lp,
        confidence=str(data["confidence"]),
    )


def _import_detectors() -> None:
    importlib.import_module(".detectors", __package__)


def load_pack(pack_dir: Path | None = None, require_detectors: bool = True) -> dict[str, RuleSpec]:
    """Load the rule pack, keyed and ordered by rule id (R1..R13)."""
    pack_dir = Path(pack_dir) if pack_dir else PACK_DIR
    specs: dict[str, RuleSpec] = {}
    for path in sorted(pack_dir.glob("*.yaml")):
        spec = _parse_descriptor(path)
        if spec.id in specs:
            raise ValueError(f"duplicate rule id {spec.id} in pack {pack_dir}")
        specs[spec.id] = spec

    def _rule_sort_key(rid: str):
        digits = "".join(ch for ch in rid if ch.isdigit())
        return (int(digits) if digits else 0, rid)

    specs = dict(sorted(specs.items(), key=lambda kv: _rule_sort_key(kv[0])))

    if require_detectors:
        _import_detectors()
        undetected = [rid for rid in specs if rid not in DETECTORS]
        if undetected:
            raise ValueError(f"rules without detectors: {undetected}")
        unspecced = [rid for rid in DETECTORS if rid not in specs]
        if unspecced:
            raise ValueError(f"detectors without descriptors: {unspecced}")
    return specs


def run_all(
    specs: dict[str, RuleSpec],
    repo: Path | None,
    probe,
) -> list[Finding]:
    """Run every rule; returns findings in pack order (matched or not)."""
    return [run_rule(spec, repo, probe) for spec in specs.values()]
