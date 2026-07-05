"""Rule pack loader + descriptor validation."""

import pytest

from armsmith.rules import DETECTORS, load_pack


def test_loads_all_13_rules_in_order():
    specs = load_pack()
    assert list(specs) == [f"R{i}" for i in range(1, 14)]


def test_every_rule_has_registered_detector():
    specs = load_pack()
    for rid in specs:
        assert rid in DETECTORS


def test_descriptor_fields_complete_and_typed():
    for spec in load_pack().values():
        assert spec.title and spec.summary and spec.fix_generator and spec.gain_note
        assert spec.kind in ("static", "probe", "hybrid")
        assert spec.confidence in ("high", "medium", "low")
        lo, hi = spec.expected_gain_range
        assert 0 < lo <= hi
        assert spec.citation_url.startswith("https://")


def test_static_rules_declare_no_probes():
    specs = load_pack()
    for rid in ("R1", "R4", "R12"):
        assert specs[rid].kind == "static"
        assert specs[rid].requires == ()


def test_probe_rules_declare_their_probe_kinds():
    specs = load_pack()
    assert specs["R3"].requires == ("numpy_show_config",)
    assert set(specs["R5"].requires) == {"gguf_header", "lscpu"}
    assert set(specs["R13"].requires) == {"llama_bench", "hyperfine"}
    assert set(specs["R2"].requires) == {"build_log", "lscpu"}


def test_r13_exists_with_two_instrument_summary():
    spec = load_pack()["R13"]
    assert "llama-bench" in spec.summary
    assert "hyperfine" in spec.summary


def test_gain_notes_label_estimates_for_gain_ranges_above_1x():
    # Every rule that projects a speedup must label the range as an estimate;
    # diagnostic/ecosystem rules (R12, R13) pin the range to 1.0x instead.
    for spec in load_pack().values():
        lo, hi = spec.expected_gain_range
        if hi > 1.0:
            assert "ESTIMATE" in spec.gain_note.upper(), spec.id
        else:
            assert spec.id in ("R12", "R13")


def test_bad_pack_dir_missing_fields(tmp_path):
    (tmp_path / "bad.yaml").write_text("id: RX\ntitle: no other fields\n")
    with pytest.raises(ValueError, match="missing required fields"):
        load_pack(pack_dir=tmp_path, require_detectors=False)


def test_bad_gain_range_rejected(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        "id: RX\ntitle: t\nkind: static\nsummary: s\nfix_generator: f\n"
        "expected_gain_range: [2.0, 1.0]\ngain_note: g\n"
        "citation_url: https://example.com\nconfidence: high\n"
    )
    with pytest.raises(ValueError, match="expected_gain_range"):
        load_pack(pack_dir=tmp_path, require_detectors=False)


def test_non_https_citation_rejected(tmp_path):
    (tmp_path / "bad.yaml").write_text(
        "id: RX\ntitle: t\nkind: static\nsummary: s\nfix_generator: f\n"
        "expected_gain_range: [1.0, 2.0]\ngain_note: g\n"
        "citation_url: http://example.com\nconfidence: high\n"
    )
    with pytest.raises(ValueError, match="https"):
        load_pack(pack_dir=tmp_path, require_detectors=False)
