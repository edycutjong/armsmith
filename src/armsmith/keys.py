"""armsmith.keys — ed25519 keypair management for report signing.

* ``armsmith keys init`` → generates an ed25519 keypair under the key dir
  (default ``~/.armsmith``, override via ``--key-dir`` or ``ARMSMITH_KEY_DIR``).
* Private key: PKCS8 PEM, file mode 0600.  Public key: SubjectPublicKeyInfo PEM.
* TODO(S1): OS-keychain storage and the ``ARMSMITH_KEY`` env injection path
  documented in COMPLEXITY §2; CI uses Sigstore keyless instead (no secrets).
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

__all__ = [
    "default_key_dir",
    "init_keys",
    "load_private_key",
    "load_public_key",
    "public_key_b64",
    "sign",
    "verify",
    "KeyError_",
]

PRIVATE_NAME = "armsmith_ed25519.pem"
PUBLIC_NAME = "armsmith_ed25519.pub.pem"


class KeyError_(RuntimeError):
    """Key management failure (missing/existing keys, bad material)."""


def default_key_dir() -> Path:
    env = os.environ.get("ARMSMITH_KEY_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".armsmith"


def init_keys(key_dir: Path | None = None, force: bool = False) -> tuple[Path, Path]:
    """Generate a new ed25519 keypair. Refuses to overwrite unless force."""
    kd = Path(key_dir) if key_dir else default_key_dir()
    kd.mkdir(parents=True, exist_ok=True)
    priv_path = kd / PRIVATE_NAME
    pub_path = kd / PUBLIC_NAME
    if priv_path.exists() and not force:
        raise KeyError_(f"{priv_path} already exists (use --force to rotate)")

    private = Ed25519PrivateKey.generate()
    priv_bytes = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path.write_bytes(priv_bytes)
    os.chmod(priv_path, 0o600)
    pub_path.write_bytes(pub_bytes)
    os.chmod(pub_path, 0o644)
    return priv_path, pub_path


def load_private_key(key_dir: Path | None = None) -> Ed25519PrivateKey:
    kd = Path(key_dir) if key_dir else default_key_dir()
    priv_path = kd / PRIVATE_NAME
    if not priv_path.is_file():
        raise KeyError_(f"no private key at {priv_path} — run `armsmith keys init`")
    key = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise KeyError_(f"{priv_path} is not an ed25519 private key")
    return key


def load_public_key(key_dir: Path | None = None) -> Ed25519PublicKey:
    kd = Path(key_dir) if key_dir else default_key_dir()
    pub_path = kd / PUBLIC_NAME
    if not pub_path.is_file():
        raise KeyError_(f"no public key at {pub_path} — run `armsmith keys init`")
    key = serialization.load_pem_public_key(pub_path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise KeyError_(f"{pub_path} is not an ed25519 public key")
    return key


def public_key_b64(pub: Ed25519PublicKey) -> str:
    import base64

    raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def public_key_from_b64(b64: str) -> Ed25519PublicKey:
    import base64

    raw = base64.b64decode(b64.encode("ascii"))
    return Ed25519PublicKey.from_public_bytes(raw)


def sign(private: Ed25519PrivateKey, data: bytes) -> bytes:
    return private.sign(data)


def verify(public: Ed25519PublicKey, signature: bytes, data: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature

    try:
        public.verify(signature, data)
        return True
    except InvalidSignature:
        return False
