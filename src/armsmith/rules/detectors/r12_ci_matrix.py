"""R12 — CI publishes amd64-only images (static, fully real).

Parses ``.github/workflows/*.yml|yaml``:
* ``docker/build-push-action`` steps → checks ``with.platforms`` for arm64;
* ``run:`` script steps calling ``docker buildx build`` → checks ``--platform``;
* plain ``docker build`` on an x86 runner with no arm64 anywhere → amd64-only.

Fires when the workflow builds/pushes an image and no arm64 platform (and no
arm64 runner label) appears in that workflow.
"""

from __future__ import annotations

import re
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
                yield job_name, runs_on, idx, step, job


#: ${{ matrix.<path> }} — the value is decided by the job's strategy block,
#: not written literally in the step.
_MATRIX_EXPR = re.compile(r"\$\{\{\s*matrix\.([A-Za-z0-9_.-]+)\s*\}\}")


def _matrix_values(job: dict, path: str) -> list[str]:
    """Resolve ``matrix.a.b`` to every value it can take in this job.

    Handles the two shapes that actually occur: a plain key (``matrix.platform``)
    and a key inside a list of objects (``matrix.config.platforms``, which is how
    llama.cpp declares its Docker matrix). ``include:`` entries are scanned too,
    since GitHub merges them into the expansion.
    """
    strategy = job.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not isinstance(matrix, dict):
        return []
    keys = path.split(".")
    found: list[str] = []

    def walk(node, remaining):
        if not remaining:
            # A terminal LIST is the commonest matrix form —
            # `matrix: {platform: [linux/amd64, linux/arm64]}` — so its scalars
            # must be yielded. Recursing without consuming a key here is what
            # made plain-key matrices resolve to nothing, silently turning an
            # amd64-only build into a clean result.
            if isinstance(node, list):
                for item in node:
                    walk(item, remaining)
            elif node is not None and not isinstance(node, dict):
                found.append(str(node))
            return
        if isinstance(node, list):
            for item in node:
                walk(item, remaining)
        elif isinstance(node, dict) and remaining[0] in node:
            walk(node[remaining[0]], remaining[1:])

    for root in (matrix, *(matrix.get("include") or [] if isinstance(matrix.get("include"), list) else ())):
        if isinstance(root, dict) and keys[0] in root:
            walk(root[keys[0]], keys[1:])
    return found


def _platforms_of(step: dict, job: dict | None = None) -> tuple[str, str | None]:
    """Return ``(raw, resolved)`` for a build step's ``platforms`` input.

    ``resolved`` is what the arm64 check should read. It is ``None`` when the
    value is a matrix expression we cannot resolve — in that case the rule must
    stay silent rather than guess. Reporting an unresolved ``${{ matrix.* }}``
    as "no linux/arm64" is a false positive, and it fired on llama.cpp, whose
    matrix does include ``linux/arm64``.
    """
    with_block = step.get("with") or {}
    if not isinstance(with_block, dict):
        return "", ""
    raw = str(with_block.get("platforms", ""))
    exprs = _MATRIX_EXPR.findall(raw)
    if not exprs:
        return raw, raw
    if job is None:
        return raw, None
    resolved_parts = [raw]
    for path in exprs:
        values = _matrix_values(job, path)
        if not values:
            return raw, None          # unresolvable — do not guess
        resolved_parts.extend(values)
    return raw, " ".join(resolved_parts)


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

        for job_name, runs_on, idx, step, job in _walk_steps(workflow):
            uses = str(step.get("uses", ""))
            run_cmd = str(step.get("run", ""))
            where = f"{rel}: job '{job_name}' step {idx + 1}"

            if uses.startswith("docker/build-push-action"):
                builds_seen += 1
                raw, resolved = _platforms_of(step, job)
                if resolved is None:
                    # Matrix-driven and not resolvable from this file. Silence
                    # beats a guess: the matrix may well include arm64.
                    evidence.append(
                        f"{where}: platforms={raw} is matrix-driven and could not be "
                        "resolved statically — not reported"
                    )
                elif "arm64" not in resolved:
                    shown = raw or "(unset → runner arch only)"
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
    # Gate on locations, not evidence: notes about steps we deliberately did
    # NOT report (unresolvable matrix expressions, unparseable files) are
    # recorded as evidence but must never make the rule fire.
    if not locations:
        return clean(spec, evidence or [f"{builds_seen} image-build step(s) all include arm64"])

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
