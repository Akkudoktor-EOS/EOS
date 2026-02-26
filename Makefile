# Define the targets
.PHONY: help install dist test test-system test-ci test-profile \
        docker-run docker-build docs read-docs clean format gitlint mypy \
        run run-dev run-dash run-dash-dev prepare-version test-version uv-update

# Use uv for all program actions
UV := uv
PYTHON := $(UV) run python
PYTEST := $(UV) run pytest
MYPY := $(UV) run mypy
PRECOMMIT := $(UV) run pre-commit
COMMITIZEN := $(UV) run cz

# - Take VERSION from version.py
VERSION := $(shell $(PYTHON) scripts/get_version.py)

# Default target
all: help

# Target to display help information
help:
	@echo "Available targets:"
	@echo "  format        - Format source code."
	@echo "  gitlint       - Lint last commit message."
	@echo "  mypy          - Run mypy."
	@echo "  install       - Install EOS in editable form (development mode) into virtual environment."
	@echo "  update-env    - Update virtual environmenr to match pyproject.toml."
	@echo "  docker-run    - Run entire setup on docker"
	@echo "  docker-build  - Rebuild docker image"
	@echo "  docs          - Generate HTML documentation (in build/docs/html/)."
	@echo "  read-docs     - Read HTML documentation in your browser."
	@echo "  gen-docs      - Generate openapi.json and docs/_generated/*."
	@echo "  clean-docs    - Remove generated documentation."
	@echo "  run           - Run EOS production server in virtual environment."
	@echo "  run-dev       - Run EOS development server in virtual environment (automatically reloads)."
	@echo "  run-dash      - Run EOSdash production server in virtual environment."
	@echo "  run-dash-dev  - Run EOSdash development server in virtual environment (automatically reloads)."
	@echo "  test          - Run tests."
	@echo "  test-finalize - Run all tests (e.g. to finalize a commit)."
	@echo "  test-system   - Run tests with system tests enabled."
	@echo "  test-ci       - Run tests as CI does. No user config file allowed."
	@echo "  test-profile  - Run single test optimization with profiling."
	@echo "  dist          - Create distribution (in dist/)."
	@echo "  clean         - Remove generated documentation, distribution and virtual environment."
	@echo "  prepare-version - Prepare a version defined in setup.py."

# Target to create a version.txt
version-txt:
	# Get the version from the package for setuptools (and pip)
	VERSION=$$(${PYTHON} scripts/get_version.py)
	@echo "$(VERSION)" > version.txt
	@echo "version.txt set to '$(VERSION)'."

# Target to install EOS in editable form (development mode) into virtual environment.
install: version-txt
	# Upgrade installation and dependencies
	$(UV) sync --extra dev
	@echo "EOS version $(VERSION) installed in editable form (development mode)."

# Target to rebuild the virtual environment.
update-env:
	@echo "Rebuilding virtual environment to match pyproject.toml..."
	uv rebuild
	@echo "Environment rebuilt."

# Target to create a distribution.
dist: version-txt
	$(PIP) install build
	$(PYTHON) -m build --wheel
	@echo "Distribution created (see dist/)."

# Target to generate documentation
gen-docs: version-txt
	$(PYTHON) ./scripts/generate_config_md.py --output-file docs/_generated/config.md
	$(PYTHON) ./scripts/generate_openapi_md.py --output-file docs/_generated/openapi.md
	$(PYTHON) ./scripts/generate_openapi.py --output-file openapi.json
	@echo "Documentation generated to openapi.json and docs/_generated."

# Target to build HTML documentation
docs: install
	$(PYTEST) --finalize tests/test_docsphinx.py
	@echo "Documentation build to build/docs/html/."

# Target to read the HTML documentation
read-docs:
	@echo "Read the documentation in your browser"
	$(PYTEST) --finalize tests/test_docsphinx.py
	$(PYTHON) -m webbrowser build/docs/html/index.html

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
	@echo "Cleaning uv environment"
	$(UV) clean
	@echo "Deletion complete."

run:
	@echo "Starting EOS production server, please wait..."
	$(PYTHON) -m akkudoktoreos.server.eos --startup_eosdash true

run-dev:
	@echo "Starting EOS development server, please wait..."
	$(PYTHON) -m akkudoktoreos.server.eos --host localhost --port 8503 --log_level DEBUG --startup_eosdash false --reload true

run-dash:
	@echo "Starting EOSdash production server, please wait..."
	$(PYTHON) -m akkudoktoreos.server.eosdash

run-dash-dev:
	@echo "Starting EOSdash development server, please wait..."
	$(PYTHON) -m akkudoktoreos.server.eosdash --host localhost --port 8504 --log_level DEBUG --reload true

# Target to setup tests.
test-setup: install
	@echo "Setup tests"

# Target to run tests.
test:
	@echo "Running tests..."
	$(PYTEST) -vs --cov src --cov-report term-missing

# Target to run tests as done by CI on Github.
test-ci:
	@echo "Running tests as CI..."
	$(PYTEST) --finalize --check-config-side-effect -vs --cov src --cov-report term-missing

# Target to run tests including the system tests.
test-system:
	@echo "Running tests incl. system tests..."
	$(PYTEST) --system-test -vs --cov src --cov-report term-missing

# Target to run all tests.
test-finalize:
	@echo "Running all tests..."
	$(PYTEST) --finalize

# Target to run tests including the single test optimization with profiling.
test-profile:
	@echo "Running single test optimization with profiling..."
	$(PYTHON) tests/single_test_optimization.py --profile

# Target to format code.
format:
	$(PRECOMMIT) run --all-files

# Target to trigger git linting using commitizen for the latest commit messages
gitlint:
	$(COMMITIZEN) check --rev-range main..HEAD

# Target to format code.
mypy:
	$(MYPY)

# Run entire setup on docker
docker-run:
	@echo "Build and run EOS docker container locally."
	@echo "Persistent data (and config) in ${HOME}/.local/share/net.akkudoktor.eos"
	@docker pull python:3.13.9-slim
	@docker compose up --remove-orphans

docker-build:
	@echo "Build EOS docker container locally."
	@echo "Persistent data (and config) in ${HOME}/.local/share/net.akkudoktor.eos"
	@docker pull python:3.13.9-slim
	@docker compose build

# Propagete version info to all version files
# Take UPDATE_FILES from GitHub action bump-version.yml
UPDATE_FILES := $(shell sed -n 's/^[[:space:]]*UPDATE_FILES[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
                        .github/workflows/bump-version.yml)
prepare-version: install
	@echo "Update version to $(VERSION) from version.py in files $(UPDATE_FILES) and doc"
	$(PYTHON) ./scripts/update_version.py $(VERSION) $(UPDATE_FILES)
	$(PYTHON) ./scripts/convert_lightweight_tags.py
	$(PYTHON) ./scripts/generate_config_md.py --output-file docs/_generated/config.md
	$(PYTHON) ./scripts/generate_openapi_md.py --output-file docs/_generated/openapi.md
	$(PYTHON) ./scripts/generate_openapi.py --output-file openapi.json
	$(PYTEST) -vv --finalize tests/test_doc.py

test-version:
	echo "Test version information to be correctly set in all version files"
	$(PYTEST) -vv tests/test_version.py
