"""R2 — native build without -mcpu/-march (probe: build_log + lscpu)."""

from __future__ import annotations

import re
from pathlib import Path

from ...fingerprint import capture_fingerprint
from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register

_COMPILER_RE = re.compile(
    r"(?:^|[/\s])(cc|gcc|g\+\+|clang|clang\+\+|aarch64-linux-gnu-gcc(?:-\d+)?|aarch64-linux-gnu-g\+\+)\s"
)
_TARGET_FLAG_RE = re.compile(r"-m(?:cpu|arch)=\S+")
_COMPILE_HINT_RE = re.compile(r"\s-(?:c|o)\s")


@register("R2")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert probe is not None
    log = probe.text("build_log")
    fp = capture_fingerprint(probe)

    compile_lines = [
        ln for ln in log.splitlines()
        if _COMPILER_RE.search(ln) and _COMPILE_HINT_RE.search(f" {ln} ")
    ]
    if not compile_lines:
        return clean(spec, ["build log contains no C/C++ compiler invocations"])

    missing = [ln for ln in compile_lines if not _TARGET_FLAG_RE.search(ln)]
    if not missing:
        return clean(
            spec,
            [f"all {len(compile_lines)} compile lines carry -mcpu=/-march= targeting"],
        )

    feats = fp.isa.present()
    march_ext = "armv8.2-a+dotprod" + ("+i8mm" if fp.isa.i8mm else "")
    sample = missing[0].strip()
    evidence = [
        f"{len(missing)}/{len(compile_lines)} compile lines lack -mcpu=/-march= "
        f"(generic ARMv8.0 codegen)",
        f"sample: {sample[:160]}",
        f"host ISA features present but unused by generic codegen: {feats or 'none reported'}",
    ]
    fix = Fix(
        rule_id=spec.id,
        kind="build_flag",
        description=(
            "Add CPU targeting to the native extension build: -mcpu=native for "
            f"builds on the deploy host, or explicit -march={march_ext} to match "
            "the detected ISA features."
        ),
        patch=(
            'CFLAGS="-O3 -mcpu=native ${CFLAGS}"\n'
            'CXXFLAGS="-O3 -mcpu=native ${CXXFLAGS}"\n'
            f'# cross-compile / pinned-target alternative: -march={march_ext}'
        ),
        commands=("pip install --no-binary :pkg: <pkg>  # rebuild the extension with the new flags",),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=tuple(evidence),
        locations=("probe:build_log",),
        fix=fix,
    )
