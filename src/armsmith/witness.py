"""armsmith.witness — ISA-witness: instruction-level proof (Tier A #1).

After a fix passes the gate, the hottest symbol is disassembled and the
occurrences of Arm dot-product / int8 matmul instructions are counted:
"before: 0 dotprod instructions in hot path · after: 1,214."  Wall-clock can
be argued with; emitted instructions cannot.

Phase 1 implements the objdump-text parser + before/after comparison against
recorded disassembly fixtures (probe kinds ``objdump_before``/``objdump_after``).
TODO(S1): drive objdump on the target box against the perf-hottest symbol
(upgrade path: ArmDeveloperEcosystem/disassembly-library).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["WITNESS_MNEMONICS", "WitnessCount", "count_witness", "witness_delta"]

#: mnemonics that witness dotprod (sdot/udot) and int8 matmul (smmla/usmmla).
WITNESS_MNEMONICS: tuple[str, ...] = ("sdot", "udot", "smmla", "usmmla")

# objdump -d line: "  4005d4:\t4e809c02 \tsdot\tv2.4s, v0.16b, v0.4b[0]"
_DISASM_RE = re.compile(
    r"^\s*[0-9a-f]+:\s+[0-9a-f]+\s+(?P<mnemonic>[a-z][a-z0-9._]*)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WitnessCount:
    counts: dict[str, int]           # mnemonic -> occurrences
    instructions_scanned: int

    @property
    def dotprod(self) -> int:
        return self.counts.get("sdot", 0) + self.counts.get("udot", 0)

    @property
    def int8_matmul(self) -> int:
        return self.counts.get("smmla", 0) + self.counts.get("usmmla", 0)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def to_dict(self) -> dict:
        return {
            "counts": dict(self.counts),
            "instructions_scanned": self.instructions_scanned,
            "dotprod": self.dotprod,
            "int8_matmul": self.int8_matmul,
            "total": self.total,
        }


def count_witness(objdump_text: str) -> WitnessCount:
    """Count witness mnemonics in objdump/disassembly text."""
    counts = {m: 0 for m in WITNESS_MNEMONICS}
    scanned = 0
    for line in objdump_text.splitlines():
        m = _DISASM_RE.match(line)
        if not m:
            continue
        scanned += 1
        mnemonic = m.group("mnemonic").lower()
        if mnemonic in counts:
            counts[mnemonic] += 1
    return WitnessCount(counts=counts, instructions_scanned=scanned)


def witness_delta(before: WitnessCount, after: WitnessCount) -> list[str]:
    """Human-readable ISA-witness evidence lines for the PR body."""
    lines = [
        f"dotprod (sdot+udot): before {before.dotprod:,} → after {after.dotprod:,}",
        f"int8 matmul (smmla+usmmla): before {before.int8_matmul:,} → after {after.int8_matmul:,}",
    ]
    if after.total > before.total:
        lines.append(
            f"hot path gained {after.total - before.total:,} witness instructions — "
            "the optimized kernel path is emitted, not inferred"
        )
    elif after.total == before.total == 0:
        lines.append("no witness instructions in either build — fix does not touch kernel ISA paths")
    return lines
