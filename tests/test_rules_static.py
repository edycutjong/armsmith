"""Static detectors R1 / R4 / R12 against positive+negative fixture repos."""

import pytest

from armsmith.probes import ReplayProbe
from armsmith.rules import FindingStatus, load_pack, run_rule


@pytest.fixture(scope="module")
def specs():
    return load_pack()


def run_on(specs, rid, bundle_dir):
    probe = ReplayProbe(bundle_dir)
    return run_rule(specs[rid], probe.repo_dir, probe)


# --- R1 ---------------------------------------------------------------------

def test_r1_positive_dockerfile_and_compose(specs, rule_bundle):
    f = run_on(specs, "R1", rule_bundle("r01_pos"))
    assert f.status is FindingStatus.MATCHED
    assert any("--platform" in e or "linux/amd64" in e for e in f.evidence)
    assert any("Dockerfile:1" in loc for loc in f.locations)
    assert any("compose" in e for e in f.evidence)  # compose pin also caught
    assert f.fix is not None and f.fix.kind == "dockerfile_edit"
    assert "FROM python:3.12-slim" in f.fix.patch  # pin removed in suggested patch


def test_r1_negative(specs, rule_bundle):
    f = run_on(specs, "R1", rule_bundle("r01_neg"))
    assert f.status is FindingStatus.CLEAN
    assert f.fix is None


def test_r1_skips_without_repo(specs):
    f = run_rule(specs["R1"], None, None)
    assert f.status is FindingStatus.SKIPPED
    assert "repo" in f.skipped_reason


# --- R4 ---------------------------------------------------------------------

def test_r4_positive_flags_ctors_without_dtype(specs, rule_bundle):
    f = run_on(specs, "R4", rule_bundle("r04_pos"))
    assert f.status is FindingStatus.MATCHED
    assert len(f.locations) == 2  # np.array + np.zeros
    assert any("embed.py:5" in loc for loc in f.locations)
    assert f.fix.kind == "code_suggestion"
    assert "dtype=np.float32" in f.fix.patch


def test_r4_negative_dtype_pinned(specs, rule_bundle):
    f = run_on(specs, "R4", rule_bundle("r04_neg"))
    assert f.status is FindingStatus.CLEAN


def test_r4_handles_from_import_alias(tmp_path, specs):
    import json
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"synthetic": True, "provenance": "inline test fixture", "scenario": "r4-alias"}))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text(
        "from numpy import array as arr\nimport numpy\n\n"
        "x = arr([1.0, 2.0])\n"
        "y = numpy.linspace(0, 1)\n"
        "z = numpy.full((2, 2), 0.5, dtype='float32')\n"
    )
    probe = ReplayProbe(tmp_path)
    f = run_rule(specs["R4"], repo, probe)
    assert f.status is FindingStatus.MATCHED
    assert len(f.locations) == 2  # arr(...) and numpy.linspace(...); full() has dtype


def test_r4_ignores_non_numpy_calls(tmp_path, specs):
    import json
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"synthetic": True, "provenance": "inline test fixture", "scenario": "r4-nonumpy"}))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("array = list\nx = array([1.0])\nzeros = dict\n")
    f = run_rule(specs["R4"], repo, ReplayProbe(tmp_path))
    assert f.status is FindingStatus.CLEAN


# --- R12 --------------------------------------------------------------------

def test_r12_positive_amd64_only_publish(specs, rule_bundle):
    f = run_on(specs, "R12", rule_bundle("r12_pos"))
    assert f.status is FindingStatus.MATCHED
    assert any("no linux/arm64" in e for e in f.evidence)
    assert f.fix.kind == "ci_patch"
    assert "ubuntu-24.04-arm" in f.fix.patch  # verified runner label in the fix
    assert "linux/amd64,linux/arm64" in f.fix.patch


def test_r12_negative_multiarch(specs, rule_bundle):
    f = run_on(specs, "R12", rule_bundle("r12_neg"))
    assert f.status is FindingStatus.CLEAN
    assert any("include arm64" in e for e in f.evidence)


def test_r12_clean_when_no_workflows(tmp_path, specs):
    import json
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"synthetic": True, "provenance": "inline test fixture", "scenario": "r12-nowf"}))
    repo = tmp_path / "repo"
    repo.mkdir()
    f = run_rule(specs["R12"], repo, ReplayProbe(tmp_path))
    assert f.status is FindingStatus.CLEAN
