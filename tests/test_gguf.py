"""Minimal GGUF header reader + stub builder."""

import pytest

from armsmith.gguf import GgufError, build_stub, read_header


def test_roundtrip_q4_k_m():
    header = read_header(build_stub(file_type=15))
    assert header.file_type_code == 15
    assert header.file_type_name == "Q4_K_M"
    assert header.architecture == "llama"
    assert header.metadata["armsmith.synthetic_fixture"] == 1  # provenance marker


def test_roundtrip_q4_0():
    header = read_header(build_stub(file_type=2))
    assert header.file_type_name == "Q4_0"


def test_unknown_file_type_labeled():
    header = read_header(build_stub(file_type=999))
    assert header.file_type_name == "unknown(999)"


def test_bad_magic_rejected():
    with pytest.raises(GgufError, match="magic"):
        read_header(b"NOPE" + b"\x00" * 32)


def test_truncated_rejected():
    blob = build_stub(file_type=2)
    with pytest.raises(GgufError, match="truncated"):
        read_header(blob[: len(blob) // 2])


def test_unsupported_version_rejected():
    blob = bytearray(build_stub(file_type=2))
    blob[4:8] = (1).to_bytes(4, "little")  # version 1
    with pytest.raises(GgufError, match="version"):
        read_header(bytes(blob))


def test_fixture_bins_match_generator(fixtures_dir):
    pos = (fixtures_dir / "rules" / "r05_pos" / "probes" / "gguf_header.bin").read_bytes()
    assert read_header(pos).file_type_name == "Q4_K_M"
    neg = (fixtures_dir / "rules" / "r05_neg" / "probes" / "gguf_header.bin").read_bytes()
    assert read_header(neg).file_type_name == "Q4_0"
