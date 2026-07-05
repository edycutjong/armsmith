"""R5 — GGUF quant vs target-ISA repack path (probe: gguf_header + lscpu)."""

from __future__ import annotations

from pathlib import Path

from ...fingerprint import capture_fingerprint
from ...gguf import K_QUANT_CODES, Q4_0_CODE, GgufError, read_header
from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register, skipped


@register("R5")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert probe is not None
    try:
        header = read_header(probe.raw("gguf_header"))
    except GgufError as exc:
        return skipped(spec, f"could not parse GGUF header: {exc}")

    code = header.file_type_code
    if code is None:
        return skipped(spec, "GGUF metadata carries no general.file_type")
    name = header.file_type_name
    fp = capture_fingerprint(probe)
    feats = fp.isa

    base_evidence = [
        f"model quant: {name} (general.file_type={code}, arch={header.architecture})",
        f"host ISA: dotprod={feats.dotprod} i8mm={feats.i8mm} sve={feats.sve} (model={fp.model_name})",
    ]

    if code == Q4_0_CODE and not feats.dotprod:
        fix = Fix(
            rule_id=spec.id,
            kind="quant_swap",
            description=(
                "Q4_0's aarch64 runtime-repack fast path needs dotprod, which "
                "this host lacks — swap to a K-quant (e.g. Q4_K_M) whose kernels "
                "don't depend on the repack path, then A/B through the gate. "
                "Quant swaps shift quality: state the perplexity trade in the PR."
            ),
            patch=None,
            commands=(
                "llama-quantize <f16.gguf> <out-Q4_K_M.gguf> Q4_K_M",
                "llama-bench -m <out-Q4_K_M.gguf> -r 7 -o json  # gate re-bench",
            ),
        )
        return Finding(
            rule_id=spec.id,
            status=FindingStatus.MATCHED,
            evidence=tuple(base_evidence + [
                "mismatch: Q4_0 on a host WITHOUT dotprod — runtime repack cannot engage",
            ]),
            locations=("probe:gguf_header", "probe:lscpu"),
            fix=fix,
        )

    if code in K_QUANT_CODES and (feats.dotprod or feats.i8mm):
        fix = Fix(
            rule_id=spec.id,
            kind="quant_swap",
            description=(
                f"Host has {'i8mm+dotprod' if feats.i8mm and feats.dotprod else 'dotprod'} "
                "but the model is a K-quant, which skips llama.cpp's aarch64 "
                "runtime-repack fast path — A/B a Q4_0 variant (repacks to the "
                "optimized layout at load) through the gate. Quant swaps shift "
                "quality: state the perplexity trade in the PR."
            ),
            patch=None,
            commands=(
                "llama-quantize <f16.gguf> <out-Q4_0.gguf> Q4_0",
                "llama-bench -m <out-Q4_0.gguf> -r 7 -o json  # gate re-bench",
            ),
        )
        return Finding(
            rule_id=spec.id,
            status=FindingStatus.MATCHED,
            evidence=tuple(base_evidence + [
                f"mismatch: {name} K-quant on a dotprod/i8mm host — repack fast path unused",
            ]),
            locations=("probe:gguf_header", "probe:lscpu"),
            fix=fix,
        )

    return clean(spec, base_evidence + ["quant format and host ISA are consistent"])
