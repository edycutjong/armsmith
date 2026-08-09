# Contributing

Thanks for your interest in improving Armsmith! 🎉

## Getting Started
1. Fork the repo and branch from `main`: `git checkout -b feat/your-feature`
2. Create a virtualenv and install: `python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'`
3. Run the suite: `python -m pytest -q` (386 tests, fully offline)

## Before You Open a PR
- `ruff check .` passes (lint gate).
- `python -m pytest -q` passes; add/update tests for any behavior change.
- `python scripts/verify_offline.py` prints `ALL CHECKS PASSED`.
- Keep commits conventional (`feat:`, `fix:`, `docs:`, `chore:`).

## Adding a rule (the common contribution)
Armsmith's 13-rule pack is data-driven — a 14th rule needs **one YAML + one
detector + one import line**, and no change to the engine itself:
1. Drop a YAML descriptor into `src/armsmith/rules/packs/rNN.yaml` (see an
   existing one for the required fields, incl. an optional `learning_path`).
2. Register a detector under the same id in `src/armsmith/rules/detectors/`.
   The signature is fixed — `repo` is the scan root (`None` for probe-only
   rules), `probe` is the replay bundle, and you must return a `Finding`:

   ```python
   from pathlib import Path
   from ..base import Finding, FindingStatus, RuleSpec, clean, register

   @register("R14")
   def detect(repo: Path | None, probe, spec: RuleSpec) -> Finding:
       assert repo is not None
       hits = [f"{p.name}:1" for p in repo.rglob("*.cfg")]
       if not hits:
           return clean(spec, ["no .cfg files present"])
       return Finding(
           rule_id=spec.id,
           status=FindingStatus.MATCHED,
           evidence=tuple(hits),
           locations=tuple(hits),
       )
   ```
3. Add the import to `src/armsmith/rules/detectors/__init__.py` so the
   `@register` decorator actually runs — without it the loader raises
   `ValueError: rules without detectors: ['R14']`.
4. Add a positive/negative fixture pair under `fixtures/rules/rNN_{pos,neg}/`.
The loader validates the pack ↔ detector registry agree 1:1.

## Honesty discipline (non-negotiable)
Armsmith never fabricates a hardware measurement. Anything not measured on real
silicon is a labeled synthetic replay fixture or a `TODO(S1)` in code. Only the
reproduce gate can claim a result; the planner proposes, the gate disposes.

## Reporting Bugs / Requesting Features
Open an issue using the provided templates. Include repro steps, expected vs.
actual behavior, and environment details.
