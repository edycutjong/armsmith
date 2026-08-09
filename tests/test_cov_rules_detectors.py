"""Edge-path coverage for the rule base plumbing and detectors R1/R4/R5/R6/R7.

Every bundle/repo built here is synthetic and declares its provenance in
``manifest.json``, exactly like ``fixtures/rules/*``.  The cases below are the
malformed-input and negative branches the fixture bundles do not exercise:
skip-instead-of-guess paths, ignored files, and single-signal matches.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest

from armsmith.gguf import MAGIC, T_STRING, build_stub
from armsmith.probes import Probe, ProbeMissing, ReplayProbe
from armsmith.rules import DETECTORS, FindingStatus, RuleSpec, load_pack, run_rule
from armsmith.rules.base import register


@pytest.fixture(scope="module")
def specs():
    return load_pack()


def make_bundle(
    root: Path,
    scenario: str,
    probes: dict[str, bytes | str] | None = None,
    repo_files: dict[str, str] | None = None,
    repo_dirs: tuple[str, ...] = (),
) -> Path:
    """Write a synthetic replay bundle (manifest + probes/ + repo/) at *root*."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "provenance": (
                    "hand-authored synthetic fixture built inside the test run; "
                    "illustrative shapes only, NOT measured on any hardware"
                ),
                "scenario": scenario,
            }
        ),
        encoding="utf-8",
    )
    for name, payload in (probes or {}).items():
        pdir = root / "probes"
        pdir.mkdir(exist_ok=True)
        target = pdir / name
        if isinstance(payload, bytes):
            target.write_bytes(payload)
        else:
            target.write_text(payload, encoding="utf-8")
    if repo_files or repo_dirs:
        (root / "repo").mkdir(exist_ok=True)
    for rel, text in (repo_files or {}).items():
        path = root / "repo" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for rel in repo_dirs:
        (root / "repo" / rel).mkdir(parents=True, exist_ok=True)
    return root


def run_bundle(specs, rid: str, bundle: Path):
    probe = ReplayProbe(bundle)
    return run_rule(specs[rid], probe.repo_dir, probe)


# --- base.py ----------------------------------------------------------------

def test_rulespec_to_dict_serializes_every_descriptor_field(specs):
    """RuleSpec.to_dict() emits the full descriptor with tuples as lists."""
    spec = specs["R5"]
    d = spec.to_dict()
    assert d["id"] == "R5"
    assert d["kind"] == "probe"
    assert d["requires"] == list(spec.requires) == ["gguf_header", "lscpu"]
    assert d["expected_gain_range"] == list(spec.expected_gain_range)
    assert isinstance(d["requires"], list) and isinstance(d["expected_gain_range"], list)
    assert d["title"] == spec.title and d["summary"] == spec.summary
    assert d["fix_generator"] == spec.fix_generator and d["gain_note"] == spec.gain_note
    assert d["citation_url"] == spec.citation_url and d["confidence"] == spec.confidence
    assert d["learning_path"] == spec.learning_path
    assert json.loads(json.dumps(d)) == d  # report-serializable


def test_register_refuses_a_second_detector_for_the_same_rule_id():
    """@register on an already-registered id raises and leaves the registry intact."""
    original = DETECTORS["R1"]
    with pytest.raises(ValueError, match="detector for R1 registered twice"):
        register("R1")(lambda repo, probe, spec: None)
    assert DETECTORS["R1"] is original


def _spec(rid: str, kind: str = "probe", requires: tuple[str, ...] = ()) -> RuleSpec:
    return RuleSpec(
        id=rid,
        title="synthetic spec",
        kind=kind,
        requires=requires,
        summary="synthetic",
        fix_generator="synthetic",
        expected_gain_range=(1.0, 1.1),
        gain_note="synthetic estimate",
        citation_url="https://example.invalid/",
        confidence="low",
    )


def test_run_rule_skips_when_no_detector_is_registered():
    """An unknown rule id skips with a naming reason instead of raising."""
    f = run_rule(_spec("R99"), None, None)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason == "no detector registered for R99"
    assert f.fix is None and f.evidence == () and f.matched is False


def test_run_rule_skips_a_probe_rule_when_no_probe_backend_is_given(specs):
    """A probe rule with no backend names the probes it would have needed."""
    f = run_rule(specs["R6"], None, None)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason == "needs probes ['env'] but no probe backend given"


class _ReadFailsProbe(Probe):
    """Probe that advertises every observation but fails at read time."""

    def has(self, kind: str) -> bool:
        return True

    def text(self, kind: str) -> str:
        raise ProbeMissing(f"{kind} vanished between has() and read")

    def json(self, kind: str) -> Any:
        raise ProbeMissing(f"{kind} vanished between has() and read")

    def raw(self, kind: str) -> bytes:
        raise ProbeMissing(f"{kind} vanished between has() and read")

    @property
    def source(self) -> str:
        return "test[read-fails]"


def test_run_rule_converts_probe_missing_at_read_time_into_a_skip(specs):
    """A detector raising ProbeMissing mid-run becomes SKIPPED, not a crash."""
    f = run_rule(specs["R5"], None, _ReadFailsProbe())
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason.startswith("probe data missing at read time:")
    assert "gguf_header vanished between has() and read" in f.skipped_reason
    assert f.fix is None


# --- R1 — amd64-pinned image -------------------------------------------------

def test_r1_ignores_dockerfiles_inside_git_and_directories_named_like_one(specs, tmp_path):
    """A pinned Dockerfile under .git/ and a Dockerfile.* directory are both skipped."""
    bundle = make_bundle(
        tmp_path / "b",
        "r1-ignored-paths",
        repo_files={
            ".git/Dockerfile": "FROM --platform=linux/amd64 python:3.12-slim\n",
            "Dockerfile": "FROM python:3.12-slim\n",
        },
        repo_dirs=("Dockerfile.d",),
    )
    f = run_bundle(specs, "R1", bundle)
    assert f.status is FindingStatus.CLEAN
    assert f.evidence == ("no amd64 platform pins found in Dockerfile/compose files",)
    assert f.locations == () and f.fix is None


def test_r1_flags_amd64_prefixed_base_image_and_strips_the_prefix(specs, tmp_path):
    """`FROM amd64/ubuntu` matches on the image prefix and the patch drops `amd64/`."""
    bundle = make_bundle(
        tmp_path / "b",
        "r1-amd64-prefix",
        repo_files={"Dockerfile": "FROM amd64/ubuntu:22.04\nRUN echo hi\n"},
    )
    f = run_bundle(specs, "R1", bundle)
    assert f.status is FindingStatus.MATCHED
    assert f.evidence == (
        "Dockerfile:1: base image 'amd64/ubuntu:22.04' is the arch-specific amd64/ variant",
    )
    assert f.locations == ("Dockerfile:1",)
    assert f.fix.kind == "dockerfile_edit"
    assert f.fix.patch == "- FROM amd64/ubuntu:22.04\n+ FROM ubuntu:22.04"


# --- R4 — float64 coercion ---------------------------------------------------

def test_r4_ignores_python_under_git_and_venv(specs, tmp_path):
    """Vendored/venv sources are not the user's code — undtyped ctors there are ignored."""
    src = "import numpy as np\nx = np.array([1.0, 2.0])\n"
    bundle = make_bundle(
        tmp_path / "b",
        "r4-ignored-paths",
        repo_files={".venv/lib/site.py": src, ".git/hooks/hook.py": src},
    )
    f = run_bundle(specs, "R4", bundle)
    assert f.status is FindingStatus.CLEAN
    assert f.evidence == ("all numpy constructor calls pin an explicit dtype",)
    assert f.locations == ()


def test_r4_records_unparseable_files_as_evidence_and_keeps_scanning(specs, tmp_path):
    """A syntax-error file is noted in evidence; later files are still scanned."""
    bundle = make_bundle(
        tmp_path / "b",
        "r4-syntax-error",
        repo_files={
            "a_broken.py": "def oops(:\n    pass\n",
            "b_good.py": "import numpy as np\n\nx = np.zeros((4, 4))\n",
        },
    )
    f = run_bundle(specs, "R4", bundle)
    assert f.status is FindingStatus.MATCHED
    assert f.evidence[0] == "a_broken.py: skipped (syntax error at line 1)"
    assert f.evidence[1] == (
        "b_good.py:3: np.zeros(...) without dtype= — floats default to float64"
    )
    assert f.locations == ("b_good.py:3",)  # the broken file contributes no location


def test_r4_is_clean_when_only_file_is_unparseable(specs, tmp_path):
    """With no locations the finding is CLEAN and the syntax note is dropped."""
    bundle = make_bundle(
        tmp_path / "b",
        "r4-syntax-error-only",
        repo_files={"broken.py": "import numpy as np\nx = np.array([1.0\n"},
    )
    f = run_bundle(specs, "R4", bundle)
    assert f.status is FindingStatus.CLEAN
    assert f.evidence == ("all numpy constructor calls pin an explicit dtype",)
    assert f.fix is None


# --- R5 — GGUF quant vs ISA --------------------------------------------------

_LSCPU_DOTPROD = (
    "Architecture:                         aarch64\n"
    "CPU(s):                               2\n"
    "Model name:                           Neoverse-N1\n"
    "Flags:                                fp asimd aes crc32 atomics asimddp\n"
)


def _gguf_without_file_type() -> bytes:
    """Valid GGUF header carrying only general.architecture (no general.file_type)."""
    key = b"general.architecture"
    value = b"llama"
    out = bytearray(MAGIC)
    out += struct.pack("<I", 3)          # version
    out += struct.pack("<Q", 0)          # n_tensors
    out += struct.pack("<Q", 1)          # n_kv
    out += struct.pack("<Q", len(key)) + key
    out += struct.pack("<I", T_STRING)
    out += struct.pack("<Q", len(value)) + value
    return bytes(out)


def test_r5_skips_when_the_gguf_header_cannot_be_parsed(specs, tmp_path):
    """Garbage in gguf_header.bin skips with the parser's reason — never a guess."""
    bundle = make_bundle(
        tmp_path / "b",
        "r5-bad-header",
        probes={"gguf_header.bin": b"NOTGGUF" + b"\x00" * 40, "lscpu.txt": _LSCPU_DOTPROD},
    )
    f = run_bundle(specs, "R5", bundle)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason == "could not parse GGUF header: not a GGUF file (bad magic)"
    assert f.fix is None and f.evidence == ()


def test_r5_skips_when_metadata_has_no_general_file_type(specs, tmp_path):
    """A parseable header without general.file_type carries no quant to judge."""
    bundle = make_bundle(
        tmp_path / "b",
        "r5-no-file-type",
        probes={"gguf_header.bin": _gguf_without_file_type(), "lscpu.txt": _LSCPU_DOTPROD},
    )
    f = run_bundle(specs, "R5", bundle)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason == "GGUF metadata carries no general.file_type"
    assert f.fix is None


def test_r5_truncated_header_skips_with_the_truncation_reason(specs, tmp_path):
    """A half-written header is a parse failure, not a mismatch."""
    blob = build_stub(file_type=15)
    bundle = make_bundle(
        tmp_path / "b",
        "r5-truncated",
        probes={"gguf_header.bin": blob[: len(blob) // 2], "lscpu.txt": _LSCPU_DOTPROD},
    )
    f = run_bundle(specs, "R5", bundle)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason.startswith("could not parse GGUF header: truncated GGUF data")


# --- R6 — thread oversubscription --------------------------------------------

@pytest.mark.parametrize(
    "payload",
    [
        {"env": {}, "workers": 4},                      # nproc absent
        {"env": {}, "workers": "4", "nproc": 16},       # workers not an int
        {"env": {}, "workers": 4, "nproc": "16"},       # nproc not an int
        {"env": {}, "workers": 0, "nproc": 16},         # workers below 1
        {"env": {}, "workers": 4, "nproc": 0},          # nproc below 1
        {"env": {}, "workers": True, "nproc": 16},      # bool is an int but < 1 check passes
    ],
    ids=["no-nproc", "str-workers", "str-nproc", "zero-workers", "zero-nproc", "bool-workers"],
)
def test_r6_skips_when_workers_or_nproc_are_not_usable_ints(specs, tmp_path, payload):
    """Without integer workers/nproc there is no ratio to compute — skip, don't guess."""
    bundle = make_bundle(
        tmp_path / "b", "r6-bad-env", probes={"env.json": json.dumps(payload)}
    )
    f = run_bundle(specs, "R6", bundle)
    if payload["workers"] is True:  # workers=1 is usable: 1 × 16 == nproc → clean
        assert f.status is FindingStatus.CLEAN
        return
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason == "env probe lacks integer 'workers'/'nproc' fields"
    assert f.fix is None


def test_r6_ignores_a_thread_knob_that_is_not_an_integer(specs, tmp_path):
    """`OMP_NUM_THREADS=auto` cannot be parsed, so the OpenMP nproc default applies."""
    bundle = make_bundle(
        tmp_path / "b",
        "r6-unparseable-knob",
        probes={
            "env.json": json.dumps(
                {
                    "env": {"OMP_NUM_THREADS": "auto", "MKL_NUM_THREADS": "not-a-number"},
                    "workers": 4,
                    "nproc": 16,
                    "worker_source": "gunicorn -w 4 app:app (synthetic)",
                }
            )
        },
    )
    f = run_bundle(specs, "R6", bundle)
    assert f.status is FindingStatus.MATCHED
    assert "workers(4) × threads/worker(16) = 64 runnable threads on 16 vCPUs" in f.evidence[0]
    # the unparseable knobs were dropped, so the default-per-vCPU branch was used
    assert f.evidence[1] == (
        "thread source: no thread env set → OpenMP default = nproc per worker"
    )
    assert f.evidence[2] == "worker source: gunicorn -w 4 app:app (synthetic)"
    assert f.fix.kind == "env_change"
    assert f.fix.patch == "OMP_NUM_THREADS=4\nOPENBLAS_NUM_THREADS=4"


def test_r6_uses_the_largest_parseable_knob_when_one_is_junk(specs, tmp_path):
    """A junk knob alongside a valid one leaves the valid one in charge."""
    bundle = make_bundle(
        tmp_path / "b",
        "r6-mixed-knobs",
        probes={
            "env.json": json.dumps(
                {
                    "env": {"OMP_NUM_THREADS": "8", "OPENBLAS_NUM_THREADS": "eight"},
                    "workers": 4,
                    "nproc": 16,
                }
            )
        },
    )
    f = run_bundle(specs, "R6", bundle)
    assert f.status is FindingStatus.MATCHED
    assert f.evidence[1] == "thread source: OMP_NUM_THREADS=8"
    assert "= 32 runnable threads on 16 vCPUs" in f.evidence[0]
    assert f.evidence[2] == "worker source: unrecorded"


# --- R7 — ONNX Runtime session defaults --------------------------------------

@pytest.mark.parametrize(
    "payload",
    [
        {"intra_op_num_threads": 0, "inter_op_num_threads": 0},          # KeyError
        {"intra_op_num_threads": None, "inter_op_num_threads": 0,
         "graph_optimization_level": "ORT_ENABLE_ALL"},                  # TypeError
        {"intra_op_num_threads": "many", "inter_op_num_threads": 0,
         "graph_optimization_level": "ORT_ENABLE_ALL"},                  # ValueError
    ],
    ids=["missing-opt-level", "null-intra", "non-numeric-intra"],
)
def test_r7_skips_when_session_option_fields_are_missing_or_malformed(specs, tmp_path, payload):
    """Incomplete SessionOptions capture skips with the offending field in the reason."""
    bundle = make_bundle(
        tmp_path / "b", "r7-bad-session", probes={"ort_session.json": json.dumps(payload)}
    )
    f = run_bundle(specs, "R7", bundle)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason.startswith("ort_session probe missing session-option fields:")
    assert f.fix is None and f.evidence == ()


def test_r7_flags_multiplied_inter_op_pools_on_an_otherwise_tuned_session(specs, tmp_path):
    """inter_op>1 with several workers multiplies thread pools even at ORT_ENABLE_ALL."""
    bundle = make_bundle(
        tmp_path / "b",
        "r7-inter-op",
        probes={
            "ort_session.json": json.dumps(
                {
                    "intra_op_num_threads": 4,
                    "inter_op_num_threads": 2,
                    "graph_optimization_level": "ORT_ENABLE_ALL",
                    "execution_mode": "ORT_PARALLEL",
                    "workers": 4,
                    "nproc": 16,
                }
            )
        },
    )
    f = run_bundle(specs, "R7", bundle)
    assert f.status is FindingStatus.MATCHED
    assert f.evidence == (
        "inter_op_num_threads=2 with 4 workers multiplies thread pools",
    )
    assert f.locations == ("probe:ort_session",)
    assert f.fix.kind == "config_patch"
    assert "so.intra_op_num_threads = 4" in f.fix.patch
    assert "so.inter_op_num_threads = 1" in f.fix.patch


def test_r7_single_worker_with_default_threads_is_clean(specs, tmp_path):
    """Thread defaults only oversubscribe with >1 worker; a lone worker stays clean."""
    bundle = make_bundle(
        tmp_path / "b",
        "r7-single-worker",
        probes={
            "ort_session.json": json.dumps(
                {
                    "intra_op_num_threads": 0,
                    "inter_op_num_threads": 4,
                    "graph_optimization_level": "ORT_ENABLE_ALL",
                    "workers": "not-an-int",
                    "nproc": None,
                }
            )
        },
    )
    f = run_bundle(specs, "R7", bundle)
    assert f.status is FindingStatus.CLEAN
    assert f.evidence == (
        "session options tuned: intra=0, inter=4, opt=ORT_ENABLE_ALL, workers=1",
    )
    assert f.fix is None
