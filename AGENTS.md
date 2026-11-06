# AGENTS.md — Akkudoktor-EOS

Akkudoktor-EOS is a Python-based energy management and optimization system.
Prioritizes reliable local operation, clear interfaces, maintainable code,
testability, and type safety.

## Skills

Detailed rules live in `ai/skills/`. Load the relevant skill for the task:

| Skill | Load when |
|---|---|
| `ai/skills/application-configuration/SKILL.md` | Config, Pydantic models, defaults |
| `ai/skills/commit-messages/SKILL.md` | Creating/checking commit messages |
| `ai/skills/completion-checklist/SKILL.md` | Before declaring work done |
| `ai/skills/energy-domain/SKILL.md` | Optimization, adapters, units/signs |
| `ai/skills/filename-conventions/SKILL.md` | Creating/renaming files |
| `ai/skills/general-development/SKILL.md` | Always (baseline) |
| `ai/skills/python-environment/SKILL.md` | Before running uv, make, pytest, mypy, ruff or any Python |
| `ai/skills/python-quality/SKILL.md` | Writing/modifying Python |
| `ai/skills/rest-api/SKILL.md` | Adding/changing REST endpoints, API error responses |
| `ai/skills/testing/SKILL.md` | Adding/updating tests, bug fixes |
