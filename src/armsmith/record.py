"""armsmith.record — write a REAL replay bundle from the host you run it on.

Every probe-backed rule (R2, R3, R5–R11, R13) reads its observation from a
replay bundle. Until now the only bundles that existed were the synthetic
fixtures shipped in this repo, which made those rules demonstrable but not
*usable*: a stranger had no way to produce a bundle for their own machine, so
ten of thirteen rules and the CI gate only ever ran against our data.

This module closes that. :func:`record_bundle` captures what the local host can
honestly provide, ingests real artifacts the caller already has, and writes the
result in the exact layout :class:`~armsmith.probes.ReplayProbe` reads — with
``"synthetic": false`` in the manifest, because none of it is invented.

The honesty rules are the same ones the rest of the tool follows:

* **Nothing is fabricated.** A probe that cannot be observed is left out, and
  the rules that need it will report ``skipped`` with a reason. There is no
  code path here that writes a plausible-looking value.
* **``env`` and ``proc_maps`` are never captured**, even though both are
  trivially readable. A bundle is an artifact people publish; an environment
  block carries CI tokens and a maps dump carries host paths. This is enforced
  by :data:`~armsmith.probes.LIVE_REFUSED` and asserted by a test.
* **Ingested files are copied verbatim.** ``--build-log`` and friends take a
  file the caller produced with the real tool; armsmith does not run the tool
  for them and does not reformat the output.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .probes import LIVE_REFUSED, PROBE_KINDS, LiveProbe

__all__ = [
    "CaptureResult",
    "RecordResult",
    "AUTO_KINDS",
    "INGEST_KINDS",
    "RULES_BY_PROBE",
    "record_bundle",
]

#: Probe kinds this module captures by itself, with no help from the caller.
AUTO_KINDS: tuple[str, ...] = ("lscpu", "thp", "numpy_show_config")

#: Probe kinds that can only come from an artifact the caller already produced
#: with the real instrument. Maps CLI option name → probe kind.
INGEST_KINDS: dict[str, str] = {
    "build_log": "build_log",
    "pip_log": "pip_install_log",
    "cmake_cache": "cmake_cache",
    "gguf": "gguf_header",
    "perf": "perf_report",
    "llama_bench": "llama_bench",
    "hyperfine": "hyperfine",
    "ort_session": "ort_session",
}

#: Which rules each probe kind unlocks — used to tell the caller exactly what
#: their bundle will and will not be able to diagnose.
RULES_BY_PROBE: dict[str, tuple[str, ...]] = {
    "build_log": ("R2",),
    "numpy_show_config": ("R3",),
    "gguf_header": ("R5",),
    "env": ("R6",),
    "ort_session": ("R7",),
    "pip_install_log": ("R8",),
    "perf_report": ("R9",),
    "cmake_cache": ("R10",),
    "thp": ("R11",),
    "proc_maps": ("R11",),
    "llama_bench": ("R13",),
    "hyperfine": ("R13",),
}


@dataclass(frozen=True)
class CaptureResult:
    """One probe kind's outcome: captured from where, or absent and why."""

    kind: str
    captured: bool
    source: str
    reason: str = ""

    @property
    def rules(self) -> tuple[str, ...]:
        return RULES_BY_PROBE.get(self.kind, ())


@dataclass
class RecordResult:
    bundle_dir: Path
    scenario: str
    captures: list[CaptureResult] = field(default_factory=list)
    repo_copied: bool = False

    @property
    def captured_kinds(self) -> list[str]:
        return [c.kind for c in self.captures if c.captured]

    @property
    def missing_kinds(self) -> list[str]:
        return [c.kind for c in self.captures if not c.captured]

    @property
    def rules_enabled(self) -> list[str]:
        """Rules whose EVERY required probe was captured.

        Read straight off the rule pack's own ``requires`` rather than a map
        maintained by hand here. R2 needs both ``build_log`` and ``lscpu``, and
        a hand-rolled map that checked only one would claim a rule is available
        when it is not — the same overstatement this tool refuses everywhere
        else, just pointed at itself.
        """
        from .rules import load_pack

        got = set(self.captured_kinds)
        enabled = [
            rid
            for rid, spec in load_pack(require_detectors=False).items()
            if spec.requires and set(spec.requires) <= got
        ]
        return sorted(enabled, key=lambda r: int(r[1:]))


def _capture_numpy_show_config(python: str | None = None) -> tuple[str | None, str]:
    """Return numpy.show_config() text, or None plus the reason it is absent.

    Runs in a subprocess deliberately, and against ``python`` when given. R3 is
    a claim about the BLAS *the caller's model actually runs on*, so probing
    armsmith's own interpreter would answer the wrong question — armsmith does
    not even depend on numpy. Point ``--python`` at the venv that serves the
    workload; a broken install there is then reported, not crashed on.
    """
    exe = python or sys.executable
    if not Path(exe).is_file() and shutil.which(exe) is None:
        return None, f"interpreter not found: {exe}"
    try:
        proc = subprocess.run(
            [exe, "-c", "import numpy; numpy.show_config()"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except OSError as exc:  # not executable, wrong arch, ...
        return None, f"could not run {exe}: {exc}"
    if proc.returncode != 0:
        return None, f"numpy is not importable in {exe}"
    if not proc.stdout.strip():
        return None, "numpy.show_config() produced no output"
    return proc.stdout, f"{exe} -c 'import numpy; numpy.show_config()'"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def record_bundle(
    repo: Path | None,
    out_dir: Path,
    *,
    scenario: str | None = None,
    ingest: dict[str, Path] | None = None,
    copy_repo: bool = True,
    note: str = "",
    python: str | None = None,
) -> RecordResult:
    """Capture a live replay bundle into ``out_dir``.

    ``ingest`` maps a probe kind to a file the caller produced with the real
    instrument; each is copied in verbatim. ``repo`` is copied into the bundle
    so the static rules (R1/R4/R12) run against it offline later.
    """
    out_dir = Path(out_dir)
    probes_dir = out_dir / "probes"
    probes_dir.mkdir(parents=True, exist_ok=True)
    scenario = scenario or (repo.resolve().name if repo else out_dir.name)
    ingest = dict(ingest or {})

    result = RecordResult(bundle_dir=out_dir, scenario=scenario)
    live = LiveProbe()

    # --- auto-captured kinds -------------------------------------------------
    for kind in AUTO_KINDS:
        if kind == "numpy_show_config":
            text, source = _capture_numpy_show_config(python)
            if text is None:
                result.captures.append(
                    CaptureResult(kind, False, "", source)
                )
                continue
            _write(probes_dir / PROBE_KINDS[kind], text)
            result.captures.append(CaptureResult(kind, True, source))
            continue

        if live.has(kind):
            _write(probes_dir / PROBE_KINDS[kind], live.text(kind))
            result.captures.append(CaptureResult(kind, True, f"live: {kind}"))
        else:
            result.captures.append(
                CaptureResult(
                    kind, False, "",
                    f"not observable on this host ({platform.system()})",
                )
            )

    # --- caller-supplied artifacts ------------------------------------------
    for kind, src in ingest.items():
        if kind not in PROBE_KINDS:
            raise ValueError(f"unknown probe kind {kind!r}")
        src = Path(src)
        if not src.is_file():
            result.captures.append(
                CaptureResult(kind, False, "", f"file not found: {src}")
            )
            continue
        shutil.copyfile(src, probes_dir / PROBE_KINDS[kind])
        result.captures.append(CaptureResult(kind, True, f"ingested: {src}"))

    # --- kinds we refuse on purpose ------------------------------------------
    for kind, why in LIVE_REFUSED.items():
        result.captures.append(CaptureResult(kind, False, "", f"refused — {why}"))

    # --- the repo under test --------------------------------------------------
    if repo is not None and copy_repo:
        dest = out_dir / "repo"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(
            repo,
            dest,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "venv", "node_modules", "__pycache__", "*.pyc"
            ),
        )
        result.repo_copied = True

    # --- manifest -------------------------------------------------------------
    captured = result.captured_kinds
    provenance = (
        f"captured live by `armsmith record` on {platform.node()} "
        f"({platform.system()}/{platform.machine()}); probes: "
        f"{', '.join(captured) if captured else 'none'}. "
        "Nothing here is synthetic: every probe present was observed on this "
        "host or copied verbatim from an artifact the operator supplied. "
        "Probes that could not be observed were omitted, not invented."
    )
    manifest = {
        "synthetic": False,
        "mode": "live",
        "provenance": provenance,
        "scenario": scenario,
        "host": {
            "node": platform.node(),
            "system": platform.system(),
            "machine": platform.machine(),
            "release": platform.release(),
            "python": platform.python_version(),
        },
        "captured_probes": captured,
        "omitted_probes": {
            c.kind: c.reason for c in result.captures if not c.captured
        },
        "rules_enabled": result.rules_enabled,
    }
    if note:
        manifest["note"] = note
    _write(out_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    return result
