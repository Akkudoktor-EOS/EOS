---
name: completion-checklist
description: Use before declaring any change complete. Final pre-flight gate.
---

# Completion Checklist

Before considering a change complete:

- [ ] New and renamed files comply with the filename rules in `pyproject.toml`.
- [ ] The filename linter passes.
- [ ] Tests have been added or updated as appropriate.
- [ ] Relevant pytest tests pass.
- [ ] Ruff checks pass.
- [ ] Mypy checks pass where applicable.
- [ ] Configuration and documentation updated when necessary.
- [ ] No unrelated changes or unnecessary exceptions introduced.
