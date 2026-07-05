"""armsmith.probes — instrument/probe abstraction + the REPLAY backend.

Runtime detectors (R2, R3, R5, R6, R7, R8, R9, R10, R11, R13) never shell out
directly.  They ask a :class:`Probe` for a named observation ("lscpu",
"numpy_show_config", "llama_bench", ...).  Two backends are planned:

* **ReplayProbe (this phase)** — reads recorded observations from a replay
  bundle directory.  Bundles are REQUIRED to carry a ``manifest.json`` with
  ``"synthetic": true|false`` and a provenance note; the loader refuses
  bundles that don't declare provenance.  Everything shipped in this repo's
  ``fixtures/replays/`` is synthetic, hand-authored, and labeled as such —
  no fabricated hardware claims.
* **LiveProbe (TODO(S1))** — will execute the real instruments (lscpu, perf,
  llama-bench, hyperfine, pip, cmake) on Arm hardware over SSH.  Not
  implemented here: this machine is not an Arm/Linux target and Armsmith
  never invents measurements.

Replay bundle layout::

    <bundle>/
      manifest.json          # {"synthetic": true, "provenance": "...", ...}
      repo/                  # optional mini-repo for static detectors
      probes/
        lscpu.txt
        numpy_show_config.txt
        env.json
        ort_session.json
        pip_install_log.txt
        perf_report.txt
        build_log.txt
        cmake_cache.txt
        thp.txt
        proc_maps.txt
        gguf_header.bin
        llama_bench.json
        hyperfine.json
        objdump_before.txt / objdump_after.txt   # ISA-witness
      bench/
        baseline.json fix_R3.json ...            # gate measurement records
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "Probe",
    "ProbeMissing",
    "ReplayProbe",
    "LiveProbe",
    "ReplayManifest",
    "load_manifest",
    "PROBE_KINDS",
]

#: Known probe kinds → the file that backs them in a replay bundle.
PROBE_KINDS: dict[str, str] = {
    "lscpu": "lscpu.txt",
    "numpy_show_config": "numpy_show_config.txt",
    "env": "env.json",
    "ort_session": "ort_session.json",
    "pip_install_log": "pip_install_log.txt",
    "perf_report": "perf_report.txt",
    "build_log": "build_log.txt",
    "cmake_cache": "cmake_cache.txt",
    "thp": "thp.txt",
    "proc_maps": "proc_maps.txt",
    "gguf_header": "gguf_header.bin",
    "llama_bench": "llama_bench.json",
    "hyperfine": "hyperfine.json",
    "objdump_before": "objdump_before.txt",
    "objdump_after": "objdump_after.txt",
}


class ProbeMissing(KeyError):
    """Requested observation is not available from this probe backend."""


class Probe(ABC):
    """Read-only access to recorded or live system observations."""

    @abstractmethod
    def has(self, kind: str) -> bool: ...

    @abstractmethod
    def text(self, kind: str) -> str: ...

    @abstractmethod
    def json(self, kind: str) -> Any: ...

    @abstractmethod
    def raw(self, kind: str) -> bytes: ...

    @property
    @abstractmethod
    def source(self) -> str:
        """Human-readable provenance label, embedded in reports."""


@dataclass(frozen=True)
class ReplayManifest:
    synthetic: bool
    provenance: str
    scenario: str
    host: dict[str, Any]
    extra: dict[str, Any]

    @property
    def label(self) -> str:
        kind = "SYNTHETIC" if self.synthetic else "recorded"
        return f"replay[{kind}]: {self.scenario}"


def load_manifest(bundle_dir: Path) -> ReplayManifest:
    """Load and validate a replay bundle manifest.

    Refuses bundles without a manifest or without an explicit ``synthetic``
    provenance flag — unlabeled measurement data must never enter a report.
    """
    path = Path(bundle_dir) / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"replay bundle {bundle_dir} has no manifest.json — refusing to "
            "load unlabeled measurement data"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if "synthetic" not in data or not isinstance(data["synthetic"], bool):
        raise ValueError(
            f"{path}: manifest must declare boolean 'synthetic' provenance flag"
        )
    if not data.get("provenance"):
        raise ValueError(f"{path}: manifest must carry a non-empty 'provenance' note")
    return ReplayManifest(
        synthetic=data["synthetic"],
        provenance=str(data["provenance"]),
        scenario=str(data.get("scenario", Path(bundle_dir).name)),
        host=dict(data.get("host", {})),
        extra={
            k: v
            for k, v in data.items()
            if k not in {"synthetic", "provenance", "scenario", "host"}
        },
    )


class ReplayProbe(Probe):
    """Probe backend that replays recorded observations from a bundle dir."""

    def __init__(self, bundle_dir: Path | str):
        self.bundle_dir = Path(bundle_dir)
        self.manifest = load_manifest(self.bundle_dir)
        self.probes_dir = self.bundle_dir / "probes"

    def _path(self, kind: str) -> Path:
        if kind not in PROBE_KINDS:
            raise ProbeMissing(f"unknown probe kind {kind!r}")
        return self.probes_dir / PROBE_KINDS[kind]

    def has(self, kind: str) -> bool:
        try:
            return self._path(kind).is_file()
        except ProbeMissing:
            return False

    def _require(self, kind: str) -> Path:
        p = self._path(kind)
        if not p.is_file():
            raise ProbeMissing(
                f"probe {kind!r} not recorded in bundle {self.bundle_dir.name} "
                f"(expected {p.name})"
            )
        return p

    def text(self, kind: str) -> str:
        return self._require(kind).read_text(encoding="utf-8")

    def json(self, kind: str) -> Any:
        return json.loads(self._require(kind).read_text(encoding="utf-8"))

    def raw(self, kind: str) -> bytes:
        return self._require(kind).read_bytes()

    @property
    def source(self) -> str:
        return self.manifest.label

    @property
    def repo_dir(self) -> Path | None:
        """Mini-repo shipped inside the bundle for static detectors, if any."""
        repo = self.bundle_dir / "repo"
        return repo if repo.is_dir() else None

    def bench_records(self) -> dict[str, Path]:
        """Map variant name → measurement record path (bench/*.json)."""
        bench = self.bundle_dir / "bench"
        if not bench.is_dir():
            return {}
        return {p.stem: p for p in sorted(bench.glob("*.json"))}


class LiveProbe(Probe):
    """Executes real instruments on Arm hardware. NOT implemented in Phase 1.

    TODO(S1): implement against a Graviton target (local exec + ssh://),
    shelling out to lscpu / perf / llama-bench / hyperfine / pip / cmake and
    recording raw outputs so every live run can be replayed later
    (scripts/record_replays.sh writes the same bundle layout ReplayProbe reads).
    """

    def __init__(self, target: str = "local"):
        raise NotImplementedError(
            "LiveProbe requires Arm/Linux hardware and lands at S1 — "
            "use ReplayProbe with a recorded bundle (armsmith diagnose --replay)"
        )

    def has(self, kind: str) -> bool:  # pragma: no cover - unreachable
        raise NotImplementedError

    def text(self, kind: str) -> str:  # pragma: no cover - unreachable
        raise NotImplementedError

    def json(self, kind: str) -> Any:  # pragma: no cover - unreachable
        raise NotImplementedError

    def raw(self, kind: str) -> bytes:  # pragma: no cover - unreachable
        raise NotImplementedError

    @property
    def source(self) -> str:  # pragma: no cover - unreachable
        raise NotImplementedError
