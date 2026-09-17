# EOS interface handoff to Home Assistant

2026-09-16 — complete EOS feature port prepared for review in eight PRs.

The combined branch is `feat/genetic-complete`, based on main `4a37244` and the
prerequisite PRs. Use [review handoff](review-handoff.md) for merge order and current
PR links, and [GENETIC rollout](genetic-rollout.md) for configuration and manual
acceptance. Pin the final merged EOS commit after CI and installation acceptance;
the earlier `integration/eos-consolidation-20260916` is a historical checkpoint.

- Configuration collections `devices.batteries`, `electric_vehicles`, `inverters`,
  `home_appliances` are maps keyed by stable device ID. A supplied `device_id` must
  match its map key; omitted IDs use that key. Old lists migrate. LCOS configuration
  uses `levelized_cost_of_storage_amt_kwh`; old files retain their values.
- Algorithm settings are separate under `optimization.genetic` / `genetic0`.
  Existing main configuration uses `interval_sec` and `horizon_hours` there.
  Do not send feature-branch top-level interval/horizon settings without migration.
- `#1305` runtime bulk/granular changes are retained above file/environment sources;
  config persistence is still an explicit existing save operation.
- New measurement routes: PUT/GET `/v1/measurement/samples`, GET
  `/v1/measurement/energy`, GET `/v1/measurement/household`, POST
  `/v1/measurement/battery-capacity/{battery_id}`. OpenAPI describes request schemas.
  Sample times require timezone; values remain in declared raw units, derived energy
  is Wh and includes coverage/quality. Missing data is not implicitly zero energy.
- Capacity estimates are separate evidence; even store_estimate=true never replaces
  devices.batteries[id].capacity_wh. Explicitly stored estimates survive runtime
  bulk changes; existing config save controls disk persistence.
- Python measurement storage-facing methods are async and must be awaited. The HTTP
  schema remains ordinary JSON; HA does not need to mirror EOS internals.
- Legacy POST `/optimize` remains GENETIC0. New POST `/v1/optimize` runs GENETIC
  from the typed EOS device configuration. Its body accepts runtime `soc`,
  `forecasts`, `start_solution` and `start_solution_datetime`; hardware overrides
  and query-string overrides are rejected. Request SoC is an integer percentage
  keyed by device ID; automatic
  measurement lookup uses recent `<device_id>-soc-factor` values from 0 to 1.
  Missing, stale, future or invalid observations fail instead of implying zero.
- GENETIC supports 900/3600-second slots. Provider power in W is converted once to
  Wh per slot; runtime forecast arrays are already Wh per slot and prices per Wh.
  Runtime arrays begin at local midnight. Missing control forecasts fail the run;
  shorter forecast tails are clipped with diagnostics. Explicit/imported sale
  prices, including zero and negative values, remain authoritative.
- The new optimizer includes timestamp-aligned warmstarts, opt-in battery export,
  EV deadlines, flexible consumer profiles and per-cycle windows/completed cycles.
  AUTO/FIXED terminal value and forecast-tail diagnostics affect scoring, never
  extend the executable control horizon, and remain separate from battery wear.
- Automatic GENETIC runs use the site's timezone derived from its coordinates;
  explicit run starts and warmstart timestamps retain their timezone/instant.
  Configure device IDs and schedules against this timezone, including DST.
- GET `/v1/energy-management/optimization/solution/{algorithm}` returns the stored
  algorithm-specific result. Native/generic results and execution plans publish
  atomically for the successful run. Failed Optimize calls return errors rather
  than a previous successful result.
- GET `/v1/energy-management/optimization/solution/GENETIC/pdf` renders the stored
  GENETIC snapshot on demand (404 before a result exists). The legacy PDF endpoint
  and GENETIC0 implementation remain separate. The local calibrated Akkudoktor PV
  backend and the measurement APIs are included in the prerequisite PRs.

No HA repository changes, lab deployment, real device control or production config
were performed. Retire the private HA core only after its differences are audited
and a final EOS commit passes the full feature/API acceptance scenarios.

GENETIC currently supports one inverter, one stationary battery, one EV and multiple
household appliances. Unsupported device counts or inconsistent inverter/battery
links are rejected. Verify both 15- and 60-minute Optimize runs, fresh measurements,
tariff units, result/plan timestamps and reports in the actual HA installation.
EOS code and synthetic scenarios are automated-test coverage; they do not validate
HA entities, physical hardware or the remaining private HA-core differences.
