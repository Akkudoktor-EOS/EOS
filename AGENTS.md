# AGENTS.md — Akkudoktor-EOS

## Project Overview

Akkudoktor-EOS is a Python-based energy management and optimization system. It provides energy forecasts, optimization, energy management plans, and integrations with local energy devices and controllers.

The project prioritizes reliable local operation, clear interfaces, maintainable code, testability, and type safety.

## General Development Guidelines

- Follow the existing architecture and coding conventions.
- Prefer small, focused changes that preserve established interfaces and behavior.
- Use type annotations for functions, methods, and relevant variables.
- Write Google-style docstrings for modules, classes, and public functions and methods, following the surrounding code.
- Keep code compatible with the Python version supported by the project. Consult `pyproject.toml` and the project configuration rather than assuming a particular interpreter version.
- Avoid introducing unnecessary dependencies.
- Use descriptive names that communicate purpose and domain meaning.
- Do not use production `assert` statements for input validation or runtime error handling. Use explicit exceptions or appropriate error handling instead.
- Use the project's existing logging facilities and conventions.
- Follow existing patterns for configuration, validation, error reporting, and resource management.
- Preserve compatibility with the project's existing tests and supported integrations unless a change explicitly requires otherwise.

## Filename Naming Rules

**Filename conventions are mandatory. All new files and renamed files must comply with the filename rules configured in `pyproject.toml`.**

Before creating, renaming, or proposing a new file:

1. Inspect the `[tool.eos-filename-linter]` configuration in `pyproject.toml`.
2. Identify the first matching rule for the file's repository-relative path.
3. Follow that rule's regular expression, allowed-name exceptions, exclusions, and maximum filename length.
4. Do not invent alternative naming conventions or infer that a filename is acceptable merely because it is valid Python.
5. Do not change the linter configuration or add exceptions simply to accommodate a nonconforming filename without a clear justification.

The custom filename linter is the authoritative source for repository filename conventions. If this document and the linter configuration appear inconsistent, follow the configuration and recommend updating this document when appropriate.

### Python source modules

Python modules under `src/akkudoktoreos/` must follow the naming pattern configured for that directory.

In particular, where the configured rule requires lowercase alphanumeric names, use lowercase letters and digits without underscores or hyphens. Preserve explicitly permitted special filenames, such as `__init__.py`, when listed in `allowed_names`.

Do not rename existing modules merely to impose a personal preference. Follow the configured rule when adding files or changing filenames.

### Pytest modules

Test modules under `tests/` must follow the applicable test filename rule.

Where the configured rule requires the `test_` prefix followed by lowercase letters and digits only:

- Begin the filename with `test_`.
- Use lowercase letters and digits after the prefix.
- Do not use underscores or hyphens after the prefix.
- Keep the `.py` extension.
- Respect any configured exceptions and exclusions.

Examples:

| Valid | Invalid |
|---|---|
| `test_adapterdevice.py` | `test_adapter_device.py` |
| `test_geneticforecastcoverage.py` | `test_genetic_forecast_coverage.py` |
| `test_invertercontrol.py` | `test_inverter-control.py` |

If a separate rule applies to files such as `single_test_*.py`, follow that rule instead.

### Python scripts

Scripts under `scripts/` must follow the applicable script naming rule.

Where the configured rule requires lowercase snake_case:

- Use lowercase letters and digits.
- Use underscores to separate words where appropriate.
- Do not use hyphens.
- Keep the `.py` extension.

Examples:

- Valid: `lint_filenames.py`
- Invalid: `lint-filenames.py`
- Invalid: `LintFilenames.py`

### Filename length

Respect the maximum filename length configured by the applicable rule. If a rule does not override the global limit, use the global default defined in `pyproject.toml` or by the linter.

The length limit includes the file extension.

### Verify filenames

When adding or renaming files, run the filename linter before completing the change:

```bash
uv run python scripts/lint_filenames.py
```

To check specific files:

```bash
uv run python scripts/lint_filenames.py path/to/file.py
```

When the linter reports a violation:

- Correct the filename where appropriate.
- Review the suggested name as a diagnostic hint; do not assume it is always semantically ideal.
- Update imports, references, test commands, and other affected paths if a file is renamed.
- Never rename files automatically without reviewing the consequences.

Do not weaken filename rules merely to make the check pass.

## Python Quality and Type Checking

- Follow the project's Ruff configuration for linting and formatting.
- Keep code compatible with the project's mypy configuration.
- Avoid introducing new type-checking errors.
- Prefer explicit, narrow types over unnecessary use of `Any`.
- Handle optional values and error cases explicitly.
- Keep abstractions proportionate to the problem; avoid unnecessary complexity.

Use the project's configured tools rather than introducing competing formatting or linting conventions.

## Testing

- Add or update pytest tests for behavior changes and bug fixes.
- **Prefer adding tests to existing test classes in existing test modules whenever possible.** Extend the relevant class or module rather than creating additional test files.
- Create a new test module only when the functionality represents a distinct aspect that does not fit naturally into an existing test module, or when it targets a different component or test target.
- Follow existing test conventions, fixtures, and markers.
- Prefer deterministic, isolated tests; mock external services and devices where appropriate.
- Cover relevant boundary conditions and error paths.
- Do not weaken tests merely to accommodate implementation defects.

Run relevant tests and checks before completing changes. Use the project's configured commands and environments.

## Configuration and Compatibility

- Follow existing Pydantic and configuration-management patterns.
- Validate external input at appropriate boundaries.
- Preserve established configuration behavior and migration compatibility.
- Avoid silently changing defaults, persisted configuration formats, or public API behavior.
- Update tests and documentation when configuration behavior changes.

## Energy Management and Device Integration

- Keep optimization logic separate from device-specific communication where practical.
- Follow existing adapter and controller interfaces.
- Respect the distinction between long-horizon energy optimization and fast local device control.
- Handle communication failures, device availability, and invalid measurements explicitly.
- Avoid introducing cloud dependencies into workflows intended to operate locally.
- Preserve existing energy sign conventions, units, and conversion semantics. Verify them before changing power, energy, or charging behavior.

## Documentation

- Update documentation when changing public interfaces, configuration, command-line behavior, or architectural decisions.
- Keep examples consistent with actual configuration and code.
- Document non-obvious constraints and compatibility requirements.
- Ensure filenames used in documentation match the actual repository paths.

## Completion Checklist

Before considering a change complete:

- [ ] New and renamed files comply with the filename rules in `pyproject.toml`.
- [ ] The filename linter passes.
- [ ] Tests have been added or updated as appropriate.
- [ ] Relevant pytest tests pass.
- [ ] Ruff checks pass.
- [ ] Mypy checks pass where applicable.
- [ ] Configuration and documentation have been updated when necessary.
- [ ] No unrelated changes or unnecessary exceptions have been introduced.
