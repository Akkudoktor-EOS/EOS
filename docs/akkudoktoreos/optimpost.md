% SPDX-License-Identifier: Apache-2.0

# `POST /optimize` Optimization

## Introduction

The `POST /optimize` API endpoint optimizes your energy management system based on various inputs
including electricity prices, battery storage capacity, PV forecast, and temperature data.

The `POST /optimize` optimization interface is the "classical" interface developed by Andreas at the
start of the projects and used and described in his videos. It allows and requires to define all the
optimization paramters on the endpoint request.

:::{admonition} Warning
:class: warning
The `POST /optimize` endpoint interface does not regard configurations set for the parameters
passed to the request. You have to set the parameters even if given in the configuration.
:::

:::{admonition} Warning
:class: warning
To prevent automatic optimization from interfering with `POST /optimize` requests, set `ems.mode`
to `DISABLED` in the configuration.
:::

## Input Payload

### Sample Request

```json
{
    "ems": {
        "preis_euro_pro_wh_akku": 0.0001,
        "einspeiseverguetung_euro_pro_wh": [
          0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007,
          0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007,
          0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007,
          0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007,
          0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007,
          0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007,
          0.00007, 0.00007, 0.00007, 0.00007, 0.00007, 0.00007
        ],
        "gesamtlast": [
          676.71, 876.19, 527.13, 468.88, 531.38, 517.95, 483.15, 472.28,
          1011.68, 995.00, 1053.07, 1063.91, 1320.56, 1132.03, 1163.67,
          1176.82, 1216.22, 1103.78, 1129.12, 1178.71, 1050.98, 988.56, 912.38,
          704.61, 516.37, 868.05, 694.34, 608.79, 556.31, 488.89, 506.91,
          804.89, 1141.98, 1056.97, 992.46, 1155.99, 827.01, 1257.98, 1232.67,
          871.26, 860.88, 1158.03, 1222.72, 1221.04, 949.99, 987.01, 733.99,
          592.97
        ],
        "pv_prognose_wh": [
          0, 0, 0, 0, 0, 0, 0, 8.05, 352.91, 728.51, 930.28, 1043.25, 1106.74,
          1161.69, 6018.82, 5519.07, 3969.88, 3017.96, 1943.07, 1007.17,
          319.67, 7.88, 0, 0, 0, 0, 0, 0, 0, 0, 0, 5.04, 335.59, 705.32,
          1121.12, 1604.79, 2157.38, 1433.25, 5718.49, 4553.96, 3027.55,
          2574.46, 1720.4, 963.4, 383.3, 0, 0, 0
        ],
        "strompreis_euro_pro_wh": [
          0.0003384, 0.0003318, 0.0003284, 0.0003283, 0.0003289, 0.0003334,
          0.0003290, 0.0003302, 0.0003042, 0.0002430, 0.0002280, 0.0002212,
          0.0002093, 0.0001879, 0.0001838, 0.0002004, 0.0002198, 0.0002270,
          0.0002997, 0.0003195, 0.0003081, 0.0002969, 0.0002921, 0.0002780,
          0.0003384, 0.0003318, 0.0003284, 0.0003283, 0.0003289, 0.0003334,
          0.0003290, 0.0003302, 0.0003042, 0.0002430, 0.0002280, 0.0002212,
          0.0002093, 0.0001879, 0.0001838, 0.0002004, 0.0002198, 0.0002270,
          0.0002997, 0.0003195, 0.0003081, 0.0002969, 0.0002921, 0.0002780
        ]
    },
    "pv_akku": {
        "device_id": "battery1",
        "capacity_wh": 26400,
        "levelized_cost_of_storage_kwh": 0.12,
        "max_charge_power_w": 5000,
        "initial_soc_percentage": 80,
        "min_soc_percentage": 15,
        "grid_export_rates": [0.25, 0.5, 0.75, 1.0]
    },
    "inverter": {
        "device_id": "inverter1",
        "max_power_wh": 10000,
        "battery_id": "battery1",
        "ac_to_dc_efficiency": 0.95,
        "dc_to_ac_efficiency": 0.95,
        "max_ac_charge_power_w": 5000
    },
    "eauto": {
        "device_id": "ev1",
        "capacity_wh": 60000,
        "charging_efficiency": 0.95,
        "charge_rates": [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0],
        "discharging_efficiency": 1.0,
        "max_charge_power_w": 11040,
        "initial_soc_percentage": 54,
        "min_soc_percentage": 0,
        "min_soc_deadline_datetime": null,
        "min_soc_max_duration_h": null
    },
    "home_appliances": [
        {
            "device_id": "dishwasher1",
            "consumption_wh": 2000,
            "duration_h": 3,
            "schedule_mode": "ONCE",
            "time_windows": null,
            "earliest_start_datetime": null,
            "deadline_datetime": "2026-07-16T03:00:00+02:00",
            "deadline_policy": "BEST_EFFORT"
        }
    ],
    "temperature_forecast": [
      18.3, 17.8, 16.9, 16.2, 15.6, 15.1, 14.6, 14.2, 14.3, 14.8, 15.7, 16.7, 17.4,
      18.0, 18.6, 19.2, 19.1, 18.7, 18.5, 17.7, 16.2, 14.6, 13.6, 13.0, 12.6, 12.2,
      11.7, 11.6, 11.3, 11.0, 10.7, 10.2, 11.4, 14.4, 16.4, 18.3, 19.5, 20.7, 21.9,
      22.7, 23.1, 23.1, 22.8, 21.8, 20.2, 19.1, 18.0, 17.4
    ],
    "start_solution": null
}
```

## Input Parameters

### Energy Management System (EMS)

#### Battery Terminal Value (`preis_euro_pro_wh_akku`)

- Unit: €/Wh
- Purpose: Represents the residual value of energy stored in the battery
- Impact: Lower values encourage battery depletion, higher values preserve charge at the end of the
  simulation.
- Separation from LCOS: This value is only applied to usable battery energy remaining at the end of
  the optimization horizon. Battery discharge throughput is priced separately with
  `pv_akku.levelized_cost_of_storage_kwh`.

#### Feed-in Tariff (`einspeiseverguetung_euro_pro_wh`)

- Unit: €/Wh
- Purpose: Compensation received for feeding excess energy back to the grid

#### Total Load Forecast (`gesamtlast`)

- Unit: W
- Time Range: 48 hours (00:00 today to 23:00 tomorrow)
- Format: Array of hourly values
- Note: Exclude optimizable loads (EV charging, battery charging, etc.)

##### Data Sources

1. Standard Load Profile: `GET /v1/prediction/list?key=load_mean` for a standard load profile based
   on your yearly consumption.
2. Adjusted Load Profile: `GET /v1/prediction/list?key=load_mean_adjusted` for a combination of a
   standard load profile based on your yearly consumption incl. data from last 48h.

#### PV Generation Forecast (`pv_prognose_wh`)

- Unit: W
- Time Range: 48 hours (00:00 today to 23:00 tomorrow)
- Format: Array of hourly values
- Data Source: `GET /v1/prediction/series?key=pvforecast_ac_power`

#### Probabilistic Direct PV Consumption and Bypass

Hourly or 15-minute mean values alone would optimistically assume that the smaller of mean PV
generation and mean load is consumed directly. Real household load varies within the interval. EOS
therefore uses a conditional probability table derived from one-minute load samples. For a forecast
mean load \(\mu_L\), the table contains load-bin powers \(L_i\) and their conditional probabilities
\(p_i = P(L=L_i\mid\mu_L)\), with \(\sum_i p_i=1\).

Because the finite 50 W table grid can deviate slightly from the requested forecast mean, the load
bins are first normalized without changing the shape of the distribution:

```{math}
\widetilde{L}_i = L_i \frac{\mu_L}{\sum_j p_j L_j}
```

For mean PV power \(P_{PV}\), the expected power flowing directly from PV to the load is:

```{math}
P_{direct} = \sum_i p_i \min\left(\widetilde{L}_i, P_{PV}\right)
```

For a slot of duration \(\Delta t\), EOS converts this power into energy and derives both residual
flows from the same direct-consumption value:

```{math}
\begin{aligned}
E_{direct} &= \Delta t\,P_{direct} \\
E_{load,residual} &= E_{load}-E_{direct} \\
E_{PV,surplus} &= E_{PV}-E_{direct}
\end{aligned}
```

The residual load is supplied by the battery and then the grid. The PV surplus charges the battery;
any remainder bypasses the battery and is exported. Both residual load and PV surplus may be
positive in the same coarse slot because they occur during different sub-intervals. This is expected
and preserves the energy balances
\(E_{direct}+E_{load,residual}=E_{load}\) and
\(E_{direct}+E_{PV,surplus}=E_{PV}\).

The bundled table is conditioned on a one-hour mean load and models load variation only; mean PV is
treated as constant inside the slot. For a 15-minute grid produced by splitting hourly energy, the
power lookup retains the original hourly mean. A native 15-minute load forecast uses the same table
as an approximation until a separately calibrated 15-minute distribution is available. Fast PV
variability, for example from clouds, is not represented by this table.

#### Electricity Price Forecast (`strompreis_euro_pro_wh`)

- Unit: €/Wh
- Time Range: 48 hours (00:00 today to 23:00 tomorrow)
- Format: Array of hourly values
- Data Source: `GET /v1/prediction/list?key=elecprice_marketprice_wh`

Verify prices against your local tariffs.

### Battery Storage System

#### Configuration

- `device_id`: ID of battery
- `capacity_wh`: Total battery capacity in Wh
- `charging_efficiency`: Charging efficiency (0-1)
- `discharging_efficiency`: Discharging efficiency (0-1)
- `levelized_cost_of_storage_kwh`: LCOS in EUR/kWh, charged once for every kWh of DC energy
  delivered by the battery. Default: `0.0`.
- `max_charge_power_w`: Maximum charging power in W
- `charge_rates`: Selectable AC charge levels as factor of `max_charge_power_w`.
  Defaults to the configured `devices.batteries[0].charge_rates`.
- `grid_export_rates`: Selectable battery-to-grid export levels, see below.
  Defaults to the configured `devices.batteries[0].grid_export_rates`.

#### Battery Grid Export Levels (`grid_export_rates`)

With direct marketing enabled (`feedintariff.direct_marketing_enabled`) the battery may discharge
into the grid. The export is not all-or-nothing: `grid_export_rates` lists the selectable export
levels as a factor of the battery's rated discharge power, for example
`[0.25, 0.5, 0.75, 1.0]` (the default). The optimizer picks one level per slot, so it can spread a
limited amount of stored energy over several expensive slots instead of emptying the battery into
the first one.

Each rate is one more state in the genetic state space, which is why the default is deliberately
coarse. `[1.0]` restores the previous all-or-nothing behaviour.

When EOS writes its configuration file it omits every value that equals the field default, so a
`grid_export_rates` of exactly `[0.25, 0.5, 0.75, 1.0]` disappears from `EOS.config.json` on the
next save. The rates are still active - `GET /v1/config` shows the effective configuration, the
saved file only shows the deviations from it.

Whether a partial level is ever selected depends on the scenario. Exporting at full power in the
best-priced slots is optimal whenever the stored energy has no more valuable use; a partial level
pays when the export competes with a later, more expensive self-consumption and the right amount
of energy falls between two whole slots.

The exported energy of one slot is bounded by

```{math}
E_{export} \le \min\bigl(P_{inv,free}\,\Delta t,\; E_{bat,remaining},\; r\,P_{bat,rated}\,\Delta t\bigr)
```

where `r` is the selected rate. The rate applies to the *rated* discharge power, so it stays a plain
power setpoint: local self-consumption earlier in the same slot lowers `E_bat,remaining`, but it does
not silently raise the export level.

#### Battery LCOS (`levelized_cost_of_storage_kwh`)

LCOS and terminal value have different purposes. LCOS is a variable battery-use cost and is added
once when the battery delivers energy, both for local load coverage and battery-to-grid export. It
is not charged when the battery is charged and is not charged again on battery-internal or
DC-to-AC inverter losses.

For battery-delivered DC energy `E_bat,out` in one slot:

```{math}
C_{LCOS} = \frac{E_{bat,out}}{1000}\,c_{LCOS}
```

where `E_bat,out` is in Wh and `c_LCOS` is in EUR/kWh. This cost is included in
`Kosten_Euro_pro_Stunde`, `Gesamtkosten_Euro`, and therefore `Gesamtbilanz_Euro`. The terminal value,
by contrast, applies only to usable energy remaining after the last slot.

#### Terminal Value of Stored Energy

The optimization stops at the horizon, but the energy still in the battery keeps its worth: it
replaces grid imports that would otherwise be paid for afterwards. How that worth is credited is
set by `optimization.terminal_value_mode`.

`AUTO` (the default) derives a **concave value curve** instead of using a single price. The value of
stored energy is not linear in the amount stored:

- The first kWh replaces the most expensive hour that PV cannot cover.
- The next one replaces the second most expensive hour, and so on.
- Once every such hour is served, further energy replaces nothing - it is worth an export at best,
  and nothing at worst.

A single price has to pick one slope for all of it: high enough for the first kWh means hoarding a
full battery, low enough for the last kWh means running the battery empty by the end of the horizon.
The latter is what `terminal_value_euro_per_kwh = 0` does, and it is why `AUTO` is the default.

There is no forecast beyond the horizon, so the trailing window of the horizon itself
(`optimization.terminal_value_window_hours`, 24 h by default) stands in for the day that follows:
same season, same household rhythm, same tariff structure. Within that window the residual load
`max(load - PV, 0)` of every slot is priced at its import price, sorted by price and accumulated -
that is the curve. The battery LCOS is subtracted from every marginal value so stored energy is not
credited twice, and energy beyond the residual load is only credited when direct marketing allows
the battery to export.

`FIXED` restores the previous behaviour: every stored kWh is credited with
`optimization.terminal_value_euro_per_kwh`, or with `preis_euro_pro_wh_akku` of the request. In
`AUTO` mode that request field is ignored.

The curve is built once per optimization run and only interpolated during the search, so it costs
nothing per candidate solution. It is a planning aid derived from a proxy day, not a forecast - see
`terminal_value` in the response to check what a run actually used.

#### State of Charge (SoC)

- `initial_soc_percentage`: Current battery level (%)
- `min_soc_percentage`: Minimum allowed SoC (%)
- `max_soc_percentage`: Maximum allowed SoC (%)

### Inverter

- `device_id`: ID of inverter
- `max_power_wh`: Maximum inverter power in Wh
- `battery_id`: ID of battery
- `ac_to_dc_efficiency`: Efficiency of AC→DC conversion for grid-to-battery AC charging (0-1).
  Set to `0` to disable AC charging via inverter. Default `1.0` (backward compatible, no additional
  inverter loss — existing battery `charging_efficiency` applies).
- `dc_to_ac_efficiency`: Efficiency of DC→AC conversion for battery discharging to AC load/grid
  (0-1). Must be > 0. Default `1.0` (backward compatible).
- `max_ac_charge_power_w`: Maximum AC charging power in watts. `null` means no additional limit
  (battery's own `max_charge_power_w` applies). Set to `0` to disable AC charging. Default `null`.

#### Efficiency Model

The inverter efficiency parameters cleanly separate the **DC battery efficiency** from the
**AC↔DC inverter conversion efficiency**:

- **DC charging from PV surplus**: PV → Battery (direct DC, only `charging_efficiency` applies)
- **AC charging from grid**: Grid (AC) → Inverter (`ac_to_dc_efficiency`) → Battery
  (`charging_efficiency`)
- **Discharging to AC load/grid**: Battery (`discharging_efficiency`) → Inverter
  (`dc_to_ac_efficiency`) → Load/Grid (AC)

Round-trip efficiency for AC charging and discharging:
`η_round_trip = ac_to_dc_efficiency × charging_efficiency × discharging_efficiency × dc_to_ac_efficiency`

For profitability, the discharge electricity price must exceed:
`buy_price / η_round_trip + LCOS / dc_to_ac_efficiency`

**Backward compatibility**: With default values (`ac_to_dc_efficiency=1.0`,
`dc_to_ac_efficiency=1.0`, `max_ac_charge_power_w=null`), existing configurations work identically.
To model realistic inverter losses, set both efficiencies to a value like `0.95` and adjust
battery efficiencies to reflect pure DC losses only (typically `0.96`–`0.99` for Li-ion).

#### AC Charging Break-Even Penalty

The genetic optimizer includes an economic break-even check as a fitness penalty to guide
convergence away from unprofitable AC grid charging. For each scheduled AC charging hour the
optimizer checks whether the best future discharge price (after accounting for round-trip losses)
actually recovers the charging cost.

**Free PV energy handling**: Energy already stored in the battery from PV generation (zero
grid cost) is treated as a free resource that covers the most expensive future hours first.
AC grid charging is only evaluated against the *remaining* uncovered hours.

The penalty magnitude is:

```text
penalty = ac_wh_charged × (break_even_price − best_uncovered_price) × factor
```

where:
- `break_even_price = charge_price / η_round_trip + LCOS / dc_to_ac_efficiency`
- `best_uncovered_price` = highest future price not already covered by free PV battery energy
- `factor` = `optimization.genetic.penalties.ac_charge_break_even` (default `1.0`)

The penalty does not replace the simulation cost — it amplifies the economic loss signal so the
algorithm converges faster away from unprofitable charging regions.

To tune the aggressiveness of this penalty, set `penalties.ac_charge_break_even` in the
optimization configuration. A value of `1.0` corresponds to the exact economic loss in €.
Larger values (e.g. `3.0`) make the algorithm more aggressively avoid unprofitable AC charging;
smaller values (e.g. `0.0`) disable the penalty entirely.

### Electric Vehicle (EV)

- `device_id`: ID of electric vehicle
- `capacity_wh`: Battery capacity in Wh
- `charging_efficiency`: Charging efficiency (0-1)
- `discharging_efficiency`: Discharging efficiency (0-1)
- `max_charge_power_w`: Maximum charging power in W
- `initial_soc_percentage`: Current charge level (%)
- `min_soc_percentage`: Charging target; minimum allowed SoC (%)
- `max_soc_percentage`: Maximum allowed SoC (%)
- `min_soc_deadline_datetime`: Absolute moment by which `min_soc_percentage` has to be reached
- `min_soc_max_duration_h`: Maximum time from the start of the optimization until
  `min_soc_percentage` has to be reached (h)

#### Charging Deadline

By default `min_soc_percentage` only has to be reached by the end of the optimization horizon, so
the optimizer is free to charge in the cheapest slots anywhere in the horizon. A deadline moves
that requirement forward - typically to the next departure:

- `min_soc_deadline_datetime`: an absolute instant (`2026-07-16T07:00:00+02:00`). A value without
  timezone is read as local time.
- `min_soc_max_duration_h`: the same thing relative to the start of the optimization
  ("full in 6 hours"), which avoids timestamp arithmetic in the calling automation.

Both may be given; the earlier one applies. A deadline beyond the horizon is ignored, a deadline in
the past means the target is due immediately. The SoC-miss penalty
(`optimization.genetic.penalties.ev_soc_miss`) is then evaluated at the deadline instead of at the
end of the horizon, and the seeding heuristics only propose charge slots before it. Charging after
the deadline is not forbidden - it simply no longer helps to avoid the penalty.

The deadline is a target, not a hard constraint: if the remaining time is too short to reach
`min_soc_percentage`, the optimizer charges as much as it can and accepts the penalty. Check
`result.EAuto_SoC_pro_Stunde` at the deadline slot to see what was actually achieved.

In practice the target behaves as if it were binding. The default penalty of `10` per missing
percentage point is roughly forty times the cost of the energy itself (one point of a 60 kWh
battery is 600 Wh, some 0.25 EUR at 0.40 EUR/kWh), so the optimizer keeps the deadline whenever
charging power and remaining time allow it. The soft formulation only exists so that an
unreachable target degrades gracefully instead of failing the whole optimization.

Note that the target is met *tightly*: the optimizer stops at the first SoC that satisfies
`min_soc_percentage`, because every further kWh only adds cost.

### Flexible Consumers (Home Appliances)

Each entry of `home_appliances` describes one consumer whose run the optimizer may place in time.
The load of a single complete run is defined **either** by an explicit profile
(`load_profile_power_w` with `load_profile_interval_seconds`) **or** by the flat fallback
`consumption_wh` + `duration_h`.

- `device_id`: Unique ID of the consumer, used in all result columns
- `schedule_mode`: `ONCE` (a single run within the horizon) or `DAILY` (one run per local calendar
  day that still has a feasible full run)

Three independent constraints decide *when* a run may happen; all of them have to hold at once:

- `time_windows`: recurring wall-clock windows, e.g. "only between 10:00 and 13:00", optionally
  restricted to a weekday or a date. See {doc}`configtimewindow`.
- `earliest_start_datetime`: absolute lower bound. The run may not start before this moment.
- `deadline_datetime`: absolute upper bound. The complete run must have **finished** at or before
  this moment - with a 3 h program and a deadline of 03:00, the last allowed start is 00:00.

Both datetimes are absolute instants and never roll over into the next day. A value without
timezone is read as local time; sending an ISO-8601 timestamp with offset
(`2026-07-16T03:00:00+02:00`) is unambiguous.

#### Missed Deadlines (`deadline_policy`)

Depending on the current time, the run duration, the horizon and the time windows, a deadline can
be unreachable. `deadline_policy` decides what happens then:

- `BEST_EFFORT` (default): the run is scheduled as early as the remaining constraints allow -
  minimize the delay instead of the cost ("it should have been done by 03:00, so start now").
  A warning is logged and `appliance_deadline_missed` reports the miss.
- `STRICT`: the deadline is kept. A `ONCE` consumer without a feasible start makes the optimization
  fail; a `DAILY` consumer is simply not scheduled on days without one.

### Temperature Forecast

- Unit: °C
- Time Range: 48 hours (00:00 today to 23:00 tomorrow)
- Format: Array of hourly values
- Data Source: `GET /v1/prediction/list?key=weather_temp_air`

## Output Format

### Sample Response

```json
{
    "ac_charge": [0.625, 0, ..., 0.75, 0],
    "dc_charge": [1, 1, ..., 1, 1],
    "discharge_allowed": [0, 0, 1, ..., 0, 0],
    "battery_grid_export_allowed": [0, 0, 0, ..., 1, 0],
    "battery_grid_export_factor": [0.0, 0.0, 0.0, ..., 0.5, 0.0],
    "eautocharge_hours_float": [0.625, 0, ..., 0.75, 0],
    "result": {
        "Last_Wh_pro_Stunde": [...],
        "EAuto_SoC_pro_Stunde": [...],
        "Einnahmen_Euro_pro_Stunde": [...],
        "Gesamt_Verluste": 1514.96,
        "Gesamtbilanz_Euro": 2.51,
        "Gesamteinnahmen_Euro": 2.88,
        "Gesamtkosten_Euro": 5.39,
        "akku_soc_pro_stunde": [...]
    }
}
```

### Output Parameters

#### Battery Control

- `ac_charge`: Grid charging schedule (0.0-1.0)
- `dc_charge`: DC charging schedule (0-1)
- `discharge_allowed`: Battery discharge permission for local self-consumption/load coverage (0 or 1)
- `battery_grid_export_allowed`: Battery discharge permission for grid export/direct marketing (0 or 1)
- `battery_grid_export_factor`: Export level per slot as factor of the rated discharge power
  (`0.0` where no export is planned). Empty when direct marketing is disabled. A solution without
  this array exports at full power wherever `battery_grid_export_allowed` is 1.
- `terminal_value`: What the run credited for the energy left in the battery, and the curve it was
  read from:
  - `mode`: `AUTO` or `FIXED`
  - `battery_energy_wh`: usable AC energy left at the end of the horizon
  - `credited_euro`: the credit applied to the total balance
  - `curve.energy_wh` / `curve.value_euro`: breakpoints of the value curve
  - `curve.marginal_euro_per_kwh`: slope of each segment, monotonically decreasing
  - `curve.window_slots`: how many trailing horizon slots the curve was derived from
  - `reason`: why that mode applied. Empty in `AUTO` mode. In `FIXED` mode it distinguishes a
    configured `FIXED` from an `AUTO` run that found no priced residual load in its window - the
    latter is nearly always an all-zero price forecast in the request.

With direct marketing enabled, `dc_charge = 1` and `discharge_allowed = 1` may occur together. This
is the normal self-consumption mode: within a coarse optimization slot, the battery may cover
probabilistic load gaps and store PV surplus from different sub-intervals. A discharge-only state
remains available when deliberately bypassing PV charging is economically preferable.

0 (no charge)
1 (charge with full load)

`ac_charge` multiplied by the maximum charge power of the battery results in the planned charging
power.

#### EV Charging

- `eautocharge_hours_float`: EV charging schedule (0.0-1.0)

#### Flexible Consumers

- `appliance_starts`: Scheduled run start times per `device_id` as absolute local datetimes
- `appliance_deadline_missed`: Per `device_id` with a `deadline_datetime`, whether the scheduled run
  misses that deadline (or was not scheduled at all). Consumers without a deadline are not listed.
- `result.home_appliance_energy_wh`: Per-device load curve of the scheduled runs in Wh

#### Results

The `result` object contains detailed information about the optimization outcome. The length of the
array is between 25 and 48 and starts at the current hour and ends at 23:00 tomorrow.

- `Last_Wh_pro_Stunde`: Array of hourly load values in Wh
  - Shows the total energy consumption per hour
  - Includes household load, battery charging/discharging, and EV charging

- `EAuto_SoC_pro_Stunde`: Array of hourly EV state of charge values (%)
  - Shows the projected EV battery level throughout the optimization period

- `Einnahmen_Euro_pro_Stunde`: Array of hourly revenue values in Euro

- `Gesamt_Verluste`: Total energy losses in Wh

- `Gesamtbilanz_Euro`: Overall financial balance in Euro

- `Gesamteinnahmen_Euro`: Total revenue in Euro

- `Gesamtkosten_Euro`: Total costs in Euro

- `akku_soc_pro_stunde`: Array of hourly battery state of charge values (%)

## Timeframe overview

```{figure} ../_static/optimization_timeframes.png
:alt: Timeframe Overview

Timeframe Overview
```
