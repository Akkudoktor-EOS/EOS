# Contributing to EOS

Thanks for taking the time to read this!

The `EOS` project is in early development, therefore we encourage contribution in the following ways:

## Bug Reports

Please report flaws or vulnerabilities in the [GitHub Issue Tracker]((https://github.com/Akkudoktor-EOS/EOS/issues)) using the corresponding issue template.

## Ideas & Features

Please first discuss the idea in a [GitHub Discussion](https://github.com/Akkudoktor-EOS/EOS/discussions) or the [Akkudoktor Forum](https://www.akkudoktor.net/forum/diy-energie-optimierungssystem-opensource-projekt/) before opening an issue.

There are just too many possibilities and the project would drown in tickets otherwise.

## Code Contributions

We welcome code contributions and bug fixes via [Pull Requests](https://github.com/Akkudoktor-EOS/EOS/pulls). We use
forks [see](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/creating-a-pull-request-from-a-fork)
for details. To make collaboration easier, we require pull requests to pass code style and unit tests.

### Code Style

Our code style checks use [`pre-commit`](https://pre-commit.com).

```bash
pip install -r requirements.txt requirements-dev.txt
```

To run formatting automatically before every commit:

```bash
pre-commit install
```

Or run them manually:

```bash
pre-commit --all
```

### Tests

Use `pytest` to run tests locally:

```bash
python -m pytest -vs --cov modules --cov-report term-missing tests/
```
