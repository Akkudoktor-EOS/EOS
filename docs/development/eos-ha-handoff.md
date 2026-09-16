# EOS interface handoff to Home Assistant

2026-09-16 — intermediate integration only; do not deploy or switch HA dependency yet.

Source branch: `integration/eos-consolidation-20260916` on main `afb7bcb8`.
See eos-consolidation.md for source commits, tests, backup, and remaining work.

- Configuration collections `devices.batteries`, `electric_vehicles`, `inverters`,
  `home_appliances` are maps keyed by stable device ID. A supplied `device_id` must
  match its map key; omitted IDs use that key. Old lists migrate. LCOS configuration
  uses `levelized_cost_of_storage_amt_kwh`; old files retain their values.
- Algorithm settings are separate under `optimization.genetic` / `genetic0`.
  Existing main configuration uses `interval_sec` and `horizon_hours` there.
  Do not send feature-branch top-level interval/horizon settings without migration.
- #1305 runtime bulk/granular changes are retained above file/environment sources;
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
- Legacy POST `/optimize` remains GENETIC0. The existing algorithm-specific raw result
  route is GET `/v1/energy-management/optimization/solution/{algorithm}`. The local
  feature ConfigOptimizationRequest and its `/v1/optimize` route are NOT yet ported.
- New GENETIC orchestration/warmstart, imported-tariff #1304 fix, flexible-consumer
  reconciliation, calibrated local PV, complete output/PDF and forecast handling
  remain pending. Passing GENETIC0 or primitive tests does not cover these.

No HA repository changes, lab deployment, real device control or production config
were performed. Retire the private HA core only after its differences are audited
and a final EOS commit passes the full feature/API acceptance scenarios.
