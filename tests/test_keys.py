"""ed25519 key management."""

import stat

import pytest

from armsmith import keys as keys_mod


def test_init_creates_keypair_with_0600_private(tmp_path):
    priv, pub = keys_mod.init_keys(key_dir=tmp_path / "k")
    assert priv.is_file() and pub.is_file()
    mode = stat.S_IMODE(priv.stat().st_mode)
    assert mode == 0o600


def test_init_refuses_overwrite_without_force(tmp_path):
    kd = tmp_path / "k"
    keys_mod.init_keys(key_dir=kd)
    with pytest.raises(keys_mod.KeyError_, match="--force"):
        keys_mod.init_keys(key_dir=kd)


def test_init_force_rotates(tmp_path):
    kd = tmp_path / "k"
    _, pub1 = keys_mod.init_keys(key_dir=kd)
    old = pub1.read_bytes()
    keys_mod.init_keys(key_dir=kd, force=True)
    assert pub1.read_bytes() != old


def test_sign_verify_roundtrip(tmp_path):
    kd = tmp_path / "k"
    keys_mod.init_keys(key_dir=kd)
    priv = keys_mod.load_private_key(kd)
    pub = keys_mod.load_public_key(kd)
    sig = keys_mod.sign(priv, b"payload")
    assert keys_mod.verify(pub, sig, b"payload")
    assert not keys_mod.verify(pub, sig, b"payload-tampered")


def test_pubkey_b64_roundtrip(tmp_path):
    kd = tmp_path / "k"
    keys_mod.init_keys(key_dir=kd)
    priv = keys_mod.load_private_key(kd)
    b64 = keys_mod.public_key_b64(priv.public_key())
    restored = keys_mod.public_key_from_b64(b64)
    sig = keys_mod.sign(priv, b"x")
    assert keys_mod.verify(restored, sig, b"x")


def test_load_missing_keys_message(tmp_path):
    with pytest.raises(keys_mod.KeyError_, match="armsmith keys init"):
        keys_mod.load_private_key(tmp_path / "nope")
