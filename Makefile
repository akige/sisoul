# sisoul Makefile — common dev commands.
# Run: make help    to list all targets

.PHONY: help install test test-fast test-smoke build pwa pwa-dev clean lint format \
        coverage release-check daemon docs serve

PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/python -m pip
PYTEST ?= .venv/bin/pytest
SISOUL ?= .venv/bin/sisoul

help:  ## show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage: make <target>\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  %-15s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## install with all extras (Python deps + PWA deps)
	$(PYTHON) -m venv .venv 2>/dev/null || true
	$(PIP) install -e ".[dev,daemon,llm,chat]"
	cd pwa && npm install

test:  ## full test suite (~75s, 2071 cases)
	$(PYTEST) tests -q --tb=line --ignore=tests/test_v1_integration_full_user_journey.py

test-fast:  ## fast tests only (skip daemon smoke)
	$(PYTEST) tests -q --tb=line \
		--ignore=tests/test_v1_integration_full_user_journey.py \
		--ignore=tests/test_alpha_daemon_smoke.py

test-smoke:  ## daemon smoke (uvicorn subprocess + real HTTP)
	$(PYTEST) tests/test_alpha_daemon_smoke.py -v

test-v2:  ## v2 module + daemon + pipeline + cli tests
	$(PYTEST) tests/test_v2*.py -v

test-alpha:  ## alpha launch e2e tests
	$(PYTEST) tests/test_alpha_launch_e2e*.py -v

coverage:  ## test with coverage report
	$(PYTEST) tests -q --cov=sisoul --cov-report=term-missing \
		--ignore=tests/test_v1_integration_full_user_journey.py

build:  ## build wheel + sdist
	$(PYTHON) -m build

pwa:  ## build PWA production bundle
	cd pwa && npm run build

pwa-dev:  ## start PWA dev server (vite on :5173)
	cd pwa && npm run dev

daemon:  ## start sisoul daemon (foreground)
	$(SISOUL) daemon

demo:  ## run sisoul demo (8-step v2 showcase, needs daemon)
	$(SISOUL) demo

health:  ## check daemon health + v2 endpoints
	$(SISOUL) health

self-check:  ## one-shot alpha launch readiness validation
	$(SISOUL) self-check

stats:  ## show local case/skill/friend counters
	$(SISOUL) stats

lint:  ## ruff check + ruff format check + mypy
	$(PYTHON) -m ruff check src/ tests/
	$(PYTHON) -m ruff format --check src/ tests/
	$(PYTHON) -m mypy src/sisoul --ignore-missing-imports || true

format:  ## ruff auto-fix + format
	$(PYTHON) -m ruff check --fix src/ tests/
	$(PYTHON) -m ruff format src/ tests/

shellcheck:  ## shellcheck install + autostart
	shellcheck -S warning ops/install.sh ops/init/install-autostart.sh

security:  ## pip-audit + bandit
	$(PYTHON) -m pip_audit --strict
	$(PYTHON) -m bandit -r src/sisoul -ll

release-check:  ## pre-release validation (test + build + pwa + shellcheck + self-check)
	@echo "=== pytest ==="
	@$(MAKE) test
	@echo "=== build ==="
	@$(MAKE) build
	@echo "=== pwa build ==="
	@$(MAKE) pwa
	@echo "=== shellcheck ==="
	@$(MAKE) shellcheck
	@echo "=== self-check (offline, tmp vault) ==="
	@TMPVAULT=$$(mktemp -d) && \
		echo '{"sisoul_version":"1.0.0-alpha","schema_version":2}' > $$TMPVAULT/dna.json && \
		echo '{}' > $$TMPVAULT/petnames.json && \
		SISOUL_VAULT=$$TMPVAULT $(SISOUL) self-check --skip-daemon --skip-pytest && \
		rm -rf $$TMPVAULT
	@echo ""
	@echo "OK All release checks pass. Ready to: git tag v$$(cat VERSION) && git push origin v$$(cat VERSION)"

clean:  ## remove build artifacts + caches
	rm -rf build/ dist/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache .mypy_cache htmlcov
	cd pwa && rm -rf dist node_modules/.vite 2>/dev/null || true

docs:  ## list available docs
	@echo "Documentation:"
	@echo "  Root:    README.md / CHANGELOG.md / SECURITY.md / CONTRIBUTING.md"
	@echo "           THANKS.md / USER-WAKEUP-SUMMARY.md / ALPHA-LAUNCH-CHECKLIST.md / RELEASE-NOTES-v1.0-alpha.md"
	@echo ""
	@echo "  Docs/:   $$(ls docs/*.md | tr '\n' ' ')"
	@echo ""
	@echo "  CLI:     sisoul cheatsheet    # quick reference"
	@echo "           sisoul <cmd> --help  # any command"

serve:  ## start daemon background + open PWA in browser
	$(SISOUL) daemon --host 127.0.0.1 --port 9876 &
	sleep 2
	@echo "PWA: http://127.0.0.1:9876/pwa (when integrated) or sisoul.github.io/sisoul-pwa/"
	@echo "API: http://127.0.0.1:9876/docs"
	@echo "Metrics: http://127.0.0.1:9876/sisoul/metrics"
