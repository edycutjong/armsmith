"""CLI surface via Typer's CliRunner (no subprocess, no network)."""

import json

from tests.conftest import FIXTURES
from typer.testing import CliRunner

from armsmith.cli import app

runner = CliRunner()
SCENARIO = str(FIXTURES / "replays" / "scenario_ragserve")


def test_version():
    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert "armsmith 0.1.0" in res.output


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
