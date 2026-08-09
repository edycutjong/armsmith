"""Hardware-free coverage of the LIVE Arm reproduce gate.

``armsmith.livebench`` only takes a real measurement on aarch64 with gcc +
objdump, so on any other host most of its logic never executes.  These tests
drive the same logic by faking the three boundaries it talks to — ``platform``,
``shutil.which``/``os.environ`` and ``subprocess.run`` — and then assert on what
the module *decides*: which command lines it builds, which hosts it refuses,
which disassembler spellings it retries, which runs it counts as samples, and
whether the resulting samples earn ``keep`` or ``drop`` from the real gate.

Nothing here fabricates an Arm number: every measurement in this file is
explicitly a fake fed to the decision logic, and the tests assert the decision.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

import pytest

from armsmith import livebench
from armsmith.benchstats import Verdict
from armsmith.gate import GateConfig, run_gate
from armsmith.witness import count_witness

# --------------------------------------------------------------------------
# Fakes for the three boundaries livebench touches
# --------------------------------------------------------------------------

FAKE_CC = "/usr/bin/fake-gcc"
FAKE_OBJDUMP = "/usr/bin/fake-objdump"

LSCPU_TEXT = "Architecture:  aarch64\nFlags:  asimddp\n"

#: An ARMv8.0 build: real instructions, but no dot-product mnemonic exists.
BASELINE_DISASM = (
    "0000000000000740 <dot_i8>:\n"
    "     740:\t4e20a400 \tmla\tv0.4s, v0.4h, v1.4h\n"
    "     744:\t91000400 \tadd\tx0, x0, #0x1\n"
    "     748:\td65f03c0 \tret\n"
)

#: The armv8.2+dotprod build: same symbol, two SDOTs that the baseline cannot have.
CANDIDATE_DISASM = (
    "0000000000000740 <dot_i8>:\n"
    "     740:\t4e809c02 \tsdot\tv2.4s, v0.16b, v1.16b\n"
    "     744:\t4e819c23 \tsdot\tv3.4s, v1.16b, v1.16b\n"
    "     748:\t91000400 \tadd\tx0, x0, #0x1\n"
    "     74c:\td65f03c0 \tret\n"
)


class FakeProc:
    """Stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeHost:
    """A scripted aarch64 host: compiler, disassembler, lscpu and two binaries.

    ``runs[variant]`` is the ordered list of ``(stdout, kernel_s)`` results the
    corresponding binary yields, one per invocation (warmups included).
    """

    def __init__(
        self,
        runs: dict[str, list[tuple[str, float]]],
        *,
        lscpu_raises: BaseException | None = None,
        compile_fails: str | None = None,
    ):
        self.runs = {k: list(v) for k, v in runs.items()}
        self.lscpu_raises = lscpu_raises
        self.compile_fails = compile_fails
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        exe = cmd[0]
        name = Path(exe).name

        if len(cmd) == 2 and cmd[1] == "--version":
            return FakeProc(stdout=f"\n  {name} (Fake Toolchain) 13.2.0\n")

        if name == "lscpu":
            if self.lscpu_raises is not None:
                raise self.lscpu_raises
            return FakeProc(stdout=LSCPU_TEXT)

        if exe == FAKE_CC:
            out = Path(cmd[cmd.index("-o") + 1])
            if self.compile_fails and out.name == self.compile_fails:
                return FakeProc(returncode=1, stderr="  error: unknown -march value\n")
            out.write_text("fake-elf", encoding="utf-8")
            return FakeProc()

        if exe == FAKE_OBJDUMP:
            target = Path(cmd[-1]).name
            disasm = CANDIDATE_DISASM if target == livebench.CANDIDATE.name else BASELINE_DISASM
            return FakeProc(stdout=disasm)

        if name in self.runs:
            if not self.runs[name]:
                raise AssertionError(f"{name} was executed more times than scripted")
            stdout, kernel_s = self.runs[name].pop(0)
            return FakeProc(stdout=stdout, stderr=f"kernel_s={kernel_s!r}\n")

        raise AssertionError(f"unexpected command: {cmd}")

    def commands_for(self, exe: str) -> list[list[str]]:
        return [c for c in self.calls if c[0] == exe]

    def binary_runs(self, variant: str) -> list[list[str]]:
        return [c for c in self.calls if Path(c[0]).name == variant]


@pytest.fixture()
def arm_host(monkeypatch):
    """Make this x86 Mac look like an aarch64 Linux box with a toolchain."""
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "release", lambda: "6.5.0-fake")
    monkeypatch.setenv("CC", FAKE_CC)
    monkeypatch.setenv("OBJDUMP", FAKE_OBJDUMP)


@pytest.fixture()
def toolchain() -> livebench.Toolchain:
    return livebench.Toolchain(
        cc=FAKE_CC,
        cc_version="fake-gcc (Fake Toolchain) 13.2.0",
        objdump=FAKE_OBJDUMP,
        objdump_version="fake-objdump (Fake Binutils) 2.42",
        machine="aarch64",
        system="Linux",
        kernel="6.5.0-fake",
    )


def _artifact(spec, disasm: str, samples, sha: str | None) -> livebench.BuildArtifact:
    return livebench.BuildArtifact(
        spec=spec,
        binary=Path("/tmp") / spec.name,
        compile_command=["fake-gcc", *spec.cflags],
        disassembly=disasm,
        witness=count_witness(disasm),
        samples=list(samples),
        output_sha256=sha,
    )


# --------------------------------------------------------------------------
# Toolchain record
# --------------------------------------------------------------------------

def test_toolchain_to_dict_carries_every_reproduce_field(toolchain):
    """A judge re-runs from this dict — nothing about the host may be dropped."""
    d = toolchain.to_dict()
    assert d == {
        "cc": FAKE_CC,
        "cc_version": "fake-gcc (Fake Toolchain) 13.2.0",
        "objdump": FAKE_OBJDUMP,
        "objdump_version": "fake-objdump (Fake Binutils) 2.42",
        "machine": "aarch64",
        "system": "Linux",
        "kernel": "6.5.0-fake",
    }


def test_first_line_skips_blanks_and_falls_back_to_unknown():
    assert livebench._first_line("\n\n   gcc 13.2.0  \nsecond line\n") == "gcc 13.2.0"
    assert livebench._first_line("   \n\t\n") == "unknown"
    assert livebench._first_line("") == "unknown"


def test_tool_version_reads_stdout_then_stderr(monkeypatch):
    """Some toolchains print --version to stderr; the report must still name them."""
    monkeypatch.setattr(
        livebench.subprocess, "run", lambda *a, **k: FakeProc(stdout="gcc 13.2.0\nrest")
    )
    assert livebench._tool_version("gcc") == "gcc 13.2.0"

    monkeypatch.setattr(
        livebench.subprocess, "run", lambda *a, **k: FakeProc(stdout="", stderr="clang 17\n")
    )
    assert livebench._tool_version("clang") == "clang 17"


@pytest.mark.parametrize(
    "exc",
    [OSError("no such file"), subprocess.TimeoutExpired(cmd="objdump", timeout=30)],
)
def test_tool_version_degrades_to_unknown_instead_of_raising(monkeypatch, exc):
    """A missing/hanging --version must not abort a run that can still measure."""
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(livebench.subprocess, "run", boom)
    assert livebench._tool_version("objdump") == "unknown"


# --------------------------------------------------------------------------
# detect_toolchain — the honesty gate plus discovery order
# --------------------------------------------------------------------------

def test_detect_toolchain_prefers_env_overrides(arm_host, monkeypatch):
    host = FakeHost({})
    monkeypatch.setattr(livebench.subprocess, "run", host)
    monkeypatch.setattr(livebench.shutil, "which", lambda _: "/should/not/be/used")

    tc = livebench.detect_toolchain(require_arm=True)

    assert (tc.cc, tc.objdump) == (FAKE_CC, FAKE_OBJDUMP)
    assert tc.cc_version == "fake-gcc (Fake Toolchain) 13.2.0"
    assert tc.objdump_version == "fake-objdump (Fake Toolchain) 13.2.0"
    assert (tc.machine, tc.system, tc.kernel) == ("aarch64", "Linux", "6.5.0-fake")


def test_detect_toolchain_walks_the_documented_fallback_order(arm_host, monkeypatch):
    """Empty $CC/$OBJDUMP must fall through to which(), last candidate included."""
    monkeypatch.setenv("CC", "")
    monkeypatch.setenv("OBJDUMP", "")
    found = {"clang": "/usr/bin/clang", "gobjdump": "/opt/bin/gobjdump"}
    monkeypatch.setattr(livebench.shutil, "which", found.get)
    monkeypatch.setattr(livebench.subprocess, "run", FakeHost({}))

    tc = livebench.detect_toolchain(require_arm=True)

    assert tc.cc == "/usr/bin/clang", "gcc/cc absent → clang is the documented last resort"
    assert tc.objdump == "/opt/bin/gobjdump"


def test_detect_toolchain_without_a_compiler_refuses(arm_host, monkeypatch):
    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setattr(livebench.shutil, "which", lambda _: None)
    with pytest.raises(livebench.ToolchainError, match="no C compiler found"):
        livebench.detect_toolchain(require_arm=True)


def test_detect_toolchain_without_a_disassembler_refuses(arm_host, monkeypatch):
    """No objdump means no ISA witness — the run must not silently degrade."""
    monkeypatch.delenv("OBJDUMP", raising=False)
    monkeypatch.setattr(livebench.shutil, "which", lambda _: None)
    monkeypatch.setattr(livebench.subprocess, "run", FakeHost({}))

    with pytest.raises(livebench.ToolchainError, match="no objdump found") as ei:
        livebench.detect_toolchain(require_arm=True)
    assert "llvm-objdump" in str(ei.value) and "gobjdump" in str(ei.value)


def test_detect_toolchain_allows_x86_only_when_arm_is_not_required(monkeypatch):
    """require_arm=False is the test-plumbing door; it must not lie about the host."""
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setenv("CC", FAKE_CC)
    monkeypatch.setenv("OBJDUMP", FAKE_OBJDUMP)
    monkeypatch.setattr(livebench.subprocess, "run", FakeHost({}))

    tc = livebench.detect_toolchain(require_arm=False)
    assert tc.machine == "x86_64", "the recorded machine is the real one, never faked to arm"

    with pytest.raises(livebench.ToolchainError, match="only be taken on Arm silicon"):
        livebench.detect_toolchain(require_arm=True)


def test_kernel_source_error_names_both_search_locations(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    with pytest.raises(livebench.ToolchainError, match="bench/int8_dot.c not found") as ei:
        livebench.kernel_source()
    msg = str(ei.value)
    assert msg.count("int8_dot.c") >= 3, "must list both candidate paths it looked in"
    assert str(Path.cwd()) in msg


def test_parse_kernel_seconds_rejects_unusable_timings():
    """A silent zero would divide the whole speed claim by nothing — refuse it."""
    with pytest.raises(ValueError, match="did not report kernel_s=") as ei:
        livebench.parse_kernel_seconds("illegal instruction (core dumped)")
    assert "illegal instruction" in str(ei.value), "the raw stderr must be quoted back"

    for bad in ("kernel_s=0.0", "kernel_s=0"):
        with pytest.raises(ValueError, match="non-positive"):
            livebench.parse_kernel_seconds(bad)


# --------------------------------------------------------------------------
# _compile / _disassemble / _run_once
# --------------------------------------------------------------------------

def test_compile_builds_the_exact_reproducible_command(toolchain, tmp_path, monkeypatch):
    host = FakeHost({})
    monkeypatch.setattr(livebench.subprocess, "run", host)
    src = tmp_path / "int8_dot.c"
    src.write_text("int main(void){return 0;}", encoding="utf-8")

    binary, cmd = livebench._compile(toolchain, livebench.CANDIDATE, src, tmp_path)

    assert binary == tmp_path / "fix_R2"
    assert cmd == [FAKE_CC, "-O3", "-march=armv8.2-a+dotprod", "-o", str(binary), str(src)]
    assert host.calls == [cmd], "one compile, exactly the command recorded in the report"


def test_compile_failure_reports_command_and_compiler_stderr(toolchain, tmp_path, monkeypatch):
    monkeypatch.setattr(
        livebench.subprocess, "run", FakeHost({}, compile_fails=livebench.BASELINE.name)
    )
    src = tmp_path / "int8_dot.c"
    src.write_text("x", encoding="utf-8")

    with pytest.raises(livebench.ToolchainError, match="compiling baseline failed") as ei:
        livebench._compile(toolchain, livebench.BASELINE, src, tmp_path)
    msg = str(ei.value)
    assert "-march=armv8-a" in msg, "the failing command must be re-runnable by hand"
    assert "error: unknown -march value" in msg


def test_disassemble_retries_gnu_llvm_and_underscored_spellings(toolchain, tmp_path, monkeypatch):
    """Wrong flag → non-zero; right flag but no match → zero instructions. Keep going."""
    binary = tmp_path / "fix_R2"
    responses = [
        FakeProc(returncode=1, stderr="unrecognized option '--disassemble='"),
        FakeProc(returncode=0, stdout="\n" + binary.name + ":\tfile format mach-o\n"),
        FakeProc(returncode=0, stdout=CANDIDATE_DISASM),
    ]
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append([str(c) for c in cmd])
        return responses[len(seen) - 1]

    monkeypatch.setattr(livebench.subprocess, "run", fake_run)

    out = livebench._disassemble(toolchain, binary, livebench.KERNEL_SYMBOL)

    assert out == CANDIDATE_DISASM
    assert [c[2] for c in seen] == [
        "--disassemble=dot_i8",
        "--disassemble-symbols=dot_i8",
        "--disassemble-symbols=_dot_i8",
    ], "an empty rc=0 result must not be accepted as a witness"
    assert count_witness(out).dotprod == 2


def test_disassemble_refuses_to_witness_what_it_never_read(toolchain, tmp_path, monkeypatch):
    binary = tmp_path / "baseline"
    monkeypatch.setattr(
        livebench.subprocess, "run", lambda *a, **k: FakeProc(returncode=0, stdout="")
    )
    with pytest.raises(livebench.ToolchainError, match="produced no disassembly") as ei:
        livebench._disassemble(toolchain, binary, livebench.KERNEL_SYMBOL)
    assert "dot_i8" in str(ei.value) and "baseline" in str(ei.value)


def test_run_once_passes_workload_args_and_parses_both_channels(tmp_path, monkeypatch):
    binary = tmp_path / "fix_R2"
    host = FakeHost({"fix_R2": [("checksum=12345\n", 0.25)]})
    monkeypatch.setattr(livebench.subprocess, "run", host)

    stdout, kernel_s = livebench._run_once(binary, n=2048, reps=1000)

    assert stdout == "checksum=12345\n"
    assert kernel_s == pytest.approx(0.25)
    assert host.calls == [[str(binary), "2048", "1000"]]


def test_run_once_reports_a_crashed_workload(tmp_path, monkeypatch):
    binary = tmp_path / "baseline"
    monkeypatch.setattr(
        livebench.subprocess,
        "run",
        lambda *a, **k: FakeProc(returncode=-11, stderr="Segmentation fault\n"),
    )
    with pytest.raises(livebench.ToolchainError, match=r"baseline exited -11") as ei:
        livebench._run_once(binary, n=8, reps=1)
    assert "Segmentation fault" in str(ei.value)


# --------------------------------------------------------------------------
# LiveBenchResult — the witness delta and the reproduce block
# --------------------------------------------------------------------------

def _result(base_sha="sha-a", cand_sha="sha-a") -> livebench.LiveBenchResult:
    return livebench.LiveBenchResult(
        toolchain=livebench.Toolchain(
            cc=FAKE_CC,
            cc_version="v",
            objdump=FAKE_OBJDUMP,
            objdump_version="v",
            machine="aarch64",
            system="Linux",
            kernel="6.5.0-fake",
        ),
        lscpu=LSCPU_TEXT,
        baseline=_artifact(livebench.BASELINE, BASELINE_DISASM, [1.00, 1.01, 0.99], base_sha),
        candidate=_artifact(livebench.CANDIDATE, CANDIDATE_DISASM, [0.30, 0.31, 0.29], cand_sha),
        n=2048,
        reps=1000,
        warmup=1,
        schedule=["w:baseline", "w:fix_R2", "m:baseline", "m:fix_R2"],
    )


def test_witness_gain_is_the_candidate_minus_baseline_count():
    res = _result()
    assert res.baseline.witness.dotprod == 0, "ARMv8.0 cannot contain a dot-product instruction"
    assert res.candidate.witness.dotprod == 2
    assert res.witness_gain == 2


@pytest.mark.parametrize(
    "base_sha,cand_sha,expected",
    [
        ("sha-a", "sha-a", True),
        ("sha-a", "sha-b", False),   # faster-but-different is not a fix
        (None, "sha-b", False),      # unmeasured behaviour is never "agrees"
        (None, None, False),
    ],
)
def test_outputs_agree_requires_a_present_and_equal_checksum(base_sha, cand_sha, expected):
    assert _result(base_sha, cand_sha).outputs_agree is expected


def test_artifacts_dict_embeds_the_full_reproduce_block():
    res = _result()
    d = res.artifacts_dict()

    assert d["toolchain"] == res.toolchain.to_dict()
    assert d["workload"] == {
        "source": "bench/int8_dot.c",
        "symbol": "dot_i8",
        "n": 2048,
        "reps": 1000,
        "warmup_rounds": 1,
        "schedule": ["w:baseline", "w:fix_R2", "m:baseline", "m:fix_R2"],
    }
    assert d["compile_commands"]["baseline"][-2:] == ["-O3", "-march=armv8-a"]
    assert d["compile_commands"]["fix_R2"][-2:] == ["-O3", "-march=armv8.2-a+dotprod"]
    assert d["isa_witness"]["baseline"]["dotprod"] == 0
    assert d["isa_witness"]["fix_R2"]["dotprod"] == 2
    assert d["isa_witness"]["delta_total"] == res.witness_gain == 2
    assert "dot_i8" in d["isa_witness"]["note"]
    # Placeholders stay explicit rather than being quietly omitted.
    assert d["flamegraph_before"] is None and d["performix_ref"] is None


# --------------------------------------------------------------------------
# run_live_bench — the whole loop, driven against a scripted host
# --------------------------------------------------------------------------

CHK = "checksum=987654321\n"


def _timings(values: list[float]) -> list[tuple[str, float]]:
    return [(CHK, v) for v in values]


def test_run_live_bench_measures_interleaved_and_earns_keep(arm_host, monkeypatch, tmp_path):
    """End-to-end: warmups discarded, ABAB schedule, and the real gate says keep."""
    host = FakeHost(
        {
            # 1 warmup + 3 measured per variant; the warmup is deliberately slow.
            "baseline": _timings([9.9, 1.00, 1.01, 0.99]),
            "fix_R2": _timings([9.9, 0.30, 0.31, 0.29]),
        }
    )
    monkeypatch.setattr(livebench.subprocess, "run", host)
    src = tmp_path / "int8_dot.c"
    src.write_text("int dot_i8(void);", encoding="utf-8")

    res = livebench.run_live_bench(
        n=2048, reps=1000, measured_rounds=3, warmup=1, require_arm=True, src=src
    )

    # Warmup rounds ran but were not sampled.
    assert len(host.binary_runs("baseline")) == 4
    assert res.baseline.samples == [1.00, 1.01, 0.99]
    assert res.candidate.samples == [0.30, 0.31, 0.29]
    assert 9.9 not in res.baseline.samples, "warmup rounds must never become samples"

    # ABAB interleave, warmups first, one 'w:' pair then three 'm:' pairs.
    assert res.schedule == [
        "w:baseline", "w:fix_R2",
        "m:baseline", "m:fix_R2",
        "m:baseline", "m:fix_R2",
        "m:baseline", "m:fix_R2",
    ]

    # Host fingerprint + both compiles are recorded verbatim.
    assert res.lscpu == LSCPU_TEXT
    assert res.baseline.compile_command[1:3] == ["-O3", "-march=armv8-a"]
    assert res.candidate.compile_command[1:3] == ["-O3", "-march=armv8.2-a+dotprod"]
    assert res.n == 2048 and res.reps == 1000 and res.warmup == 1

    # The ISA witness, and behaviour equality, both hold.
    assert res.witness_gain == 2 and res.baseline.witness.dotprod == 0
    assert res.outputs_agree and res.baseline.output_sha256 == livebench.checksum_of(CHK)

    # The verdict comes from the ordinary gate, not from this module.
    outcome = run_gate(
        res.baseline.to_measurement(),
        [res.candidate.to_measurement()],
        GateConfig(primary_metrics=(livebench.METRIC,)),
    )
    fix = outcome.results[0]
    assert fix.verdict == "keep" and fix.rule_id == "R2"
    cmp = fix.comparisons[livebench.METRIC]
    assert cmp.verdict is Verdict.IMPROVED
    assert cmp.delta == pytest.approx(-0.7, abs=1e-9)
    assert cmp.delta_pct == pytest.approx(-70.0, abs=1e-6)
    assert res.candidate.to_measurement().synthetic is False


def test_run_live_bench_in_band_result_is_dropped_not_massaged(arm_host, monkeypatch, tmp_path):
    """A run that fails to beat its own noise band must be reported as no change."""
    host = FakeHost(
        {
            "baseline": _timings([1.00, 1.01, 0.99]),
            "fix_R2": _timings([1.001, 1.002, 1.000]),
        }
    )
    monkeypatch.setattr(livebench.subprocess, "run", host)
    src = tmp_path / "int8_dot.c"
    src.write_text("int dot_i8(void);", encoding="utf-8")

    res = livebench.run_live_bench(
        n=64, reps=10, measured_rounds=3, warmup=0, require_arm=True, src=src
    )
    assert res.schedule[0] == "m:baseline", "warmup=0 means the schedule starts measured"

    outcome = run_gate(
        res.baseline.to_measurement(),
        [res.candidate.to_measurement()],
        GateConfig(primary_metrics=(livebench.METRIC,)),
    )
    fix = outcome.results[0]
    assert fix.comparisons[livebench.METRIC].verdict is Verdict.NO_CHANGE
    assert fix.verdict == "drop"
    assert any("noise band" in r for r in fix.reasons)
    # ...even though the ISA witness did fire. Instructions are not a speed claim.
    assert res.witness_gain == 2


def test_run_live_bench_survives_a_host_without_lscpu(arm_host, monkeypatch, tmp_path):
    host = FakeHost(
        {
            "baseline": _timings([1.0, 1.0, 1.0]),
            "fix_R2": _timings([0.5, 0.5, 0.5]),
        },
        lscpu_raises=OSError("lscpu: command not found"),
    )
    monkeypatch.setattr(livebench.subprocess, "run", host)
    src = tmp_path / "int8_dot.c"
    src.write_text("int dot_i8(void);", encoding="utf-8")

    res = livebench.run_live_bench(
        n=8, reps=1, measured_rounds=3, warmup=0, require_arm=True, src=src
    )

    assert res.lscpu == "", "a missing fingerprint is empty, never invented"
    assert len(res.candidate.samples) == 3, "the measurement still completes"


def test_run_live_bench_refuses_a_nondeterministic_workload(arm_host, monkeypatch, tmp_path):
    """Different checksum between two runs of the SAME build → refuse to measure."""
    host = FakeHost(
        {
            "baseline": [("checksum=1\n", 1.0), ("checksum=2\n", 1.0), ("checksum=1\n", 1.0)],
            "fix_R2": _timings([0.5, 0.5, 0.5]),
        }
    )
    monkeypatch.setattr(livebench.subprocess, "run", host)
    src = tmp_path / "int8_dot.c"
    src.write_text("int dot_i8(void);", encoding="utf-8")

    with pytest.raises(livebench.ToolchainError, match="not deterministic") as ei:
        livebench.run_live_bench(
            n=8, reps=1, measured_rounds=3, warmup=0, require_arm=True, src=src
        )
    assert "baseline produced a different checksum" in str(ei.value)


def test_run_live_bench_compiles_from_the_packaged_kernel_when_src_is_omitted(
    arm_host, monkeypatch
):
    """With no explicit source it must reach for bench/int8_dot.c, not invent one."""
    host = FakeHost(
        {
            "baseline": _timings([1.0, 1.0, 1.0]),
            "fix_R2": _timings([0.4, 0.4, 0.4]),
        }
    )
    monkeypatch.setattr(livebench.subprocess, "run", host)

    res = livebench.run_live_bench(
        n=8, reps=1, measured_rounds=3, warmup=0, require_arm=True
    )

    compiled = [c[-1] for c in host.commands_for(FAKE_CC) if "-o" in c]
    assert compiled and all(c == str(livebench.kernel_source()) for c in compiled)
    assert len(compiled) == 2, "one compile per variant, from one shared source file"
    assert res.baseline.binary.parent == res.candidate.binary.parent
