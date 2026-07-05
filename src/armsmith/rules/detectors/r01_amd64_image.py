"""R1 — amd64-pinned container image (static, fully real).

Scans Dockerfiles for FROM lines pinned to linux/amd64 (``--platform=linux/amd64``
or an ``amd64/`` image prefix) and compose files for ``platform: linux/amd64``.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..base import Finding, FindingStatus, Fix, RuleSpec, clean, register

_FROM_RE = re.compile(
    r"^\s*FROM\s+(?:--platform=(?P<platform>\S+)\s+)?(?P<image>\S+)",
    re.IGNORECASE,
)
_COMPOSE_PLATFORM_RE = re.compile(r"^\s*platform:\s*[\"']?(?P<platform>linux/amd64)[\"']?\s*$")

_DOCKERFILE_GLOBS = ("Dockerfile", "Dockerfile.*", "*.dockerfile")
_COMPOSE_GLOBS = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def _iter_files(repo: Path, patterns) -> list[Path]:
    seen: set[Path] = set()
    for pat in patterns:
        for p in sorted(repo.rglob(pat)):
            if ".git" in p.parts or not p.is_file():
                continue
            seen.add(p)
    return sorted(seen)


def _is_amd64_platform(platform: str | None) -> bool:
    if not platform:
        return False
    p = platform.strip().strip("\"'").lower()
    return p in {"linux/amd64", "amd64", "linux/x86_64", "linux/amd64/v2", "linux/amd64/v3"}


@register("R1")
def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
    assert repo is not None  # run_rule guarantees repo for static rules
    evidence: list[str] = []
    locations: list[str] = []
    patched_lines: list[str] = []

    for df in _iter_files(repo, _DOCKERFILE_GLOBS):
        rel = df.relative_to(repo)
        for lineno, line in enumerate(df.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = _FROM_RE.match(line)
            if not m:
                continue
            platform, image = m.group("platform"), m.group("image")
            if _is_amd64_platform(platform):
                evidence.append(f"{rel}:{lineno}: FROM pinned to {platform} → QEMU emulation on arm64 hosts")
                locations.append(f"{rel}:{lineno}")
                fixed = re.sub(r"--platform=\S+\s+", "", line, count=1)
                patched_lines.append(f"- {line.strip()}\n+ {fixed.strip()}")
            elif image.lower().startswith("amd64/"):
                evidence.append(f"{rel}:{lineno}: base image '{image}' is the arch-specific amd64/ variant")
                locations.append(f"{rel}:{lineno}")
                fixed = line.replace("amd64/", "", 1)
                patched_lines.append(f"- {line.strip()}\n+ {fixed.strip()}")

    for cf in _iter_files(repo, _COMPOSE_GLOBS):
        rel = cf.relative_to(repo)
        for lineno, line in enumerate(cf.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _COMPOSE_PLATFORM_RE.match(line):
                evidence.append(f"{rel}:{lineno}: compose service pinned to linux/amd64")
                locations.append(f"{rel}:{lineno}")
                patched_lines.append(f"- {line.strip()}\n+ # platform pin removed — use native arch (or linux/arm64)")

    if not evidence:
        return clean(spec, ["no amd64 platform pins found in Dockerfile/compose files"])

    fix = Fix(
        rule_id=spec.id,
        kind="dockerfile_edit",
        description=(
            "Remove the amd64 platform pin so the native arm64 image variant is "
            "pulled; if the base image is amd64-only, switch to a multi-arch base "
            "or add a buildx multi-platform build."
        ),
        patch="\n".join(patched_lines),
        commands=("docker buildx imagetools inspect <base-image>  # confirm arm64 variant exists",),
    )
    return Finding(
        rule_id=spec.id,
        status=FindingStatus.MATCHED,
        evidence=tuple(evidence),
        locations=tuple(locations),
        fix=fix,
    )
