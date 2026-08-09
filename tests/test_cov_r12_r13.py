"""Edge-path coverage for R12 (CI matrix) and R13 (instrument divergence).

Everything here is synthetic, hand-authored fixture data built in ``tmp_path``
(same shape as ``fixtures/rules/*``: a manifest.json declaring provenance, an
optional ``repo/`` mini-checkout and an optional ``probes/`` dir).  No numbers
in this file were measured on any hardware — they are chosen to exercise a
specific decision in the detector and are asserted through the detector's
public verdict/evidence/reason strings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from armsmith.probes import ReplayProbe
from armsmith.rules import FindingStatus, load_pack, run_rule


@pytest.fixture(scope="module")
def specs():
    return load_pack()


def make_bundle(
    tmp_path: Path,
    scenario: str,
    workflows: dict[str, str] | None = None,
    probes: dict[str, object] | None = None,
) -> Path:
    """Build a synthetic replay bundle and return its directory."""
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "provenance": (
                    "hand-authored synthetic fixture for offline tests; values are "
                    "illustrative shapes only and were NOT measured on any hardware"
                ),
                "scenario": scenario,
            }
        ),
        encoding="utf-8",
    )
    if workflows is not None:
        wf_dir = tmp_path / "repo" / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        for name, body in workflows.items():
            (wf_dir / name).write_text(body, encoding="utf-8")
    if probes is not None:
        probes_dir = tmp_path / "probes"
        probes_dir.mkdir()
        for name, payload in probes.items():
            (probes_dir / name).write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def run_bundle(specs, rid: str, bundle: Path):
    probe = ReplayProbe(bundle)
    return run_rule(specs[rid], probe.repo_dir, probe)


# ===========================================================================
# R12 — CI publishes amd64-only images
# ===========================================================================

def test_r12_clean_when_no_workflow_yields_a_build_step(tmp_path, specs):
    """Odd-but-legal workflow shapes yield no build steps → CLEAN, not a crash."""
    bundle = make_bundle(
        tmp_path,
        "r12-odd-shapes",
        workflows={
            # jobs is a list, not a mapping
            "a_jobs_list.yml": "name: a\njobs:\n  - image\n  - test\n",
            # job value is a scalar, not a mapping
            "b_job_scalar.yml": "name: b\njobs:\n  image: not-a-mapping\n",
            # steps is a scalar, not a list
            "c_steps_scalar.yml": (
                "name: c\njobs:\n  image:\n    runs-on: ubuntu-latest\n"
                "    steps: oops-not-a-list\n"
            ),
            # whole document is a list, not a mapping
            "d_doc_list.yml": "- one\n- two\n",
            # a real job whose steps simply build nothing
            "e_no_build.yaml": (
                "name: e\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/checkout@v4\n"
                "      - run: pytest -q\n"
            ),
        },
    )
    f = run_bundle(specs, "R12", bundle)
    assert f.status is FindingStatus.CLEAN
    assert f.fix is None
    assert f.evidence == ("workflows found, but none build/push container images",)


def test_r12_unparseable_workflow_is_reported_beside_a_real_amd64_finding(tmp_path, specs):
    """A broken YAML file is recorded as evidence, not swallowed, and scanning continues."""
    bundle = make_bundle(
        tmp_path,
        "r12-unparseable",
        workflows={
            "a_broken.yml": "name: broken\njobs: [unclosed\n  : : :\n",
            "b_publish.yml": (
                "name: publish\njobs:\n  image:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: docker build -t ghcr.io/example/app:latest .\n"
            ),
        },
    )
    f = run_bundle(specs, "R12", bundle)
    assert f.status is FindingStatus.MATCHED
    assert any("a_broken.yml" in e and "unparseable workflow" in e for e in f.evidence)
    assert any(
        "plain 'docker build' on runner 'ubuntu-latest'" in e
        and "publishes the runner's arch only (amd64)" in e
        for e in f.evidence
    )
    # only the file that actually builds an image is reported as a location
    assert f.locations == (".github/workflows/b_publish.yml",)
    assert f.fix is not None and f.fix.kind == "ci_patch"


def test_r12_buildx_run_step_without_platform_matches(tmp_path, specs):
    bundle = make_bundle(
        tmp_path,
        "r12-buildx-no-platform",
        workflows={
            "publish.yml": (
                "name: publish\njobs:\n  image:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: docker buildx build --push -t ghcr.io/example/app:latest .\n"
            ),
        },
    )
    f = run_bundle(specs, "R12", bundle)
    assert f.status is FindingStatus.MATCHED
    assert f.evidence == (
        ".github/workflows/publish.yml: job 'image' step 1: "
        "buildx build without an arm64 --platform",
    )
    assert f.locations == (".github/workflows/publish.yml",)
    assert "linux/amd64,linux/arm64" in f.fix.patch


def test_r12_buildx_run_step_with_arm64_platform_is_clean(tmp_path, specs):
    bundle = make_bundle(
        tmp_path,
        "r12-buildx-multiarch",
        workflows={
            "publish.yml": (
                "name: publish\njobs:\n  image:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: >-\n"
                "          docker buildx build --platform linux/amd64,linux/arm64\n"
                "          --push -t ghcr.io/example/app:latest .\n"
            ),
        },
    )
    f = run_bundle(specs, "R12", bundle)
    assert f.status is FindingStatus.CLEAN
    assert f.evidence == ("1 image-build step(s) all include arm64",)
    assert f.fix is None


def test_r12_build_push_action_with_non_mapping_with_block(tmp_path, specs):
    """``with:`` given as a scalar cannot declare platforms → treated as unset."""
    bundle = make_bundle(
        tmp_path,
        "r12-with-scalar",
        workflows={
            "publish.yml": (
                "name: publish\njobs:\n  image:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/checkout@v4\n"
                "      - uses: docker/build-push-action@v6\n"
                "        with: not-a-mapping\n"
            ),
        },
    )
    f = run_bundle(specs, "R12", bundle)
    assert f.status is FindingStatus.MATCHED
    assert f.evidence == (
        ".github/workflows/publish.yml: job 'image' step 2: "
        "build-push-action platforms=(unset → runner arch only) — no linux/arm64",
    )


def test_r12_plain_docker_build_on_native_arm_runner_is_clean(tmp_path, specs):
    """A plain ``docker build`` on an arm64 runner does publish arm64 → CLEAN."""
    bundle = make_bundle(
        tmp_path,
        "r12-arm-runner",
        workflows={
            "publish.yml": (
                "name: publish\njobs:\n  image:\n    runs-on: ubuntu-24.04-arm\n    steps:\n"
                "      - run: docker build -t ghcr.io/example/app:arm64 .\n"
            ),
        },
    )
    f = run_bundle(specs, "R12", bundle)
    assert f.status is FindingStatus.CLEAN
    assert f.evidence == ("1 image-build step(s) all include arm64",)


def test_r12_runs_on_label_list_containing_arm_runner_is_clean(tmp_path, specs):
    bundle = make_bundle(
        tmp_path,
        "r12-arm-runner-list",
        workflows={
            "publish.yml": (
                "name: publish\njobs:\n  image:\n"
                "    runs-on: [self-hosted, ubuntu-24.04-arm]\n    steps:\n"
                "      - run: docker build -t ghcr.io/example/app:arm64 .\n"
            ),
        },
    )
    f = run_bundle(specs, "R12", bundle)
    assert f.status is FindingStatus.CLEAN
    assert f.evidence == ("1 image-build step(s) all include arm64",)


def test_r12_runs_on_label_list_and_group_mapping_without_arm_match(tmp_path, specs):
    """Neither an arm-less label list nor a runner *group* mapping proves arm64."""
    bundle = make_bundle(
        tmp_path,
        "r12-runs-on-shapes",
        workflows={
            "publish.yml": (
                "name: publish\njobs:\n"
                "  labels:\n"
                "    runs-on: [self-hosted, linux, x64]\n    steps:\n"
                "      - run: docker build -t ghcr.io/example/app:labels .\n"
                "  grouped:\n"
                "    runs-on:\n      group: my-runner-group\n    steps:\n"
                "      - run: docker build -t ghcr.io/example/app:grouped .\n"
            ),
        },
    )
    f = run_bundle(specs, "R12", bundle)
    assert f.status is FindingStatus.MATCHED
    assert len(f.evidence) == 2
    assert any("job 'labels'" in e and "'self-hosted', 'linux', 'x64'" in e for e in f.evidence)
    assert any("job 'grouped'" in e and "'group': 'my-runner-group'" in e for e in f.evidence)
    # both findings live in the same file → locations are de-duplicated
    assert f.locations == (".github/workflows/publish.yml",)


# ===========================================================================
# R13 — two-instrument divergence
# ===========================================================================

def hyperfine_json(times: list[float], **extra) -> dict:
    result = {"command": "python serve.py", "times": list(times)}
    result.update(extra)
    return {"results": [result]}


def lb_entry(n_prompt: int, n_gen: int, samples: list[float], **extra) -> dict:
    entry = {
        "build_commit": "synthetic",
        "model_type": "synthetic 3B",
        "n_prompt": n_prompt,
        "n_gen": n_gen,
        "samples_ts": list(samples),
    }
    entry.update(extra)
    return entry


@pytest.mark.parametrize(
    "payload", [[], {"error": "llama-bench failed"}], ids=["empty-array", "not-an-array"]
)
def test_r13_skips_when_llama_bench_is_not_a_non_empty_array(tmp_path, specs, payload):
    bundle = make_bundle(
        tmp_path,
        "r13-bad-llama-bench",
        probes={
            "llama_bench.json": payload,
            "hyperfine.json": hyperfine_json([1.0, 1.1, 1.2]),
        },
    )
    f = run_bundle(specs, "R13", bundle)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason == "llama_bench JSON is not a non-empty result array"


def test_r13_skips_when_hyperfine_has_no_results(tmp_path, specs):
    bundle = make_bundle(
        tmp_path,
        "r13-no-results",
        probes={
            "llama_bench.json": [lb_entry(0, 128, [50.0, 50.1, 49.9])],
            "hyperfine.json": {"results": []},
        },
    )
    f = run_bundle(specs, "R13", bundle)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason == "hyperfine JSON carries no results[]"


def test_r13_skips_when_hyperfine_has_too_few_timing_samples(tmp_path, specs):
    bundle = make_bundle(
        tmp_path,
        "r13-few-samples",
        probes={
            "llama_bench.json": [lb_entry(0, 128, [50.0, 50.1, 49.9])],
            "hyperfine.json": hyperfine_json([5.6, 5.5]),
        },
    )
    f = run_bundle(specs, "R13", bundle)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason == "hyperfine has 2 timing samples (< 3)"


def test_r13_skips_when_hyperfine_selfreport_disagrees_with_its_samples(tmp_path, specs):
    """The hyperfine-side refusal: reported mean 9.9s vs samples averaging ~5.6s."""
    bundle = make_bundle(
        tmp_path,
        "r13-hyperfine-corrupt",
        probes={
            "llama_bench.json": [
                lb_entry(0, 128, [50.0, 49.6, 50.5], avg_ts=50.033333, stddev_ts=0.450925)
            ],
            "hyperfine.json": hyperfine_json(
                [5.60, 5.55, 5.68, 5.58, 5.63], mean=9.9, stddev=0.05
            ),
        },
    )
    f = run_bundle(specs, "R13", bundle)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason.startswith("hyperfine self-report disagrees with its samples: ")
    assert "reported mean 9.9 disagrees with samples mean 5.608" in f.skipped_reason


def test_r13_entry_without_samples_contributes_no_kernel_time(tmp_path, specs):
    """A sample-less llama-bench entry is skipped by both the cross-check and the sum."""
    bundle = make_bundle(
        tmp_path,
        "r13-empty-samples-entry",
        probes={
            "llama_bench.json": [
                lb_entry(512, 0, [], avg_ts=400.0, stddev_ts=99.0),  # no samples at all
                lb_entry(0, 128, [50.0, 49.6, 50.5, 49.9, 50.3, 49.7, 50.1]),
            ],
            "hyperfine.json": hyperfine_json([4.0, 3.98, 4.02, 3.99, 4.01]),
        },
    )
    f = run_bundle(specs, "R13", bundle)
    assert f.status is FindingStatus.MATCHED
    # only tg128 counts: 128 / 50.0 t/s = 2.560s of the 4.000s wall clock
    assert any(
        "kernel 2.560s (tg128: 2.560s) vs end-to-end 4.000s" in e
        and "36.0% of wall time outside kernels" in e
        for e in f.evidence
    )
    assert "pp512" not in " ".join(f.evidence)
    assert f.fix is not None and f.fix.kind == "code_suggestion"


def test_r13_skips_when_throughput_samples_are_all_zero(tmp_path, specs):
    """A zero-tokens/sec entry cannot be inverted into seconds → no usable data."""
    bundle = make_bundle(
        tmp_path,
        "r13-zero-throughput",
        probes={
            "llama_bench.json": [lb_entry(0, 128, [0.0, 0.0, 0.0])],
            "hyperfine.json": hyperfine_json([4.0, 3.98, 4.02]),
        },
    )
    f = run_bundle(specs, "R13", bundle)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason == "no usable pp/tg samples in llama-bench JSON"


def test_r13_skips_when_entry_declares_neither_prompt_nor_generation(tmp_path, specs):
    bundle = make_bundle(
        tmp_path,
        "r13-no-token-counts",
        probes={
            "llama_bench.json": [lb_entry(0, 0, [50.0, 49.6, 50.5])],
            "hyperfine.json": hyperfine_json([4.0, 3.98, 4.02]),
        },
    )
    f = run_bundle(specs, "R13", bundle)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason == "no usable pp/tg samples in llama-bench JSON"


def test_r13_combined_prompt_and_generation_entry_sums_both_token_counts(tmp_path, specs):
    """A single pp+tg entry contributes (n_prompt + n_gen) / tokens-per-second."""
    bundle = make_bundle(
        tmp_path,
        "r13-combined-entry",
        probes={
            "llama_bench.json": [lb_entry(128, 128, [100.0, 99.5, 100.5, 100.0, 100.2])],
            "hyperfine.json": hyperfine_json([4.0, 3.98, 4.02, 3.99, 4.01]),
        },
    )
    f = run_bundle(specs, "R13", bundle)
    assert f.status is FindingStatus.MATCHED
    # (128 + 128) / 100.0 t/s = 2.560s kernel vs 4.000s end-to-end
    assert any(
        "kernel 2.560s (pg 128+128: 2.560s) vs end-to-end 4.000s" in e
        and "36.0% of wall time outside kernels" in e
        for e in f.evidence
    )


def test_r13_refuses_when_kernel_time_exceeds_end_to_end(tmp_path, specs):
    """Kernel time above wall time means the instruments measured different work."""
    bundle = make_bundle(
        tmp_path,
        "r13-inconsistent-instruments",
        probes={
            "llama_bench.json": [lb_entry(0, 128, [50.0, 49.6, 50.5, 49.9, 50.3])],
            "hyperfine.json": hyperfine_json([1.0, 0.99, 1.01, 1.0, 1.02]),
        },
    )
    f = run_bundle(specs, "R13", bundle)
    assert f.status is FindingStatus.SKIPPED
    assert f.skipped_reason == (
        "kernel time 2.560s exceeds end-to-end 1.000s — instruments "
        "measured different workloads; refusing to diagnose"
    )
    assert f.fix is None
