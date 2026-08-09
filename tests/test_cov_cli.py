"""CLI paths that only fire on broken input, empty bundles, or real Arm silicon.

The `bench-live` command normally executes only on an aarch64 runner: it compiles
bench/int8_dot.c twice, disassembles both binaries with objdump, and times them.
Here the *boundaries* it talks to — ``platform``, ``shutil.which`` and
``subprocess.run`` — are replaced by a scripted fake host, so the command's own
logic (fingerprint table, ISA witness, gate, report, signing) is exercised for
real without Arm hardware. Everything the fake returns is shaped like the real
tool output it stands in for; the assertions are on what armsmith *concludes*
from it.
"""

from __future__ import annotations

import itertools
import json
import platform
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.conftest import FIXTURES
from typer.testing import CliRunner

from armsmith.cli import app

runner = CliRunner()

LSCPU_N1 = (FIXTURES / "hosts" / "lscpu_neoverse_n1.txt").read_text(encoding="utf-8")

# objdump -d --disassemble=dot_i8 output shapes. The ARMv8.0 build physically
# cannot contain SDOT; the +dotprod build does.
_PROLOGUE = (
    "\n"
    "binary:     file format elf64-littleaarch64\n"
    "\n"
    "Disassembly of section .text:\n"
    "\n"
    "0000000000000740 <dot_i8>:\n"
    "  740:\t4ea01c01 \tmov\tv1.16b, v0.16b\n"
    "  744:\t4e21b802 \tsaddlp\tv2.8h, v0.16b\n"
    "  748:\t4e31b843 \tsaddlp\tv3.4s, v2.8h\n"
    "  74c:\t4eb18463 \tadd\tv3.4s, v3.4s, v17.4s\n"
)
_EPILOGUE = "  790:\td65f03c0 \tret\n"
_SDOT_LINES = "".join(
    f"  {0x750 + 4 * i:x}:\t4e809424 \tsdot\tv4.4s, v1.16b, v0.16b\n" for i in range(4)
)

BASELINE_DISASM = _PROLOGUE + _EPILOGUE
CANDIDATE_DISASM = _PROLOGUE + _SDOT_LINES + _EPILOGUE

# One deterministic stdout for both builds: the gate's behaviour-equality anchor.
WORKLOAD_STDOUT = "checksum=1234567890\n"


class FakeArmHost:
    """Scripted stand-in for cc / objdump / lscpu / the compiled workload."""

    def __init__(self, *, candidate_disasm: str, has_lscpu: bool):
        self.candidate_disasm = candidate_disasm
        self.has_lscpu = has_lscpu
        self.cc = "/usr/bin/gcc"
        self.objdump = "/usr/bin/objdump"
        self.compiled: dict[str, list[str]] = {}
        self.timings = {
            "baseline": itertools.cycle([2.00, 2.01, 1.99, 2.02, 1.98]),
            "fix_R2": itertools.cycle([0.50, 0.51, 0.49, 0.52, 0.48]),
        }
        self.ran: list[str] = []

    # -- shutil.which ----------------------------------------------------
    def which(self, name: str, *args, **kwargs):
        if name == "gcc":
            return self.cc
        if name == "objdump":
            return self.objdump
        if name == "lscpu":
            return "/usr/bin/lscpu" if self.has_lscpu else None
        return None

    # -- subprocess.run --------------------------------------------------
    def run(self, cmd, *args, **kwargs):
        def done(returncode=0, stdout="", stderr=""):
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

        exe = str(cmd[0])
        if "--version" in cmd:
            return done(stdout=f"{Path(exe).name} (fake toolchain) 13.2.0\n")
        if Path(exe).name == "lscpu":
            return done(stdout=LSCPU_N1)
        if exe == self.cc:
            binary = cmd[cmd.index("-o") + 1]
            self.compiled[Path(binary).name] = list(cmd)
            return done()
        if exe == self.objdump:
            binary = Path(str(cmd[-1])).name
            return done(
                stdout=self.candidate_disasm if binary == "fix_R2" else BASELINE_DISASM
            )
        variant = Path(exe).name
        if variant in self.timings:
            self.ran.append(variant)
            return done(
                stdout=WORKLOAD_STDOUT,
                stderr=f"kernel_s={next(self.timings[variant]):.6f}\n",
            )
        raise AssertionError(f"unexpected subprocess call: {cmd}")


@pytest.fixture()
def fake_arm(monkeypatch):
    """Install the fake Arm host; returns a factory so tests can vary it."""

    def _install(*, candidate_disasm: str = CANDIDATE_DISASM, has_lscpu: bool = True):
        host = FakeArmHost(candidate_disasm=candidate_disasm, has_lscpu=has_lscpu)
        monkeypatch.setattr(platform, "machine", lambda: "aarch64")
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform, "release", lambda: "6.8.0-fake")
        monkeypatch.delenv("CC", raising=False)
        monkeypatch.delenv("OBJDUMP", raising=False)
        monkeypatch.setattr(shutil, "which", host.which)
        monkeypatch.setattr(subprocess, "run", host.run)
        return host

    return _install


_BOX = str.maketrans({c: " " for c in "┏━┳┓┃┡╇┩│└┴┘├┼┤─┌┐┬"})


def flat(text: str) -> str:
    """Rich wraps at the terminal width and draws tables with box characters;
    compare on the collapsed, box-free text."""
    return " ".join(text.translate(_BOX).split())


def _bundle(path: Path, *, bench: dict[str, dict] | None = None) -> str:
    """Write a minimal, correctly-provenanced replay bundle."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "provenance": "hand-authored for tests; never measured on hardware",
                "scenario": path.name,
            }
        ),
        encoding="utf-8",
    )
    for name, record in (bench or {}).items():
        (path / "bench").mkdir(exist_ok=True)
        (path / "bench" / f"{name}.json").write_text(json.dumps(record), encoding="utf-8")
    return str(path)


def _record(variant: str, wall: list[float], rule_id: str | None = None) -> dict:
    return {
        "synthetic": True,
        "provenance": "hand-authored for tests",
        "variant": variant,
        "rule_id": rule_id,
        "instrument": "hyperfine",
        "metrics": {"wall_s": wall},
        "pmu": {},
        "output_sha256": "b" * 64,
    }


# ---------------------------------------------------------------------------
# diagnose / ci / doctor — the refusal and empty-bundle paths
# ---------------------------------------------------------------------------

def test_diagnose_unlabeled_bundle_exits_2(tmp_path):
    """A directory with no manifest.json is refused, not silently diagnosed."""
    bad = tmp_path / "nomanifest"
    bad.mkdir()
    res = runner.invoke(app, ["diagnose", "--replay", str(bad), "--no-sign"])
    assert res.exit_code == 2
    assert "diagnose failed" in res.output
    assert "no manifest.json" in flat(res.output)
    assert not (tmp_path / "report.json").exists()


def test_diagnose_bundle_without_bench_records_is_scan_only(tmp_path):
    """No bench/ dir → the gate section is replaced by an explicit scan-only note."""
    bundle = _bundle(tmp_path / "scanonly")
    out = tmp_path / "report.json"
    res = runner.invoke(
        app, ["diagnose", "--replay", bundle, "--out", str(out), "--no-sign"]
    )
    assert res.exit_code == 0, res.output
    assert "no bench records in bundle — scan-only report" in flat(res.output)
    assert "Reproduce gate" not in res.output
    report = json.loads(out.read_text())
    assert report["fixes"] == []
    assert report["baseline"] is None


def test_ci_unlabeled_bundle_exits_2(tmp_path):
    """The CI twin distinguishes 'could not run' (2) from 'regression' (1)."""
    bad = tmp_path / "nomanifest"
    bad.mkdir()
    res = runner.invoke(app, ["ci", "--replay", str(bad)])
    assert res.exit_code == 2
    assert "ci gate failed to run" in res.output


def test_ci_fails_on_regression(tmp_path):
    """A candidate 50% slower than baseline fails the gate with exit code 1."""
    bundle = _bundle(
        tmp_path / "regressed",
        bench={
            "baseline": _record("baseline", [2.01, 1.98, 2.03, 2.00, 1.99]),
            "fix_R6": _record("fix_R6", [3.01, 2.98, 3.03, 3.00, 2.99], rule_id="R6"),
        },
    )
    res = runner.invoke(app, ["ci", "--replay", bundle])
    assert res.exit_code == 1
    out = flat(res.output)
    assert "1 regression(s)" in out
    assert "✗ regression fix_R6: wall_s" in out
    assert "CI GATE FAILED — performance regression detected" in out


def test_doctor_replay_bundle_without_lscpu_exits_2(tmp_path):
    """doctor refuses to fingerprint a bundle that recorded no lscpu."""
    bundle = _bundle(tmp_path / "nolscpu")
    res = runner.invoke(app, ["doctor", "--offline", "--replay", bundle])
    assert res.exit_code == 2
    assert "records no lscpu probe" in flat(res.output)


# ---------------------------------------------------------------------------
# bench-live
# ---------------------------------------------------------------------------

def test_bench_live_refuses_non_arm_host(monkeypatch):
    """There is no code path that emits an Arm measurement off Arm silicon."""
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    res = runner.invoke(app, ["bench-live", "--no-sign"])
    assert res.exit_code == 2
    out = flat(res.output)
    assert "live bench failed" in out
    assert "only be taken on Arm silicon" in out


def test_bench_live_full_run_signs_and_verifies(tmp_path, fake_arm, key_dir):
    """End-to-end live run: fingerprint, SDOT witness, gate keep, signed report."""
    host = fake_arm()
    out = tmp_path / "report-live.json"
    md = tmp_path / "evidence.md"
    res = runner.invoke(app, [
        "bench-live", "--out", str(out), "--markdown", str(md),
        "--n", "256", "--reps", "100", "--rounds", "5", "--warmup", "1",
        "--instance", "c8g.xlarge", "--key-dir", str(key_dir), "--require-witness",
    ])
    assert res.exit_code == 0, res.output
    text = flat(res.output)

    # LIVE banner names the host the numbers came off.
    assert "LIVE MODE — measured on this host (aarch64/Linux)" in text

    # Host table came from the real lscpu the probe ran.
    assert "Host (live lscpu)" in text
    assert "Neoverse-N1" in text
    assert "fake toolchain" in text  # compiler version recorded

    # ISA witness: 0 SDOT in the ARMv8.0 build, 4 in the +dotprod build.
    assert "ISA-witness" in text
    assert "sdot 0 4" in text
    assert "dotprod (sdot+udot): before 0 → after 4" in text
    assert "hot path gained 4 witness instructions" in text

    # Gate: 2.00s → 0.50s is far outside the noise band, outputs identical.
    assert "baseline median 2.000000 s" in text
    assert "candidate median 0.500000 s" in text
    assert "-1.500000 s (-75.00%)" in text
    assert "improved" in text
    assert "outputs identical yes" in text
    assert "gate keep" in text

    # Report + evidence markdown were written, and the report is signed.
    assert "signed sha256:" in text
    assert "evidence →" in text
    report = json.loads(out.read_text())
    assert report["mode"] == "live"
    assert report["synthetic"] is False
    assert report["findings"][0]["rule_id"] == "R2"
    assert report["artifacts"]["isa_witness"]["delta_total"] == 4
    assert "-march=armv8.2-a+dotprod" in report["artifacts"]["compile_commands"]["fix_R2"]
    assert "-march=armv8-a" in report["artifacts"]["compile_commands"]["baseline"]
    assert report["signature"]["algorithm"] == "ed25519"
    assert "Armsmith diagnosis" in md.read_text(encoding="utf-8")

    # warmup + measured rounds actually ran for both variants (1 + 5 each).
    assert host.ran.count("baseline") == 6
    assert host.ran.count("fix_R2") == 6

    # The signed live report survives the ordinary verifier (hash + schema + recompute).
    ver = runner.invoke(app, ["verify", str(out)])
    assert ver.exit_code == 0, ver.output
    assert "VERIFY OK" in ver.output


def test_bench_live_no_sign_leaves_report_unsigned(tmp_path, fake_arm):
    """--no-sign is reported honestly, and the report carries no signature block."""
    fake_arm()
    out = tmp_path / "report-live.json"
    res = runner.invoke(app, [
        "bench-live", "--out", str(out), "--no-sign",
        "--n", "256", "--reps", "100", "--rounds", "3", "--warmup", "0",
    ])
    assert res.exit_code == 0, res.output
    assert "UNSIGNED (signing disabled)" in flat(res.output)
    assert "signature" not in json.loads(out.read_text())


def test_bench_live_missing_key_reports_unsigned_but_still_writes(tmp_path, fake_arm):
    """A missing keypair downgrades to UNSIGNED with the reason — it never aborts."""
    fake_arm()
    out = tmp_path / "report-live.json"
    res = runner.invoke(app, [
        "bench-live", "--out", str(out), "--key-dir", str(tmp_path / "absent"),
        "--n", "256", "--reps", "100", "--rounds", "3", "--warmup", "0",
    ])
    assert res.exit_code == 0, res.output
    assert "UNSIGNED (report left unsigned:" in flat(res.output)
    assert json.loads(out.read_text())["mode"] == "live"


def test_bench_live_without_lscpu_skips_host_table(tmp_path, fake_arm):
    """No lscpu on the host → no host block invented, and the run still completes."""
    fake_arm(has_lscpu=False)
    out = tmp_path / "report-live.json"
    res = runner.invoke(app, [
        "bench-live", "--out", str(out), "--no-sign",
        "--n", "256", "--reps", "100", "--rounds", "3", "--warmup", "0",
    ])
    assert res.exit_code == 0, res.output
    assert "Host (live lscpu)" not in flat(res.output)
    assert json.loads(out.read_text())["host"] is None


def test_bench_live_require_witness_fails_when_no_sdot_emitted(tmp_path, fake_arm):
    """A toolchain that emits no SDOT is reported, not massaged into a win."""
    fake_arm(candidate_disasm=BASELINE_DISASM)  # +dotprod build emitted no SDOT
    out = tmp_path / "report-live.json"
    res = runner.invoke(app, [
        "bench-live", "--out", str(out), "--no-sign", "--require-witness",
        "--n", "256", "--reps", "100", "--rounds", "3", "--warmup", "0",
    ])
    assert res.exit_code == 1
    text = flat(res.output)
    assert "no witness instructions in either build" in text
    assert "did not emit SDOT/UDOT/SMMLA/USMMLA, so the R2 premise is unproven" in text
    # the report is still written before the non-zero exit — the failure is evidence
    assert json.loads(out.read_text())["artifacts"]["isa_witness"]["delta_total"] == 0
