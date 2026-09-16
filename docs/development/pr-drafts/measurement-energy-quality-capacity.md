# feat(measurement): add typed energy, quality and capacity APIs

Local branch: `feat/measurement-energy-quality-capacity`.
Review base: `feat/config-integration-base` (`d546f08`).
Eventual target: official main, after its configuration prerequisites land.
Status: tested local package, NOT yet an independent main-target PR.

## Proposed PR body

Add typed measurement channels with explicit units and timestamp semantics, sample
quality, energy integration, household energy balances and battery capacity estimates.
Adapt persistence and REST access to main's asynchronous storage. Battery estimates
use keyed device identities and remain separate from the configured active capacity;
storing an estimate requires an explicit request and preserves runtime config updates.

Expose `/v1/measurement/samples`, `/v1/measurement/energy`,
`/v1/measurement/household` and `/v1/measurement/battery-capacity/{device_id}`.
Regenerate the configuration and OpenAPI contracts. Use synthetic data only.

Validation: 453 configuration/measurement/device simulation tests and 5 documentation
tests pass. A further 74 measurement/capacity tests pass after carrying over fixture
isolation. Ruff passes for measurement source and the measurement REST module.
Local Windows/Python 3.11.9 validation; full pinned CI remains required.

Depends on the device maps/converters in #1256, runtime configuration in #1305 and
local fixes for stable device IDs, LCOS migration and charge-rate compatibility.
Includes the independently prepared JSON restore fix; it should land separately first.
No new GENETIC orchestration or battery/inverter physics is included in this branch.

## Submission gate

Do not open this whole branch against main now: its ancestry still includes the
unmerged configuration PRs. Preserve those contributors' existing PRs and credit.
Once prerequisites are merged, rebuild/rebase the measurement package onto that
main, inspect the resulting diff and rerun relevant tests before publication.
The comparison to `feat/config-integration-base` isolates today's measurement work.
