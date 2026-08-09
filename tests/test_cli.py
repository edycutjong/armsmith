"""CLI surface via Typer's CliRunner (no subprocess, no network)."""

import json

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


def test_doctor_requires_offline():
    res = runner.invoke(app, ["doctor"])
    assert res.exit_code == 2
    assert "TODO(S1)" in res.output


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
