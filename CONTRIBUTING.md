# Contributing to EOS

Thanks for taking the time to read this!

The `EOS` project is in early development, therefore we encourage contribution in the following ways:

## Documentation

Latest development documentation can be found at [Akkudoktor-EOS](https://akkudoktor-eos.readthedocs.io/en/latest/).

## Bug Reports

Please report flaws or vulnerabilities in the [GitHub Issue Tracker](https://github.com/Akkudoktor-EOS/EOS/issues) using the corresponding issue template.

## Ideas & Features

Issues in the [GitHub Issue Tracker](https://github.com/Akkudoktor-EOS/EOS/issues) are also fine
to discuss ideas and features.

You may first discuss the idea in the [Akkudoktor Forum](https://www.akkudoktor.net/forum/diy-energie-optimierungssystem-opensource-projekt/) before opening an issue.

## Code Contributions

We welcome code contributions and bug fixes via [Pull Requests](https://github.com/Akkudoktor-EOS/EOS/pulls).
To make collaboration easier, we require pull requests to pass code style, unit tests, and commit
message style checks.

### Setup development environment

Setup virtual environment, then activate virtual environment and install development dependencies.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .
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

The server can be started with `make run`. A full overview of the main shortcuts is given by `make help`.

### Code Style

Our code style checks use [`pre-commit`](https://pre-commit.com).

To run formatting automatically before every commit:

```bash
pre-commit install
pre-commit install --hook-type commit-msg --hook-type pre-push
```

Or run them manually:

```bash
pre-commit run --all-files
```

### Tests

Use `pytest` to run tests locally:

```bash
python -m pytest -vs --cov src --cov-report term-missing tests/
```

### Commit message style

Our commit message checks use
[`commitizen`](https://commitizen-tools.github.io/commitizen/#pre-commit-integration). The checks
enforce the [`Conventional Commits`](https://www.conventionalcommits.org) commit message style.

You may use [`commitizen`](https://commitizen-tools.github.io/commitizen) also to create a
commit message and commit your change.

## Thank you!

And last but not least thanks to all our contributors

[![Contributors](https://contrib.rocks/image?repo=Akkudoktor-EOS/EOS)](https://github.com/Akkudoktor-EOS/EOS/graphs/contributors)
