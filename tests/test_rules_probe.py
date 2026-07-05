"""Probe-backed detectors R2,R3,R5,R6,R7,R8,R9,R10,R11,R13 — pos/neg fixtures."""

import pytest

from armsmith.probes import ReplayProbe
from armsmith.rules import FindingStatus, load_pack, run_rule


@pytest.fixture(scope="module")
def specs():
    return load_pack()


def run_on(specs, rid, bundle_dir):
    probe = ReplayProbe(bundle_dir)
    return run_rule(specs[rid], probe.repo_dir, probe)


# --- R2 — march flags --------------------------------------------------------

def test_r2_positive_no_target_flags(specs, rule_bundle):
    f = run_on(specs, "R2", rule_bundle("r02_pos"))
    assert f.status is FindingStatus.MATCHED
    assert any("lack -mcpu=/-march=" in e for e in f.evidence)
    assert "-mcpu=native" in f.fix.patch
    assert "i8mm" in f.fix.patch  # host has i8mm → explicit march includes it


def test_r2_negative_mcpu_present(specs, rule_bundle):
    f = run_on(specs, "R2", rule_bundle("r02_neg"))
    assert f.status is FindingStatus.CLEAN


def test_r2_skips_without_probe(specs, rule_bundle):
    # bundle with no build_log recorded
    f = run_on(specs, "R2", rule_bundle("r03_pos"))
    assert f.status is FindingStatus.SKIPPED
    assert "build_log" in f.skipped_reason


# --- R3 — reference BLAS -----------------------------------------------------

def test_r3_positive_reference_blas(specs, rule_bundle):
    f = run_on(specs, "R3", rule_bundle("r03_pos"))
    assert f.status is FindingStatus.MATCHED
    assert any("NOT AVAILABLE" in e for e in f.evidence)
    assert f.fix.kind == "pip_pin"
    assert any("--only-binary" in c for c in f.fix.commands)


def test_r3_negative_openblas_meson_format(specs, rule_bundle):
    f = run_on(specs, "R3", rule_bundle("r03_neg"))
    assert f.status is FindingStatus.CLEAN
    assert any("openblas" in e for e in f.evidence)


# --- R5 — GGUF quant vs ISA --------------------------------------------------

def test_r5_positive_kquant_on_i8mm_host(specs, rule_bundle):
    f = run_on(specs, "R5", rule_bundle("r05_pos"))
    assert f.status is FindingStatus.MATCHED
    assert any("Q4_K_M" in e for e in f.evidence)
    assert f.fix.kind == "quant_swap"
    assert any("Q4_0" in c for c in f.fix.commands)
    assert "perplexity" in f.fix.description  # quality trade disclosed


def test_r5_negative_q4_0_on_dotprod_host(specs, rule_bundle):
    f = run_on(specs, "R5", rule_bundle("r05_neg"))
    assert f.status is FindingStatus.CLEAN


def test_r5_positive_q4_0_without_dotprod(specs, rule_bundle):
    f = run_on(specs, "R5", rule_bundle("r05_pos_nodotprod"))
    assert f.status is FindingStatus.MATCHED
    assert any("WITHOUT dotprod" in e for e in f.evidence)
    assert any("Q4_K_M" in c for c in f.fix.commands)


# --- R6 — thread oversubscription ---------------------------------------------

def test_r6_positive_default_omp_times_workers(specs, rule_bundle):
    f = run_on(specs, "R6", rule_bundle("r06_pos"))
    assert f.status is FindingStatus.MATCHED
    assert any("64 runnable threads on 16 vCPUs" in e for e in f.evidence)
    assert "OMP_NUM_THREADS=4" in f.fix.patch


def test_r6_negative_capped_threads(specs, rule_bundle):
    f = run_on(specs, "R6", rule_bundle("r06_neg"))
    assert f.status is FindingStatus.CLEAN
    assert any("no oversubscription" in e for e in f.evidence)


# --- R7 — ORT session defaults --------------------------------------------------

def test_r7_positive_defaults_and_oversub(specs, rule_bundle):
    f = run_on(specs, "R7", rule_bundle("r07_pos"))
    assert f.status is FindingStatus.MATCHED
    assert any("ORT_ENABLE_BASIC" in e for e in f.evidence)
    assert any("intra_op_num_threads=0" in e for e in f.evidence)
    assert "intra_op_num_threads = 4" in f.fix.patch
    assert "ORT_ENABLE_ALL" in f.fix.patch


def test_r7_negative_tuned_session(specs, rule_bundle):
    f = run_on(specs, "R7", rule_bundle("r07_neg"))
    assert f.status is FindingStatus.CLEAN


# --- R8 — sdist fallback ---------------------------------------------------------

def test_r8_positive_numpy_sdist(specs, rule_bundle):
    f = run_on(specs, "R8", rule_bundle("r08_pos"))
    assert f.status is FindingStatus.MATCHED
    assert any("numpy-1.24.0" in e and "sdist" in e for e in f.evidence)
    assert any("wheel built from source" in e for e in f.evidence)
    assert f.fix.commands == ("pip install --only-binary=:all: numpy",)


def test_r8_negative_aarch64_wheel(specs, rule_bundle):
    f = run_on(specs, "R8", rule_bundle("r08_neg"))
    assert f.status is FindingStatus.CLEAN


# --- R9 — memcpy storm -----------------------------------------------------------

def test_r9_positive_memcpy_dominates(specs, rule_bundle):
    f = run_on(specs, "R9", rule_bundle("r09_pos"))
    assert f.status is FindingStatus.MATCHED
    # 28.40 + 9.10 + 2.05 = 39.55 → one-decimal rendering (float-safe prefix)
    assert any("account for 39.5" in e or "account for 39.6" in e for e in f.evidence)
    assert "TODO(S1)" in f.fix.description  # LLM-drafted rewrite deferred honestly


def test_r9_negative_kernels_dominate(specs, rule_bundle):
    f = run_on(specs, "R9", rule_bundle("r09_neg"))
    assert f.status is FindingStatus.CLEAN
    assert any("5.9%" in e for e in f.evidence)


# --- R10 — KleidiAI flags ----------------------------------------------------------

def test_r10_positive_kleidiai_off(specs, rule_bundle):
    f = run_on(specs, "R10", rule_bundle("r10_pos"))
    assert f.status is FindingStatus.MATCHED
    assert any("GGML_CPU_KLEIDIAI=OFF" in e for e in f.evidence)
    assert "-DGGML_CPU_KLEIDIAI=ON" in f.fix.patch
    assert any("GGML_KLEIDIAI_SME" in c for c in f.fix.commands)  # SME sweep variant


def test_r10_negative_kleidiai_on(specs, rule_bundle):
    f = run_on(specs, "R10", rule_bundle("r10_neg"))
    assert f.status is FindingStatus.CLEAN


def test_r10_not_a_ggml_build_is_clean(specs, rule_bundle):
    f = run_on(specs, "R10", rule_bundle("r10_na"))
    assert f.status is FindingStatus.CLEAN
    assert any("not a ggml" in e for e in f.evidence)


# --- R11 — THP / allocator -----------------------------------------------------------

def test_r11_positive_thp_never_no_allocator(specs, rule_bundle):
    f = run_on(specs, "R11", rule_bundle("r11_pos"))
    assert f.status is FindingStatus.MATCHED
    assert any("hugepages disabled" in e for e in f.evidence)
    assert any("no tcmalloc/jemalloc" in e for e in f.evidence)
    assert "LD_PRELOAD" in f.fix.patch


def test_r11_negative_madvise_plus_jemalloc(specs, rule_bundle):
    f = run_on(specs, "R11", rule_bundle("r11_neg"))
    assert f.status is FindingStatus.CLEAN


# --- R13 — two-instrument divergence ---------------------------------------------------

def test_r13_positive_serving_overhead_dominates(specs, rule_bundle):
    f = run_on(specs, "R13", rule_bundle("r13_pos"))
    assert f.status is FindingStatus.MATCHED
    # kernel ≈ 512/400 + 128/50 = 3.84s vs E2E 5.60s → 31.4% outside kernels
    assert any("31.4% of wall time outside kernels" in e for e in f.evidence)
    assert any("exclude tokenization" in e for e in f.evidence)
    assert any("cross-checked" in e for e in f.evidence)


def test_r13_negative_within_threshold(specs, rule_bundle):
    f = run_on(specs, "R13", rule_bundle("r13_neg"))
    assert f.status is FindingStatus.CLEAN
    assert any("kernels dominate" in e for e in f.evidence)


def test_r13_corrupt_selfreport_skips(specs, rule_bundle):
    f = run_on(specs, "R13", rule_bundle("r13_corrupt"))
    assert f.status is FindingStatus.SKIPPED
    assert "disagrees with its samples" in f.skipped_reason


# --- cross-cutting: probe rules skip cleanly when probes are absent -----------

@pytest.mark.parametrize("rid", ["R2", "R5", "R7", "R10", "R13"])
def test_probe_rules_skip_on_scenario_without_their_probes(specs, rid, scenario_bundle):
    probe = ReplayProbe(scenario_bundle)
    f = run_rule(specs[rid], probe.repo_dir, probe)
    assert f.status is FindingStatus.SKIPPED
    assert "not recorded" in f.skipped_reason
