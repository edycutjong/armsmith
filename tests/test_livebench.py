"""Live Arm reproduce gate — plumbing everywhere, real compile+measure on aarch64.

The pure-logic tests run on every runner. The integration test only runs where a
live Arm measurement is actually possible (aarch64 Linux with a compiler and a
disassembler) and is skipped everywhere else — Armsmith does not pretend to
measure Arm on machines that are not Arm.
"""

from __future__ import annotations

import platform
import shutil

import pytest

from armsmith import livebench
from armsmith.benchstats import Direction, Verdict
from armsmith.gate import METRIC_DIRECTIONS, GateConfig, run_gate

IS_ARM = platform.machine() in ("aarch64", "arm64")
CAN_MEASURE = (
    IS_ARM
    and platform.system() == "Linux"
    and shutil.which("gcc") is not None
    and shutil.which("objdump") is not None
)


# --------------------------------------------------------------------------
# The honesty invariants
# --------------------------------------------------------------------------

def test_refuses_to_measure_arm_on_non_arm(monkeypatch):
    """There must be no code path that produces an Arm number off Arm silicon."""
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    with pytest.raises(livebench.ToolchainError, match="only be taken on Arm"):
        livebench.detect_toolchain(require_arm=True)


def test_live_measurements_are_not_flagged_synthetic():
    art = livebench.BuildArtifact(
        spec=livebench.CANDIDATE,
        binary=None,  # type: ignore[arg-type]
        compile_command=["gcc"],
        disassembly="",
        witness=livebench.count_witness(""),
        samples=[0.1, 0.2, 0.3],
        output_sha256="abc",
    )
    ms = art.to_measurement()
    assert ms.synthetic is False, "live samples must never carry the synthetic flag"
    assert ms.rule_id == "R2"
    assert ms.metrics[livebench.METRIC] == [0.1, 0.2, 0.3]


def test_variants_differ_only_in_the_isa_flag():
    """R2's whole claim is that the source is fine and the flags are not."""
    base = set(livebench.BASELINE.cflags)
    cand = set(livebench.CANDIDATE.cflags)
    assert "-O3" in base and "-O3" in cand, "optimization level must not be the variable"
    assert base ^ cand == {"-march=armv8-a", "-march=armv8.2-a+dotprod"}
    assert not any("dotprod" in f for f in livebench.BASELINE.cflags)


def test_metric_direction_is_registered():
    """The gate refuses metrics with no declared direction — don't regress that."""
    assert METRIC_DIRECTIONS[livebench.METRIC] is Direction.LOWER_BETTER


def test_kernel_source_exists_and_compiles_one_symbol():
    src = livebench.kernel_source().read_text(encoding="utf-8")
    assert f"{livebench.KERNEL_SYMBOL}(" in src
    assert "noinline" in src, "the witness needs a real symbol, not an inlined fragment"
    assert "kernel_s=" in src and "checksum=" in src


# --------------------------------------------------------------------------
# Workload contract parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [("kernel_s=0.123456789\n", 0.123456789), ("noise\nkernel_s=1e-3\n", 0.001)],
)
def test_parse_kernel_seconds(text, expected):
    assert livebench.parse_kernel_seconds(text) == pytest.approx(expected)


def test_parse_kernel_seconds_refuses_missing():
    with pytest.raises(ValueError, match="did not report"):
        livebench.parse_kernel_seconds("segfault")


def test_parse_kernel_seconds_refuses_nonpositive():
    with pytest.raises(ValueError, match="non-positive"):
        livebench.parse_kernel_seconds("kernel_s=0.0")


def test_checksum_is_whitespace_stable():
    assert livebench.checksum_of("checksum=42\n") == livebench.checksum_of("  checksum=42  ")
    assert livebench.checksum_of("checksum=42") != livebench.checksum_of("checksum=43")


# --------------------------------------------------------------------------
# The real thing — only where a real measurement is possible
# --------------------------------------------------------------------------

@pytest.mark.skipif(not CAN_MEASURE, reason="needs aarch64 Linux with gcc + objdump")
def test_live_bench_end_to_end():
    res = livebench.run_live_bench(n=2048, reps=2000, measured_rounds=3, warmup=1)

    # Both builds ran and produced identical behaviour.
    assert len(res.baseline.samples) == 3
    assert len(res.candidate.samples) == 3
    assert res.outputs_agree, "same source must produce the same checksum"

    # The witness actually read instructions out of both binaries.
    assert res.baseline.witness.instructions_scanned > 0
    assert res.candidate.witness.instructions_scanned > 0

    # ARMv8.0 has no dot-product instruction — the baseline cannot contain one.
    assert res.baseline.witness.dotprod == 0

    # The samples must survive the real gate without special-casing.
    outcome = run_gate(
        res.baseline.to_measurement(),
        [res.candidate.to_measurement()],
        GateConfig(primary_metrics=(livebench.METRIC,)),
    )
    cmp = outcome.results[0].comparisons[livebench.METRIC]
    assert cmp.verdict is not Verdict.INSUFFICIENT_SAMPLES
    assert res.artifacts_dict()["isa_witness"]["delta_total"] == res.witness_gain
