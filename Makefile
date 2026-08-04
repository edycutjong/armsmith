# Armsmith — developer harness (Python CLI, hardware-free).
# All targets run offline; none require Arm hardware.
.PHONY: help install lint typecheck test coverage e2e security cards ci-gate all

help:
	@echo "Armsmith make targets:"
	@echo "  install    - editable install with dev extras"
	@echo "  lint       - ruff check (gate)"
	@echo "  typecheck  - mypy (advisory)"
	@echo "  test       - pytest (219 tests, offline)"
	@echo "  coverage   - pytest with coverage report"
	@echo "  e2e        - full offline loop (scripts/verify_offline.py)"
	@echo "  ci-gate    - reproduce-gate CI twin on the replay bundle"
	@echo "  cards      - regenerate the 13 migration-template cards"
	@echo "  security   - pip-audit dependency scan (advisory)"
	@echo "  all        - lint + typecheck + coverage + e2e + ci-gate"

install:
	python -m pip install -e '.[dev]'

lint:
	ruff check .

typecheck:
	mypy src || true   # advisory, not a gate

test:
	python -m pytest -q

coverage:
	python -m pytest -q --cov=armsmith --cov-report=term-missing

e2e:
	@echo "🔁 Offline end-to-end: scan -> gate -> sign -> verify"
	python scripts/verify_offline.py

ci-gate:
	@echo "🚦 Reproduce-gate CI twin (fails on regression)"
	armsmith ci --replay fixtures/replays/scenario_ragserve

cards:
	@echo "📇 Regenerating x86->Arm migration templates"
	armsmith rules export --format md

security:
	@echo "=== pip-audit (advisory) ==="
	pip install -q pip-audit && pip-audit --skip-editable || true

all: lint typecheck coverage e2e ci-gate
	@echo "✅ full harness green"
