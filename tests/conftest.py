"""Shared test helpers. All fixture data is synthetic (see fixtures/*/manifest.json)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture()
def rule_bundle():
    def _bundle(name: str) -> Path:
        path = FIXTURES / "rules" / name
        assert path.is_dir(), f"missing fixture bundle {name}"
        return path
    return _bundle


@pytest.fixture()
def scenario_bundle() -> Path:
    return FIXTURES / "replays" / "scenario_ragserve"


@pytest.fixture()
def key_dir(tmp_path: Path) -> Path:
    """Fresh key dir with a generated keypair."""
    from armsmith.keys import init_keys

    kd = tmp_path / "keys"
    init_keys(key_dir=kd)
    return kd
