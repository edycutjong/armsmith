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


def _inline_repo(tmp_path, scenario, source):
    import json
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"synthetic": True, "provenance": "inline test fixture", "scenario": scenario}))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text(source)
    return repo, ReplayProbe(tmp_path)


def test_r4_respects_numpy_dtype_inference(tmp_path, specs):
    """A missing dtype= is only float64 when numpy would actually infer float64.

    Every expectation below was checked against real numpy: np.array([0, 2, 4])
    is int64, np.full(n, 0) is int64, np.array([True, False]) is bool, and
    np.zeros/linspace are float64 no matter what. Flagging the integer forms
    was a false positive whose suggested patch (pin dtype=np.float32) would
    silently corrupt an index array.
    """
    repo, probe = _inline_repo(tmp_path, "r4-dtype-inference", "\n".join([
        "import numpy as np",                     # 1
        "",                                       # 2
        "idx = np.array([0, 2, 4])",              # 3  int64      -> clean
        "grid = np.array([[1, 2], [3, 4]])",      # 4  int64      -> clean
        "signed = np.array([-1, 2])",             # 5  int64      -> clean
        "mask = np.array([True, False])",         # 6  bool       -> clean
        "fill_i = np.full((2, 2), 0)",            # 7  int64      -> clean
        "mixed = np.array([1.0, 2])",             # 8  float64    -> FLAG
        "fill_f = np.full((2, 2), 0.5)",          # 9  float64    -> FLAG
        "unknown = np.array(payload)",            # 10 unprovable -> FLAG
        "buf = np.zeros(8)",                      # 11 float64    -> FLAG
    ]) + "\n")
    f = run_rule(specs["R4"], repo, probe)

    assert f.status is FindingStatus.MATCHED
    flagged = sorted(int(loc.rsplit(":", 1)[1]) for loc in f.locations)
    assert flagged == [8, 9, 10, 11]


def test_r4_does_not_flag_an_integer_permutation_array(tmp_path, specs):
    """Regression: the shape that produced 5/5 false positives on a real repo.

    huggingface/text-generation-inference builds integer permutation arrays for
    GPTQ weight shuffling. R4 flagged every one and proposed dtype=np.float32,
    which would have turned an index into a float and broken the indexing.
    """
    repo, probe = _inline_repo(tmp_path, "r4-permutation", (
        "import numpy\n"
        "g_idx = numpy.argsort(numpy.array([0, 2, 4, 6, 1, 3, 5, 7]))\n"
    ))
    f = run_rule(specs["R4"], repo, probe)
    assert f.status is FindingStatus.CLEAN
    assert f.fix is None


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


def test_r4_never_suggests_a_patch_it_cannot_prove(tmp_path, specs):
    """The regression the rule exists to avoid: corrupting an index array.

    A dtype-less call on a non-literal payload is still REPORTED — under-
    reporting a real float64 coercion on an inference path costs more than a
    question. But it must not carry a "pin float32" patch, because if the
    payload holds integers that edit silently breaks the indexing. This is the
    exact shape found in huggingface/text-generation-inference.
    """
    repo, probe = _inline_repo(tmp_path, "r4-unprovable", (
        "import numpy as np\n"
        "def f(adapter_indices):\n"
        "    a = np.array(adapter_indices)\n"
        "    return a\n"
    ))
    f = run_rule(specs["R4"], repo, probe)

    assert f.status is FindingStatus.MATCHED
    assert f.fix.kind == "advisory"
    assert "dtype=np.float32" not in f.fix.patch
    assert "CORRUPTS" in f.fix.patch
    assert "not statically provable" in f.evidence[0]


def test_r4_still_patches_what_it_can_prove(tmp_path, specs):
    repo, probe = _inline_repo(tmp_path, "r4-proven", "import numpy as np\nb = np.zeros(8)\n")
    f = run_rule(specs["R4"], repo, probe)

    assert f.fix.kind == "code_suggestion"
    assert "add dtype=np.float32" in f.fix.patch
    assert "this call returns float64" in f.evidence[0]


def test_r4_separates_proven_from_unprovable_in_one_report(tmp_path, specs):
    repo, probe = _inline_repo(tmp_path, "r4-mixed", (
        "import numpy as np\n"
        "def f(payload):\n"
        "    a = np.zeros(4)\n"          # 3 proven
        "    b = np.array(payload)\n"    # 4 unprovable
        "    return a, b\n"
    ))
    f = run_rule(specs["R4"], repo, probe)

    assert len(f.locations) == 2
    assert f.fix.kind == "code_suggestion"
    assert "safe to pin" in f.fix.patch and "NOT auto-patchable" in f.fix.patch
    assert "1 site(s) are reported for review only" in f.fix.description


def test_r4_ignores_integer_comprehensions(tmp_path, specs):
    """Found on vllm: np.array([i for i in range(n) ...]) is int64, not float64."""
    repo, probe = _inline_repo(tmp_path, "r4-comprehension", (
        "import numpy as np\n"
        "idx = np.array([i for i in range(vocab_size)])\n"
        "pairs = np.array([2 for _ in range(4)])\n"
        "vals = np.array([x for x in floats])\n"   # not a range -> unprovable, reported
    ))
    f = run_rule(specs["R4"], repo, probe)

    flagged = sorted(int(loc.rsplit(":", 1)[1]) for loc in f.locations)
    assert flagged == [4]


def test_r4_treats_an_argless_constructor_as_unprovable(tmp_path, specs):
    """np.array() has no payload to reason about, so it must not be patched."""
    repo, probe = _inline_repo(tmp_path, "r4-argless", "import numpy as np\nx = np.array()\n")
    f = run_rule(specs["R4"], repo, probe)
    assert f.status is FindingStatus.MATCHED
    assert f.fix.kind == "advisory"


# --- R12 matrix resolution ---------------------------------------------------

def _wf_repo(tmp_path, name, workflow_yaml):
    import json
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"synthetic": True, "provenance": "inline test fixture", "scenario": name}))
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "docker.yml").write_text(workflow_yaml)
    return repo, ReplayProbe(tmp_path)


def test_r12_resolves_a_matrix_expression_that_includes_arm64(tmp_path, specs):
    """Regression: llama.cpp declares platforms via matrix and DOES ship arm64."""
    repo, probe = _wf_repo(tmp_path, "r12-matrix-arm", """
name: docker
jobs:
  push:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        config:
          - { tag: cpu, platforms: "linux/amd64" }
          - { tag: cpu, platforms: "linux/arm64" }
    steps:
      - uses: docker/build-push-action@v6
        with:
          platforms: ${{ matrix.config.platforms }}
""")
    f = run_rule(specs["R12"], repo, probe)
    assert f.status is FindingStatus.CLEAN


def test_r12_still_fires_when_the_resolved_matrix_has_no_arm64(tmp_path, specs):
    repo, probe = _wf_repo(tmp_path, "r12-matrix-noarm", """
name: docker
jobs:
  push:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        config:
          - { tag: cpu, platforms: "linux/amd64" }
          - { tag: gpu, platforms: "linux/386" }
    steps:
      - uses: docker/build-push-action@v6
        with:
          platforms: ${{ matrix.config.platforms }}
""")
    f = run_rule(specs["R12"], repo, probe)
    assert f.status is FindingStatus.MATCHED


def test_r12_stays_silent_on_an_unresolvable_matrix_expression(tmp_path, specs):
    """The matrix is built at runtime (llama.cpp uses fromJSON of a generated file).

    We cannot see the values, so guessing 'no arm64' would be a false positive.
    """
    repo, probe = _wf_repo(tmp_path, "r12-matrix-runtime", """
name: docker
jobs:
  push:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        config: ${{ fromJSON(needs.prepare.outputs.matrix) }}
    steps:
      - uses: docker/build-push-action@v6
        with:
          platforms: ${{ matrix.config.platforms }}
""")
    f = run_rule(specs["R12"], repo, probe)
    assert f.status is FindingStatus.CLEAN
    assert any("could not be resolved statically" in e for e in f.evidence)
    assert f.locations == ()


def test_r12_resolves_a_plain_matrix_key_and_include_entries(tmp_path, specs):
    repo, probe = _wf_repo(tmp_path, "r12-matrix-include", """
name: docker
jobs:
  push:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        platform: ["linux/amd64"]
        include:
          - platform: "linux/arm64"
    steps:
      - uses: docker/build-push-action@v6
        with:
          platforms: ${{ matrix.platform }}
""")
    f = run_rule(specs["R12"], repo, probe)
    assert f.status is FindingStatus.CLEAN


def test_r12_matrix_expression_without_a_strategy_block_is_not_reported(tmp_path, specs):
    repo, probe = _wf_repo(tmp_path, "r12-no-strategy", """
name: docker
jobs:
  push:
    runs-on: ubuntu-latest
    steps:
      - uses: docker/build-push-action@v6
        with:
          platforms: ${{ matrix.platform }}
""")
    f = run_rule(specs["R12"], repo, probe)
    assert f.status is FindingStatus.CLEAN
    assert f.locations == ()


def test_r12_platforms_helper_refuses_to_resolve_without_a_job():
    """Contract: no job context means no resolution, never a guess."""
    from armsmith.rules.detectors.r12_ci_matrix import _platforms_of

    step = {"with": {"platforms": "${{ matrix.config.platforms }}"}}
    assert _platforms_of(step) == ("${{ matrix.config.platforms }}", None)
    literal = {"with": {"platforms": "linux/amd64,linux/arm64"}}
    assert _platforms_of(literal)[1] == "linux/amd64,linux/arm64"
