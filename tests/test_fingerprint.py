"""Host fingerprint parsing from recorded lscpu fixtures."""

from armsmith.fingerprint import _features_from_flags, capture_fingerprint, parse_lscpu
from armsmith.probes import ReplayProbe


def _kv(fixtures_dir, name):
    return parse_lscpu((fixtures_dir / "hosts" / name).read_text())


def test_parse_lscpu_key_values(fixtures_dir):
    kv = _kv(fixtures_dir, "lscpu_neoverse_v2.txt")
    assert kv["Architecture"] == "aarch64"
    assert kv["Model name"] == "Neoverse-V2"
    assert kv["CPU(s)"] == "16"


def test_v2_features_all_present(fixtures_dir):
    kv = _kv(fixtures_dir, "lscpu_neoverse_v2.txt")
    isa = _features_from_flags(kv["Flags"].split())
    assert isa.dotprod and isa.i8mm and isa.sve and isa.sve2 and isa.bf16
    assert not isa.sme
    assert isa.present() == ["dotprod", "i8mm", "sve", "sve2", "bf16"]


def test_n1_dotprod_only(fixtures_dir):
    kv = _kv(fixtures_dir, "lscpu_neoverse_n1.txt")
    isa = _features_from_flags(kv["Flags"].split())
    assert isa.dotprod
    assert not (isa.i8mm or isa.sve or isa.sve2 or isa.bf16 or isa.sme)


def test_a53_no_features(fixtures_dir):
    kv = _kv(fixtures_dir, "lscpu_cortex_a53.txt")
    isa = _features_from_flags(kv["Flags"].split())
    assert isa.present() == []


def test_sve2_implies_sve_normalization():
    isa = _features_from_flags(["sve2"])  # hand-edited fixture defense
    assert isa.sve and isa.sve2


def test_capture_fingerprint_from_bundle(scenario_bundle):
    probe = ReplayProbe(scenario_bundle)
    fp = capture_fingerprint(probe, probe.manifest.host)
    assert fp.model_name == "Neoverse-V2"
    assert fp.cpus == 16
    assert fp.instance == "synthetic-c8g.4xlarge"
    assert fp.kernel == "6.8.0-synthetic"
    assert fp.source.startswith("replay[SYNTHETIC]")
    d = fp.to_dict()
    assert d["isa_feats"] == ["dotprod", "i8mm", "sve", "sve2", "bf16"]
    assert d["source"] == fp.source


def test_capture_fingerprint_defaults_without_meta(scenario_bundle):
    probe = ReplayProbe(scenario_bundle)
    fp = capture_fingerprint(probe)  # no host meta supplied
    assert fp.instance == "unknown" and fp.kernel == "unknown"
