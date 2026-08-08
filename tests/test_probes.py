"""ReplayProbe backend + provenance-label enforcement."""

import json

import pytest

from armsmith.probes import LiveProbe, ProbeMissing, ReplayProbe, load_manifest


def test_manifest_required(tmp_path):
    (tmp_path / "probes").mkdir()
    with pytest.raises(FileNotFoundError, match="unlabeled"):
        ReplayProbe(tmp_path)


def test_manifest_requires_synthetic_flag(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"provenance": "x"}))
    with pytest.raises(ValueError, match="synthetic"):
        load_manifest(tmp_path)


def test_manifest_requires_provenance_note(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"synthetic": True}))
    with pytest.raises(ValueError, match="provenance"):
        load_manifest(tmp_path)


def test_manifest_label_marks_synthetic(scenario_bundle):
    m = load_manifest(scenario_bundle)
    assert m.synthetic is True
    assert "SYNTHETIC" in m.label
    assert m.scenario == "scenario_ragserve"


def test_has_and_text(scenario_bundle):
    probe = ReplayProbe(scenario_bundle)
    assert probe.has("lscpu")
    assert "Neoverse-V2" in probe.text("lscpu")
    assert not probe.has("llama_bench")


def test_unknown_kind_is_not_has(scenario_bundle):
    probe = ReplayProbe(scenario_bundle)
    assert not probe.has("made_up_kind")
    with pytest.raises(ProbeMissing):
        probe.text("made_up_kind")


def test_missing_recorded_kind_raises_probemissing(scenario_bundle):
    probe = ReplayProbe(scenario_bundle)
    with pytest.raises(ProbeMissing, match="not recorded"):
        probe.text("cmake_cache")


def test_json_loading(rule_bundle):
    probe = ReplayProbe(rule_bundle("r06_pos"))
    data = probe.json("env")
    assert data["workers"] == 4 and data["nproc"] == 16


def test_raw_bytes_loading(rule_bundle):
    probe = ReplayProbe(rule_bundle("r05_pos"))
    blob = probe.raw("gguf_header")
    assert blob[:4] == b"GGUF"


def test_repo_dir_and_bench_records(scenario_bundle, rule_bundle):
    probe = ReplayProbe(scenario_bundle)
    assert probe.repo_dir is not None and (probe.repo_dir / "Dockerfile").is_file()
    records = probe.bench_records()
    assert "baseline" in records and "fix_R1" in records

    probe2 = ReplayProbe(rule_bundle("r03_pos"))
    assert probe2.repo_dir is None
    assert probe2.bench_records() == {}


def test_source_label_propagates(scenario_bundle):
    probe = ReplayProbe(scenario_bundle)
    assert probe.source.startswith("replay[SYNTHETIC]")


def test_live_probe_refuses_remote_targets():
    """Local execution is implemented; ssh:// targets still land at S1."""
    with pytest.raises(NotImplementedError, match="S1"):
        LiveProbe("ssh://graviton")


def test_live_probe_refuses_env_and_proc_maps():
    """A published report must never carry CI secrets or host paths."""
    probe = LiveProbe("local")
    for kind, needle in (("env", "secrets"), ("proc_maps", "leak")):
        assert probe.has(kind) is False
        with pytest.raises(ProbeMissing, match=needle):
            probe.text(kind)


def test_live_probe_skips_rather_than_guesses():
    probe = LiveProbe("local")
    with pytest.raises(ProbeMissing, match="S1"):
        probe.text("llama_bench")


def test_live_probe_rejects_unknown_kind():
    probe = LiveProbe("local")
    with pytest.raises(ProbeMissing, match="unknown probe kind"):
        probe.capture("not_a_probe", "text")


def test_live_probe_serves_captured_observations():
    probe = LiveProbe("local")
    probe.capture("objdump_before", "  400: 4e809c02 \tsdot\tv2.4s, v0.16b, v0.4b[0]\n")
    assert probe.has("objdump_before") is True
    assert "sdot" in probe.text("objdump_before")


def test_live_probe_source_is_labeled_live():
    assert LiveProbe("local").source.startswith("live[")
