"""R11 — THP/allocator tuning for large-model RSS (probes: thp + proc_maps).

* ``thp.txt`` = contents of /sys/kernel/mm/transparent_hugepage/enabled,
  e.g. ``always [madvise] never`` (brackets mark the active mode).
* ``proc_maps.txt`` = /proc/<pid>/maps snippet; we look for
  libtcmalloc/libjemalloc mappings.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register, skipped

_ACTIVE_RE = re.compile(r"\[(?P<mode>\w+)\]")
_ALLOCATOR_RE = re.compile(r"lib(?:tcmalloc|jemalloc)[^\s]*\.so[^\s]*")


@register("R11")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert probe is not None
    thp_text = probe.text("thp").strip()
    maps_text = probe.text("proc_maps")

    m = _ACTIVE_RE.search(thp_text)
    if not m:
        return skipped(spec, f"could not parse THP mode from {thp_text!r}")
    thp_mode = m.group("mode")

    allocators = sorted(set(_ALLOCATOR_RE.findall(maps_text)))

    problems: list[str] = []
    if thp_mode == "never":
        problems.append(
            f"transparent hugepages disabled (enabled={thp_text!r}) — multi-GB model "
            "heaps pay 4K-page TLB pressure"
        )
    if not allocators:
        problems.append(
            "no tcmalloc/jemalloc mapped — glibc malloc fragmentation tax on "
            "long-lived multi-GB heaps"
        )

    if not problems:
        return clean(
            spec,
            [f"THP mode = {thp_mode}; allocator(s) mapped: {allocators}"],
        )

    fix = Fix(
        rule_id=spec.id,
        kind="env_change",
        description=(
            "Environment-level tuning, A/B-able with zero code change: set THP "
            "to madvise and/or preload a modern allocator for the serving "
            "process."
        ),
        patch=(
            "# THP (needs root; persist via tuned/sysfs unit)\n"
            "echo madvise > /sys/kernel/mm/transparent_hugepage/enabled\n"
            "# allocator preload for the serving process\n"
            "LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libjemalloc.so.2"
        ),
        commands=(),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=tuple(problems + [f"THP raw: {thp_text!r}", f"allocators in maps: {allocators or 'none'}"]),
        locations=("probe:thp", "probe:proc_maps"),
        fix=fix,
    )
