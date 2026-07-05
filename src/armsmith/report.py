"""armsmith.report — report model, content addressing, ed25519 sign/verify.

Tamper-evidence chain (COMPLEXITY §2):
1. the report embeds RAW samples next to every claimed summary statistic;
2. the report (minus its ``signature`` block) is canonically serialized
   (sorted keys, compact separators, UTF-8, NaN/inf forbidden) and
   content-addressed with SHA-256;
3. the canonical bytes are ed25519-signed; the signature block carries the
   hash, the signature, and the raw public key;
4. ``armsmith verify`` re-hashes, checks the signature, and — crucially —
   RECOMPUTES every claimed statistic and gate verdict from the embedded
   samples.  Editing a number without re-running the math is detectable.

CI-side Sigstore/cosign attestation is additive and lands at S1 (the PR
footer's ``cosign verify-blob`` line is rendered by armsmith.evidence).
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__, benchstats, keys
from .benchstats import Direction, Verdict
from .fingerprint import HostFingerprint
from .gate import GateConfig, GateOutcome, MeasurementSet

__all__ = [
    "SCHEMA_VERSION",
    "canonical_bytes",
    "content_hash",
    "build_report",
    "sign_report",
    "write_report",
    "load_report",
    "VerifyIssue",
    "VerifyResult",
    "verify_report",
    "validate_schema",
    "schema_path",
]

SCHEMA_VERSION = "1.0.0"

_REL_TOL = 1e-9  # float agreement tolerance for recomputation checks


def canonical_bytes(obj: Any) -> bytes:
    """Canonical JSON: sorted keys, compact separators, UTF-8, no NaN/inf."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def content_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _measurement_block(ms: MeasurementSet) -> dict:
    return {
        "variant": ms.variant,
        "instrument": ms.instrument,
        "rule_id": ms.rule_id,
        "synthetic": ms.synthetic,
        "samples": ms.metrics,  # RAW samples — the tamper-evidence anchor
        "metrics_summary": {
            name: benchstats.summarize(samples).to_dict()
            for name, samples in ms.metrics.items()
        },
        "pmu": ms.pmu,
        "output_sha256": ms.output_sha256,
    }


def build_report(
    *,
    mode: str,
    scenario: str,
    repo: dict[str, str],
    host: HostFingerprint | None,
    findings: list[dict],
    outcome: GateOutcome | None,
    gate_config: GateConfig | None = None,
    plan: list[dict] | None = None,
    artifacts: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
    run_id: str | None = None,
    created_at: str | None = None,
) -> dict:
    """Assemble the report dict (unsigned)."""
    if mode not in ("replay", "live"):
        raise ValueError(f"mode must be replay|live, got {mode!r}")
    cfg = gate_config or GateConfig()
    synthetic = mode == "replay"

    fixes: list[dict] = []
    baseline_block = None
    if outcome is not None:
        baseline_block = _measurement_block(outcome.baseline)
        for res, ms in zip(outcome.results, outcome.candidates):
            block = res.to_dict()
            block["measurement"] = _measurement_block(ms)
            fixes.append(block)

    report = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id or str(uuid.uuid4()),
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "mode": mode,
        "synthetic": synthetic,
        "tool": {"name": "armsmith", "version": __version__},
        "scenario": scenario,
        "repo": {"url": repo.get("url", "unknown"), "sha": repo.get("sha", "unknown")},
        "host": host.to_dict() if host else None,
        "gate_config": {
            "band_k": cfg.band_k,
            "min_samples": cfg.min_samples,
            "require_output_hash": cfg.require_output_hash,
            "primary_metrics": list(cfg.primary_metrics) if cfg.primary_metrics else None,
        },
        "findings": findings,
        "plan": plan or [],
        "baseline": baseline_block,
        "fixes": fixes,
        "artifacts": artifacts
        or {"flamegraph_before": None, "flamegraph_after": None, "performix_ref": None},
        "cost": cost or {"cost_usd": 0.0, "note": "replay mode — no hardware spend"},
    }
    return report


# ---------------------------------------------------------------------------
# Sign / write / load
# ---------------------------------------------------------------------------

def sign_report(report: dict, key_dir: Path | None = None) -> dict:
    """Return a copy of the report with a signature block appended."""
    body = {k: v for k, v in report.items() if k != "signature"}
    payload = canonical_bytes(body)
    digest = hashlib.sha256(payload).hexdigest()
    private = keys.load_private_key(key_dir)
    signature = keys.sign(private, payload)
    signed = dict(body)
    signed["signature"] = {
        "algorithm": "ed25519",
        "report_sha256": digest,
        "signature_b64": base64.b64encode(signature).decode("ascii"),
        "public_key_b64": keys.public_key_b64(private.public_key()),
    }
    return signed


def write_report(report: dict, path: Path) -> Path:
    path = Path(path)
    path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return path


def load_report(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VerifyIssue:
    kind: str      # "signature" | "hash" | "recompute" | "schema" | "structure"
    detail: str


@dataclass
class VerifyResult:
    ok: bool
    checks: list[str] = field(default_factory=list)
    issues: list[VerifyIssue] = field(default_factory=list)

    def add_ok(self, msg: str) -> None:
        self.checks.append(msg)

    def add_issue(self, kind: str, detail: str) -> None:
        self.issues.append(VerifyIssue(kind, detail))
        self.ok = False


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=_REL_TOL, abs_tol=1e-12)


def _recompute_summary(result: VerifyResult, label: str, block: dict) -> None:
    samples = block.get("samples") or {}
    claimed = block.get("metrics_summary") or {}
    for metric, sample_list in samples.items():
        got = benchstats.summarize(sample_list).to_dict()
        want = claimed.get(metric)
        if want is None:
            result.add_issue("recompute", f"{label}: metric {metric} has samples but no summary")
            continue
        for field_name, value in got.items():
            claimed_value = want.get(field_name)
            if claimed_value is None or (
                isinstance(value, float) and not _close(float(claimed_value), value)
            ) or (isinstance(value, int) and int(claimed_value) != value):
                result.add_issue(
                    "recompute",
                    f"{label}: {metric}.{field_name} claimed {claimed_value!r} but "
                    f"recomputed {value!r} from embedded samples",
                )
                break
    for metric in claimed:
        if metric not in samples:
            result.add_issue("recompute", f"{label}: summary for {metric} has no raw samples")


def _recompute_gate(result: VerifyResult, report: dict) -> None:
    baseline = report.get("baseline")
    if not baseline:
        return
    cfg = report.get("gate_config") or {}
    k = float(cfg.get("band_k", benchstats.DEFAULT_BAND_K))
    min_samples = int(cfg.get("min_samples", benchstats.MIN_SAMPLES_FOR_VERDICT))
    base_samples = baseline.get("samples") or {}

    from .gate import METRIC_DIRECTIONS  # local import to avoid cycle at module load

    for fix in report.get("fixes", []):
        label = f"fix {fix.get('variant')}"
        meas = fix.get("measurement") or {}
        fix_samples = meas.get("samples") or {}
        for metric, claimed_cmp in (fix.get("comparisons") or {}).items():
            if metric not in base_samples or metric not in fix_samples:
                result.add_issue("recompute", f"{label}: comparison metric {metric} lacks raw samples")
                continue
            direction = METRIC_DIRECTIONS.get(metric)
            if direction is None:
                dirname = claimed_cmp.get("direction", "")
                try:
                    direction = Direction(dirname)
                except ValueError:
                    result.add_issue("recompute", f"{label}: unknown direction for {metric}")
                    continue
            got = benchstats.compare(
                base_samples[metric], fix_samples[metric],
                direction=direction, k=k, min_samples=min_samples,
            )
            if got.verdict.value != claimed_cmp.get("verdict"):
                result.add_issue(
                    "recompute",
                    f"{label}: {metric} verdict claimed {claimed_cmp.get('verdict')!r} "
                    f"but recomputes to {got.verdict.value!r}",
                )
            claimed_band = claimed_cmp.get("band")
            if got.band is not None and claimed_band is not None and not _close(got.band, float(claimed_band)):
                result.add_issue(
                    "recompute",
                    f"{label}: {metric} noise band claimed {claimed_band} but recomputes to {got.band}",
                )
        # keep/drop consistency: a keep with any regressed comparison is invalid
        if fix.get("verdict") == "keep":
            for metric, claimed_cmp in (fix.get("comparisons") or {}).items():
                if claimed_cmp.get("verdict") == Verdict.REGRESSED.value:
                    result.add_issue(
                        "recompute",
                        f"{label}: verdict 'keep' despite regressed metric {metric}",
                    )


def verify_report(
    report: dict,
    trusted_public_key_b64: str | None = None,
    check_schema: bool = True,
) -> VerifyResult:
    result = VerifyResult(ok=True)

    signature = report.get("signature")
    body = {k: v for k, v in report.items() if k != "signature"}
    payload = canonical_bytes(body)
    digest = hashlib.sha256(payload).hexdigest()

    if not signature:
        result.add_issue("signature", "report carries no signature block")
    else:
        if signature.get("report_sha256") != digest:
            result.add_issue(
                "hash",
                f"content hash mismatch: signature says {signature.get('report_sha256')!r}, "
                f"canonical body hashes to {digest!r}",
            )
        else:
            result.add_ok(f"content hash OK (sha256 {digest[:16]}…)")
        try:
            pub = keys.public_key_from_b64(signature.get("public_key_b64", ""))
            sig = base64.b64decode(signature.get("signature_b64", ""))
            if keys.verify(pub, sig, payload):
                result.add_ok("ed25519 signature OK (embedded key)")
            else:
                result.add_issue("signature", "ed25519 signature INVALID for canonical body")
        except Exception as exc:  # malformed key/signature material
            result.add_issue("signature", f"malformed signature material: {exc}")
        if trusted_public_key_b64 is not None:
            if signature.get("public_key_b64") == trusted_public_key_b64:
                result.add_ok("signer matches trusted public key")
            else:
                result.add_issue("signature", "signer public key does not match the trusted key")

    if check_schema:
        schema_issues = validate_schema(report)
        if schema_issues:
            for issue in schema_issues:
                result.add_issue("schema", issue)
        else:
            result.add_ok("report validates against schema/report.schema.json")

    if report.get("baseline"):
        _recompute_summary(result, "baseline", report["baseline"])
    for fix in report.get("fixes", []):
        if fix.get("measurement"):
            _recompute_summary(result, f"fix {fix.get('variant')}", fix["measurement"])
    _recompute_gate(result, report)
    if result.ok:
        result.add_ok("claimed statistics recompute exactly from embedded raw samples")
    return result


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def schema_path() -> Path:
    """Locate schema/report.schema.json (repo checkout or installed layout)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "schema" / "report.schema.json"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("schema/report.schema.json not found in package tree")


def validate_schema(report: dict) -> list[str]:
    import jsonschema

    schema = json.loads(schema_path().read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(report), key=lambda e: list(e.absolute_path))
    ]
