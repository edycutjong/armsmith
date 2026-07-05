"""Report model: canonical hashing, ed25519 sign/verify, tamper evidence, schema."""

import copy
import json

import pytest

from armsmith import keys as keys_mod
from armsmith import report as report_mod
from armsmith.diagnose import run_replay_diagnosis


@pytest.fixture(scope="module")
def signed_report(tmp_path_factory):
    kd = tmp_path_factory.mktemp("keys")
    keys_mod.init_keys(key_dir=kd)
    fixtures = __import__("tests.conftest", fromlist=["FIXTURES"]).FIXTURES
    result = run_replay_diagnosis(fixtures / "replays" / "scenario_ragserve", key_dir=kd, sign=True)
    assert result.signed
    return result.report


def test_canonical_bytes_stable_ordering():
    a = report_mod.canonical_bytes({"b": 1, "a": [2, 3]})
    b = report_mod.canonical_bytes({"a": [2, 3], "b": 1})
    assert a == b == b'{"a":[2,3],"b":1}'


def test_canonical_bytes_rejects_nan():
    with pytest.raises(ValueError):
        report_mod.canonical_bytes({"x": float("nan")})


def test_content_hash_is_sha256_hex():
    h = report_mod.content_hash({"x": 1})
    assert len(h) == 64 and int(h, 16) >= 0


def test_signature_block_shape(signed_report):
    sig = signed_report["signature"]
    assert sig["algorithm"] == "ed25519"
    assert len(sig["report_sha256"]) == 64
    assert sig["signature_b64"] and sig["public_key_b64"]


def test_verify_ok_full_chain(signed_report):
    result = report_mod.verify_report(signed_report)
    assert result.ok, [i.detail for i in result.issues]
    assert any("signature OK" in c for c in result.checks)
    assert any("recompute exactly" in c for c in result.checks)
    assert any("schema" in c for c in result.checks)


def test_verify_detects_metric_tamper(signed_report):
    tampered = copy.deepcopy(signed_report)
    # inflate a claimed median without touching raw samples
    fix0 = tampered["fixes"][0]
    metric = next(iter(fix0["measurement"]["metrics_summary"]))
    fix0["measurement"]["metrics_summary"][metric]["median"] *= 0.5
    result = report_mod.verify_report(tampered)
    assert not result.ok
    kinds = {i.kind for i in result.issues}
    assert "recompute" in kinds and "hash" in kinds  # both layers catch it


def test_verify_detects_verdict_tamper(signed_report):
    tampered = copy.deepcopy(signed_report)
    dropped = [f for f in tampered["fixes"] if f["verdict"] == "drop"]
    assert dropped, "scenario must include a dropped fix"
    dropped[0]["verdict"] = "keep"  # promote a dropped fix
    result = report_mod.verify_report(tampered)
    assert not result.ok


def test_verify_detects_comparison_verdict_tamper(signed_report):
    tampered = copy.deepcopy(signed_report)
    for fx in tampered["fixes"]:
        for metric, cmp in fx["comparisons"].items():
            if cmp["verdict"] == "no_change":
                cmp["verdict"] = "improved"
                break
    result = report_mod.verify_report(tampered)
    assert not result.ok
    assert any("recomputes to" in i.detail for i in result.issues)


def test_verify_detects_body_bit_flip(signed_report):
    tampered = copy.deepcopy(signed_report)
    tampered["scenario"] = tampered["scenario"] + "-evil"
    result = report_mod.verify_report(tampered)
    assert not result.ok
    assert any(i.kind == "hash" for i in result.issues)


def test_verify_rejects_wrong_trusted_key(signed_report):
    result = report_mod.verify_report(signed_report, trusted_public_key_b64="QQ==")
    assert not result.ok
    assert any("does not match the trusted key" in i.detail for i in result.issues)


def test_verify_accepts_matching_trusted_key(signed_report):
    pub = signed_report["signature"]["public_key_b64"]
    result = report_mod.verify_report(signed_report, trusted_public_key_b64=pub)
    assert result.ok


def test_unsigned_report_fails_verify(signed_report):
    unsigned = {k: v for k, v in signed_report.items() if k != "signature"}
    result = report_mod.verify_report(unsigned)
    assert not result.ok
    assert any("no signature block" in i.detail for i in result.issues)


def test_schema_flags_bad_mode(signed_report):
    bad = copy.deepcopy(signed_report)
    bad["mode"] = "hallucinated"
    issues = report_mod.validate_schema(bad)
    assert any("mode" in i for i in issues)


def test_schema_flags_missing_required():
    issues = report_mod.validate_schema({"schema_version": "1.0.0"})
    assert issues


def test_write_and_load_roundtrip(tmp_path, signed_report):
    p = report_mod.write_report(signed_report, tmp_path / "r.json")
    loaded = report_mod.load_report(p)
    assert loaded == signed_report
    assert report_mod.verify_report(loaded).ok


def test_report_is_strict_json(signed_report):
    # json.dumps with allow_nan=False must succeed → no inf/nan anywhere
    json.dumps(signed_report, allow_nan=False)


def test_build_report_rejects_bad_mode():
    with pytest.raises(ValueError, match="replay|live"):
        report_mod.build_report(
            mode="fake", scenario="s", repo={}, host=None,
            findings=[], outcome=None,
        )
