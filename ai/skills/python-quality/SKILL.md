---
name: python-quality
description: Use when writing or modifying Python code. Enforces Ruff, mypy, typing and tooling conventions of this project.
---

# Python Quality and Type Checking

These rules add to the rules for every change in `AGENTS.md` (typing,
docstrings, naming, `assert`, logging, dependencies).

- Follow the project's Ruff configuration for lint and format.
- Keep compatible with the project's mypy configuration; do not add new type errors.
- Prefer explicit, narrow types over `Any`.
- Handle optional values and error cases explicitly.
- Keep abstractions proportionate; avoid unnecessary complexity.
- Use the project's configured tools; do not introduce competing formatters or
  linters.
