"""R3 — NumPy linked to reference BLAS (probe: numpy_show_config).

Handles both numpy.show_config() text shapes:
* legacy distutils sections (``openblas_info:`` / ``NOT AVAILABLE`` /
  ``libraries = ['openblas', ...]``);
* meson-era "Build Dependencies:" blocks (``name: openblas64`` etc.).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register

#: positive-context patterns proving an optimized BLAS is actually LINKED.
#: A bare "openblas_info:" section header followed by NOT AVAILABLE must NOT
#: count — only libraries=/name:/configuration lines naming the backend do.
_OPT_NAMES = r"(?:openblas\w*|blis|armpl|arm\s+performance\s+libraries|accelerate|mkl|scipy-openblas|flexiblas)"
_OPTIMIZED_PATTERNS = (
    re.compile(r"libraries\s*=\s*\[[^\]]*'" + _OPT_NAMES + r"'", re.IGNORECASE),
    re.compile(r"^\s*name:\s*\"?" + _OPT_NAMES, re.IGNORECASE | re.MULTILINE),
    re.compile(_OPT_NAMES + r"\s+configuration\s*:", re.IGNORECASE),
    re.compile(r"^\s*" + _OPT_NAMES + r"_info\s*:\s*\n(?!\s+NOT AVAILABLE)\s+libraries", re.IGNORECASE | re.MULTILINE),
)

_REFERENCE_PATTERNS = (
    re.compile(r"libraries\s*=\s*\[\s*'(?:blas|cblas|lapack)'", re.IGNORECASE),
    re.compile(r"\bname:\s*(?:blas|lapack|reference|netlib)\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bnetlib\b", re.IGNORECASE),
)


@register("R3")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert probe is not None
    text = probe.text("numpy_show_config")

    optimized_hits = [
        p.search(text).group(0).strip().splitlines()[0]
        for p in _OPTIMIZED_PATTERNS
        if p.search(text)
    ]
    if optimized_hits:
        return clean(spec, [f"optimized BLAS detected in show_config: {optimized_hits[:2]}"])

    evidence: list[str] = ["no optimized BLAS (OpenBLAS/BLIS/ArmPL/Accelerate/MKL) in numpy.show_config()"]
    for pat in _REFERENCE_PATTERNS:
        m = pat.search(text)
        if m:
            evidence.append(f"reference-BLAS marker: {m.group(0).strip()!r}")
            break
    not_avail = len(re.findall(r"NOT AVAILABLE", text))
    if not_avail:
        evidence.append(f"{not_avail} backend section(s) report NOT AVAILABLE")

    fix = Fix(
        rule_id=spec.id,
        kind="pip_pin",
        description=(
            "Reinstall NumPy from the official PyPI manylinux aarch64 wheel "
            "(bundles OpenBLAS) instead of the distro/source build, and pin it "
            "in the lockfile."
        ),
        patch="numpy>=1.26  # official manylinux_aarch64 wheels bundle OpenBLAS",
        commands=(
            "pip install --force-reinstall --only-binary=:all: numpy",
            "python -c \"import numpy; numpy.show_config()\"  # re-verify backend",
        ),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=tuple(evidence),
        locations=("probe:numpy_show_config",),
        fix=fix,
    )
