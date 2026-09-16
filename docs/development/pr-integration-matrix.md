# Remaining EOS packages and Optimize compatibility

Snapshot: 2026-09-16. Official main: `4a37244`. Feature source: `d2e2d58`, plus
separately backed-up local work. This is a functional package estimate, not a claim
that every differing commit requires its own PR.

## What is still missing on main

| Package | Missing behavior relative to feature/local work | Local state | Dependency |
| --- | --- | --- | --- |
| Device physics | Slot-duration-aware battery/inverter flows, export control, efficiency and limits | Already ported/tested in integration; needs isolated review branch | #1256 device settings/converters |
| Complete GENETIC | Quarter-hour orchestration, adaptive evolution, export states, warmstart alignment, forecast tail/terminal value, EV deadlines and flexible consumer profiles | Physics and primitives exist; orchestration/parameter/output integration remains open | Device physics, #1256, tariff contract |
| Imported tariff protection | Preserve supplied/imported revenue; avoid silent demo or market-price replacement | Independent main adaptation in progress; feature-specific override still required inside GENETIC port | Main patch independently possible; second part belongs with GENETIC |
| Local calibrated PV | Local Akkudoktor PV calculation, measurement calibration and outage handling under existing provider ID | Independent main port in progress | Provider-specific settings; combined forecast/optimizer acceptance later |
| Measurement APIs | Typed channels, quality, energy integration, household balance and capacity estimate APIs | Isolated tested local branch exists | #1256, #1305, configuration corrections; JSON fix #1322 |
| Config-owned Optimize request | Local ConfigOptimizationRequest, /v1/optimize, runtime observations and common parameter resolver | Backed up; async/maps/converters adaptation pending | New GENETIC and #1305 |
| Result/PDF output | Quarter-hour, flexible-consumer, export, tail/rest-value diagnostics in main's on-demand algorithm-specific output | Pending | Final GENETIC result contract |

Seven feature packages remain in this planning snapshot. They may produce roughly
seven further feature PRs; tightly coupled packages may be combined, and independently
confirmed defects may require an additional small fix. #1256 and #1305 are existing
foundation PRs, not two newly invented replacements. #1224/#1304 already address
parts of the tariff work against the old feature branch; preserve/reconcile those
contributions rather than count duplicate implementations as separate deliverables.
PR #1322 is already published and is additional to this remaining-work table.

Local helper scripts and private HA-core divergence are not silently included in this
count. They remain separately secured/to be audited. Changelog/release work follows
acceptance; it is not another optimizer implementation.

## Parallel lanes

- Local PV: `feat/local-pv-main-port`, sibling worktree `EOS-pr-local-pv`.
- Tariff preparation: `fix/imported-feedin-main`, sibling `EOS-pr-feedin-main`.
- Optimize compatibility tests: `test/optimize-pr-contracts`, sibling
  `EOS-pr-optimize-contracts`. These are shared acceptance coverage, not necessarily
  an extra standalone public PR.
- Root/integration: configuration prerequisites, shared API/algorithm contracts and
  eventual combination of the tested packages.

Do not have multiple lanes independently rewrite geneticparams.py, EMS.run or the
same configuration structure. A tested tariff patch will be carried into the core
port; the core port must keep its regression cases. PV keeps the public
PVForecastAkkudoktor ID and existing remote behavior unless explicitly selected.
Parallel preparation does not imply parallel unreviewed merges or publishing all
branches. Only the JSON fix has publication approval at this point.

## Three different Optimize contracts

1. Legacy POST `/optimize` on main explicitly runs GENETIC0, hourly. Existing payload
   and legacy response aliases must keep working regardless of configured default.
2. Automatic EMS with `ems.mode=OPTIMIZATION` selects `optimization.algorithm`:
   GENETIC or GENETIC0. Preparation and solution conversion are asynchronous.
   PREDICTION and DISABLED must not accidentally run optimization or dispatch controls.
3. The feature worktree's LOCAL POST `/v1/optimize` and ConfigOptimizationRequest are
   not present on main/integration yet. They must be adapted to current maps,
   converters and algorithm-specific settings. Old feature `/optimize` meant a
   different optimizer; clients need explicit migration, not a silent route switch.

Current blockers: GENETIC.prepare still forces interval_sec to 3600 and EMS start
alignment floors to the hour. Fifteen-minute device tests do not prove quarter-hour
Optimize-mode support. The core package must change preparation, slot alignment,
optimization and response metadata together.

A further suspected failure-path defect is being verified: after a failed explicit
optimization, the HTTP route may return a previous stored solution. Until confirmed
on unchanged main, treat this as an audit finding, not a proven upstream bug. A failed
new request must never claim an old result as its fresh successful optimization.

## Shared acceptance before dependent PRs can land

| Area | Required combined check |
| --- | --- |
| Routing | Legacy /optimize remains GENETIC0; automatic mode selects the configured algorithm; the new explicit API selects GENETIC deliberately |
| Configuration | Stable device IDs/maps and converters, runtime changes retained, old aliases migrated without losing values |
| Time | 900/3600-second slots, non-hour-aligned start and advancing warmstart, timezone/DST boundaries, matching output timestamps |
| Units | PV/load predictions are W; integrate once to slot Wh. `feed_in_tariff_wh` and `elecprice_marketprice_wh` already yield amount/Wh; convert amount/kWh configuration exactly once |
| Tariffs | Positive/zero/negative/imported revenue remains distinct from purchase cost; missing or invalid imported data does not silently become demo data |
| Devices | SOC/energy balance, charge/discharge and inverter limits, allowed/blocked battery export |
| Consumers | Feature profiles/deadlines and #1256 per-cycle windows/gaps both preserved; impossible schedules fail explicitly |
| Results | Raw algorithm result, generic optimization solution, execution plan and on-demand PDF agree on slots and device IDs |
| Errors | Failed preparation/optimization produces no new control dispatch and no misleading fresh-success response using old results |

A passing individual PR is insufficient: after combining dependent packages, run
these synthetic end-to-end API cases together with the GENETIC0 regression suite.
No device control, HA deployment or production configuration is part of this work.
