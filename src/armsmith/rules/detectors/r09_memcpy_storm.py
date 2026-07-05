"""R9 — tokenizer/preprocess memcpy storm (probe: perf_report).

Parses ``perf report --stdio`` overhead lines and sums the share attributed
to memcpy/memmove/memset symbol families.  Fires at >= 15% combined.

R9 is the pack's only LLM-creative rule: the concrete rewrite is drafted by
the planner (TODO(S1)); the deterministic fallback emits the hot-symbol
evidence and generic zero-copy guidance.  Any drafted patch still faces the
reproduce gate.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register

MEMCPY_THRESHOLD_PCT = 15.0

_OVERHEAD_RE = re.compile(
    r"^\s*(?P<pct>\d+(?:\.\d+)?)%\s+(?P<cmd>\S+)\s+(?P<dso>\S+)\s+\[[.k]\]\s+(?P<sym>.+?)\s*$"
)
_MEM_SYM_RE = re.compile(r"\b(__)?mem(cpy|move|set)\w*", re.IGNORECASE)


@register("R9")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert probe is not None
    text = probe.text("perf_report")

    mem_rows: list[tuple[float, str, str]] = []
    parsed_any = False
    for line in text.splitlines():
        m = _OVERHEAD_RE.match(line)
        if not m:
            continue
        parsed_any = True
        sym = m.group("sym")
        if _MEM_SYM_RE.search(sym):
            mem_rows.append((float(m.group("pct")), sym, m.group("dso")))

    if not parsed_any:
        return clean(spec, ["perf report contained no parseable overhead rows"])

    total = sum(pct for pct, _, _ in mem_rows)
    if total < MEMCPY_THRESHOLD_PCT:
        return clean(
            spec,
            [f"memcpy-family symbols account for {total:.1f}% of cycles (< {MEMCPY_THRESHOLD_PCT:.0f}% threshold)"],
        )

    top = max(mem_rows)
    evidence = [
        f"memcpy-family symbols account for {total:.1f}% of cycles (threshold {MEMCPY_THRESHOLD_PCT:.0f}%)",
        *(f"{pct:.2f}% {sym} [{dso}]" for pct, sym, dso in sorted(mem_rows, reverse=True)[:5]),
    ]
    fix = Fix(
        rule_id=spec.id,
        kind="code_suggestion",
        description=(
            f"Hot copy symbol {top[1]} ({top[0]:.1f}%): draft a batch/zero-copy "
            "rewrite of the calling tokenizer/preprocess path — pre-allocated "
            "buffers, memoryview slices instead of bytes copies, batched "
            "tokenization. Planner drafts the concrete patch (TODO(S1)); the "
            "reproduce gate keeps or drops it like any other fix."
        ),
        patch=None,
        commands=(
            "perf record -g -- <workload>  # capture callers of the hot copy symbol",
            "perf report --stdio --call-graph=graph,0.5,caller",
        ),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=tuple(evidence),
        locations=("probe:perf_report",),
        fix=fix,
    )
