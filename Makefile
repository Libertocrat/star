.PHONY: help deps deps-local fmt fmt-shell lint lint-shell lint-shell-format lint-actions typecheck test \
	bandit pip-audit hadolint semgrep trivy \
	quality ci-security deep-security ci full \
	build

PYTHON ?= python
PIP ?= pip

SRC_DIRS = src tests scripts
SHELL_FILES := $(shell find . \
	-type f \
	\( -name '*.sh' -o -name 'star' \) \
	-not -path './.git/*' \
	-not -path './.venv/*' \
	-not -path './venv/*' \
	-not -path './node_modules/*' \
	-not -path './private/*')
REQ_RUNTIME = requirements/runtime.txt
REQ_DEV = requirements/dev.txt
REQ_TESTING = requirements/testing.txt
REQ_LINTING = requirements/linting.txt
REQ_SECURITY = requirements/security.txt

BANDIT_TARGETS = src/
BANDIT_FLAGS = --recursive --severity-level medium --confidence-level medium
PIP_AUDIT_FLAGS = -r $(REQ_RUNTIME)
SEMGREP_FLAGS = --error --config p/ci --config p/python --config p/security-audit --exclude .venv .
SEMGREP_VERSION := 1.155.0
TRIVY_FS_FLAGS = fs \
	--scanners secret,misconfig \
	--severity HIGH,CRITICAL \
	--no-progress \
	--quiet \
	--format json \
	--skip-dirs .venv \
	.
TRIVY_IMAGE_FLAGS = image \
	--scanners vuln \
	--severity HIGH,CRITICAL \
	--ignore-unfixed \
	--no-progress \
	--format json
IMAGE_NAME ?= star
IMAGE_TAG ?= local
IMAGE_TARGET := $(IMAGE_NAME):$(IMAGE_TAG)

# -----------------------------
# Help
# -----------------------------

help:
	@echo "== Setup =="
	@echo "make deps           - Install Python project dependencies"
	@echo "make deps-local     - Install local CLI tools (pipx + semgrep)"
	@echo ""
	@echo "== DX =="
	@echo "make fmt            - Fix formatting and lint issues (shfmt + black + ruff + EOF/Whitespaces)"
	@echo "make fmt-shell      - Format shell scripts with shfmt"
	@echo ""
	@echo "== Quality =="
	@echo "make lint-shell     - Validate shell formatting and run ShellCheck"
	@echo "make quality        - Lint + typecheck + tests"
	@echo ""
	@echo "== Build =="
	@echo "make build          - Build Docker image locally"
	@echo ""
	@echo "== Security =="
	@echo "make ci-security    - Baseline security (bandit + pip-audit + hadolint)"
	@echo "make deep-security  - Advanced scans (semgrep + trivy fs + trivy image)"
	@echo ""
	@echo "== CI =="
	@echo "make ci             - quality + ci-security"
	@echo "make full           - ci + build + deep-security"

# -----------------------------
# Dependency Setup
# -----------------------------

deps:
	$(PIP) install -U pip
	$(PIP) install -r $(REQ_DEV)

deps-local:
	$(PIP) install -U pipx
	pipx install --force "semgrep==$(SEMGREP_VERSION)"
	pipx ensurepath || true
	@echo "Ensure Trivy is installed system-wide (apt install trivy)"

# -----------------------------
# Quality
# -----------------------------

fmt: fmt-shell
	@echo "Running formatting fixes..."
	black $(SRC_DIRS)
	ruff check --fix $(SRC_DIRS)
	pre-commit run trailing-whitespace --all-files || true
	pre-commit run end-of-file-fixer --all-files || true
	@echo "Formatting complete."

fmt-shell:
	@echo "Formatting shell scripts with shfmt..."
	@if [ -z "$(SHELL_FILES)" ]; then \
		echo "No shell scripts found."; \
	else \
		shfmt -w -i 4 -ci -sr $(SHELL_FILES); \
	fi

lint: lint-shell lint-actions
	black --check $(SRC_DIRS)
	ruff check $(SRC_DIRS)

lint-shell: lint-shell-format
	@echo "Running ShellCheck..."
	@if [ -z "$(SHELL_FILES)" ]; then \
		echo "No shell scripts found."; \
	else \
		shellcheck -x $(SHELL_FILES); \
	fi

lint-shell-format:
	@echo "Validating shell formatting with shfmt..."
	@if [ -z "$(SHELL_FILES)" ]; then \
		echo "No shell scripts found."; \
	else \
		shfmt -d -i 4 -ci -sr $(SHELL_FILES); \
	fi

lint-actions:
	@echo "Running actionlint..."
	actionlint

typecheck:
	mypy --config-file mypy.ini $(SRC_DIRS)

test:
	pytest -q tests

quality: lint typecheck test
	@echo "Quality checks passed."

# -----------------------------
# Baseline Security (Fast)
# -----------------------------

bandit:
	bandit $(BANDIT_FLAGS) $(BANDIT_TARGETS)

pip-audit:
	pip-audit $(PIP_AUDIT_FLAGS)

hadolint:
	hadolint ./Dockerfile

ci-security: bandit pip-audit hadolint
	@echo "Baseline security checks passed."

# -----------------------------
# Build
# -----------------------------

build:
	docker build -t $(IMAGE_TARGET) .
	@echo "Docker image $(IMAGE_TARGET) built successfully."

# -----------------------------
# Deep Security (Heavy)
# -----------------------------

semgrep:
	semgrep scan $(SEMGREP_FLAGS)

# To keep both Trivy's log outputs clean, json reports are parsed to extract
# HIGH/CRITICAL vulnerability counts. The CI gates fail, with exit code 1, if any are found.
trivy-fs:
	@echo "Running Trivy filesystem scan..."
	@set -e; \
	trivy $(TRIVY_FS_FLAGS) -o trivy-fs-report.json; \
	HIGH_COUNT=$$(jq '[.Results[]? | .Misconfigurations[]? | select(.Severity=="HIGH")] | length' trivy-fs-report.json); \
	CRITICAL_COUNT=$$(jq '[.Results[]? | .Misconfigurations[]? | select(.Severity=="CRITICAL")] | length' trivy-fs-report.json); \
	SECRET_COUNT=$$(jq '[.Results[]? | .Secrets[]?] | length' trivy-fs-report.json); \
	echo "TRIVY_FS_SUMMARY HIGH=$$HIGH_COUNT CRITICAL=$$CRITICAL_COUNT SECRETS=$$SECRET_COUNT"; \
	rm -f trivy-fs-report.json; \
	[ "$$HIGH_COUNT" -eq 0 ] && [ "$$CRITICAL_COUNT" -eq 0 ] && [ "$$SECRET_COUNT" -eq 0 ]

trivy-image: build
	@echo "Running Trivy image scan..."
	@set -e; \
	trivy $(TRIVY_IMAGE_FLAGS) -o trivy-image.json $(IMAGE_TARGET); \
	HIGH_COUNT=$$(jq '[.Results[]? | .Vulnerabilities[]? | select(.Severity=="HIGH")] | length' trivy-image.json); \
	CRITICAL_COUNT=$$(jq '[.Results[]? | .Vulnerabilities[]? | select(.Severity=="CRITICAL")] | length' trivy-image.json); \
	echo "TRIVY_IMAGE_SUMMARY HIGH=$$HIGH_COUNT CRITICAL=$$CRITICAL_COUNT"; \
	rm -f trivy-image.json; \
	[ "$$HIGH_COUNT" -eq 0 ] && [ "$$CRITICAL_COUNT" -eq 0 ]

deep-security: semgrep trivy-fs trivy-image
	@echo "Deep security scans passed."

# -----------------------------
# CI Aggregates
# -----------------------------

ci: quality ci-security
	@echo "CI gate passed."

full: ci build deep-security
	@echo "Full pipeline passed."
