# AGENTS.md — Akkudoktor-EOS

Akkudoktor-EOS is a Python-based energy management and optimization system. It
provides energy forecasts, optimization, energy management plans, and
integrations with local energy devices and controllers.

The project prioritizes reliable local operation, clear interfaces,
maintainable code, testability, and type safety.

## Rules for every change

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

## Skills

Detailed rules live in `ai/skills/`. Load the relevant skill for the task:

| Skill | Load when |
|---|---|
| `ai/skills/application-configuration/SKILL.md` | Config, Pydantic models, defaults |
| `ai/skills/commit-messages/SKILL.md` | Creating/checking commit messages |
| `ai/skills/energy-domain/SKILL.md` | Optimization, adapters, units/signs |
| `ai/skills/filename-conventions/SKILL.md` | Creating/renaming files |
| `ai/skills/python-environment/SKILL.md` | Before running uv, make, pytest, mypy, ruff or any Python |
| `ai/skills/python-quality/SKILL.md` | Writing/modifying Python |
| `ai/skills/rest-api/SKILL.md` | Adding/changing REST endpoints, API error responses |
| `ai/skills/testing/SKILL.md` | Adding/updating tests, bug fixes |

## Completion checklist

Before considering a change complete:

- [ ] New and renamed files comply with the filename rules in `pyproject.toml`.
- [ ] The filename linter passes.
- [ ] Tests have been added or updated as appropriate.
- [ ] Relevant pytest tests pass.
- [ ] Ruff checks pass.
- [ ] Mypy checks pass where applicable.
- [ ] Configuration and documentation updated when necessary.
- [ ] No unrelated changes or unnecessary exceptions introduced.
