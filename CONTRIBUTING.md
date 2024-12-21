# Contributing to EOS

Thanks for taking the time to read this!

The `EOS` project is in early development, therefore we encourage contribution in the following ways:

## Bug Reports

Please report flaws or vulnerabilities in the [GitHub Issue Tracker](https://github.com/Akkudoktor-EOS/EOS/issues) using the corresponding issue template.

## Ideas & Features

Please first discuss the idea in a [GitHub Discussion](https://github.com/Akkudoktor-EOS/EOS/discussions) or the [Akkudoktor Forum](https://akkudoktor.net/c/der-akkudoktor/eos/85) before opening an issue.

There are just too many possibilities and the project would drown in tickets otherwise.

## Code Contributions

We welcome code contributions and bug fixes via [Pull Requests](https://github.com/Akkudoktor-EOS/EOS/pulls).
To make collaboration easier, we require pull requests to pass code style and unit tests.


### Setup development environment

Setup virtual environment, then activate virtual environment and install development dependencies.
See also [README.md](README.md).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Install make to get access to helpful shortcuts (documentation generation, manual formatting, etc.).

- On Linux (Ubuntu/Debian):

  ```bash
  sudo apt install make
  ```

- On MacOS (requires [Homebrew](https://brew.sh)):

  ```zsh
  brew install make
  ```

Install development dependencies:

```bash
make pip-dev
```

Start the servers (API server and HTML server with automatic reload on file changes):

```bash
make run-dev
```

A full overview of the main shortcuts is given by `make help`.

### Code Style and Type Checking

[`pre-commit`](https://pre-commit.com) is used for code style and type checks.

To run those checks automatically before every commit:

```bash
pre-commit install
```

Or run them manually:

```bash
pre-commit run --all-files
```

Note: The type check with mypy does not use the mypy installed in the current virtual environment and might lead therefore to different results compared to the pre-commit execution.

### Tests

Use `pytest` to run tests locally:

```bash
pytest
```

Show more debug output and coverage:

```bash
pytest -vs --cov src --cov-report term-missing
```

To run all optimization tests (takes some time):

```bash
pytest --full-run
```
