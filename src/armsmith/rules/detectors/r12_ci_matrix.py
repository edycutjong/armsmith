"""R12 — CI publishes amd64-only images (static, fully real).

Parses ``.github/workflows/*.yml|yaml``:
* ``docker/build-push-action`` steps → checks ``with.platforms`` for arm64;
* ``run:`` script steps calling ``docker buildx build`` → checks ``--platform``;
* plain ``docker build`` on an x86 runner with no arm64 anywhere → amd64-only.

Fires when the workflow builds/pushes an image and no arm64 platform (and no
arm64 runner label) appears in that workflow.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register

ARM64_RUNNER_LABELS = {"ubuntu-24.04-arm", "ubuntu-22.04-arm"}  # verified labels


def _walk_steps(workflow: dict):
    jobs = workflow.get("jobs") or {}
    if not isinstance(jobs, dict):
        return
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        runs_on = job.get("runs-on", "")
        steps = job.get("steps") or []
        if not isinstance(steps, list):
            continue
        for idx, step in enumerate(steps):
            if isinstance(step, dict):
                yield job_name, runs_on, idx, step


def _platforms_of(step: dict) -> str:
    with_block = step.get("with") or {}
    if isinstance(with_block, dict):
        return str(with_block.get("platforms", ""))
    return ""


def _runs_on_arm(runs_on) -> bool:
    if isinstance(runs_on, str):
        return runs_on in ARM64_RUNNER_LABELS
    if isinstance(runs_on, list):
        return any(r in ARM64_RUNNER_LABELS for r in runs_on)
    return False


@register("R12")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert repo is not None
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return clean(spec, ["no GitHub workflows directory — nothing publishes images from CI"])

    evidence: list[str] = []
    locations: list[str] = []
    builds_seen = 0

    for wf_path in sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml"))):
        rel = wf_path.relative_to(repo)
        try:
            workflow = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            evidence.append(f"{rel}: unparseable workflow ({exc.__class__.__name__})")
            continue
        if not isinstance(workflow, dict):
            continue

        for job_name, runs_on, idx, step in _walk_steps(workflow):
            uses = str(step.get("uses", ""))
            run_cmd = str(step.get("run", ""))
            where = f"{rel}: job '{job_name}' step {idx + 1}"

            if uses.startswith("docker/build-push-action"):
                builds_seen += 1
                platforms = _platforms_of(step)
                if "arm64" not in platforms:
                    shown = platforms or "(unset → runner arch only)"
                    evidence.append(
                        f"{where}: build-push-action platforms={shown} — no linux/arm64"
                    )
                    locations.append(str(rel))
            elif "docker buildx build" in run_cmd:
                builds_seen += 1
                if "--platform" not in run_cmd or "arm64" not in run_cmd:
                    evidence.append(f"{where}: buildx build without an arm64 --platform")
                    locations.append(str(rel))
            elif "docker build" in run_cmd and "buildx" not in run_cmd:
                builds_seen += 1
                if not _runs_on_arm(runs_on):
                    evidence.append(
                        f"{where}: plain 'docker build' on runner {runs_on!r} — "
                        "publishes the runner's arch only (amd64)"
                    )
                    locations.append(str(rel))

    if builds_seen == 0:
        return clean(spec, ["workflows found, but none build/push container images"])
    if not evidence:
        return clean(spec, [f"{builds_seen} image-build step(s) all include arm64"])

    fix = Fix(
        rule_id=spec.id,
        kind="ci_patch",
        description=(
            "Add linux/arm64 to the image build matrix: either "
            "platforms: linux/amd64,linux/arm64 on the buildx step (with "
            "docker/setup-qemu-action + docker/setup-buildx-action), or build "
            "natively on the free public-repo arm64 runners."
        ),
        patch=(
            "# option A — buildx multi-arch on the existing runner\n"
            "      - uses: docker/setup-qemu-action@v3\n"
            "      - uses: docker/setup-buildx-action@v3\n"
            "      - uses: docker/build-push-action@v6\n"
            "        with:\n"
            "          platforms: linux/amd64,linux/arm64\n"
            "# option B — native arm64 job (free for public repos)\n"
            "  build-arm64:\n"
            "    runs-on: ubuntu-24.04-arm\n"
        ),
        commands=(),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=tuple(evidence),
        locations=tuple(dict.fromkeys(locations)),
        fix=fix,
    )
