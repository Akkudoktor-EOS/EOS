---
name: python-quality
description: Use when writing or modifying Python code. Enforces Ruff, mypy, typing, docstring, and logging conventions of this project.
---

# Python Quality and Type Checking

- Follow the project's Ruff configuration for lint and format.
- Keep compatible with the project's mypy configuration; do not add new type errors.
- Prefer explicit, narrow types over `Any`.
- Handle optional values and error cases explicitly.
- Keep abstractions proportionate; avoid unnecessary complexity.
- Use type annotations for functions, methods, and relevant variables.
- Write Google-style docstrings for modules, classes, and public
  functions/methods, matching surrounding code.
- Keep compatible with the Python version supported by the project (check
  `pyproject.toml`, do not assume).
- Avoid unnecessary dependencies.
- Use descriptive, domain-meaningful names.
- **Never** use production `assert` for input validation or runtime error
  handling — use explicit exceptions.
- Use existing logging facilities and conventions.
- Follow existing patterns for config, validation, error reporting, and
  resource management.
- Preserve compatibility with existing tests and supported integrations unless
  the change explicitly requires otherwise.
- Use the project's configured tools; do not introduce competing formatters or
  linters.
