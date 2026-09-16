# fix(measurement): restore JSON records into the existing singleton

Target: `Akkudoktor-EOS/EOS:main`.
Local branch: `fix/measurement-json-reload`.
Local head: `dba0c9c` (based on main `7ebe6d7`).
Status: locally ready for review; not pushed or published.

## Proposed PR body

When measurement persistence falls back to JSON, loading a saved file reports success
but does not restore its records: validating a second `Measurement` returns the
existing singleton. Parse and validate the individual records before inserting them
into that singleton. Invalid files now return `False` instead of reporting success.

Regression coverage checks round trips, timestamp preservation, repeated loading,
merging with existing timestamps, malformed files without partial validation writes,
and database-provider precedence. Four regression cases fail on unchanged main.
With the fix, all 49 measurement tests pass.

Validation: `python -m pytest tests/test_measurement_file_restore.py tests/test_measurement.py -q`;
Ruff check and format check of the changed source; `git diff --check`.
Run locally on Windows/Python 3.11.9. The repository's Linux/Python 3.13 pinned CI
and complete test suite have not run for this branch yet.

No settings or API schema changes. This fix is independent of #1256, #1305 and the
GENETIC port.

## Exact review scope

- `src/akkudoktoreos/measurement/measurement.py`
- `tests/test_measurement_file_restore.py`

Only these two files differ from the pinned main. Do not publish the integration
branch as part of this PR. After explicit publication approval, push this named
branch and open a PR against main; inspect CI before any merge.
