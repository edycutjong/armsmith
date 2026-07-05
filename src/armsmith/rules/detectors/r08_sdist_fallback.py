"""R8 — pip sdist fallback for perf-critical packages (probe: pip_install_log)."""

from __future__ import annotations

import re
from pathlib import Path

from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register

#: packages whose aarch64 performance depends on shipping tuned native wheels.
WATCHLIST = frozenset({
    "numpy", "scipy", "pillow", "onnxruntime", "tokenizers", "sentencepiece",
    "opencv-python", "pandas", "torch", "safetensors", "cffi", "lxml",
    "scikit-learn", "pyarrow",
})

_SDIST_RE = re.compile(
    r"Downloading\s+(?P<name>[A-Za-z0-9_.\-]+?)-(?P<ver>[0-9][A-Za-z0-9_.!+]*)"
    r"(?:\.tar\.gz|\.zip)\b"
)
_BUILDING_RE = re.compile(r"Building wheel for (?P<name>[A-Za-z0-9_.\-]+)")


def _canon(name: str) -> str:
    return name.lower().replace("_", "-")


@register("R8")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert probe is not None
    log = probe.text("pip_install_log")

    sdists: dict[str, str] = {}
    for m in _SDIST_RE.finditer(log):
        name = _canon(m.group("name"))
        if name in WATCHLIST:
            sdists[name] = m.group("ver")
    built = {_canon(m.group("name")) for m in _BUILDING_RE.finditer(log)}

    flagged = sorted(sdists)
    if not flagged:
        return clean(spec, ["no watchlist package was installed from an sdist"])

    evidence = []
    for name in flagged:
        corroborated = " (wheel built from source in this log)" if name in built else ""
        evidence.append(
            f"{name}-{sdists[name]} downloaded as sdist — no aarch64 wheel used{corroborated}"
        )
    fix = Fix(
        rule_id=spec.id,
        kind="pip_pin",
        description=(
            "Pin flagged packages to versions that publish manylinux aarch64 "
            "wheels and enforce --only-binary for the watchlist so CI fails "
            "loudly instead of silently compiling untuned builds."
        ),
        patch="\n".join(f"{name}  # pin to a release with manylinux_*_aarch64 wheels" for name in flagged),
        commands=tuple(
            f"pip install --only-binary=:all: {name}" for name in flagged
        ),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=tuple(evidence),
        locations=("probe:pip_install_log",),
        fix=fix,
    )
