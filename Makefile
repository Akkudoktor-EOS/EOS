# Define the targets
.PHONY: help venv pip docs clean

# Default target
all: help

# Target to display help information
help:
	@echo "Available targets:"
	@echo "  venv    - Set up a Python 3 virtual environment."
	@echo "  pip     - Install dependencies from requirements.txt."
	@echo "  docs    - Generate HTML documentation using pdoc."
	@echo "  run     - Run flask_server.py in the virtual environment."
	@echo "  clean   - Remove generated documentation and virtual environment."

# Target to set up a Python 3 virtual environment
venv:
	python3 -m venv .venv
	@echo "Virtual environment created in '.venv'. Activate it using 'source .venv/bin/activate'."

# Target to install dependencies from requirements.txt
pip: venv
	.venv/bin/pip install -r requirements.txt
	@echo "Dependencies installed from requirements.txt."

# Target to generate HTML documentation
docs: pip
	pdoc --html --force modules -o docs

# Clean target to remove generated documentation and virtual environment
clean:
	@echo "Cleaning virtual env and documentation directories"
	rm -rf docs
	rm -rf .venv

run:
	@echo "Starting flask server, please wait..."
	.venv/bin/python ./flask_server.py

# Run entire setup on docker
docker-run:
	@docker-compose up
