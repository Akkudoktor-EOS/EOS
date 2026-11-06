---
name: application-configuration
description: Use when changing configuration models, defaults, persisted formats, external input validation, or migration behavior.
---

# Configuration and Compatibility

- Follow existing Pydantic and configuration-management patterns.
- Validate external input at appropriate boundaries.
- Preserve established configuration behavior and migration compatibility.
- Do not silently change defaults, persisted configuration formats, or public
  API behavior.
- Update tests and documentation when configuration behavior changes.
