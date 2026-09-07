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

Use `uv` to create the virtual environment and install development dependencies.

```bash
uv sync --locked --extra dev
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

All code, comments, docstrings, identifiers and API field names are written in English.

Our code style checks use [`pre-commit`](https://pre-commit.com).

To run formatting automatically before every commit:

```bash
uv run --locked --extra dev pre-commit install
uv run --locked --extra dev pre-commit install --hook-type commit-msg --hook-type pre-push
```

Or run them manually:

```bash
uv run --locked --extra dev pre-commit run --all-files
```

### Static typing

Use `uv` on your `PATH` and the Python version pinned in `.python-version` (also used by the
pre-commit CI job). The supported typing entry points are:

```bash
make mypy
uv run --locked --extra dev pre-commit run mypy --all-files
```

Both run `uv run --locked --exact --extra dev python -m mypy --config-file pyproject.toml`.
CI runs the same local pre-commit hook. The environment includes all runtime dependencies and
development stubs from `uv.lock`, including the type information supplied by Pydantic and Pendulum.
`--locked` rejects an out-of-date lockfile instead of updating it, and `--exact` removes packages
outside the selected locked dependencies. Keep the Makefile and hook commands identical.

`[tool.mypy]` in `pyproject.toml` defines the policy for all of `src` and `tests`, targeting Python
3.13 and Linux. The hook always checks this complete scope, including on configuration-only changes.
It does not add the old mirror hook's `--ignore-missing-imports` or `--scripts-are-modules` defaults.
Only the existing per-module missing-import exceptions in `pyproject.toml` apply.
Incremental analysis is disabled because mypy 2.3.1 produces different Pendulum diagnostics with
warm and empty caches. Each entry point therefore performs a full analysis; this costs time but
keeps diagnostics independent of cache history without suppressing checks.

To regression-test the entry points run:

```bash
uv run --locked --extra dev pytest -q --finalize tests/test_typingmypytoolchain.py
```

This test creates a temporary project and a fresh locked development environment and hook/type-check
caches. It verifies valid Pydantic and Pendulum assignments, then deliberate type errors in both
`src` and `tests`, through Makefile, a configuration-only hook run, and the CI command. All probes
stay in the temporary project. It may download locked packages; set `UV_CACHE_DIR` to an empty
temporary directory as well to verify without a warm package cache.

### Tests

Use `pytest` to run tests locally:

```bash
uv run python -m pytest -vs --cov src --cov-report term-missing tests/
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
