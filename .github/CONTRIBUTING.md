# Contributing

Thanks for your interest in improving Armsmith! 🎉

## Getting Started
1. Fork the repo and branch from `main`: `git checkout -b feat/your-feature`
2. Create a virtualenv and install: `python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'`
3. Run the suite: `python -m pytest -q` (219 tests, fully offline)

## Before You Open a PR
- `ruff check .` passes (lint gate).
- `python -m pytest -q` passes; add/update tests for any behavior change.
- `python scripts/verify_offline.py` prints `ALL CHECKS PASSED`.
- Keep commits conventional (`feat:`, `fix:`, `docs:`, `chore:`).

## Adding a rule (the common contribution)
Armsmith's 13-rule pack is data-driven — a 14th rule needs **no core change**:
1. Drop a YAML descriptor into `src/armsmith/rules/packs/rNN.yaml` (see an
   existing one for the required fields, incl. an optional `learning_path`).
2. Register a detector under the same id in `src/armsmith/rules/detectors/`.
3. Add a positive/negative fixture pair under `fixtures/rules/rNN_{pos,neg}/`.
The loader validates the pack ↔ detector registry agree 1:1.

## Honesty discipline (non-negotiable)
Armsmith never fabricates a hardware measurement. Anything not measured on real
silicon is a labeled synthetic replay fixture or a `TODO(S1)` in code. Only the
reproduce gate can claim a result; the planner proposes, the gate disposes.

## Reporting Bugs / Requesting Features
Open an issue using the provided templates. Include repro steps, expected vs.
actual behavior, and environment details.
