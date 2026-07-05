"""armsmith.fingerprint — host fingerprint capture (fixture/replay-backed).

Every report embeds the host it was measured on so a judge reproducing on
different silicon sees an honest "different host" signal instead of silent
drift (SEED_DATA.md determinism requirements).

Phase-1 rule: fingerprints come ONLY from recorded fixtures (lscpu text +
bundle manifest host block).  There is no code path that fabricates a
Graviton fingerprint from this development machine.  TODO(S1): live capture
via lscpu/uname/sysreport on the target box (sysreport is a Python CLI with
no formal API — `doctor` will shell out and parse the text summary, per
crawl/clean/sdk_sysreport.md).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .probes import Probe

__all__ = ["IsaFeatures", "HostFingerprint", "parse_lscpu", "capture_fingerprint"]

#: lscpu flag token → canonical ISA feature name.
#: asimddp is the lscpu spelling of the ARMv8.2 dot-product extension.
_FLAG_MAP: dict[str, str] = {
    "asimddp": "dotprod",
    "i8mm": "i8mm",
    "sve": "sve",
    "sve2": "sve2",
    "bf16": "bf16",
    "sme": "sme",
}

#: Order in which features are reported (stable output for tables/tests).
FEATURE_ORDER = ("dotprod", "i8mm", "sve", "sve2", "bf16", "sme")


@dataclass(frozen=True)
class IsaFeatures:
    dotprod: bool = False
    i8mm: bool = False
    sve: bool = False
    sve2: bool = False
    bf16: bool = False
    sme: bool = False

    def present(self) -> list[str]:
        return [name for name in FEATURE_ORDER if getattr(self, name)]

    def to_dict(self) -> dict[str, bool]:
        return {name: getattr(self, name) for name in FEATURE_ORDER}


@dataclass(frozen=True)
class HostFingerprint:
    architecture: str
    model_name: str
    vendor: str
    cpus: int
    isa: IsaFeatures
    flags: tuple[str, ...]
    instance: str = "unknown"
    kernel: str = "unknown"
    governor: str = "unknown"
    source: str = "unknown"  # provenance label, e.g. "replay[SYNTHETIC]: ..."

    def to_dict(self) -> dict:
        return {
            "architecture": self.architecture,
            "model_name": self.model_name,
            "vendor": self.vendor,
            "cpus": self.cpus,
            "isa_feats": self.isa.present(),
            "isa": self.isa.to_dict(),
            "instance": self.instance,
            "kernel": self.kernel,
            "governor": self.governor,
            "source": self.source,
        }


_KV_RE = re.compile(r"^\s*(?P<key>[A-Za-z0-9()\-\s/]+?):\s*(?P<val>.*)$")


def parse_lscpu(text: str) -> dict[str, str]:
    """Parse lscpu's ``Key: value`` lines into a flat dict (last wins)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _KV_RE.match(line)
        if m:
            out[m.group("key").strip()] = m.group("val").strip()
    return out


def _features_from_flags(flags: list[str]) -> IsaFeatures:
    flag_set = set(flags)
    kwargs: dict[str, bool] = {}
    for token, feat in _FLAG_MAP.items():
        kwargs.setdefault(feat, False)
        if token in flag_set:
            kwargs[feat] = True
    # sve2 implies sve at the ISA level; keep lscpu's word but never report
    # sve2 without sve (defensive normalization for hand-edited fixtures).
    if kwargs.get("sve2") and not kwargs.get("sve"):
        kwargs["sve"] = True
    return IsaFeatures(**kwargs)


def capture_fingerprint(
    probe: Probe,
    host_meta: Mapping[str, str] | None = None,
) -> HostFingerprint:
    """Build a fingerprint from a probe's recorded lscpu + manifest host block.

    ``host_meta`` supplies instance/kernel/governor (values recorded at
    capture time; in replay bundles they live in manifest.json's "host").
    """
    kv = parse_lscpu(probe.text("lscpu"))
    flags = kv.get("Flags", "").split()
    meta = dict(host_meta or {})
    try:
        cpus = int(kv.get("CPU(s)", "0"))
    except ValueError:
        cpus = 0
    return HostFingerprint(
        architecture=kv.get("Architecture", "unknown"),
        model_name=kv.get("Model name", "unknown"),
        vendor=kv.get("Vendor ID", "unknown"),
        cpus=cpus,
        isa=_features_from_flags(flags),
        flags=tuple(flags),
        instance=str(meta.get("instance", "unknown")),
        kernel=str(meta.get("kernel", "unknown")),
        governor=str(meta.get("governor", "unknown")),
        source=probe.source,
    )
