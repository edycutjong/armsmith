"""`armsmith bench-cmd` — the reproduce gate pointed at the operator's own workload.

The gate is the whole product, and until this existed it could only ever be
aimed at this project's own C file. These tests pin the behaviour that makes it
trustworthy on someone else's command: it refuses off Arm, refuses
non-deterministic workloads, and never claims an ISA witness it does not have.
"""

import sys

import pytest

from armsmith import benchcmd
from armsmith.livebench import ToolchainError

PY = sys.executable


def _cmd(body: str) -> str:
    return f'{PY} -c "{body}"'


@pytest.fixture
def on_arm(monkeypatch):
    monkeypatch.setattr(benchcmd.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(benchcmd.platform, "system", lambda: "Linux")


def test_measures_two_commands_and_feeds_the_gate(on_arm):
    res = benchcmd.run_command_bench(
        _cmd("print(1)"), _cmd("print(1)"), measured_rounds=3, warmup=1
    )
    assert len(res.baseline.samples) == 3
    assert len(res.candidate.samples) == 3
    assert res.outputs_agree                      # identical stdout
    m = res.candidate.to_measurement()
    assert m.metrics[benchcmd.METRIC] == res.candidate.samples
    assert m.synthetic is False                   # timed on a real host


def test_refuses_to_run_off_arm(monkeypatch):
    """A wall-clock number from an x86 box is not an Arm result."""
    monkeypatch.setattr(benchcmd.platform, "machine", lambda: "x86_64")
    with pytest.raises(ToolchainError, match="not an Arm result"):
        benchcmd.run_command_bench(_cmd("print(1)"), _cmd("print(1)"))


def test_non_arm_can_be_opted_into_explicitly(monkeypatch):
    monkeypatch.setattr(benchcmd.platform, "machine", lambda: "x86_64")
    res = benchcmd.run_command_bench(
        _cmd("print(1)"), _cmd("print(1)"),
        measured_rounds=3, warmup=0, require_arm=False,
    )
    assert res.machine == "x86_64"                # recorded, not hidden


def test_refuses_a_non_deterministic_workload(on_arm):
    """Changing output means the two sides computed different things."""
    jitter = _cmd("import random; print(random.random())")
    with pytest.raises(ToolchainError, match="not deterministic"):
        benchcmd.run_command_bench(jitter, _cmd("print(1)"), measured_rounds=3, warmup=0)


def test_a_failing_command_is_reported_not_timed(on_arm):
    with pytest.raises(ToolchainError, match="exited 3"):
        benchcmd.run_command_bench(
            _cmd("import sys; sys.exit(3)"), _cmd("print(1)"),
            measured_rounds=3, warmup=0,
        )


def test_both_commands_are_required(on_arm):
    with pytest.raises(ValueError, match="required"):
        benchcmd.run_command_bench("   ", _cmd("print(1)"))


def test_never_claims_an_isa_witness_it_does_not_have(on_arm):
    """Command mode is a stopwatch. Reporting zero counts would read as measured."""
    res = benchcmd.run_command_bench(
        _cmd("print(1)"), _cmd("print(1)"), measured_rounds=3, warmup=0
    )
    witness = res.artifacts_dict()["isa_witness"]
    assert witness["available"] is False
    assert "no binary to disassemble" in witness["note"]
    # The note may NAME sdot when pointing at bench-live; what must not exist
    # is a counter value, which a reader could mistake for a measurement.
    # bool is a subclass of int, and `available: False` is a flag, not a count.
    counts = [v for v in witness.values()
              if isinstance(v, (int, float)) and not isinstance(v, bool)]
    assert counts == []
    assert "counts" not in witness and "delta_total" not in witness


def test_schedule_interleaves_the_two_sides(on_arm):
    """ABAB, so machine drift lands on both sides rather than the second one."""
    res = benchcmd.run_command_bench(
        _cmd("print(1)"), _cmd("print(1)"), measured_rounds=3, warmup=1
    )
    measured = [s.split(":")[1] for s in res.schedule if s.startswith("m:")]
    assert measured.count("baseline") == 3
    assert measured.count("candidate") == 3
    # never the same side twice running, which is what interleaving buys
    assert all(a != b for a, b in zip(measured, measured[1:]))


def test_artifacts_record_the_commands_and_cwd(on_arm, tmp_path):
    res = benchcmd.run_command_bench(
        _cmd("print(1)"), _cmd("print(2)"),
        measured_rounds=3, warmup=0, cwd=str(tmp_path),
    )
    wl = res.artifacts_dict()["workload"]
    assert wl["cwd"] == str(tmp_path)
    assert "print(1)" in wl["baseline_command"]
    assert "print(2)" in wl["candidate_command"]
    assert not res.outputs_agree                  # different stdout, honestly reported
