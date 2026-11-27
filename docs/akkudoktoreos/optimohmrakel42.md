% SPDX-License-Identifier: Apache-2.0

# OHMRAKEL42 optimization

## Introduction

OHMRAKEL42 is the EOS optimizer. It plans batteries, electric vehicles, household
appliances and heat pumps. Set the time grid, horizon, forecast tail, value of stored
energy and penalty prices directly under optimization.

## How it works in short

The plan decides, per device and time slot, a few control values. The search improves the plan
device by device:

1. It starts from an empty plan, or from the plan of the last run moved by the slots that have
   passed (**warm start**).
2. For one device at a time it computes the best control values over the whole horizon with
   dynamic programming over the state of the device (a heat pump: the exact solution of a
   linear program over its heat power), against the current plans of all other devices. Two orders of the devices are tried from scratch, the better result counts. With a
   warm start the plan of the last run is one start next to the full set of searches from
   scratch (`warm_start_compare`, default on): it is kept, which keeps the plan calm, unless a
   search from scratch is cheaper by more than `warm_start_keep_margin_eur` (default 0.01 EUR).
   A plan that fails the check of the allowed values (including the electric power limit of a
   heat pump) never wins while another one passes it: the other candidate counts.
   The statistics of the solution name the winner (`winner`: `warm` or `cold`).
3. A new plan is accepted only if the exact objective of the whole house falls. The result is
   never worse than the start plan.

The objective is the cost balance (import cost, storage cost, minus feed-in revenue) minus the
value of the energy left in the batteries, plus penalties: missed vehicle charge target, missed
periodic full charge, import above the grid connection limit, and leaving the comfort band of a
heat pump zone. The search is deterministic: the same input gives the same plan.

## Control values

| Device | Control value per time slot |
| --- | --- |
| Battery | Discharge ceiling on levels (`discharge_rates`), grid charge level (`charge_rates`), PV charging on or off, export level (direct marketing only) |
| Electric vehicle | Charge level (`charge_rates`); a vehicle can be plugged in only part of the time (`vehicle_available`) |
| Household appliance | Start slot of every cycle, out of the slots its windows, deadline, cycles per day and gap allow |
| Heat pump | Heat power per slot, continuous from 0 to the maximum of the slot (the run share; below the first operating point the heat pump time-shares) |

A discharge level is an **upper limit**: the battery covers at most this share of its rated
discharge power, never more than the load that is left. `discharge_rates: []` or `[1.0]` gives
plain on/off discharging. Charging from PV surplus is always on unless `pv_charge_switchable` is
set for the battery.

For the heat pump EOS sets temperature targets and limits, not a power set point. The planned
electric power is a forecast of consumption. The target of a slot is the planned room
temperature at the end of the slot, held within the comfort band of the slot.

## Settings

| Setting | Meaning |
| --- | --- |
| `devices.batteries.<id>.discharge_rates` | Available discharge levels with a capable adapter, default 0.1 … 1.0 |
| `devices.batteries.<id>.pv_charge_switchable` | PV charging may be switched off per slot, default off |
| `devices.grid_connection.import_limit_w`, `export_limit_w` | Limits at the grid connection point |
| `devices.grid_connection.import_limit_penalty_eur_per_kwh` | Price of exceeding the import limit, default 10 |
| `devices.heat_sources.<id>.comfort_penalty_eur_per_kh`, `protection_penalty_eur_per_kh` | Default 100 and 1000 |
| `optimization.search.time_budget_seconds` | Stop starting new moves after this time |
| `optimization.search.effort` | `LOW`, `NORMAL` (default) or `HIGH` |

All settings are listed in the [configuration reference](../_generated/config.md) with their
scope label `OHMRAKEL42`.

### Every device is taken over

All configured batteries, vehicles and inverters take part; an inverter belongs to its battery by
`battery_id`, an inverter without `battery_id` serves PV only. Every battery needs exactly one
inverter. The state of charge comes from the request or from a fresh measurement
(`optimization.measurement_max_age_seconds`); without either, a storage starts at **0 %**
and a warning is logged and reported (`battery_soc_missing`, `vehicle_soc_missing`).

### Heat pump

A heat source becomes a thermal device if it is `enabled`, of kind `heat_pump` with a performance
map, and bound to exactly one enabled zone in `thermal.zones`. The zone needs calibrated building
parameters (`model_parameters`), a comfort band (`comfort`) and the measurement keys of the room
and outdoor temperature; the heat source needs `electric_power_measurement_key`. The start state is
estimated from the measured histories. A negative delivered heat (the heat pump takes heat from the room while it defrosts) counts as heat taken from the room, down to the size of the heat pump (the largest heat of its performance map); a lower value is a sensor error and is rejected like a non-finite one (the heat pump is then left out as below). The comfort band is lowered while the presence forecast
(`presence_area_id`) or a reported absence says nobody is home; the protection minimum
(`absence_minimum_temperature_c`, default 14 °C) always holds. The presence forecast is a two-state Markov chain
(rates of time of day and day type fitted on the measured history, started from the sensor state); a radar
detection counts as presence for `presence.occupancy_hold_seconds` (default 600) and a zone can ignore
detections in daily `ignore_windows` (for example a robot vacuum at night). The shortfall below the occupied
comfort minimum is priced by expected cost: three tiers (0 to 0.5 K, 0.5 to 1.5 K, beyond) with rising prices, the
first two `shortfall_price_first_eur_per_kh` (0.5) and `shortfall_price_second_eur_per_kh` (2.0) of the heat source and the third its comfort price, each weighted with the
presence probability of the slot; the thermal plan then follows the planned room temperature instead of lifting
its target to the minimum. The plan keeps to `max_electric_power_w`: the heat of a slot is limited to what stays within it together with standby, circulation and auxiliaries, and a slot in which not even the lowest operating point fits is barred. The energy of a start is continuous: it is the rise of the run share (heat over the highest heat of the slot) from one slot to the next times the energy of one start, and it counts against the limit on top of the slot's own electricity, in the planning and in the check of the plan alike. A heat source whose device
cannot be set up (stale or missing measurement, no outdoor forecast, incomplete configuration) is left out: the batteries,
vehicles and appliances are planned anyway, the log carries a warning, and the solution carries a
`warnings` entry with the code `thermal_not_planned`, the heat source ids and the cause. Water and buffer distributions are not supported yet.

## Output

Run `POST /v1/energy-management/run` or `ems.run()`; the automatic
optimization uses the same code. A failed planning of the electrical devices raises an error with its cause, no replacement
plan is made up; a heat pump that cannot be planned is reported in `warnings` of the solution.

The `warnings` of the solution (code, message, device ids) name what the planning left out or
adjusted; the log carries the same text.

| Code | Meaning |
| --- | --- |
| `thermal_not_planned` | The listed heat sources are not planned, the electrical devices are. The message names the cause. |
| `thermal_heat_plan_failed` | The solver could not compute the heat plan of the listed heat sources during the search (see the log); the heat plan of the other results was kept. The message gives how often. |
| `thermal_protection_violated` | The planned room temperature of the zone of the listed heat source falls below its protection minimum, for example during a long heating block at frost. The block stays in force; the message names the zone, the first slot and the lowest temperature. |
| `vehicle_target_unreachable` | The plan charges the vehicle as much as it can but does not reach its target by the deadline (or the end of the horizon). The message gives the state of charge reached and the shortfall. |
| `vehicle_deadline_adjusted` | The deadline of the vehicle has passed or is shorter than one slot; the target is charged as early as possible. |
| `battery_soc_missing`, `vehicle_soc_missing` | Neither the request nor a fresh measurement gave the state of charge; the device is planned as 0 % (not a measurement). The message names the cause. |


| Result | Where |
| --- | --- |
| Series per slot, forecasts and planned heat | `GET /v1/energy-management/optimization/solution` |
| Instructions (operation mode and factor) | `GET /v1/energy-management/plan` |
| Native solution (plan, objective, search effort) | `GET /v1/energy-management/optimization/solution/OHMRAKEL42` |
| Thermal plan per heat source: target and limits per slot | `GET /v1/energy-management/thermal-plan` |

`POST /v1/energy-management/run` takes optional runtime values: `soc` per device, `forecasts`,
`vehicle_available`, `heat_source_blocks` (periods in which a heat source must not run),
`absences` and `changed_devices` (ids of devices an event concerns; they are planned first,
starting from the last plan).

### Operation modes

The existing operation modes carry the control values, with the factor as given:

| Mode | Controls | Factor |
| --- | --- | --- |
| `IDLE` | no PV charging, no discharging | 1.0 |
| `NON_EXPORT` | PV charging only | 1.0 |
| `SELF_CONSUMPTION` | discharge ceiling, PV charging on | ceiling |
| `FORCED_CHARGE` | grid charge level (battery and vehicle) | level |
| `GRID_SUPPORT_EXPORT` | export level, PV charging off, full discharge (PV surplus and battery feed in side by side) | level |

An appliance is `RUN` or `OFF`. Discharging without export and grid charging always come with PV
charging on; export comes with PV charging off and the full discharge ceiling, independent of
`pv_charge_switchable`, which only allows switching PV charging off without export (`IDLE`). The search chooses no other combination, because no mode carries it; if
a plan with one ever reaches the solution, building it is an error. The columns `<id>_<mode>_op_mode` and
`<id>_<mode>_op_factor` of the solution and the instructions carry the active mode's factor, so the
Home Assistant and Node-RED adapters need no change.

## Limits of the first version

- Heat pumps with a performance map and direct or air distribution only; the building model is
  not learned, it comes from the configuration.
- The heat pump is planned in time steps of the grid (900 or 3600 s), the comfort band is checked
  at the start of a step.
- Households with several heat sources plan each source with its own zone.
- Paired moves of two devices are off by default; they brought no gain in the measurements.
- The first slot may be shortened to the part still ahead; plans for appliances that overlap
  windows of different cycles are placed in chronological order of their slots.
- Timing and quality were measured on generated days only, not yet on real ones.
