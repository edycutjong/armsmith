"""CLI surface via Typer's CliRunner (no subprocess, no network)."""

import json

import pytest
from tests.conftest import FIXTURES
from typer.testing import CliRunner

from armsmith.cli import app

runner = CliRunner()
SCENARIO = str(FIXTURES / "replays" / "scenario_ragserve")


def test_version():
    # Asserted against the package's own __version__, never a hardcoded string:
    # semantic-release rewrites the version on every release, and a literal here
    # would turn each one into a red build.
    from armsmith import __version__

    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert f"armsmith {__version__}" in res.output


def test_rules_list_shows_all_13():
    res = runner.invoke(app, ["rules", "list"])
    assert res.exit_code == 0
    for rid in [f"R{i}" for i in range(1, 14)]:
        assert rid in res.output
    assert "estimates from citations" in res.output


def test_rules_explain_r13():
    res = runner.invoke(app, ["rules", "explain", "r13"])
    assert res.exit_code == 0
    assert "Serving overhead dominates kernel time" in res.output
    assert "llama-bench" in res.output


def test_rules_explain_unknown_exits_2():
    res = runner.invoke(app, ["rules", "explain", "R99"])
    assert res.exit_code == 2


def test_keys_init_and_refuse_overwrite(tmp_path):
    kd = str(tmp_path / "keys")
    res = runner.invoke(app, ["keys", "init", "--key-dir", kd])
    assert res.exit_code == 0
    assert "private key" in res.output
    res2 = runner.invoke(app, ["keys", "init", "--key-dir", kd])
    assert res2.exit_code == 1


def test_diagnose_replay_end_to_end(tmp_path):
    kd = str(tmp_path / "keys")
    runner.invoke(app, ["keys", "init", "--key-dir", kd])
    out = tmp_path / "report.json"
    res = runner.invoke(app, [
        "diagnose", "--replay", SCENARIO, "--out", str(out), "--key-dir", kd,
    ])
    assert res.exit_code == 0, res.output
    assert "REPLAY MODE" in res.output
    assert "4 kept" in res.output and "2 dropped" in res.output
    report = json.loads(out.read_text())
    assert report["mode"] == "replay"
    assert report["signature"]["algorithm"] == "ed25519"


def test_diagnose_then_verify_roundtrip(tmp_path):
    kd = str(tmp_path / "keys")
    runner.invoke(app, ["keys", "init", "--key-dir", kd])
    out = tmp_path / "report.json"
    runner.invoke(app, ["diagnose", "--replay", SCENARIO, "--out", str(out), "--key-dir", kd])
    res = runner.invoke(app, ["verify", str(out)])
    assert res.exit_code == 0, res.output
    assert "VERIFY OK" in res.output


def test_verify_fails_on_tamper(tmp_path):
    kd = str(tmp_path / "keys")
    runner.invoke(app, ["keys", "init", "--key-dir", kd])
    out = tmp_path / "report.json"
    runner.invoke(app, ["diagnose", "--replay", SCENARIO, "--out", str(out), "--key-dir", kd])
    data = json.loads(out.read_text())
    data["fixes"][0]["measurement"]["metrics_summary"]["wall_s"]["median"] = 0.001
    out.write_text(json.dumps(data))
    res = runner.invoke(app, ["verify", str(out)])
    assert res.exit_code == 1
    assert "recompute" in res.output


def test_diagnose_no_sign_reports_unsigned(tmp_path):
    out = tmp_path / "report.json"
    res = runner.invoke(app, ["diagnose", "--replay", SCENARIO, "--out", str(out), "--no-sign"])
    assert res.exit_code == 0
    assert "UNSIGNED" in res.output


def test_diagnose_pr_dry_run(tmp_path):
    out = tmp_path / "report.json"
    res = runner.invoke(app, [
        "diagnose", "--replay", SCENARIO, "--out", str(out), "--no-sign", "--pr-dry-run",
    ])
    assert res.exit_code == 0
    assert "DRY RUN — nothing was sent to GitHub" in res.output
    assert "cosign verify-blob" in res.output


def test_rules_explain_shows_learning_path():
    res = runner.invoke(app, ["rules", "explain", "R1"])
    assert res.exit_code == 0
    flat = " ".join(res.output.split())
    assert "Arm Learning Path" in flat
    assert "learn.arm.com/learning-paths" in flat


def test_rules_explain_honest_when_no_learning_path():
    # R3 has no direct Arm LP — must say so, not fake one.
    res = runner.invoke(app, ["rules", "explain", "R3"])
    assert res.exit_code == 0
    flat = " ".join(res.output.split())
    assert "no direct LP" in flat or "none" in flat


def test_rules_export_writes_cards(tmp_path):
    out = tmp_path / "cards"
    res = runner.invoke(app, ["rules", "export", "--format", "md", "--out-dir", str(out)])
    assert res.exit_code == 0, res.output
    cards = sorted(out.glob("R[0-9]*.md"))
    assert len(cards) == 13
    assert (out / "README.md").is_file()
    r1 = (out / "R1.md").read_text()
    assert "migration template" in r1
    assert "learn.arm.com/learning-paths" in r1  # R1 links an LP
    r3 = (out / "R3.md").read_text()
    assert "no direct Arm LP" in r3  # honest
    assert "10 link an Arm Learning Path" in " ".join(res.output.split())


def test_rules_export_rejects_unknown_format(tmp_path):
    res = runner.invoke(app, ["rules", "export", "--format", "sarif", "--out-dir", str(tmp_path)])
    assert res.exit_code == 2


def test_scan_static_only_matches(tmp_path):
    # scan resolves a bundle's repo/ subdir and runs static rules with no probes.
    res = runner.invoke(app, ["scan", SCENARIO])
    assert res.exit_code == 0, res.output
    flat = " ".join(res.output.split())
    assert "Static scan" in flat
    assert "3 matched of 3 static rules" in flat  # R1, R4, R12
    assert "R1" in flat and "R4" in flat and "R12" in flat


def test_scan_strict_exits_1_on_match():
    res = runner.invoke(app, ["scan", SCENARIO, "--strict"])
    assert res.exit_code == 1


def test_scan_clean_repo_exits_0(tmp_path):
    # a bare directory with no anti-patterns: static rules report clean, exit 0.
    (tmp_path / "README.md").write_text("nothing to see")
    res = runner.invoke(app, ["scan", str(tmp_path)])
    assert res.exit_code == 0
    assert "0 matched" in " ".join(res.output.split())


def test_witness_before_after_counts():
    before = str(FIXTURES / "witness" / "objdump_before.txt")
    after = str(FIXTURES / "witness" / "objdump_after.txt")
    res = runner.invoke(app, ["witness", before, after])
    assert res.exit_code == 0, res.output
    flat = " ".join(res.output.split())
    assert "ISA-witness" in flat
    assert "before 0 → after 4" in flat  # dotprod
    assert "emitted, not inferred" in flat


def test_pr_dry_run_from_report(tmp_path):
    out = tmp_path / "report.json"
    runner.invoke(app, ["diagnose", "--replay", SCENARIO, "--out", str(out), "--no-sign"])
    res = runner.invoke(app, ["pr", str(out), "--repo", "edycu/ragserve"])
    assert res.exit_code == 0, res.output
    assert "DRY RUN — nothing was sent to GitHub" in res.output
    assert "edycu/ragserve" in res.output
    assert "reproduce-gated Arm optimization" in res.output


def test_pr_no_dry_run_is_honest_todo(tmp_path):
    out = tmp_path / "report.json"
    runner.invoke(app, ["diagnose", "--replay", SCENARIO, "--out", str(out), "--no-sign"])
    res = runner.invoke(app, ["pr", str(out), "--no-dry-run"])
    assert res.exit_code == 2
    assert "TODO(S1)" in res.output


def test_ci_replay_passes_without_regression(tmp_path):
    res = runner.invoke(app, ["ci", "--replay", SCENARIO, "--key-dir", str(tmp_path / "k")])
    assert res.exit_code == 0, res.output
    flat = " ".join(res.output.split())
    assert "CI GATE PASSED" in flat
    assert "0 regression(s)" in flat


def test_doctor_fails_once_with_the_whole_invocation():
    """It used to error on --offline, then error again on the missing source.

    Two failures to learn one command is a bad first minute. One failure now
    prints the complete working invocation.
    """
    res = runner.invoke(app, ["doctor"])
    assert res.exit_code == 2
    flat = " ".join(res.output.split())
    assert "armsmith doctor --offline --replay" in flat
    assert "--lscpu-file" in flat
    assert "armsmith record" in flat          # how to make a source of your own

    # --offline alone is still incomplete, and must fail the SAME way.
    res2 = runner.invoke(app, ["doctor", "--offline"])
    assert res2.exit_code == 2
    assert "armsmith doctor --offline --replay" in " ".join(res2.output.split())


def test_version_flag_and_subcommand_agree():
    """`--version` is what people type first; erroring on it is a poor hello."""
    from armsmith import __version__

    for argv in (["--version"], ["-V"], ["version"]):
        res = runner.invoke(app, argv)
        assert res.exit_code == 0, argv
        assert f"armsmith {__version__}" in res.output


def test_doctor_offline_replay_bundle():
    res = runner.invoke(app, ["doctor", "--offline", "--replay", SCENARIO])
    assert res.exit_code == 0
    flat = " ".join(res.output.split())  # rich wraps lines at terminal width
    assert "Neoverse-V2" in flat
    assert "dotprod" in flat
    assert "recorded data, not this machine" in flat
    assert "sysreport" in flat  # TODO(S1) note visible


def test_doctor_offline_lscpu_file():
    lscpu = str(FIXTURES / "hosts" / "lscpu_neoverse_n1.txt")
    res = runner.invoke(app, ["doctor", "--offline", "--lscpu-file", lscpu])
    assert res.exit_code == 0
    assert "Neoverse-N1" in res.output


def test_doctor_offline_without_source_exits_2():
    res = runner.invoke(app, ["doctor", "--offline"])
    assert res.exit_code == 2


# --- record ------------------------------------------------------------------

def _repo(tmp_path):
    d = tmp_path / "svc"
    d.mkdir()
    (d / "app.py").write_text("import numpy as np\nx = np.zeros(4)\n")
    return d


def test_record_writes_a_real_bundle_and_reports_what_it_could_not_get(tmp_path, monkeypatch):
    """The table must show refused/absent probes, not quietly omit them."""
    from armsmith import record as rec

    monkeypatch.setattr(rec, "_capture_numpy_show_config",
                        lambda python=None: ("name: openblas64\n", "py -c ..."))

    out = tmp_path / "bundle"
    res = runner.invoke(app, ["record", str(_repo(tmp_path)), "--out", str(out),
                              "--scenario", "svc-live"])

    assert res.exit_code == 0, res.output
    assert '"synthetic": false' in res.output          # the headline claim
    assert "numpy_show_config" in res.output
    assert "refused" in res.output                     # env / proc_maps are shown
    assert "R3" in res.output                          # rules the bundle unlocks
    assert json.loads((out / "manifest.json").read_text())["synthetic"] is False
    assert (out / "repo" / "app.py").is_file()


def test_record_ingests_supplied_artifacts_and_can_skip_the_repo_copy(tmp_path, monkeypatch):
    from armsmith import record as rec

    monkeypatch.setattr(rec, "_capture_numpy_show_config",
                        lambda python=None: (None, "numpy absent"))
    log = tmp_path / "build.log"
    log.write_text("gcc -O3 -march=armv8-a k.c\n")
    out = tmp_path / "bundle2"

    res = runner.invoke(app, ["record", str(_repo(tmp_path)), "--out", str(out),
                              "--build-log", str(log), "--no-copy-repo",
                              "--note", "captured on c8g.4xlarge"])

    assert res.exit_code == 0, res.output
    assert (out / "probes" / "build_log.txt").read_text() == log.read_text()
    assert not (out / "repo").exists()
    assert "no repo copy" in res.output
    assert json.loads((out / "manifest.json").read_text())["note"] == "captured on c8g.4xlarge"


def test_record_says_none_when_it_can_capture_nothing(tmp_path, monkeypatch):
    from armsmith import record as rec

    monkeypatch.setattr(rec, "_capture_numpy_show_config",
                        lambda python=None: (None, "numpy absent"))
    monkeypatch.setattr(rec, "LiveProbe", lambda: type("P", (), {"has": lambda s, k: False})())

    out = tmp_path / "bundle3"
    res = runner.invoke(app, ["record", str(_repo(tmp_path)), "--out", str(out)])

    assert res.exit_code == 0, res.output
    assert "0 probes captured" in res.output
    assert "none" in res.output
    assert list((out / "probes").iterdir()) == []      # nothing invented


def test_rules_export_cards_contain_a_real_before_after_diff(tmp_path):
    """The rubric asks for migration templates; an index of links is not one."""
    out = tmp_path / "cards"
    res = runner.invoke(app, ["rules", "export", "--format", "md", "--out-dir", str(out)])
    assert res.exit_code == 0, res.output

    r12 = (out / "R12.md").read_text()
    assert "## Before → after" in r12
    assert "```yaml" in r12
    assert "platforms: linux/amd64\n```" in r12          # the anti-pattern
    assert "platforms: linux/amd64,linux/arm64" in r12   # the fix

    # Diagnostic rules name no specific edit, so they render no fence at all
    # rather than an invented one.
    assert "## Before → after" not in (out / "R13.md").read_text()
    assert "```" not in (out / "R13.md").read_text()


def test_diagnose_banner_reports_provenance_not_transport(tmp_path, monkeypatch):
    """A recorded bundle is replayed but REAL — the banner must say so.

    Keying the banner off `mode == "replay"` labelled genuine host observations
    as synthetic, which understates a real measurement exactly as badly as the
    reverse would overstate one.
    """
    from armsmith import record as rec

    monkeypatch.setattr(rec, "_capture_numpy_show_config",
                        lambda python=None: ("name: openblas64\n", "py -c ..."))
    src = tmp_path / "svc"
    src.mkdir()
    (src / "app.py").write_text("import numpy as np\nx = np.zeros(4)\n")
    bundle = tmp_path / "bundle"
    rec.record_bundle(src, bundle, scenario="live-capture")

    res = runner.invoke(app, ["diagnose", "--replay", str(bundle),
                              "--no-sign", "--out", str(tmp_path / "r.json")])
    assert res.exit_code == 0, res.output
    assert "RECORDED" in res.output
    assert "REPLAY MODE" not in res.output

    # and the synthetic fixture must still be called synthetic
    res2 = runner.invoke(app, ["diagnose", "--replay", SCENARIO,
                               "--no-sign", "--out", str(tmp_path / "r2.json")])
    assert "REPLAY MODE" in res2.output
    assert "synthetic fixture data" in res2.output


# --- bench-cmd: the gate pointed at the operator's own workload ---------------

@pytest.fixture
def _arm(monkeypatch):
    from armsmith import benchcmd
    monkeypatch.setattr(benchcmd.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(benchcmd.platform, "system", lambda: "Linux")


def _pycmd(body):
    import sys
    return f'{sys.executable} -c "{body}"'


def test_bench_cmd_gates_two_commands_and_writes_a_signed_shaped_report(tmp_path, _arm):
    out, md = tmp_path / "r.json", tmp_path / "e.md"
    res = runner.invoke(app, [
        "bench-cmd",
        "--baseline-cmd", _pycmd("print(1)"),
        "--candidate-cmd", _pycmd("print(1)"),
        "--rounds", "3", "--warmup", "0", "--no-sign",
        "--out", str(out), "--markdown", str(md), "--scenario", "unit",
    ])
    assert res.exit_code == 0, res.output
    assert "LIVE MODE" in res.output
    assert "no ISA witness in command mode" in res.output   # never implied

    rpt = json.loads(out.read_text())
    assert rpt["mode"] == "live" and rpt["synthetic"] is False
    assert rpt["artifacts"]["isa_witness"]["available"] is False
    assert md.exists()


def test_bench_cmd_refuses_off_arm_with_exit_2(monkeypatch, tmp_path):
    from armsmith import benchcmd
    monkeypatch.setattr(benchcmd.platform, "machine", lambda: "x86_64")
    res = runner.invoke(app, [
        "bench-cmd", "--baseline-cmd", _pycmd("print(1)"),
        "--candidate-cmd", _pycmd("print(1)"), "--out", str(tmp_path / "r.json"),
    ])
    assert res.exit_code == 2


def test_bench_cmd_strict_exits_nonzero_when_the_gate_drops(tmp_path, _arm):
    """Two identical commands cannot beat the noise band, so the gate drops."""
    res = runner.invoke(app, [
        "bench-cmd",
        "--baseline-cmd", _pycmd("print(1)"),
        "--candidate-cmd", _pycmd("print(1)"),
        "--rounds", "3", "--warmup", "0", "--no-sign", "--strict",
        "--out", str(tmp_path / "r.json"),
    ])
    assert res.exit_code == 1
    assert "drop" in res.output


def test_bench_cmd_reports_a_broken_command_rather_than_timing_it(tmp_path, _arm):
    res = runner.invoke(app, [
        "bench-cmd",
        "--baseline-cmd", _pycmd("import sys; sys.exit(3)"),
        "--candidate-cmd", _pycmd("print(1)"),
        "--rounds", "3", "--warmup", "0", "--no-sign",
        "--out", str(tmp_path / "r.json"),
    ])
    assert res.exit_code == 2
    assert "bench-cmd failed" in res.output


def test_bench_cmd_surfaces_a_timeout(tmp_path, _arm, monkeypatch):
    import subprocess as sp

    from armsmith import benchcmd

    def boom(*a, **k):
        raise sp.TimeoutExpired(cmd="sleep", timeout=1)
    monkeypatch.setattr(benchcmd.subprocess, "run", boom)

    res = runner.invoke(app, [
        "bench-cmd", "--baseline-cmd", _pycmd("print(1)"),
        "--candidate-cmd", _pycmd("print(1)"), "--no-sign",
        "--out", str(tmp_path / "r.json"),
    ])
    assert res.exit_code == 2
    assert "timed out" in res.output


def test_bench_cmd_signs_the_report_when_keys_exist(tmp_path, _arm):
    kd = str(tmp_path / "keys")
    runner.invoke(app, ["keys", "init", "--key-dir", kd])
    out = tmp_path / "signed.json"
    res = runner.invoke(app, [
        "bench-cmd", "--baseline-cmd", _pycmd("print(1)"),
        "--candidate-cmd", _pycmd("print(1)"),
        "--rounds", "3", "--warmup", "0", "--key-dir", kd, "--out", str(out),
    ])
    assert res.exit_code == 0, res.output
    assert "signed" in res.output
    assert json.loads(out.read_text())["signature"]["report_sha256"]

    # And verify re-derives it, like every other report this tool emits.
    v = runner.invoke(app, ["verify", str(out)])
    assert v.exit_code == 0, v.output
    assert "VERIFY OK" in v.output


def test_bench_cmd_reports_unsigned_rather_than_failing_without_keys(tmp_path, _arm):
    out = tmp_path / "unsigned.json"
    res = runner.invoke(app, [
        "bench-cmd", "--baseline-cmd", _pycmd("print(1)"),
        "--candidate-cmd", _pycmd("print(1)"),
        "--rounds", "3", "--warmup", "0",
        "--key-dir", str(tmp_path / "absent"), "--out", str(out),
    ])
    assert res.exit_code == 0, res.output
    assert "UNSIGNED" in res.output
    assert "keys init" in res.output          # tells you how to fix it


def test_bench_cmd_fingerprints_the_host_when_lscpu_is_available(tmp_path, _arm, monkeypatch):
    """On a real Arm box lscpu exists, and the report carries the host block."""
    from armsmith import probes

    lscpu = (
        "Architecture:  aarch64\nCPU(s): 4\nVendor ID: ARM\n"
        "Model name: Neoverse-N2\nFlags: asimddp i8mm sve\n"
    )
    monkeypatch.setattr(probes.LiveProbe, "has", lambda self, kind: kind == "lscpu")
    monkeypatch.setattr(probes.LiveProbe, "text", lambda self, kind: lscpu)

    out = tmp_path / "host.json"
    res = runner.invoke(app, [
        "bench-cmd", "--baseline-cmd", _pycmd("print(1)"),
        "--candidate-cmd", _pycmd("print(1)"),
        "--rounds", "3", "--warmup", "0", "--no-sign",
        "--instance", "c8g.4xlarge", "--out", str(out),
    ])
    assert res.exit_code == 0, res.output
    host = json.loads(out.read_text())["host"]
    assert host["model_name"] == "Neoverse-N2"
    assert host["instance"] == "c8g.4xlarge"


def test_bench_cmd_with_a_rule_id_records_a_finding_and_still_verifies(tmp_path, _arm):
    kd = str(tmp_path / "k")
    runner.invoke(app, ["keys", "init", "--key-dir", kd])
    out = tmp_path / "ruled.json"
    res = runner.invoke(app, [
        "bench-cmd", "--baseline-cmd", _pycmd("print(1)"),
        "--candidate-cmd", _pycmd("print(1)"),
        "--rounds", "3", "--warmup", "0", "--rule", "R3",
        "--key-dir", kd, "--out", str(out),
    ])
    assert res.exit_code == 0, res.output
    rpt = json.loads(out.read_text())
    assert rpt["findings"][0]["rule_id"] == "R3"
    assert rpt["plan"][0]["rule_id"] == "R3"
    assert runner.invoke(app, ["verify", str(out)]).exit_code == 0
