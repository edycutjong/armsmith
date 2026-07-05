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


def test_live_probe_refuses_in_phase1():
    with pytest.raises(NotImplementedError, match="S1"):
        LiveProbe()
