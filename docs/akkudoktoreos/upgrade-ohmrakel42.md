% SPDX-License-Identifier: Apache-2.0

# Upgrade to OHMRAKEL42

OHMRAKEL42 is used for scheduled and on-demand EOS planning. It plans configured
batteries, electric vehicles, home appliances and heat sources on the same grid.

## Configuration migration

Keep a copy of your configuration and data before upgrading. EOS backs up the prior
configuration when it migrates the file. Existing GENETIC and GENETIC0
configurations start using OHMRAKEL42 automatically. The algorithm selector and the
two algorithm-specific sections are removed from newly written configuration.

| Previous configuration | Current configuration |
| --- | --- |
| optimization.genetic.interval_sec | optimization.interval_sec |
| optimization.genetic.horizon_hours or optimization.genetic0.horizon_hours | optimization.horizon_hours |
| optimization.genetic.tail_horizon_hours | optimization.tail_horizon_hours |
| optimization.genetic.terminal_value_mode and terminal_value_* | Same keys directly under optimization |
| optimization.genetic.measurement_max_age_seconds | optimization.measurement_max_age_seconds |
| optimization.genetic.penalties or optimization.genetic0.penalties | optimization.penalties |
| individuals, generations, seed, workers, pin_workers, fitness_cache_max_entries | Removed. See optimization.search for effort and the time budget. |

When both old sections are present, EOS uses the section selected by the old
algorithm. GENETIC0 keeps hourly slots and defaults to a fixed terminal value
with zero forecast tail. Any explicitly configured new key takes priority.
Adjust environment variables and scripts that refer to an algorithm-specific path
to use the new configuration keys.

## Requests and reports

- POST /v1/optimize keeps its configured devices, optional SoC and forecast requests,
  and its compatibility fields. Its discharge plan remains binary. Existing genome
  fields are accepted and the returned genome fields are null. The response still
  reports each slot, its duration, and the remaining fraction of the first slot.
- POST /optimize still accepts hourly forecasts and request-local equipment, including
  the established German field aliases. The response contains midnight-indexed
  controls and results from the requested hour onward. Missing inverter or
  unsupported input yields a validation problem. The old genome, ngen, and
  temperature for display have no effect. Move to POST /v1/energy-management/run
  after configuring your equipment in EOS. The hourly endpoint is deprecated.
- GET /visualization_results.pdf provides a summary of the latest hourly request.
  Its replacement data is in the response from POST /optimize. After moving to
  configuration-based runs, use the replacement endpoints below.
- GET /v1/energy-management/optimization/solution/GENETIC/pdf provides a summary
  of the latest configuration-based OHMRAKEL42 run. Both PDF routes remain
  available during deprecation. Their removal version is pending and will be
  announced before removal.

Use GET /v1/energy-management/plan for device instructions, GET
/v1/energy-management/optimization/solution for aggregate totals and the
result and forecast series, and GET
/v1/energy-management/optimization/solution/OHMRAKEL42 for the native controls
and objective. The corresponding visual plan is available in EOSdash.

## Discharge control

By default every stationary battery plans either no discharge or full discharge
permission. An adapter that accepts a power ceiling can set
devices.batteries.<id>.adapter_supports_discharge_limit to true for the battery.
The selected ceiling then reaches Node-RED as the battery's op_factor and
Home Assistant as the instruction's operation_mode_factor in SELF_CONSUMPTION.
A factor of 0.4 caps discharge at 40% of rated power. Actual discharge also depends
on demand, the battery and inverter limits, and state of charge. Configure the
downstream flow or automation to apply the ceiling before enabling this setting.

For single-battery and single-EV systems the old genetic_* solution columns are
still available, including the binary discharge flag. Use device-qualified
instruction fields for systems with several batteries or several EVs. The native
plan distinguishes grid charging with PV charging enabled as FORCED_CHARGE and
battery export as GRID_SUPPORT_EXPORT with PV charging disabled.
