"""armsmith.benchcmd — run the reproduce gate against YOUR workload.

`bench-live` compiles this project's own `bench/int8_dot.c` two ways. That
proves the gate works on real silicon, but it cannot tell you anything about
your service: the one thing the tool is *for* — deciding whether a proposed fix
is real — could not be pointed at a fix you actually made.

This module closes that. Give it two commands, a before and an after, and it
puts them through the identical path: ABAB interleaving so drift lands on both
sides, median-of-N with a scaled-MAD noise band, output-hash equality, and the
same signed report `armsmith verify` re-derives.

What it deliberately does NOT do:

* **No ISA witness.** There is no binary to disassemble, so the report carries
  no SDOT/SMMLA counts rather than zeros that could be mistaken for a
  measurement. `bench-live` keeps that proof; command mode is a stopwatch, and
  says so.
* **No running off Arm.** It refuses on non-`aarch64` hosts exactly as
  `bench-live` does. A wall-clock number from an x86 box is not an Arm result,
  and there is no code path here that would let one be reported as though it
  were.
* **No non-deterministic workloads.** If a command's stdout changes between
  runs, the gate cannot tell a real improvement from a different computation,
  so it refuses to measure rather than compare two different things.
"""

from __future__ import annotations

import platform
import shlex
import subprocess
import time
from dataclasses import dataclass, field

from .benchstats import plan_interleaved
from .gate import MeasurementSet
from .livebench import ToolchainError, checksum_of

__all__ = [
    "METRIC",
    "CommandVariant",
    "CommandArtifact",
    "CommandBenchResult",
    "run_command_bench",
]

#: Wall-clock seconds of the whole command. Named differently from livebench's
#: `kernel_s` on purpose: that one times an instrumented inner loop, this one
#: times a process, and conflating them in a report would be a lie of naming.
METRIC = "wall_s"


@dataclass(frozen=True)
class CommandVariant:
    name: str
    command: str
    rule_id: str | None = None


@dataclass
class CommandArtifact:
    spec: CommandVariant
    samples: list[float] = field(default_factory=list)
    output_sha256: str | None = None

    def to_measurement(self) -> MeasurementSet:
        return MeasurementSet(
            variant=self.spec.name,
            instrument="armsmith-benchcmd/perf_counter(wall clock)",
            metrics={METRIC: list(self.samples)},
            pmu={},
            output_sha256=self.output_sha256,
            rule_id=self.spec.rule_id,
            # Timed on this host, on the operator's own workload. Real.
            synthetic=False,
        )


@dataclass
class CommandBenchResult:
    baseline: CommandArtifact
    candidate: CommandArtifact
    machine: str
    system: str
    rounds: int
    warmup: int
    schedule: list[str]
    cwd: str

    @property
    def outputs_agree(self) -> bool:
        return (
            self.baseline.output_sha256 is not None
            and self.baseline.output_sha256 == self.candidate.output_sha256
        )

    def artifacts_dict(self) -> dict:
        return {
            "flamegraph_before": None,
            "flamegraph_after": None,
            "performix_ref": None,
            "toolchain": {"machine": self.machine, "system": self.system},
            "workload": {
                "source": "operator-supplied commands",
                "cwd": self.cwd,
                "baseline_command": self.baseline.spec.command,
                "candidate_command": self.candidate.spec.command,
                "measured_rounds": self.rounds,
                "warmup_rounds": self.warmup,
                "schedule": self.schedule,
            },
            # Stated, not omitted: a reader must be able to tell that the
            # instruction-level proof is absent here rather than zero.
            "isa_witness": {
                "available": False,
                "note": (
                    "command mode times processes and has no binary to "
                    "disassemble — use `armsmith bench-live` for the "
                    "SDOT/SMMLA instruction witness"
                ),
            },
        }


def _run_once(command: str, cwd: str | None, timeout: int) -> tuple[str, float]:
    """Time one execution, returning (stdout, wall seconds).

    perf_counter brackets the whole subprocess, so process start-up is inside
    the measurement. That is the honest thing to time when the operator asked
    about a command rather than a kernel.
    """
    start = time.perf_counter()
    proc = subprocess.run(
        shlex.split(command),
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        check=False,
    )
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        raise ToolchainError(
            f"command exited {proc.returncode}: {command!r}\n"
            f"{proc.stderr.strip()[:300]}"
        )
    return proc.stdout, elapsed


def run_command_bench(
    baseline_cmd: str,
    candidate_cmd: str,
    *,
    rule_id: str | None = None,
    measured_rounds: int = 7,
    warmup: int = 1,
    timeout: int = 900,
    cwd: str | None = None,
    require_arm: bool = True,
) -> CommandBenchResult:
    """ABAB-measure two commands and return gate-ready measurements."""
    machine, system = platform.machine(), platform.system()
    if require_arm and machine not in ("aarch64", "arm64"):
        raise ToolchainError(
            f"refusing to run on {machine}: a wall-clock number from a non-Arm "
            "host is not an Arm result. Run this on the aarch64 box you are "
            "gating, or pass require_arm=False if you genuinely want a "
            "non-Arm comparison."
        )
    if not baseline_cmd.strip() or not candidate_cmd.strip():
        raise ValueError("both --baseline-cmd and --candidate-cmd are required")

    baseline = CommandArtifact(CommandVariant("baseline", baseline_cmd))
    candidate = CommandArtifact(CommandVariant("candidate", candidate_cmd, rule_id))
    by_name = {a.spec.name: a for a in (baseline, candidate)}

    slots = plan_interleaved(
        [baseline.spec.name, candidate.spec.name], reps=measured_rounds, warmup=warmup
    )
    schedule = [f"{'w' if s.warmup else 'm'}:{s.variant}" for s in slots]

    for slot in slots:
        art = by_name[slot.variant]
        stdout, elapsed = _run_once(art.spec.command, cwd, timeout)
        digest = checksum_of(stdout)
        if art.output_sha256 is None:
            art.output_sha256 = digest
        elif art.output_sha256 != digest:
            raise ToolchainError(
                f"{slot.variant} produced different stdout between runs — the "
                "workload is not deterministic, so the gate cannot tell a real "
                "improvement from a different computation. Refusing to measure it."
            )
        if not slot.warmup:
            art.samples.append(elapsed)

    return CommandBenchResult(
        baseline=baseline,
        candidate=candidate,
        machine=machine,
        system=system,
        rounds=measured_rounds,
        warmup=warmup,
        schedule=schedule,
        cwd=cwd or ".",
    )
