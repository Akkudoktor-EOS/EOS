---
name: general-development
description: Always-on baseline rules for this repository. Apply by default to every change.
---

# General Development Guidelines

## Project Overview

Akkudoktor-EOS is a Python-based energy management and optimization system. It
provides energy forecasts, optimization, energy management plans, and
integrations with local energy devices and controllers.

The project prioritizes reliable local operation, clear interfaces,
maintainable code, testability, and type safety.

## Rules

- Follow existing architecture and coding conventions.
- Prefer small, focused changes that preserve established interfaces and
  behavior.
- Use type annotations for functions, methods, and relevant variables.
- Write Google-style docstrings for modules, classes, and public
  functions/methods, following surrounding code.
- Keep code compatible with the supported Python version (`pyproject.toml`).
- Avoid introducing unnecessary dependencies.
- Use descriptive, domain-meaningful names.
- Never use production `assert` for input validation or runtime error
  handling; use explicit exceptions.
- Use existing logging facilities and conventions.
- Follow existing patterns for config, validation, error reporting, resource
  management.
- Preserve compatibility with existing tests and supported integrations unless
  a change explicitly requires otherwise.
- Never use the workspace `.venv`: run `uv`, `make` and all Python tools in a
  virtual environment outside the workspace (`UV_PROJECT_ENVIRONMENT`), see
  `ai/skills/python-environment/SKILL.md`.
