# Define the targets
.PHONY: help venv pip install dist test test-full docker-run docker-build docs read-docs clean format run run-dev

# Default target
all: help

# Target to display help information
help:
	@echo "Available targets:"
	@echo "  venv         - Set up a Python 3 virtual environment."
	@echo "  pip          - Install dependencies from requirements.txt."
	@echo "  pip-dev      - Install dependencies from requirements-dev.txt."
	@echo "  format       - Format source code."
	@echo "  install      - Install EOS in editable form (development mode) into virtual environment."
	@echo "  docker-run   - Run entire setup on docker"
	@echo "  docker-build - Rebuild docker image"
	@echo "  docs         - Generate HTML documentation (in build/docs/html/)."
	@echo "  read-docs    - Read HTML documentation in your browser."
	@echo "  run          - Run FastAPI production server in the virtual environment."
	@echo "  run-dev      - Run FastAPI development server in the virtual environment (automatically reloads)."
	@echo "  dist         - Create distribution (in dist/)."
	@echo "  clean        - Remove generated documentation, distribution and virtual environment."

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
install: pip
	.venv/bin/pip install build
	.venv/bin/pip install -e .
	@echo "EOS installed in editable form (development mode)."

# Target to create a distribution.
dist: pip
	.venv/bin/pip install build
	.venv/bin/python -m build --wheel
	@echo "Distribution created (see dist/)."

# Target to generate HTML documentation
docs: pip-dev
	mkdir -p docs/develop
	cp README.md docs/develop/getting_started.md
	# remove top level header and coresponding description
	sed -i '/^##[^#]/,$$!d' docs/develop/getting_started.md
	sed -i "1i\# Getting Started\n" docs/develop/getting_started.md
	cp CONTRIBUTING.md docs/develop
	sed -i "s/README.md/getting_started.md/g" docs/develop/CONTRIBUTING.md
	.venv/bin/sphinx-build -M html docs build/docs
	@echo "Documentation generated to build/docs/html/."

# Target to read the HTML documentation
read-docs: docs
	@echo "Read the documentation in your browser"
	.venv/bin/python -m webbrowser build/docs/html/index.html

# Clean target to remove generated documentation, distribution and virtual environment
clean:
	@echo "Cleaning virtual env, distribution and build directories"
	rm -rf dist build .venv
	@echo "Searching and deleting all '_autosum' directories in docs..."
	@find docs -type d -name '_autosummary' -exec rm -rf {} +;
	@echo "Deletion complete."

run:
	@echo "Starting FastAPI server, please wait..."
	.venv/bin/fastapi run --port 8503 src/akkudoktoreos/server/fastapi_server.py

run-dev:
	@echo "Starting FastAPI development server, please wait..."
	.venv/bin/fastapi dev --port 8503 src/akkudoktoreos/server/fastapi_server.py

# Target to setup tests.
test-setup: pip-dev
	@echo "Setup tests"

# Target to run tests.
test:
	@echo "Running tests..."
	.venv/bin/pytest -vs --cov src --cov-report term-missing

# Target to run all tests.
test-full:
	@echo "Running all tests..."
	.venv/bin/pytest --full-run

# Target to format code.
format:
	.venv/bin/pre-commit run --all-files

# Run entire setup on docker
docker-run:
	@docker compose up --remove-orphans

docker-build:
	@docker compose build --pull
