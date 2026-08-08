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
* **LiveProbe** — executes real instruments on the Arm host it runs on.
  Implemented for what can be observed honestly today (``lscpu``, THP state,
  plus harness-captured disassembly for the ISA witness); every other kind
  raises ``ProbeMissing`` instead of inventing an answer, and ``env`` /
  ``proc_maps`` are refused outright so a published report can never carry CI
  secrets or host paths.  Remote (``ssh://``) targets and the remaining
  instruments (perf, llama-bench, hyperfine, pip, cmake) are TODO(S1).

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
import platform
import shutil
import subprocess
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
    "LIVE_COMMANDS",
    "LIVE_FILES",
    "LIVE_REFUSED",
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


#: Probe kinds LiveProbe can genuinely observe by running a command on this
#: host, mapped to that command. Deliberately short: a kind belongs here only
#: when local execution answers it *truthfully and safely*.
LIVE_COMMANDS: dict[str, list[str]] = {
    "lscpu": ["lscpu"],
}

#: Files LiveProbe reads directly rather than shelling out for.
LIVE_FILES: dict[str, str] = {
    "thp": "/sys/kernel/mm/transparent_hugepage/enabled",
}

#: Kinds LiveProbe will never self-serve, with the reason. ``env`` and
#: ``proc_maps`` are refused on PURPOSE: a report is a published artifact, and
#: a CI environment block contains tokens. Armsmith does not put the machine's
#: environment into a file it asks the world to trust.
LIVE_REFUSED: dict[str, str] = {
    "env": "environment dumps can carry CI secrets — never captured into a report",
    "proc_maps": "process maps leak host paths and add nothing a report can use",
}


class LiveProbe(Probe):
    """Executes real instruments on the local host (Arm silicon, live mode).

    Scope is deliberately narrow. This backend answers only what it can observe
    honestly right now — ``lscpu`` and transparent-hugepage state — plus
    observations a harness hands it via :meth:`capture` (e.g. the objdump text
    :mod:`armsmith.livebench` produces for the ISA witness). Every other probe
    kind raises :class:`ProbeMissing` rather than inventing a plausible answer;
    "skip, don't guess" is the same rule the rule pack follows.

    TODO(S1): ``ssh://`` targets and the remaining instruments (perf,
    llama-bench, hyperfine, pip, cmake), plus ``scripts/record_replays.sh`` to
    write live captures back out in the bundle layout ReplayProbe reads.
    """

    def __init__(self, target: str = "local"):
        if target != "local":
            raise NotImplementedError(
                f"LiveProbe target {target!r} is not supported — remote (ssh://) "
                "targets land at S1; run armsmith on the Arm host itself"
            )
        self.target = target
        self._captured: dict[str, str] = {}
        self._cache: dict[str, str] = {}

    def capture(self, kind: str, text: str) -> None:
        """Record a real observation produced by a harness on this host.

        Used for probe kinds that only exist relative to something built during
        the run — the ISA witness's ``objdump_before``/``objdump_after``.
        """
        if kind not in PROBE_KINDS:
            raise ProbeMissing(f"unknown probe kind {kind!r}")
        self._captured[kind] = text

    def _read(self, kind: str) -> str | None:
        if kind in self._captured:
            return self._captured[kind]
        if kind in self._cache:
            return self._cache[kind]
        if kind in LIVE_FILES:
            path = Path(LIVE_FILES[kind])
            if not path.is_file():
                return None
            value = path.read_text(encoding="utf-8")
            self._cache[kind] = value
            return value
        if kind in LIVE_COMMANDS:
            exe = shutil.which(LIVE_COMMANDS[kind][0])
            if not exe:
                return None
            proc = subprocess.run(
                [exe, *LIVE_COMMANDS[kind][1:]],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.returncode != 0:
                return None
            self._cache[kind] = proc.stdout
            return proc.stdout
        return None

    def has(self, kind: str) -> bool:
        if kind not in PROBE_KINDS or kind in LIVE_REFUSED:
            return False
        return self._read(kind) is not None

    def text(self, kind: str) -> str:
        if kind not in PROBE_KINDS:
            raise ProbeMissing(f"unknown probe kind {kind!r}")
        if kind in LIVE_REFUSED:
            raise ProbeMissing(f"probe {kind!r} refused in live mode: {LIVE_REFUSED[kind]}")
        value = self._read(kind)
        if value is None:
            raise ProbeMissing(
                f"probe {kind!r} is not available from a live local run — "
                "the live instrument for it lands at S1 (skip, don't guess)"
            )
        return value

    def json(self, kind: str) -> Any:
        return json.loads(self.text(kind))

    def raw(self, kind: str) -> bytes:
        return self.text(kind).encode("utf-8")

    @property
    def source(self) -> str:
        return f"live[{platform.system()}/{platform.machine()}]: {platform.node()}"
