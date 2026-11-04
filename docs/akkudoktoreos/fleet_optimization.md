# Multi-device planning with grid limits

`FLEET` schedules multiple batteries and EVs on one shared AC bus with hard
import and export limits. It uses the existing SciPy/HiGHS mixed integer solver.
The default algorithm remains `GENETIC`; the hourly legacy API is unchanged.

## Configured EOS operation

Select `optimization.algorithm: "FLEET"` and configure `optimization.fleet`:

```json
{
  "algorithm": "FLEET",
  "fleet": {
    "interval_sec": 900,
    "horizon_hours": 24,
    "import_limit_w": 11000,
    "export_limit_w": 6000,
    "prevent_export_at_negative_market_price": false,
    "solver_time_limit_seconds": 30,
    "measurement_max_age_seconds": 300,
    "terminal_value_eur_per_kwh": 0.15,
    "allow_battery_export": false,
    "devices": {
      "battery1": {"max_discharge_power_w": 4000},
      "car1": {"power_group": "chargers"},
      "car2": {"power_group": "chargers"}
    },
    "power_groups": {
      "chargers": {"max_charge_power_w": 11000, "max_discharge_power_w": 0}
    }
  }
}
```

The device IDs above must already exist in `devices.batteries` or
`devices.electric_vehicles`. Those collections remain the source of capacity,
efficiency, charging power, minimum/maximum SoC and EV charging steps/deadlines.
There is no one-device restriction for FLEET. Configured collection maxima
still apply. The solver supports up to 32 storage/EV devices and 384 slots.

Set `prevent_export_at_negative_market_price: true` to prohibit planned grid
export whenever the raw market price (`elecprice_marketprice_raw_wh`) is
negative. This uses the market price before import fees, independently of
the export remuneration. Zero prices remain eligible for export. Prices
must cover the entire horizon; missing or invalid values fail explicitly.
For hourly planning, any negative quarter-hour blocks export for that hour.
This option defaults to false and applies only to FLEET. External fallbacks
to another optimizer must enforce their own policy.

The explicit simulation API accepts optional run-relative `export_allowed`
booleans, one per slot. A false value imposes a hard zero export bound in
addition to `export_limit_w`; available PV is stored, consumed or curtailed.
Neither this policy nor a watt limit implements a physical grid clamp:
the installation's local controller must enforce curtailment at the grid
connection, including PV sources outside a battery inverter's control.

Call the existing `POST /v1/optimize`, or run the regular automatic EMS schedule.
An empty request reads configured providers and fresh SoC measurements. Runtime
overrides have this shape:

```json
{
  "soc": {"battery1": 42.5, "car1": 30, "car2": 60},
  "available": {"car1": [true, true, false, false]}
}
```

This availability example is for a one-hour horizon at 900 seconds; supply
one value per run-relative slot for your configured horizon. Missing availability
means available throughout. Unknown IDs, missing/stale/invalid SoC and short
forecast coverage fail explicitly. SoC overrides are percentages. Availability
does not subtract driving consumption. After a trip provide the updated SoC.

Optional `forecasts` use the existing `/v1/optimize` contract:
`pv_forecast_wh`, `total_load` (Wh per slot), `electricity_price_per_wh` and
`feed_in_tariff_per_wh` (currency/Wh), starting at local midnight. Missing series
come from providers. Slicing uses elapsed time, including repeated DST hours.
The resolved native result always starts at the aligned run time and uses W
and currency/kWh. Previous genetic chromosomes are not accepted by FLEET.

Stationary `min_soc_percentage` is a withdrawal reserve; EV `min_soc_percentage`
is a charging target. `max_soc_percentage` limits stored energy. States above
that configured maximum are rejected, not silently clipped. EV deadlines use
the earlier of the absolute deadline and maximum duration, rounded down to a
slot boundary so charging never finishes after departure. A past unmet target
fails. A deadline beyond the horizon requires the target by horizon end.

Batteries without a linked inverter are modeled as AC-equivalent storage.
With a linked inverter, its constant conversion efficiencies and AC power
limits are applied in addition to the battery ratings. Battery charge/discharge
ratings are then DC-side, and the resolved schedule exposes AC-side power.
`optimization.fleet.devices.<id>.max_discharge_power_w` defaults to that
battery's charging rating. An inverter with zero AC charging efficiency or
zero AC charging limit disables AC charging. Load-dependent efficiency curves
and several inverters attached to the same battery are rejected.

FLEET uses aggregate **available AC PV**. It does not model DC-coupled hybrid
PV/battery paths, shared PV/battery inverter headroom or DC clipping recovery.
Use a validated AC equivalent for such an installation; do not interpret this
as a detailed hybrid-inverter model. Shiftable home appliances are rejected
rather than silently omitted; their fixed demand can be included in the load
forecast. These restrictions do not affect the existing GENETIC algorithms.

Successful runs publish the usual general solution and energy-management plan,
plus the native result at
`GET /v1/energy-management/optimization/solution/FLEET`. Every device has its own
SoC, net energy, charge/discharge power and operation columns. Plans use
`FORCED_CHARGE`, `FORCED_DISCHARGE` and `IDLE`, with factors relative to the
resolved device's AC power rating, and explicitly end in IDLE at horizon end.
Adapters must support those modes and ratings before using these instructions
for real hardware. No hardware adapter is added by this change.

An infeasible or failed run does not overwrite the last successful plan.
Callers must check the status and validity window instead of treating an old
plan as a new result. The native result distinguishes `optimal` from `feasible`
(verified incumbent at the time limit). No plan is published for `limit` without
an incumbent or for solver/validation errors.

## Explicit simulation API

`POST /v1/optimize/fleet` accepts the explicit model below. It neither reads
configuration/measurements nor publishes an EMS plan. This is useful for
simulation comparisons independent of the configured algorithm.

All forecasts start at the timezone-aware `start_datetime`. Arrays have the same
length (1–384 slots). `interval_seconds` is 900 or 3600. Power values are average
**AC watts**, stored energy is **Wh**, prices are **EUR/kWh**. A slot limit is
not an instantaneous hardware protection guarantee. Percentage export limits
must be converted to watts by the caller using the applicable reference rating.

### Request example

```json
{
  "start_datetime": "2026-11-04T10:00:00+01:00",
  "interval_seconds": 3600,
  "load_w": [0, 0],
  "pv_w": [1000, 2000],
  "import_price_eur_per_kwh": [0.30, 0.30],
  "export_price_eur_per_kwh": [0.10, 0.10],
  "import_limit_w": 5000,
  "export_limit_w": 1000,
  "devices": [{
    "device_id": "house-battery",
    "kind": "battery",
    "capacity_wh": 1000,
    "initial_energy_wh": 0,
    "max_charge_power_w": 1000,
    "max_discharge_power_w": 1000,
    "target_energy_wh": 1000
  }]
}
```

The optimal plan exports 1000 W in each slot and charges in the second slot,
when that energy would otherwise be curtailed. Only exported energy earns
revenue. Without storage, the second slot reports 1000 W curtailed PV.

### Devices and shared limits

Add devices with unique `device_id` values. Batteries have independent capacities,
initial energies, reserves, AC charge/discharge ratings and total conversion
efficiencies. A reserve limits withdrawal; it never raises initial energy for
free. `discharge_cost_eur_per_kwh` prices delivered AC discharge energy.
`max_energy_wh` optionally caps stored energy below nominal capacity.
`min_charge_power_w` enforces a minimum positive charging power. Battery export
is disabled unless `allow_battery_export` is true: discharge cannot exceed
base load plus EV charging and cannot coincide with grid export. PV can still
be exported while batteries are idle or charging.
`terminal_value_eur_per_kwh` optionally values stored energy at horizon end;
its default is zero, so set a terminal value or target when preserving energy
beyond the supplied horizon matters.

For an EV use `kind: "ev"` and zero discharge power. `available` is a boolean
per slot. `charge_levels_w`, when supplied, lists exact permitted AC powers
including zero (for example `[0, 4140, 4830, 5520]`). Without this list charging
is continuously variable. Capacity is never exceeded, including the final
charging slot; partial-slot charging at a discrete setting is not modeled.
`target_energy_wh` is required at the end of `target_slot` (1-based boundary,
default horizon end). This version has one target per device and no trip-energy
series or V2G. Availability does not invent driving consumption.

Devices can name a `power_group`. Define matching `power_groups` entries with
`group_id`, `max_charge_power_w` and `max_discharge_power_w` to enforce shared AC
converter limits. Members cannot charge and discharge through that shared
converter simultaneously. This is an AC-equivalent model: DC-coupled PV,
individual PV inverter clipping and internal DC topology must not be passed
as if they were already available AC generation.

## Results and infeasibility

The balance per slot is:

`import + PV + discharge = load + charge + export + curtailed_PV`.

Import and export are mutually exclusive, as are each battery's charge and
discharge. Grid limits are hard constraints. Base load is never silently clipped.
Impossible demand, availability or target combinations return `infeasible`
with no schedule. A solver time limit without an incumbent returns `limit`
without a schedule. If a numerically verified feasible incumbent exists, it
returns `feasible` with a plan and `relative_gap` when available; it is not
claimed to be optimal. `optimal` means the solver proved optimality within its
numerical tolerances. Discrete charging over long horizons may reach the time
limit (30 seconds by default). `energy_wh` contains
n+1 boundary states; all power arrays contain n slot values.

`grid_cost_eur` is import cost less export revenue. `objective_eur` additionally
includes discharge costs and subtracts terminal energy value. Curtailment is
reported separately from conversion losses, which follow from the device
energy balance. Negative prices are supported. Economically equivalent plans
may differ: there is no flattening tie-breaker or demand charge in this version.

Device and fleet settings use the existing configuration API. No new dashboard
or device adapter is required to use the simulation endpoints. Demand charges
and a smoothing tie-breaker remain separate future extensions.
