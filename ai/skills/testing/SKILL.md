---
name: testing
description: Use when adding or updating pytest tests, fixing bugs, or changing behavior that needs test coverage.
---

# Testing

## When to use

- Any behavior change or bug fix.

## Rules

- **Prefer adding tests to existing test classes in existing test modules.**
  Extend the relevant class/module rather than creating new files.
- Create a new test module only when:
  - functionality is a distinct aspect that does not fit any existing module, or
  - it targets a different component/test target.
- Follow existing conventions, fixtures, and markers.
- Prefer deterministic, isolated tests; mock external services/devices.
- Cover boundary conditions and error paths.
- Do not weaken tests to accommodate implementation defects.

## Verify

- Run the relevant pytest tests and checks with the project's configured
  commands and environment before declaring the change complete.
- Run a full test run:
  - Only one run at a time. Stop any running full test run before start of a new.
  - Run the equivalent of make gen-docs before a full run.
