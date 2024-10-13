# Define the targets
.PHONY: help venv pip install dist test test-full docker-run docs clean

# Default target
all: help

# Target to display help information
help:
	@echo "Available targets:"
	@echo "  venv         - Set up a Python 3 virtual environment."
	@echo "  pip          - Install dependencies from requirements.txt."
	@echo "  pip-dev      - Install dependencies from requirements-dev.txt."
	@echo "  install      - Install EOS in editable form (development mode) into virtual environment."
	@echo "  docker-run   - Run entire setup on docker"
	@echo "  docker-build - Rebuild docker image"
	@echo "  docs         - Generate HTML documentation (in build/docs/html/)."
	@echo "  run          - Run flask_server in the virtual environment (needs install before)."
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
	.venv/bin/sphinx-build -M html docs build/docs
	@echo "Documentation generated to build/docs/html/."

# Clean target to remove generated documentation, distribution and virtual environment
clean:
	@echo "Cleaning virtual env, distribution and documentation directories"
	rm -rf dist
	rm -rf .venv

run:
	@echo "Starting flask server, please wait..."
	.venv/bin/python -m akkudoktoreosserver.flask_server

# Target to setup tests.
test-setup: pip-dev
	@echo "Setup tests"

# Target to run tests.
test:
	@echo "Running tests..."
	.venv/bin/pytest

# Target to run all tests.
test-full:
	@echo "Running all tests..."
	.venv/bin/pytest --full-run

# Run entire setup on docker
docker-run:
	@docker compose up --remove-orphans

docker-build:
	@docker compose build
