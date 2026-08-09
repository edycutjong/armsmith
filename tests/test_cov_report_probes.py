"""Error, tamper and fallback paths of armsmith.report + armsmith.probes.

Every report test here follows the same adversarial pattern: tamper with a
*signed* report, then **re-sign the tampered body with a valid key**.  The
signature and the content hash therefore both check out — so anything the
verifier still flags was caught by the recompute layer, which is the property
the whole design claims (COMPLEXITY §2: "editing a number without re-running
the math is detectable").

The probes tests drive LiveProbe's real file/command readers with real
executables (``echo``, ``false``) rather than mocking subprocess, so the
assertions describe genuine behaviour of the backend.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from armsmith import benchstats, probes
from armsmith import keys as keys_mod
from armsmith import report as report_mod
from armsmith.benchstats import Direction
from armsmith.diagnose import run_replay_diagnosis
from armsmith.gate import GateConfig, MeasurementSet, run_gate
from armsmith.probes import LiveProbe, ProbeMissing

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# local fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cov_key_dir(tmp_path_factory):
    kd = tmp_path_factory.mktemp("cov_keys")
    keys_mod.init_keys(key_dir=kd)
    return kd


@pytest.fixture(scope="module")
def signed(cov_key_dir):
    """A real signed replay report (scenario_ragserve)."""
    result = run_replay_diagnosis(
        FIXTURES / "replays" / "scenario_ragserve", key_dir=cov_key_dir, sign=True
    )
    assert result.signed
    return result.report


def _resign(report: dict, key_dir) -> dict:
    """Re-sign a tampered body so signature + hash pass and only math fails."""
    return report_mod.sign_report(report, key_dir)


def _details(result) -> str:
    return " | ".join(i.detail for i in result.issues)


def _first_comparison_with_band(report: dict) -> tuple[dict, str]:
    for fix in report["fixes"]:
        for metric, cmp in fix["comparisons"].items():
            if cmp.get("band") is not None:
                return fix, metric
    raise AssertionError("fixture must contain a comparison carrying a noise band")


def _custom_metric_report(cov_key_dir, *, regress: bool = False) -> dict:
    """Signed report whose only metric is unknown to gate.METRIC_DIRECTIONS.

    The gate config's ``directions`` map is deliberately NOT serialized into the
    report, so verification must fall back to the direction each comparison
    declares — that fallback is what these reports exercise.
    """
    base_samples = [100.0, 101.0, 99.0, 100.5, 100.2, 99.8, 100.1]
    cand_samples = (
        [40.0, 41.0, 39.0, 40.5, 40.2, 39.8, 40.1]
        if regress
        else [140.0, 141.0, 139.0, 140.5, 140.2, 139.8, 140.1]
    )
    digest = "c0ffee" * 10 + "cafe"
    baseline = MeasurementSet(
        variant="baseline",
        instrument="unit-test-synthetic",
        metrics={"custom_score_x": base_samples},
        output_sha256=digest,
    )
    candidate = MeasurementSet(
        variant="fix_custom",
        rule_id="R3",
        instrument="unit-test-synthetic",
        metrics={"custom_score_x": cand_samples},
        output_sha256=digest,
    )
    cfg = GateConfig(directions={"custom_score_x": Direction.HIGHER_BETTER})
    outcome = run_gate(baseline, [candidate], cfg)
    unsigned = report_mod.build_report(
        mode="replay",
        scenario="custom-metric",
        repo={"url": "replay:unit-test", "sha": "n/a"},
        host=None,
        findings=[],
        outcome=outcome,
        gate_config=cfg,
    )
    return _resign(unsigned, cov_key_dir)


# ---------------------------------------------------------------------------
# report.py — recompute layer: claimed statistics vs embedded raw samples
# ---------------------------------------------------------------------------

def test_summary_dropped_for_a_measured_metric_is_flagged(signed, cov_key_dir):
    """Deleting a summary while keeping its raw samples is caught (report.py 209-210)."""
    tampered = copy.deepcopy(signed)
    del tampered["baseline"]["metrics_summary"]["rss_peak_mb"]
    tampered = _resign(tampered, cov_key_dir)

    result = report_mod.verify_report(tampered)

    assert not result.ok
    # the signature/hash layer is happy — only the recompute layer objects
    assert any("signature OK" in c for c in result.checks)
    assert any("content hash OK" in c for c in result.checks)
    assert {i.kind for i in result.issues} == {"recompute"}
    assert "baseline: metric rss_peak_mb has samples but no summary" in _details(result)


def test_claimed_summary_without_raw_samples_is_flagged(signed, cov_key_dir):
    """A statistic with no embedded samples behind it is rejected (report.py 224)."""
    tampered = copy.deepcopy(signed)
    fabricated = benchstats.summarize([1.7, 1.8, 1.75, 1.72, 1.79]).to_dict()
    tampered["baseline"]["metrics_summary"]["ipc"] = fabricated
    tampered = _resign(tampered, cov_key_dir)

    result = report_mod.verify_report(tampered)

    assert not result.ok
    assert {i.kind for i in result.issues} == {"recompute"}
    assert "baseline: summary for ipc has no raw samples" in _details(result)


def test_comparison_metric_without_raw_samples_is_flagged(signed, cov_key_dir):
    """A comparison invented for a metric that was never measured (report.py 244-245)."""
    tampered = copy.deepcopy(signed)
    fix = tampered["fixes"][0]
    fix["comparisons"]["latency_ms"] = copy.deepcopy(fix["comparisons"]["wall_s"])
    tampered = _resign(tampered, cov_key_dir)

    result = report_mod.verify_report(tampered)

    assert not result.ok
    assert {i.kind for i in result.issues} == {"recompute"}
    assert "comparison metric latency_ms lacks raw samples" in _details(result)


def test_noise_band_tamper_is_flagged(signed, cov_key_dir):
    """Widening the claimed noise band is recomputed and rejected (report.py 266)."""
    tampered = copy.deepcopy(signed)
    fix, metric = _first_comparison_with_band(tampered)
    honest_band = fix["comparisons"][metric]["band"]
    fix["comparisons"][metric]["band"] = honest_band * 2.0
    tampered = _resign(tampered, cov_key_dir)

    result = report_mod.verify_report(tampered)

    assert not result.ok
    assert any(
        i.kind == "recompute" and "noise band claimed" in i.detail for i in result.issues
    ), _details(result)
    # the honest value is the one the verifier recomputes
    assert f"recomputes to {honest_band}" in _details(result)


def test_keep_verdict_over_a_regressed_metric_is_flagged(cov_key_dir):
    """A 'keep' that hides a regression is rejected (report.py 274)."""
    honest = _custom_metric_report(cov_key_dir, regress=True)
    fix = honest["fixes"][0]
    assert fix["verdict"] == "drop"
    assert fix["comparisons"]["custom_score_x"]["verdict"] == "regressed"

    tampered = copy.deepcopy(honest)
    tampered["fixes"][0]["verdict"] = "keep"  # promote a regressing fix
    tampered = _resign(tampered, cov_key_dir)

    result = report_mod.verify_report(tampered)

    assert not result.ok
    assert (
        "fix fix_custom: verdict 'keep' despite regressed metric custom_score_x"
        in _details(result)
    )


# ---------------------------------------------------------------------------
# report.py — direction fallback for metrics gate.METRIC_DIRECTIONS doesn't know
# ---------------------------------------------------------------------------

def test_unknown_metric_verifies_via_declared_direction(cov_key_dir):
    """Direction falls back to the comparison's own field (report.py 246-250)."""
    honest = _custom_metric_report(cov_key_dir)
    cmp = honest["fixes"][0]["comparisons"]["custom_score_x"]
    assert cmp["direction"] == "higher_better" and cmp["verdict"] == "improved"

    result = report_mod.verify_report(honest)

    assert result.ok, _details(result)
    assert any("recompute exactly" in c for c in result.checks)


def test_unparseable_declared_direction_is_flagged(cov_key_dir):
    """A direction outside the Direction enum is rejected (report.py 251-253)."""
    tampered = _custom_metric_report(cov_key_dir)
    tampered["fixes"][0]["comparisons"]["custom_score_x"]["direction"] = "sideways_better"
    tampered = _resign(tampered, cov_key_dir)

    result = report_mod.verify_report(tampered, check_schema=False)

    assert not result.ok
    assert {i.kind for i in result.issues} == {"recompute"}
    assert "fix fix_custom: unknown direction for custom_score_x" in _details(result)
    # the schema layer independently rejects the same edit
    assert any("direction" in i for i in report_mod.validate_schema(tampered))


# ---------------------------------------------------------------------------
# report.py — signature / schema / structure paths
# ---------------------------------------------------------------------------

def test_report_without_measurements_verifies(cov_key_dir):
    """A report with no baseline skips gate recomputation cleanly (report.py 230)."""
    unsigned = report_mod.build_report(
        mode="replay",
        scenario="no-bench",
        repo={"url": "replay:no-bench", "sha": "n/a"},
        host=None,
        findings=[],
        outcome=None,
    )
    assert unsigned["baseline"] is None and unsigned["fixes"] == []

    result = report_mod.verify_report(_resign(unsigned, cov_key_dir))

    assert result.ok, _details(result)


def test_malformed_public_key_material_is_rejected(signed):
    """Garbage key material fails verification instead of crashing (report.py 310-311)."""
    tampered = copy.deepcopy(signed)
    tampered["signature"]["public_key_b64"] = "not-base64!!"

    result = report_mod.verify_report(tampered)

    assert not result.ok
    assert any(
        i.kind == "signature" and "malformed signature material" in i.detail
        for i in result.issues
    ), _details(result)
    # body is untouched, so the content hash still matches — only the key is bad
    assert any("content hash OK" in c for c in result.checks)


def test_truncated_signature_is_rejected(signed, cov_key_dir):
    """A signature that does not verify under the embedded key is rejected."""
    tampered = copy.deepcopy(signed)
    tampered["signature"]["signature_b64"] = keys_mod.public_key_b64(
        keys_mod.load_private_key(cov_key_dir).public_key()
    )  # valid base64, wrong bytes → InvalidSignature, not an exception

    result = report_mod.verify_report(tampered)

    assert not result.ok
    assert any("signature INVALID" in i.detail for i in result.issues), _details(result)


def test_schema_violation_surfaces_through_verify(signed, cov_key_dir):
    """verify_report reports schema errors as issues (report.py 321-322)."""
    tampered = copy.deepcopy(signed)
    tampered["mode"] = "hallucinated"
    tampered = _resign(tampered, cov_key_dir)

    result = report_mod.verify_report(tampered, check_schema=True)

    assert not result.ok
    schema_issues = [i for i in result.issues if i.kind == "schema"]
    assert schema_issues and any("mode" in i.detail for i in schema_issues)
    assert not any("validates against schema" in c for c in result.checks)


def test_schema_violation_ignored_when_check_schema_is_false(signed, cov_key_dir):
    """The same report passes when schema checking is switched off."""
    tampered = copy.deepcopy(signed)
    tampered["mode"] = "hallucinated"
    tampered = _resign(tampered, cov_key_dir)

    result = report_mod.verify_report(tampered, check_schema=False)

    assert result.ok, _details(result)


def test_schema_path_raises_when_schema_is_absent(tmp_path, monkeypatch):
    """schema_path refuses to guess when the schema is not in the tree (report.py 348)."""
    monkeypatch.setattr(report_mod, "__file__", str(tmp_path / "pkg" / "report.py"))
    with pytest.raises(FileNotFoundError, match="report.schema.json"):
        report_mod.schema_path()


# ---------------------------------------------------------------------------
# probes.py — LiveProbe file reader
# ---------------------------------------------------------------------------

def test_live_file_probe_reads_and_caches(tmp_path, monkeypatch):
    """LiveProbe reads a real file and serves later reads from cache (probes.py 277-283, 276)."""
    thp = tmp_path / "thp_enabled"
    thp.write_text("always [madvise] never\n", encoding="utf-8")
    monkeypatch.setitem(probes.LIVE_FILES, "thp", str(thp))

    probe = LiveProbe()
    assert probe.has("thp")
    assert probe.text("thp") == "always [madvise] never\n"

    thp.unlink()  # the cached observation must survive the file going away
    assert probe.text("thp") == "always [madvise] never\n"


def test_live_file_probe_missing_file_is_probemissing(tmp_path, monkeypatch):
    """An absent sysfs file yields ProbeMissing, never a guessed value (probes.py 278-279)."""
    monkeypatch.setitem(probes.LIVE_FILES, "thp", str(tmp_path / "nope"))

    probe = LiveProbe()
    assert probe.has("thp") is False
    with pytest.raises(ProbeMissing, match="not available from a live local run"):
        probe.text("thp")


# ---------------------------------------------------------------------------
# probes.py — LiveProbe command reader
# ---------------------------------------------------------------------------

def test_live_command_probe_runs_command_and_caches(monkeypatch):
    """LiveProbe shells out once and reuses the captured stdout (probes.py 284-285, 297-298)."""
    monkeypatch.setitem(probes.LIVE_COMMANDS, "lscpu", ["echo", "Architecture: aarch64"])

    probe = LiveProbe()
    assert probe.has("lscpu")
    assert probe.text("lscpu").strip() == "Architecture: aarch64"

    # swap the command: a second read must come from the cache, not a new run
    monkeypatch.setitem(probes.LIVE_COMMANDS, "lscpu", ["echo", "REPLACED"])
    assert probe.text("lscpu").strip() == "Architecture: aarch64"


def test_live_command_probe_absent_executable(monkeypatch):
    """A missing instrument yields ProbeMissing (probes.py 285-287)."""
    monkeypatch.setitem(probes.LIVE_COMMANDS, "lscpu", ["armsmith-no-such-instrument"])

    probe = LiveProbe()
    assert probe.has("lscpu") is False
    with pytest.raises(ProbeMissing, match="skip, don't guess"):
        probe.text("lscpu")


def test_live_command_probe_nonzero_exit(monkeypatch):
    """A failing instrument is discarded rather than reported (probes.py 295-296)."""
    monkeypatch.setitem(probes.LIVE_COMMANDS, "lscpu", ["false"])

    probe = LiveProbe()
    assert probe.has("lscpu") is False
    with pytest.raises(ProbeMissing):
        probe.text("lscpu")


# ---------------------------------------------------------------------------
# probes.py — LiveProbe accessors
# ---------------------------------------------------------------------------

def test_live_text_rejects_unknown_kind():
    """An unknown probe kind is a ProbeMissing, not a silent None (probes.py 307-308)."""
    probe = LiveProbe()
    with pytest.raises(ProbeMissing, match="unknown probe kind"):
        probe.text("totally_made_up_kind")


def test_live_json_and_raw_serve_captured_observations():
    """json()/raw() decode a harness-captured observation (probes.py 320, 323)."""
    payload = {"model": "q4_0", "tokens_s": 12.5}
    probe = LiveProbe()
    probe.capture("llama_bench", json.dumps(payload))

    assert probe.json("llama_bench") == payload
    assert probe.raw("llama_bench") == json.dumps(payload).encode("utf-8")


def test_live_refused_kinds_never_reach_the_readers():
    """env/proc_maps stay refused — a published report must not carry secrets."""
    probe = LiveProbe()
    for kind in ("env", "proc_maps"):
        assert probe.has(kind) is False
        with pytest.raises(ProbeMissing, match="refused in live mode"):
            probe.text(kind)
