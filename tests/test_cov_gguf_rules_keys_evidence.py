"""Error / fallback / malformed-input paths of gguf, rules loader, keys, evidence.

Every test here drives a real public entry point (``read_header``,
``load_pack``, ``load_private_key``, ``render_fix_table`` …) and asserts the
concrete observable result: the raised exception message, the fallback value,
or the rendered markdown cell. Nothing under ``src/`` is touched; key material
is always written into ``tmp_path``.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
import yaml

from armsmith import evidence, gguf, keys
from armsmith.rules import load_pack

# ---------------------------------------------------------------------------
# gguf helpers
# ---------------------------------------------------------------------------


def _gguf_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<Q", len(b)) + b


def _gguf_head(n_kv: int, version: int = 3, n_tensors: int = 0) -> bytes:
    return (
        gguf.MAGIC
        + struct.pack("<I", version)
        + struct.pack("<Q", n_tensors)
        + struct.pack("<Q", n_kv)
    )


# ---------------------------------------------------------------------------
# gguf.py
# ---------------------------------------------------------------------------


def test_file_type_name_is_none_without_file_type_kv():
    """No general.file_type KV → file_type_code/name fall back to None (not a crash)."""
    header = gguf.GgufHeader(version=3, n_tensors=0, n_kv=1, metadata={"general.architecture": "llama"})
    assert header.file_type_code is None
    assert header.file_type_name is None
    assert header.architecture == "llama"


def test_read_header_parses_array_metadata_value():
    """A T_ARRAY KV is read element-wise and returned as a Python list."""
    data = (
        _gguf_head(1)
        + _gguf_str("tokenizer.ggml.token_type")
        + struct.pack("<I", gguf.T_ARRAY)
        + struct.pack("<I", gguf.T_UINT32)  # element type
        + struct.pack("<Q", 3)  # count
        + struct.pack("<I", 7)
        + struct.pack("<I", 8)
        + struct.pack("<I", 9)
    )
    header = gguf.read_header(data)
    assert header.metadata["tokenizer.ggml.token_type"] == [7, 8, 9]
    assert header.n_kv == 1


def test_read_header_nested_array_of_strings():
    """Arrays recurse: an array of strings decodes to a list of str."""
    data = (
        _gguf_head(1)
        + _gguf_str("general.tags")
        + struct.pack("<I", gguf.T_ARRAY)
        + struct.pack("<I", gguf.T_STRING)
        + struct.pack("<Q", 2)
        + _gguf_str("alpha")
        + _gguf_str("beta")
    )
    assert gguf.read_header(data).metadata["general.tags"] == ["alpha", "beta"]


def test_read_header_rejects_unknown_value_type():
    """An out-of-range GGUF value type is rejected, not silently skipped."""
    data = _gguf_head(1) + _gguf_str("bogus.kv") + struct.pack("<I", 99)
    with pytest.raises(gguf.GgufError, match="unknown GGUF value type 99"):
        gguf.read_header(data)


def test_read_header_refuses_absurd_kv_count():
    """n_kv above max_kv is refused before any allocation-driven parse loop."""
    data = _gguf_head(5000)
    with pytest.raises(gguf.GgufError, match=r"refusing to parse 5000 KVs \(max 64"):
        gguf.read_header(data)
    # explicit max_kv is honoured too
    with pytest.raises(gguf.GgufError, match=r"max 2 "):
        gguf.read_header(_gguf_head(3), max_kv=2)


def test_read_header_file_reads_from_disk(tmp_path: Path):
    """read_header_file slices the file and parses the same header as read_header."""
    blob = gguf.build_stub(file_type=15, architecture="llama")
    path = tmp_path / "model.gguf"
    path.write_bytes(blob + b"\xff" * 1024)  # trailing junk must be ignored
    header = gguf.read_header_file(path)
    assert header.file_type_code == 15
    assert header.file_type_name == "Q4_K_M"
    assert header.architecture == "llama"
    assert header.metadata["armsmith.synthetic_fixture"] == 1


def test_build_stub_with_extra_meta_roundtrips():
    """extra_meta entries are appended as uint32 KVs and survive a re-read."""
    blob = gguf.build_stub(file_type=2, extra_meta={"armsmith.repack_hint": 4, "llama.block_count": 32})
    header = gguf.read_header(blob)
    assert header.n_kv == 5
    assert header.metadata["armsmith.repack_hint"] == 4
    assert header.metadata["llama.block_count"] == 32
    assert header.file_type_code == gguf.Q4_0_CODE
    assert header.file_type_name == "Q4_0"


def test_unknown_file_type_code_is_labelled_not_dropped():
    """An ftype outside FILE_TYPE_NAMES renders as unknown(<code>)."""
    header = gguf.read_header(gguf.build_stub(file_type=99))
    assert header.file_type_name == "unknown(99)"


# ---------------------------------------------------------------------------
# rules/__init__.py
# ---------------------------------------------------------------------------

_GOOD_DESCRIPTOR = {
    "id": "R1",
    "title": "test rule",
    "kind": "static",
    "summary": "s",
    "fix_generator": "f",
    "expected_gain_range": [1.1, 2.0],
    "gain_note": "ESTIMATE",
    "citation_url": "https://example.invalid/doc",
    "confidence": "high",
}


def _write_descriptor(pack_dir: Path, name: str, **overrides) -> Path:
    data = dict(_GOOD_DESCRIPTOR)
    data.update(overrides)
    path = pack_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture()
def pack_dir(tmp_path: Path) -> Path:
    d = tmp_path / "packs"
    d.mkdir()
    return d


def test_descriptor_must_be_a_mapping(pack_dir: Path):
    """A YAML list (not a mapping) is rejected with the offending path."""
    (pack_dir / "bad.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="descriptor must be a mapping"):
        load_pack(pack_dir, require_detectors=False)


def test_descriptor_rejects_unknown_kind(pack_dir: Path):
    """kind outside static/probe/hybrid is rejected."""
    _write_descriptor(pack_dir, "r.yaml", kind="dynamic")
    with pytest.raises(ValueError, match=r"kind must be one of \['hybrid', 'probe', 'static'\]"):
        load_pack(pack_dir, require_detectors=False)


def test_descriptor_rejects_unknown_confidence(pack_dir: Path):
    """confidence must be one of the ranked levels."""
    _write_descriptor(pack_dir, "r.yaml", confidence="certain")
    with pytest.raises(ValueError, match="confidence must be one of"):
        load_pack(pack_dir, require_detectors=False)


def test_descriptor_rejects_non_https_learning_path(pack_dir: Path):
    """An http:// learning_path is refused (citations must be https)."""
    _write_descriptor(pack_dir, "r.yaml", learning_path="http://learn.arm.com/insecure")
    with pytest.raises(ValueError, match="learning_path must be https when present"):
        load_pack(pack_dir, require_detectors=False)


def test_descriptor_accepts_https_learning_path(pack_dir: Path):
    """The happy path keeps learning_path on the parsed spec."""
    _write_descriptor(pack_dir, "r.yaml", learning_path="https://learn.arm.com/lp")
    specs = load_pack(pack_dir, require_detectors=False)
    assert specs["R1"].learning_path == "https://learn.arm.com/lp"


def test_duplicate_rule_id_is_rejected(pack_dir: Path):
    """Two descriptors claiming the same id abort the load."""
    _write_descriptor(pack_dir, "a.yaml", id="R7")
    _write_descriptor(pack_dir, "b.yaml", id="R7", title="clashing twin")
    with pytest.raises(ValueError, match="duplicate rule id R7"):
        load_pack(pack_dir, require_detectors=False)


def test_rule_without_detector_is_rejected(pack_dir: Path):
    """require_detectors=True fails when a descriptor has no registered detector."""
    _write_descriptor(pack_dir, "r99.yaml", id="R99")
    with pytest.raises(ValueError, match=r"rules without detectors: \['R99'\]"):
        load_pack(pack_dir, require_detectors=True)


def test_detector_without_descriptor_is_rejected(pack_dir: Path):
    """A pack covering only R1 leaves the other registered detectors unspecced."""
    _write_descriptor(pack_dir, "r01.yaml", id="R1")
    with pytest.raises(ValueError, match="detectors without descriptors") as exc:
        load_pack(pack_dir, require_detectors=True)
    assert "R13" in str(exc.value)
    assert "R1'" not in str(exc.value)


# ---------------------------------------------------------------------------
# keys.py
# ---------------------------------------------------------------------------


def _ec_private_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _ec_public_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def test_default_key_dir_honours_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """ARMSMITH_KEY_DIR wins and is user-expanded."""
    monkeypatch.setenv("ARMSMITH_KEY_DIR", str(tmp_path / "kd"))
    assert keys.default_key_dir() == tmp_path / "kd"
    monkeypatch.setenv("ARMSMITH_KEY_DIR", "~/somewhere-else")
    assert keys.default_key_dir() == Path.home() / "somewhere-else"


def test_default_key_dir_falls_back_to_home(monkeypatch: pytest.MonkeyPatch):
    """Without the env var the default is ~/.armsmith (path only — nothing written)."""
    monkeypatch.delenv("ARMSMITH_KEY_DIR", raising=False)
    assert keys.default_key_dir() == Path.home() / ".armsmith"


def test_default_key_dir_ignores_empty_env(monkeypatch: pytest.MonkeyPatch):
    """An empty ARMSMITH_KEY_DIR is treated as unset, not as the cwd."""
    monkeypatch.setenv("ARMSMITH_KEY_DIR", "")
    assert keys.default_key_dir() == Path.home() / ".armsmith"


def test_load_private_key_rejects_non_ed25519(tmp_path: Path):
    """A valid-but-wrong-algorithm PEM is rejected rather than used for signing."""
    kd = tmp_path / "keys"
    kd.mkdir()
    priv = kd / keys.PRIVATE_NAME
    priv.write_bytes(_ec_private_pem())
    with pytest.raises(keys.KeyError_, match="is not an ed25519 private key"):
        keys.load_private_key(key_dir=kd)


def test_load_public_key_missing_file(tmp_path: Path):
    """A missing public key points the user at `armsmith keys init`."""
    kd = tmp_path / "keys"
    kd.mkdir()
    with pytest.raises(keys.KeyError_, match=r"no public key at .*armsmith keys init"):
        keys.load_public_key(key_dir=kd)


def test_load_public_key_rejects_non_ed25519(tmp_path: Path):
    """An EC public key is refused by the ed25519-only loader."""
    kd = tmp_path / "keys"
    kd.mkdir()
    (kd / keys.PUBLIC_NAME).write_bytes(_ec_public_pem())
    with pytest.raises(keys.KeyError_, match="is not an ed25519 public key"):
        keys.load_public_key(key_dir=kd)


def test_init_and_load_roundtrip_in_tmp_dir(tmp_path: Path):
    """Sanity anchor for the negative tests: the real keypair still loads and verifies."""
    kd = tmp_path / "keys"
    priv_path, pub_path = keys.init_keys(key_dir=kd)
    assert priv_path.is_file() and pub_path.is_file()
    private = keys.load_private_key(key_dir=kd)
    public = keys.load_public_key(key_dir=kd)
    sig = keys.sign(private, b"payload")
    assert keys.verify(public, sig, b"payload") is True
    assert keys.verify(public, sig, b"tampered") is False


# ---------------------------------------------------------------------------
# evidence.py
# ---------------------------------------------------------------------------


def test_fix_table_renders_em_dashes_for_missing_values():
    """Missing medians/delta/band all degrade to the em-dash cell, not to a crash."""
    table = evidence.render_fix_table(
        {
            "comparisons": {
                "latency_ms": {"baseline": {}, "candidate": None, "delta": None, "band": None},
            }
        }
    )
    row = [line for line in table.splitlines() if line.startswith("| latency_ms")][0]
    assert row == "| latency_ms | — | — | — | — | — |"


def test_fix_table_formats_non_float_medians_as_plain_str():
    """An int median goes through str() (no float formatting), and the delta arrow is set."""
    table = evidence.render_fix_table(
        {
            "comparisons": {
                "allocs": {
                    "baseline": {"median": 12},
                    "candidate": {"median": 9},
                    "delta": -3.0,
                    "delta_pct": -25.0,
                    "band": 0.5,
                    "verdict": "improved",
                }
            }
        }
    )
    row = [line for line in table.splitlines() if line.startswith("| allocs")][0]
    assert row == "| allocs | 12 | 9 | ↓ -3 (-25.0%) | ±0.5 (outside band) | — |"


def test_fix_table_skips_empty_and_pctless_pmu_entries():
    """PMU counters that are empty or carry no delta_pct are dropped from the cell."""
    fix = {
        "pmu_delta": {
            "ipc": {},                       # falsy entry → skipped
            "cache_miss_pct": {"delta_pct": None},  # no pct → skipped
            "cycles": {"delta_pct": -4.25},
        },
        "comparisons": {
            "wall_s": {
                "baseline": {"median": 1.0},
                "candidate": {"median": 0.9},
                "delta": -0.1,
                "delta_pct": -10.0,
                "band": 0.02,
                "verdict": "improved",
            }
        },
    }
    table = evidence.render_fix_table(fix)
    assert "cycles -4.2%" in table
    assert "ipc" not in table
    assert "cache_miss_pct" not in table


def test_fix_table_with_no_shared_metrics():
    """A fix with zero comparisons still renders a well-formed table with a placeholder row."""
    table = evidence.render_fix_table({})
    lines = table.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("| metric | before | after |")
    assert lines[2] == "| _no shared metrics_ | — | — | — | — | — |"


def test_band_cell_marks_in_band_deltas_as_inside():
    """A no-change verdict is labelled 'inside band' — in-band deltas are never wins."""
    table = evidence.render_fix_table(
        {
            "comparisons": {
                "wall_s": {
                    "baseline": {"median": 1.0},
                    "candidate": {"median": 1.0},
                    "delta": 0.0,
                    "band": 0.05,
                    "verdict": "no_change",
                }
            }
        }
    )
    assert "→ +0" in table
    assert "±0.05 (inside band)" in table


def test_render_markdown_carries_missing_value_cells_into_the_pr_body():
    """End-to-end: the degraded cells survive into the rendered PR body."""
    body = evidence.render_markdown(
        {
            "scenario": "ragserve",
            "mode": "replay",
            "fixes": [
                {
                    "variant": "v1",
                    "verdict": "keep",
                    "comparisons": {"latency_ms": {"delta": None, "band": None}},
                }
            ],
        }
    )
    assert "| latency_ms | — | — | — | — | — |" in body
    assert "REPLAY MODE — SYNTHETIC DATA" in body
