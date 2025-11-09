# Define the targets
.PHONY: help venv pip install dist test test-full test-system test-ci test-profile docker-run docker-build docs read-docs clean format gitlint mypy run run-dev run-dash run-dash-dev bumps

# Default target
all: help

# Target to display help information
help:
	@echo "Available targets:"
	@echo "  venv         - Set up a Python 3 virtual environment."
	@echo "  pip          - Install dependencies from requirements.txt."
	@echo "  pip-dev      - Install dependencies from requirements-dev.txt."
	@echo "  format       - Format source code."
	@echo "  gitlint      - Lint last commit message."
	@echo "  mypy         - Run mypy."
	@echo "  install      - Install EOS in editable form (development mode) into virtual environment."
	@echo "  docker-run   - Run entire setup on docker"
	@echo "  docker-build - Rebuild docker image"
	@echo "  docs         - Generate HTML documentation (in build/docs/html/)."
	@echo "  read-docs    - Read HTML documentation in your browser."
	@echo "  gen-docs     - Generate openapi.json and docs/_generated/*."
	@echo "  clean-docs   - Remove generated documentation."
	@echo "  run          - Run EOS production server in virtual environment."
	@echo "  run-dev      - Run EOS development server in virtual environment (automatically reloads)."
	@echo "  run-dash     - Run EOSdash production server in virtual environment."
	@echo "  run-dash-dev - Run EOSdash development server in virtual environment (automatically reloads)."
	@echo "  test         - Run tests."
	@echo "  test-full    - Run tests with full optimization."
	@echo "  test-system  - Run tests with system tests enabled."
	@echo "  test-ci      - Run tests as CI does. No user config file allowed."
	@echo "  test-profile - Run single test optimization with profiling."
	@echo "  dist         - Create distribution (in dist/)."
	@echo "  clean        - Remove generated documentation, distribution and virtual environment."
	@echo "  bump         - Bump version to next release version."

# Target to set up a Python 3 virtual environment
venv:
	python3 -m venv .venv
	@echo "Virtual environment created in '.venv'. Activate it using 'source .venv/bin/activate'."

# Target to install dependencies from requirements.txt
pip: venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	@echo "Dependencies installed from requirements.txt."

# Target to install dependencies from requirements.txt
pip-dev: pip
	.venv/bin/pip install -r requirements-dev.txt
	@echo "Dependencies installed from requirements-dev.txt."

# Target to install EOS in editable form (development mode) into virtual environment.
install: pip-dev
	.venv/bin/pip install build
	.venv/bin/pip install -e .
	@echo "EOS installed in editable form (development mode)."

# Target to create a distribution.
dist: pip
	.venv/bin/pip install build
	.venv/bin/python -m build --wheel
	@echo "Distribution created (see dist/)."

# Target to generate documentation
gen-docs: pip-dev
	.venv/bin/pip install -e .
	.venv/bin/python ./scripts/generate_config_md.py --output-file docs/_generated/config.md
	.venv/bin/python ./scripts/generate_openapi_md.py --output-file docs/_generated/openapi.md
	.venv/bin/python ./scripts/generate_openapi.py --output-file openapi.json
	@echo "Documentation generated to openapi.json and docs/_generated."

# Target to build HTML documentation
docs: pip-dev
	.venv/bin/sphinx-build -M html docs build/docs
	@echo "Documentation build to build/docs/html/."

# Target to read the HTML documentation
read-docs: docs
	@echo "Read the documentation in your browser"
	.venv/bin/python -m webbrowser build/docs/html/index.html

# Clean Python bytecode
clean-bytecode:
	find . -type d -name "__pycache__" -exec rm -r {} +
	find . -type f -name "*.pyc" -delete

# Clean target to remove generated documentation and documentation artefacts
clean-docs:
	@echo "Searching and deleting all '_autosum' directories in docs..."
	@find docs -type d -name '_autosummary' -exec rm -rf {} +;
	@echo "Cleaning docs build directories"
	rm -rf build/docs

# Clean target to remove generated documentation, distribution and virtual environment
clean: clean-docs
	@echo "Cleaning virtual env, distribution and build directories"
	rm -rf build .venv
	@echo "Deletion complete."

run:
	@echo "Starting EOS production server, please wait..."
	.venv/bin/python -m akkudoktoreos.server.eos

run-dev:
	@echo "Starting EOS development server, please wait..."
	.venv/bin/python -m akkudoktoreos.server.eos --host localhost --port 8503 --log_level DEBUG --startup_eosdash false --reload true

run-dash:
	@echo "Starting EOSdash production server, please wait..."
	.venv/bin/python -m akkudoktoreos.server.eosdash

run-dash-dev:
	@echo "Starting EOSdash development server, please wait..."
	.venv/bin/python -m akkudoktoreos.server.eosdash --host localhost --port 8504 --log_level DEBUG --reload true

# Target to setup tests.
test-setup: pip-dev
	@echo "Setup tests"

# Target to run tests.
test:
	@echo "Running tests..."
	.venv/bin/pytest -vs --cov src --cov-report term-missing

# Target to run tests as done by CI on Github.
test-ci:
	@echo "Running tests as CI..."
	.venv/bin/pytest --full-run --check-config-side-effect -vs --cov src --cov-report term-missing

# Target to run tests including the system tests.
test-system:
	@echo "Running tests incl. system tests..."
	.venv/bin/pytest --system-test -vs --cov src --cov-report term-missing

# Target to run all tests.
test-full:
	@echo "Running all tests..."
	.venv/bin/pytest --full-run

# Target to run tests including the single test optimization with profiling.
test-profile:
	@echo "Running single test optimization with profiling..."
	.venv/bin/python tests/single_test_optimization.py --profile

# Target to format code.
format:
	.venv/bin/pre-commit run --all-files

# Target to trigger gitlint using pre-commit for the latest commit messages
gitlint:
	.venv/bin/cz check --rev-range main..HEAD

# Target to format code.
mypy:
	.venv/bin/mypy

# Run entire setup on docker
docker-run:
	@docker compose up --remove-orphans

docker-build:
	@docker compose build --pull

# Bump Akkudoktoreos version
VERSION ?= 0.2.0
NEW_VERSION ?= $(VERSION)+dev

bump: pip-dev
	@echo "Bumping akkudoktoreos version from $(VERSION) to $(NEW_VERSION) (dry-run: $(EXTRA_ARGS))"
	.venv/bin/python scripts/convert_lightweight_tags.py
	.venv/bin/python scripts/bump_version.py $(VERSION) $(NEW_VERSION) $(EXTRA_ARGS)

bump-dry: pip-dev
	@echo "Bumping akkudoktoreos version from $(VERSION) to $(NEW_VERSION) (dry-run: --dry-run)"
	.venv/bin/python scripts/convert_lightweight_tags.py
	.venv/bin/python scripts/bump_version.py $(VERSION) $(NEW_VERSION) --dry-run
