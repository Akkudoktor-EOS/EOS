---
name: filename-conventions
description: Use when creating, renaming, or proposing any new file in this repository. Mandatory filename rules derived from [tool.eos-filename-linter] in pyproject.toml.
---

# Filename Conventions

**Filename conventions are mandatory.** All new and renamed files must comply
with the rules configured in `pyproject.toml`. The custom filename linter is the
authoritative source; if this document and the configuration appear
inconsistent, follow the configuration.

## Procedure

1. Inspect `[tool.eos-filename-linter]` in `pyproject.toml`.
2. Find the first matching rule for the file's repository-relative path.
3. Apply that rule's regex, `allowed_names`, exclusions, and max length.
4. Do not invent alternative conventions or infer acceptability from valid Python.
5. Do not weaken the linter configuration to accommodate a filename.

## Python source modules (`src/akkudoktoreos/`)

- Follow the configured pattern for that directory.
- Where lowercase-alphanumeric is required: lowercase letters and digits only,
  no underscores or hyphens.
- Preserve listed `allowed_names` (e.g. `__init__.py`).
- Do not rename existing modules for stylistic preference.

## Pytest modules (`tests/`)

Where the rule requires `test_` + lowercase alphanumerics:

- Start with `test_`, then lowercase letters/digits only.
- No underscores or hyphens after the prefix.
- Keep `.py`.
- Respect configured exceptions/exclusions.
- If a separate rule applies (e.g. `single_test_*.py`), follow that rule.

| Valid | Invalid |
|---|---|
| `test_adapterdevice.py` | `test_adapter_device.py` |
| `test_geneticforecastcoverage.py` | `test_genetic_forecast_coverage.py` |
| `test_invertercontrol.py` | `test_inverter-control.py` |

## Python scripts (`scripts/`)

Where lowercase snake_case is required:

- Lowercase letters and digits, underscores to separate words, no hyphens, `.py`.
- Valid: `lint_filenames.py`
- Invalid: `lint-filenames.py`, `LintFilenames.py`

## Filename length

Respect the max length from the applicable rule, else the global default in
`pyproject.toml` / linter. The limit includes the extension.

## Verify

```bash
uv run python scripts/lint_filenames.py
uv run python scripts/lint_filenames.py path/to/file.py
