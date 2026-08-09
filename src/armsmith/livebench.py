"""armsmith.livebench — the LIVE reproduce gate on real Arm silicon.

Everything else in this package proves the loop is *honest*; this module is
where it stops being a replay.  It compiles ``bench/int8_dot.c`` twice from one
source, differing only in the ``-march`` flag that rule **R2** exists to flag,
then measures both builds on the machine it is running on:

1. **host fingerprint** — real ``lscpu``, so the ISA feature table is this CPU's;
2. **ISA witness** — real ``objdump`` of the ``dot_i8`` symbol in both binaries,
   counting SDOT/UDOT/SMMLA/USMMLA.  A wall-clock delta is arguable; an
   instruction that is present in one build and absent in the other is not;
3. **ABAB-interleaved timing** — the schedule comes from
   :func:`armsmith.benchstats.plan_interleaved`, so thermal drift and noisy
   neighbours hit both variants equally;
4. **output-hash equality** — both builds print a checksum, and the gate drops
   the fix if they disagree.  Faster-but-different is not a fix.

The samples this produces are fed to the ordinary :mod:`armsmith.gate`, so the
live path earns a verdict under exactly the same refuse-to-claim-inside-the-noise-band
rule as every replay bundle.  A run that fails to beat its own noise band is
reported as ``no_change`` and DROPPED — that outcome is a success for the tool
and is never massaged into a win.

Honesty invariants enforced here:

* refuses to run unless the host really is ``aarch64`` — there is no code path
  that produces an Arm number on a non-Arm machine;
* records the exact compiler, compile commands, and toolchain versions into the
  report, so a judge can re-run them by hand;
* if the candidate build contains no witness instructions, that is *reported*,
  not hidden — the premise simply did not hold on that toolchain.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .benchstats import plan_interleaved
from .gate import MeasurementSet
from .witness import WitnessCount, count_witness

__all__ = [
    "KERNEL_SYMBOL",
    "BenchCase",
    "CASES",
    "DEFAULT_CASE",
    "BASELINE",
    "CANDIDATE",
    "VariantSpec",
    "ToolchainError",
    "Toolchain",
    "detect_toolchain",
    "kernel_source",
    "parse_kernel_seconds",
    "checksum_of",
    "BuildArtifact",
    "LiveBenchResult",
    "run_live_bench",
]

#: The function the ISA witness disassembles. ``noinline`` in the C source keeps
#: it a real symbol instead of an inlined fragment we would have to guess at.
KERNEL_SYMBOL = "dot_i8"

#: Metric emitted by this harness (registered in gate.METRIC_DIRECTIONS).
METRIC = "kernel_s"


@dataclass(frozen=True)
class VariantSpec:
    """One build of the kernel: a name and the flags that define it."""

    name: str
    cflags: tuple[str, ...]
    rule_id: str | None
    summary: str


#: ARMv8.0 baseline — the dot-product extension is simply not in this ISA, so no
#: compiler can emit SDOT here however hard it tries. This is the "generic
#: container image" build that R2 detects in the wild.
BASELINE = VariantSpec(
    name="baseline",
    cflags=("-O3", "-march=armv8-a"),
    rule_id=None,
    summary="generic ARMv8.0 build — the dot-product unit is off the table",
)

#: What R2 tells you to turn on. Same source, same -O3, one extra ISA level.
CANDIDATE = VariantSpec(
    name="fix_R2",
    cflags=("-O3", "-march=armv8.2-a+dotprod"),
    rule_id="R2",
    summary="ARMv8.2 + dotprod — lets the vectorizer emit SDOT",
)


@dataclass(frozen=True)
class BenchCase:
    """One live A/B: a source, the symbol to disassemble, and the two builds.

    Two cases exist so the live leg is a harness rather than one lucky
    microbenchmark — a different ISA extension, a different instruction, the
    same gate.
    """

    key: str
    source: str
    symbol: str
    baseline: VariantSpec
    candidate: VariantSpec
    rule_id: str
    scenario: str
    headline: str


#: SDOT via the compiler's own vectorizer — plain C, no intrinsics.
CASE_DOT = BenchCase(
    key="dot",
    source="int8_dot.c",
    symbol="dot_i8",
    baseline=BASELINE,
    candidate=CANDIDATE,
    rule_id="R2",
    scenario="live-int8-dot-r2",
    headline="int8 dot product — +dotprod lets GCC emit SDOT",
)

#: SMMLA via ACLE intrinsics behind the feature macro the flag defines. GCC does
#: not reliably auto-vectorize this shape, and the source says so: the flag
#: gates the code path, which is how KleidiAI and llama.cpp actually ship
#: per-capability kernels. Both paths compute identical arithmetic, so a
#: checksum difference would (correctly) drop the fix on output inequality.
CASE_MMLA = BenchCase(
    key="mmla",
    source="int8_mmla.c",
    symbol="mmla_i8",
    baseline=VariantSpec(
        name="baseline",
        cflags=("-O3", "-march=armv8.2-a"),
        rule_id=None,
        summary="ARMv8.2 without i8mm — the int8 matmul unit is off the table",
    ),
    candidate=VariantSpec(
        name="fix_i8mm",
        cflags=("-O3", "-march=armv8.2-a+i8mm"),
        rule_id="R2",
        summary="ARMv8.2 + i8mm — enables the SMMLA matmul path",
    ),
    rule_id="R2",
    scenario="live-int8-mmla-i8mm",
    headline="int8 2x8*8x2 matmul — +i8mm enables SMMLA",
)

CASES = {c.key: c for c in (CASE_DOT, CASE_MMLA)}
DEFAULT_CASE = CASE_DOT


class ToolchainError(RuntimeError):
    """The host cannot honestly run a live Arm benchmark."""


@dataclass(frozen=True)
class Toolchain:
    cc: str
    cc_version: str
    objdump: str
    objdump_version: str
    machine: str
    system: str
    kernel: str

    def to_dict(self) -> dict:
        return {
            "cc": self.cc,
            "cc_version": self.cc_version,
            "objdump": self.objdump,
            "objdump_version": self.objdump_version,
            "machine": self.machine,
            "system": self.system,
            "kernel": self.kernel,
        }


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return "unknown"


def _tool_version(exe: str) -> str:
    try:
        proc = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
        return _first_line(proc.stdout or proc.stderr)
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def detect_toolchain(require_arm: bool = True) -> Toolchain:
    """Locate cc + objdump and refuse dishonest hosts.

    ``require_arm=False`` exists for unit tests of the plumbing on x86 CI legs;
    nothing that writes a report ever passes it.
    """
    machine = platform.machine()
    system = platform.system()
    if require_arm and machine not in ("aarch64", "arm64"):
        raise ToolchainError(
            f"live bench refuses to run on {machine!r}: an Arm measurement can "
            "only be taken on Arm silicon. Use `armsmith diagnose --replay` for "
            "the hardware-free loop."
        )

    cc = os.environ.get("CC") or shutil.which("gcc") or shutil.which("cc") or shutil.which("clang")
    if not cc:
        raise ToolchainError("no C compiler found (looked for $CC, gcc, cc, clang)")
    objdump = (
        os.environ.get("OBJDUMP")
        or shutil.which("objdump")
        or shutil.which("llvm-objdump")
        or shutil.which("gobjdump")
    )
    if not objdump:
        raise ToolchainError(
            "no objdump found — the ISA witness needs a disassembler "
            "(looked for $OBJDUMP, objdump, llvm-objdump, gobjdump)"
        )
    return Toolchain(
        cc=cc,
        cc_version=_tool_version(cc),
        objdump=objdump,
        objdump_version=_tool_version(objdump),
        machine=machine,
        system=system,
        kernel=platform.release(),
    )


def kernel_source(filename: str = "int8_dot.c") -> Path:
    """Absolute path to a bench/ kernel source, wherever the package lives."""
    # src/armsmith/livebench.py -> src/armsmith -> src -> repo root
    candidates = [
        Path(__file__).resolve().parents[2] / "bench" / filename,
        Path.cwd() / "bench" / filename,
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise ToolchainError(
        f"bench/{filename} not found — the live bench needs its kernel source "
        f"(looked in {[str(c) for c in candidates]})"
    )


_KERNEL_S_RE = re.compile(r"kernel_s=([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)")


def parse_kernel_seconds(stderr_text: str) -> float:
    """Pull the in-process kernel timing out of the workload's stderr."""
    m = _KERNEL_S_RE.search(stderr_text)
    if not m:
        raise ValueError(
            f"workload did not report kernel_s= on stderr (got {stderr_text!r:.200})"
        )
    value = float(m.group(1))
    if value <= 0.0:
        raise ValueError(f"workload reported non-positive kernel_s={value!r}")
    return value


def checksum_of(stdout_text: str) -> str:
    """SHA-256 of the workload's stdout — the gate's behaviour-equality anchor."""
    return hashlib.sha256(stdout_text.strip().encode("utf-8")).hexdigest()


@dataclass
class BuildArtifact:
    spec: VariantSpec
    binary: Path
    compile_command: list[str]
    disassembly: str
    witness: WitnessCount
    samples: list[float] = field(default_factory=list)
    output_sha256: str | None = None

    def to_measurement(self) -> MeasurementSet:
        return MeasurementSet(
            variant=self.spec.name,
            instrument="armsmith-livebench/clock_gettime(CLOCK_MONOTONIC)",
            metrics={METRIC: list(self.samples)},
            pmu={},
            output_sha256=self.output_sha256,
            rule_id=self.spec.rule_id,
            # The load-bearing line in this file: these numbers came off real
            # silicon, so they are NOT flagged synthetic.
            synthetic=False,
        )


def _compile(tc: Toolchain, spec: VariantSpec, src: Path, out_dir: Path) -> tuple[Path, list[str]]:
    binary = out_dir / spec.name
    cmd = [tc.cc, *spec.cflags, "-o", str(binary), str(src)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if proc.returncode != 0:
        raise ToolchainError(
            f"compiling {spec.name} failed ({' '.join(cmd)}):\n{proc.stderr.strip()}"
        )
    return binary, cmd


def _disassemble(tc: Toolchain, binary: Path, symbol: str) -> str:
    """Disassemble one symbol, tolerating GNU vs LLVM objdump spelling."""
    attempts = [
        [tc.objdump, "-d", f"--disassemble={symbol}", str(binary)],
        [tc.objdump, "-d", f"--disassemble-symbols={symbol}", str(binary)],
        [tc.objdump, "-d", f"--disassemble-symbols=_{symbol}", str(binary)],
    ]
    for cmd in attempts:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        # A wrong flag spelling exits non-zero; a right one with no match exits 0
        # but prints no instruction lines. Require actual disassembly.
        if proc.returncode == 0 and count_witness(proc.stdout).instructions_scanned > 0:
            return proc.stdout
    raise ToolchainError(
        f"objdump produced no disassembly for symbol {symbol!r} in {binary.name} — "
        "cannot witness instructions that were never read"
    )


def _run_once(binary: Path, n: int, reps: int) -> tuple[str, float]:
    proc = subprocess.run(
        [str(binary), str(n), str(reps)],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if proc.returncode != 0:
        raise ToolchainError(
            f"{binary.name} exited {proc.returncode}: {proc.stderr.strip()[:300]}"
        )
    return proc.stdout, parse_kernel_seconds(proc.stderr)


@dataclass
class LiveBenchResult:
    toolchain: Toolchain
    case: BenchCase
    lscpu: str
    baseline: BuildArtifact
    candidate: BuildArtifact
    n: int
    reps: int
    warmup: int
    schedule: list[str]

    @property
    def witness_gain(self) -> int:
        return self.candidate.witness.total - self.baseline.witness.total

    @property
    def outputs_agree(self) -> bool:
        return (
            self.baseline.output_sha256 is not None
            and self.baseline.output_sha256 == self.candidate.output_sha256
        )

    def artifacts_dict(self) -> dict:
        """The reproducibility block embedded in the report."""
        return {
            "flamegraph_before": None,
            "flamegraph_after": None,
            "performix_ref": None,
            "toolchain": self.toolchain.to_dict(),
            "workload": {
                "source": f"bench/{self.case.source}",
                "symbol": self.case.symbol,
                "case": self.case.key,
                "n": self.n,
                "reps": self.reps,
                "warmup_rounds": self.warmup,
                "schedule": self.schedule,
            },
            "compile_commands": {
                self.baseline.spec.name: self.baseline.compile_command,
                self.candidate.spec.name: self.candidate.compile_command,
            },
            "isa_witness": {
                self.baseline.spec.name: self.baseline.witness.to_dict(),
                self.candidate.spec.name: self.candidate.witness.to_dict(),
                "delta_total": self.witness_gain,
                "note": (
                    "SDOT/UDOT/SMMLA/USMMLA counted in the disassembly of "
                    f"{self.case.symbol} in each binary"
                ),
            },
        }


def run_live_bench(
    n: int = 8192,
    reps: int = 50000,
    measured_rounds: int = 7,
    warmup: int = 2,
    require_arm: bool = True,
    src: Path | None = None,
    case: BenchCase | None = None,
) -> LiveBenchResult:
    """Compile, witness, and ABAB-measure both builds on this machine."""
    case = case or DEFAULT_CASE
    tc = detect_toolchain(require_arm=require_arm)
    source = Path(src) if src else kernel_source(case.source)

    try:
        lscpu = subprocess.run(
            ["lscpu"], capture_output=True, text=True, timeout=60, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        lscpu = ""

    with tempfile.TemporaryDirectory(prefix="armsmith-livebench-") as tmp:
        out_dir = Path(tmp)
        artifacts: dict[str, BuildArtifact] = {}
        for spec in (case.baseline, case.candidate):
            binary, cmd = _compile(tc, spec, source, out_dir)
            disasm = _disassemble(tc, binary, case.symbol)
            artifacts[spec.name] = BuildArtifact(
                spec=spec,
                binary=binary,
                compile_command=cmd,
                disassembly=disasm,
                witness=count_witness(disasm),
            )

        # ABAB interleave: the same planner the replay path documents, so slow
        # drift lands on both variants instead of on whichever ran second.
        slots = plan_interleaved(
            [case.baseline.name, case.candidate.name], reps=measured_rounds, warmup=warmup
        )
        schedule = [f"{'w' if s.warmup else 'm'}:{s.variant}" for s in slots]
        for slot in slots:
            art = artifacts[slot.variant]
            stdout, kernel_s = _run_once(art.binary, n, reps)
            if slot.warmup:
                continue
            art.samples.append(kernel_s)
            digest = checksum_of(stdout)
            if art.output_sha256 is None:
                art.output_sha256 = digest
            elif art.output_sha256 != digest:
                raise ToolchainError(
                    f"{slot.variant} produced a different checksum between runs "
                    "— the workload is not deterministic; refusing to measure it"
                )

        return LiveBenchResult(
            toolchain=tc,
            case=case,
            lscpu=lscpu,
            baseline=artifacts[case.baseline.name],
            candidate=artifacts[case.candidate.name],
            n=n,
            reps=reps,
            warmup=warmup,
            schedule=schedule,
        )
