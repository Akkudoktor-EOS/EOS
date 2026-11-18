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
- Keep each test case well under a second. No real-time sleeps or wait loops
  (use synthetic timestamps), no full cartesian `parametrize` over independent
  axes, and extend an existing parametrized test before writing a new one.
  The CI log lists the 25 slowest tests (`--durations=25`).
- A test that starts an EOS or EOSdash server on the fixed ports uses the server
  fixtures (`server_setup_for_class`, `server_setup_for_function`), which mark it
  `eos_server` through the `xprocess` fixture. If a test must bind these ports
  without them, mark it `@pytest.mark.eos_server` itself: CI runs these tests in
  their own job, all others in parallel.

## Verify

- Run the relevant pytest tests and checks with the project's configured
  commands and environment before declaring the change complete.
- Run a full test run:
  - Only one run at a time. Stop any running full test run before start of a new.
  - Run the equivalent of make gen-docs before a full run.
