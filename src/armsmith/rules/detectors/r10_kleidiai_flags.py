"""R10 — llama.cpp/ggml built without KleidiAI (probe: cmake_cache).

Flag surface verified against the llama.cpp build docs:
``-DGGML_CPU_KLEIDIAI=ON`` build flag + ``GGML_KLEIDIAI_SME`` env
(unset=auto / 0=disable / >0=force) + macOS ``--device none`` caveat.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register

_CACHE_VAR_RE = re.compile(r"^(?P<name>[A-Za-z0-9_]+):(?P<type>[A-Z]+)=(?P<val>.*)$")


def _parse_cache(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _CACHE_VAR_RE.match(line.strip())
        if m:
            out[m.group("name")] = m.group("val").strip()
    return out


def _is_on(val: str | None) -> bool:
    return (val or "").upper() in {"ON", "1", "TRUE", "YES"}


@register("R10")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert probe is not None
    cache = _parse_cache(probe.text("cmake_cache"))

    ggml_vars = {k: v for k, v in cache.items() if k.startswith("GGML_")}
    if not ggml_vars:
        return clean(spec, ["CMake cache has no GGML_* variables — not a ggml/llama.cpp build"])

    kleidiai = cache.get("GGML_CPU_KLEIDIAI")
    native = cache.get("GGML_NATIVE")

    if _is_on(kleidiai):
        return clean(spec, [f"GGML_CPU_KLEIDIAI={kleidiai} — KleidiAI microkernels enabled"])

    evidence = [
        f"GGML_CPU_KLEIDIAI={'absent from cache' if kleidiai is None else kleidiai} — "
        "KleidiAI dotprod/i8mm/SVE/SME microkernels not built",
    ]
    if native is not None and not _is_on(native):
        evidence.append(f"GGML_NATIVE={native} — host-specific optimization also disabled")

    fix = Fix(
        rule_id=spec.id,
        kind="build_flag",
        description=(
            "Rebuild the ggml/llama.cpp target with KleidiAI microkernels and "
            "A/B both builds through the reproduce gate; on SME-capable hosts "
            "sweep GGML_KLEIDIAI_SME (unset/0/>0) as additional gate variants. "
            "On macOS pin --device none for any CPU claim (Metal outranks CPU)."
        ),
        patch="-DGGML_CPU_KLEIDIAI=ON" + ("" if _is_on(native) else " -DGGML_NATIVE=ON"),
        commands=(
            "cmake -B build -DGGML_CPU_KLEIDIAI=ON && cmake --build build --config Release",
            "GGML_KLEIDIAI_SME=0 llama-bench -m model.gguf -r 7 -o json  # SME sweep variant",
        ),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=tuple(evidence),
        locations=("probe:cmake_cache",),
        fix=fix,
    )
