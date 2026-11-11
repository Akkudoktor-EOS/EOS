% SPDX-License-Identifier: Apache-2.0

# Automatic Optimization

## Introduction

EOS offers two approaches to optimize your energy management system: `post /optimize optimization` and
`automatic optimization`.

The `post /optimize optimization` interface, based on a **POST** request to `/optimize`, is widely
used. It was originally developed by Andreas at the start of the project and is still demonstrated
in his instructional videos. This interface allows users or external systems to trigger an
optimization manually, supplying custom parameters and timing.

As an alternative, EOS supports `automatic optimization`, which runs automatically at configured
intervals. It retrieves all required input data — including electricity prices, battery storage
capacity, PV production forecasts, and temperature data — based on your system configuration.

### Genetic Algorithm

Both optimization modes use the same core optimization engine.

EOS uses a [genetic algorithm](https://en.wikipedia.org/wiki/Genetic_algorithm) to find an optimal
control strategy for home energy devices such as household loads, batteries, and electric vehicles.

In this context, each **individual** represents a possible solution — a specific control schedule
that defines how devices should operate over time. These individuals are evaluated using
[resource simulations](#resource-page), which model the system’s energy behavior over a defined time
period divided into fixed intervals.

The quality of each solution (its *fitness*) is determined by how well it performs during
simulation, based on objectives such as minimizing electricity costs, maximizing self-consumption,
or meeting battery charge targets.

Through an iterative process of selection, crossover, and mutation, the algorithm gradually evolves
more effective solutions. The final result is an optimized control strategy that balances multiple
system goals within the constraints of the input data and configuration.

:::{admonition} Note
:class: note
You don’t need to understand the internal workings of the genetic algorithm to benefit from
automatic optimization. EOS handles everything behind the scenes based on your configuration.
However, advanced users can fine-tune the optimization behavior using additional settings like
population size, penalties, and random seed.
:::

## Energy Management Plan

Whenever the optimization is run, the energy management plan is updated. The energy management plan
provides a list of energy management instructions in chronological order. The instructions lean on
to the [S2 standard](https://docs.s2standard.org/) to have maximum flexibility and stay completely
independent from any manufacturer.

### Battery Instructions

The battery control instructions assume an idealized battery model. Under this model, the battery
can be operated in four discrete operation modes:

| **Operation Mode ID** | **Description**                                                                      |
| --------------------- | ------------------------------------------------------------------------------------ |
| **IDLE**              | Battery neither charges nor discharges; holds its state of charge.                   |
| **CHARGE**            | Charge at a specified power rate up to the allowable maximum.                        |
| **DISCHARGE**         | Discharge at a specified power rate up to the allowable maximum.                     |
| **ALLOW_DISCHARGE**   | Allow the battery to freely discharge depending on its instantaneous power setpoint. |

The **operation mode factor** (0.0–1.0) specifies the normalized power rate relative to the
battery's nominal maximum charge or discharge power. A value of 1.0 corresponds to full-rate
charging or discharging, while 0.0 indicates no power transfer. Intermediate values scale the power
proportionally.

### Electric Vehicle Instructions

The electric vehicle control instructions assume an idealized EV battery model. Under this model,
the EV battery can be operated in two operation modes:

| **Operation Mode ID** | **Description**                                                                      |
| --------------------- | ------------------------------------------------------------------------------------ |
| **IDLE**              | Battery neither charges nor discharges; holds its state of charge.                   |
| **CHARGE**            | Charge at a specified power rate up to the allowable maximum.                        |

The **operation mode factor** (0.0–1.0) specifies the normalized power rate relative to the
battery's nominal maximum charge power. A value of 1.0 corresponds to full-rate charging, while 0.0
indicates no power transfer. Intermediate values scale the power proportionally.

### Home Appliance Instructions

The home appliance instructions assume an idealized home appliance model. Under this model,
the home appliance can be operated in two operation modes:

| **Operation Mode ID** | **Description**                                                                      |
| --------------------- | ------------------------------------------------------------------------------------ |
| **RUN**               | The home appliance is started and runs until the end of it's power sequence.         |
| **IDLE**              | The home appliance does not run.                                                     |

The **operation mode factor** (0.0–1.0) is ignored.

## Configuration

### Energy management configuration

The energy management is run on configured intervals with some startup delay after server start.
Both values are given in seconds.

:::{admonition} Note
:class: note
If no interval is configured (`None`, `null`) there will be only one energy management run at
startup.
:::

The energy management can be run in three modes:

- **OPTIMIZATION**: A full optimization is done. This includes update of predictions.
- **PREDICTION**: Only the predictions are updated.
- **DISABLED**: No energy management is run.

**Example:**

```json
{
    "ems": {
        "startup_delay": 5.0,
        "interval": 300.0,
        "mode": "OPTIMIZATION"
    }
}
```

### Optimization Configuration

#### Optimization Time Configuration

- **horizon_hours**:
    The optimization horizon parameter defines the default time window — in hours — within which
    the energy optimization goal shall be achieved.

    Specific devices, like the home appliance, have their own configuration for time windows. If
    the time windows are not configured the simulation uses the default time window.

    Each device simulation run must ensure that all tasks or appliance cycles (e.g., running a
    dishwasher) are completed within the configured time windows.

- **interval**: Defines the time step in seconds between control actions
    (e.g. `3600` for one hour, `900` for 15 minutes).

:::{warning}
**Current Limitation**

At present, the `interval` setting is **not used** by the genetic algorithm. Instead:

- The control interval is fixed to **1 hour**.

Support for configurable intervals (e.g. 15-minute steps) may be added in a future release.
:::

#### Genetic Algorithm Parameters

The behavior of the genetic algorithm can be customized using the following configuration options:

- **individuals** (`int`, default: `300`):
  Sets the number of individuals (candidate solutions) in the (first) generation. A higher number
  increases solution diversity and the chance of finding a good result, but also increases
  computation time.

- **generations** (`int`, default: `400`):
  Sets the number of generations to evaluate the optimal solution. In each generation, solutions are
  evaluated and evolved. More generations can improve optimization quality but increase computation
  time. Best results are usually found within a moderate number of generations.

- **seed** (`int` or `null`, default: `null`):
  Sets the random seed for reproducible results.

  - If `null`, a random seed is used (non-reproducible).
  - If an integer is provided, it ensures that the same optimization input yields the same output.

    A fixed seed to ensure reproducibility. Runs with the same seed and configuration will
    produce the same results.

- **fitness_cache_max_entries** (`int` or `null`, default: `null`):
  Limits the fitness cache of one optimization run. The cache remembers the fitness of every
  evaluated plan so that a repeated plan is not simulated again. It is cleared after each run,
  but during a run it grows with individuals × generations (roughly 100 MB at 300 × 400) while
  typically only 5–15 % of the evaluations hit it.

  - If `null`, the cache is unbounded (previous behaviour).
  - If `0`, the cache is disabled; every plan is simulated.
  - If a positive integer N, the cache holds at most N keys and drops the oldest first. Each
    evaluated plan takes one or two keys.

  The value never changes the result: with a fixed seed, all settings give the same plan. Small
  devices (e.g. 512 MB RAM) should use `0` or a few thousand; this costs a few percent of run
  time.

- **penalties** (`dict`):
  Defines how penalties are applied to solutions that violate constraints (e.g., undercharged
  batteries). Penalty function parameter values influence the fitness score, discouraging
  undesirable solutions.

:::{note}
**Supported Penalty Functions**

- `ev_soc_miss`:
  Applies a penalty when the **state of charge (SOC)** of the electric vehicle battery falls below
  the required minimum. This encourages the optimizer to ensure sufficient EV charging.
  Default: `10`.

- `ac_charge_break_even`:
  Applies a penalty for each scheduled AC grid-charging hour where the round-trip losses
  (AC→DC inverter, battery internal, DC→AC inverter) mean the stored energy can never be
  discharged at a price that recovers the charging cost. Energy already stored in the battery
  from PV generation is treated as free and covers the most expensive future hours first, so
  the penalty only fires for the hours that remain genuinely uncovered.

  A value of `1.0` (default) means the penalty equals the actual economic loss in €.
  Use larger values (e.g. `3.0`) to make the optimizer more aggressively avoid unprofitable
  AC charging, or `0.0` to disable this penalty entirely.

- `battery_full_charge_miss`:
  Applies a penalty per percent the battery stays below its target when a periodic full
  charge is due and enforced (see [Periodic full charge](#periodic-full-charge)).
  Default: `10`.
:::

#### Value Formats

- **Time-related values**:
  - `hours`: specified in **hours** (e.g. `24`)
  - `interval`: specified in **seconds** (e.g. `3600`)

- **Genetic algorithm parameters**:
  - `individuals`: must be an **integer**
  - `seed`: must be an **integer** or `null` for random behavior

- **Penalty function parameter values**: may be `float`, `int`, or `string`, depending on the type
  of penalty function.

#### Optimization configuration example

```json
{
    "optimization": {
        "hours": 24,
        "interval": 3600,
        "genetic" : {
            "individuals": 300,
            "generations": 400,
            "seed": null,
            "penalties": {
                "ev_soc_miss": 10,
                "ac_charge_break_even": 1.0
            }
        }
    }
}
```

### Device simulation configuration

The device simulations are used to evaluate the fitness of the individuals of the solution
population.

The GENETIC algorithm supports 4 devices:

- **inverter**: A photovoltaic power inverter that can export to the grid and charge a battery.
  The inverter is mandatory.
- **electric_vehicle**: An electric vehicle, basically the battery of an electric vehicle. The
  The electrical vehicle is optional.
- **battery**: A battery that can be charged by the inverter. The battery is mandatory.
- **home_appliance**: A home appliance, like a washing machine or a dish washer. The home
  appliance is optional.

:::{admonition} Warning
:class: warning
The GENETIC algorithm can only use the first inverter, electrical vehicle, battery, home appliance
that is configured, even if more devices are configured.
:::

#### Inverter simulation configuration

**Example:**

```json
{
    "devices": {
        "max_inverters": 1,
        "inverters": [
            {
                "device_id": "inv1",
                "max_power_w": 10000,
                "battery_id": "bat1",
                "ac_to_dc_efficiency": 0.95,
                "dc_to_ac_efficiency": 0.95,
                "max_ac_charge_power_w": 5000
            }
        ]
    }
}
```

The inverter supports separate AC↔DC conversion efficiencies:

- `ac_to_dc_efficiency`: Conversion loss when charging the battery from AC grid power (0-1).
  Set to `0` to disable AC charging. Default `1.0`.
- `dc_to_ac_efficiency`: Conversion loss when discharging battery to AC load/grid (0-1).
  Must be > 0. Default `1.0`.
- `max_ac_charge_power_w`: Maximum AC charging power in watts. `null` = no additional limit.
  Set to `0` to disable AC charging. Default `null`.
- `dc_to_ac_efficiency_curve`: Optional load-dependent DC→AC efficiency as a list of
  `[load_fraction, efficiency]` points, with the load fraction relative to `max_power_w`.
  Real inverters are much less efficient at low load, e.g. when the battery serves a small
  night-time base load. The points must be strictly increasing in load fraction (0-1) with
  efficiencies in (0, 1]; values in between are interpolated linearly, values outside are
  clamped to the first/last point. When set, the curve replaces `dc_to_ac_efficiency` for
  battery discharge. The efficiency is always the one at the AC power that is delivered: if
  the battery limits a discharge (low state of charge or its power limit), the conversion is
  evaluated at that lower load, not at the requested one. Default `null` (constant
  `dc_to_ac_efficiency`, unchanged behaviour).
- `dc_to_ac_efficiency_reference_load_fraction`: Load fraction at which the curve is evaluated
  where stored energy is valued without a specific discharge, e.g. the energy left in the
  battery at the end of the horizon and the AC charge break-even. Choose the typical load
  the battery serves. Only used with `dc_to_ac_efficiency_curve`. Default `0.06`.

**Example with an efficiency curve:**

```json
{
    "devices": {
        "inverters": {
            "inv1": {
                "max_power_w": 10000,
                "battery_id": "bat1",
                "dc_to_ac_efficiency_curve": [
                    [0.02, 0.80], [0.07, 0.93], [0.30, 0.96], [1.00, 0.95]
                ],
                "dc_to_ac_efficiency_reference_load_fraction": 0.06
            }
        }
    }
}
```

#### Electric vehicle simulation configuration

**Example:**

```json
{
    "devices": {
        "max_electric_vehicles": 1,
        "electric_vehicles": [
            {
                "device_id": "ev1",
                "capacity_wh": 50000,
                "max_charge_power_w": 10000,
                "charge_rates": [0.0, 0.25, 0.5, 0.75, 1.0],
                "min_soc_percentage": 10,
                "max_soc_percentage": 80
            }
        ]
    },
    "measurement": {
        "electric_vehicle_soc_keys": ["ev1_soc"]
    }
}
```

The GENETIC algorithm can delay EV charging until an earliest charge start and require the
target state of charge by a deadline:

- `charge_start_datetime`: earliest charge start. Use it for a vehicle that is not connected yet
  (for example its expected arrival or plug-in time) or for a connected vehicle that may only
  charge from a given time on. Before it the vehicle neither charges nor discharges, and its state
  of charge stays at the initial state of charge. For a vehicle that is not connected yet, supply
  the state of charge you expect at the charge start as initial state of charge (for
  `POST /v1/optimize` in `soc`, since there is no fresh measurement while the vehicle is away).
  A slot that begins before the charge start is not used; a charge start that has already passed
  lets the vehicle charge at once. If the charge start lies after the control horizon, the vehicle
  gets no charging plan in this run.
- `min_soc_deadline_datetime`: deadline (for example the departure time) by which
  `min_soc_percentage` has to be reached. The target is checked at the deadline itself. A
  deadline inside a slot counts the part of that slot before it: the planned charge of a slot
  is taken as spread evenly over the slot, so with a departure at 02:50 on the hourly grid 50 of
  the 60 minutes of 02:00-03:00 count, and the charge after 02:50 does not. In the current slot
  only the time from now to the deadline counts (see below); a deadline that has passed is due
  right now.
  Unlike the charge start, a partly used slot can count here: its charge is only checked at the
  deadline, it is never placed before the moment the vehicle may charge. The deadline does not
  end charging: the plan may still charge after it, for example at negative prices or in the
  rest of the deadline's slot, so a client should not execute EV charging after the vehicle has
  left.
  A deadline at or before the charge start can not be met by any plan; it is then ignored with a
  warning and the target applies at the end of the horizon, also in runs after the charge start.
  Every later deadline is kept. When the vehicle has no time to charge before it - charge start
  and deadline inside one slot that begins before the charge start, for example 01:07 and 01:50
  on the hourly grid - EOS warns and plans no charging for the target; once the charge start has
  passed, the vehicle may charge in the current slot, so a run during that slot plans the
  charging up to the deadline.

A run that starts inside a slot (EOS rounds the start of the optimization down to the slot)
charges the vehicle in that current slot only for the rest of it: a charge factor `f` stores
`f` x charging power x the time left in the slot, not a whole slot's energy. The exported factor
stays a share of the charging power, to be applied for the rest of the slot. A client that runs
the optimization again every few minutes therefore gets the factor the remaining time needs,
which rises towards the end of the slot when needed, instead of a smaller factor with every run.
Only the vehicle is treated this way; load, PV and the stationary battery keep their whole-slot
forecasts and controls in the current slot.

A date time without timezone is read as local time. Without these settings, the vehicle can charge
throughout the horizon and has to reach its target by the end of it.

By default energy above `min_soc_percentage` has no value for GENETIC: it costs money and earns
nothing, so the optimizer has no reason to charge beyond the target (it may still do so where the
charge rates round up or prices are negative), and a vehicle that already reached its target gets
no charging plan - even in a cheap night or with PV surplus that would otherwise be fed in. Energy
above the target can be given a value:

- `soc_value_steps`: `[[soc_percentage, amount_per_kwh], ...]` with strictly ascending state of
  charge in ]0, 100] and finite, non-negative amounts that do not rise with the state of charge.
  Every kWh stored in the vehicle (SoC energy, after charging losses) between the previous step -
  at first `min_soc_percentage` - and a step's state of charge is credited with that step's
  amount. Energy below the target, above the last step or above `max_soc_percentage` earns
  nothing. The credit is taken where the target is checked: at the deadline
  (`min_soc_deadline_datetime` or `min_soc_max_duration_h`), or else at the end of the control
  horizon. Charging after the deadline therefore earns nothing, also inside the deadline's
  slot: only the share of that slot before the deadline is credited. A deadline at or before
  `charge_start_datetime` is relaxed and not checked, so the credit is then taken at the end of
  the control horizon, like the target.

The optimizer then charges beyond the target, up to `max_soc_percentage`, in slots where storing a
kWh costs less than the value of the next kWh: a grid kWh costs the import price divided by the
charging efficiency, PV surplus the feed-in tariff it no longer earns. Non-rising amounts make
the value concave, so the first kWh above the target is worth the most. The target keeps its
penalty and does not compete with the value: more energy only lowers the penalty and raises the
credit. The value does compete with the other penalties of the fitness (for example a grid
import limit), so amounts far above energy prices should be avoided.

A vehicle that already reached its target is planned as long as it is below
`max_soc_percentage` and a step with a positive amount ends above its state of charge (a step
beyond `max_soc_percentage` counts up to the maximum). With a charge start, the vehicle
can only charge after it. The plan never charges in a slot that begins at `max_soc_percentage`;
inside a slot the vehicle may reach its maximum, and a client should stop charging there.

What energy above the target is worth is up to the caller, typically what it saves later: for
a commuter, the expected cost of charging that energy before the following trips; for PV
surplus charging without a target (`min_soc_percentage` 0), a value just above the feed-in tariff
divided by the charging efficiency, so that surplus is stored and grid energy is not. Without
`soc_value_steps` (or with an empty list) plans are unchanged.

```json
{
    "min_soc_percentage": 50,
    "max_soc_percentage": 80,
    "soc_value_steps": [[65, 0.31], [80, 0.27]]
}
```

Here a car that has to reach 50 % by its departure is charged further where a stored kWh costs
less than 0.31 (between 50 and 65 %) or 0.27 (between 65 and 80 %).

#### Battery simulation configuration

**Example:**

```json
{
    "devices": {
        "max_batteries": 1,
        "batteries": [
            {
                "device_id": "battery1",
                "capacity_wh": 8000,
                "charging_efficiency": 0.88,
                "discharging_efficiency": 0.88,
                "levelized_cost_of_storage_kwh": 0.12,
                "max_charge_power_w": 8000,
                "min_charge_power_w": 50,
                "charge_rates": null,
                "min_soc_percentage": 5,
                "max_soc_percentage": 95
            }
        ]
    }
}
```

##### Periodic full charge

LFP batteries have an almost flat voltage curve in the middle of their range. The battery
management system can only recalibrate the state of charge close to full charge, so many
manufacturers expect a full charge every few weeks, and some batteries start one on their own
at an arbitrary time. A periodic full charge lets EOS plan it instead, preferably from PV
surplus or at a cheap price.

The feature is off by default. It is enabled per stationary battery with
`full_charge_interval_days` (GENETIC only):

- `full_charge_interval_days`: maximum number of days between two full charges. `null` (default)
  disables the feature. For LFP batteries 21 to 25 days are a reasonable start; check what the
  manufacturer requires.
- `full_charge_soc_percentage` (default `100`): the target. It may be above `max_soc_percentage`,
  which keeps applying on all other days.
- `full_charge_detect_percentage` (default `99`): the measured state of charge from which the
  battery counts as full. Many battery management systems never report exactly 100 %.
- `full_charge_allow_grid` (default `false`): with `false`, EOS only raises the maximum state of
  charge while the full charge is due, so that PV surplus can fill the battery; the target is not
  enforced. With `true`, the optimizer has to reach the target and charges from the grid if
  needed, at the cheapest time before the deadline.
- `cell_chemistry` (`LFP`, `NMC`, `NCA`, `LTO` or `null`): informational only, it does not
  enable anything.

The last full charge is taken from the measured state of charge of the battery
(`<device_id>-soc-factor`), so a full charge from PV or one the battery started itself counts as
well. Nothing else is stored. Without a full charge in the last interval, the interval counts
from the oldest measurement, so a new installation is not forced into a full charge on its first
day. The next full charge is due one interval after the last one. EOS acts once that deadline
falls within the optimization horizon or has passed, and logs an INFO message for every run in
which the full charge is due. The optimizer always gets at least 24 hours to finish it, so that
a close or overdue full charge can still use the cheapest time.

```json
{
    "devices": {
        "batteries": {
            "battery1": {
                "device_id": "battery1",
                "capacity_wh": 10000,
                "max_soc_percentage": 90,
                "cell_chemistry": "LFP",
                "full_charge_interval_days": 21,
                "full_charge_allow_grid": true
            }
        }
    }
}
```

#### Home appliance simulation configuration

**Example:**

```json
{
    "devices": {
        "max_home_appliances": 1,
        "home_appliances": [
            {
                "device_id": "washing machine",
                "consumption_wh": 600,
                "duration_h": 3,
                "time_windows": null,
            }
        ]
    }
}
```

The time windows the home appliance may run can be [configured](#configtimewindow-page) in several
ways. See the [time window configuration](#configtimewindow-page) for details.

## Predictions configuration

The device simulation may rely on predictions to simulate proper behaviour. E.g. the inverter needs
to know the PV forecast.

Configure the [predictions](#prediction-page) as described on the [prediction page](#prediction-page).

### Providing your own prediction data

If EOS does not have a suitable prediction provider you can provide your own data for a prediction.
Configure the respective import provider (ElecPriceImport, LoadImport, PVForecastImport,
WeatherImport) and use one of the following endpoints to provide your own data:

- **PUT** `/v1/prediction/import/ElecPriceImport`
- **PUT** `/v1/prediction/import/LoadImport`
- **PUT** `/v1/prediction/import/PVForecastImport`
- **PUT** `/v1/prediction/import/WeatherImport`

## Measurement configuration

Predictions and device simulations often rely on **measurement data** to produce accurate results.
For example:

- A **load forecast** requires past energy meter readings.
- A **battery simulation** needs the current **state of charge (SoC)** to start from the correct
  condition.

Before using these features, make sure to configure the [measurement](#measurement-page) as
described on the [measurement page](#measurement-page).

### Providing your own measurement data

You can provide your own measurement data to the prediction and simulation engine through the
following REST endpoints (see the [measurement page](#measurement-page) for details on the data
format):

- **PUT** `/v1/measurement/data`
- **PUT** `/v1/measurement/dataframe`
- **PUT** `/v1/measurement/series`
- **PUT** `/v1/measurement/value`

### Example: Supplying Battery and EV SoC

For **batteries** and **electric vehicles**, it is strongly recommended to provide
**current SoC**. This ensures that simulations start with the correct state.

The simplest way is to use the `/v1/measurement/value` endpoint.
Assuming the battery is named `battery1` and the EV is named `ev11`:

1. **Use the measurement keys** that are pre-configured for your **devices**. For example:

    ```json
    {
        "devices": {
            "batteries": [
                {
                "device_id": "battery1", "capacity_wh": 8000,  ...
                "measurement_key_soc_factor": "battery1-soc-factor", ...
                }
            ],
            "electric_vehicles": [
                {
                "device_id": "ev11", "capacity_wh": 8000,  ...
                "measurement_key_soc_factor": "ev11-soc-factor", ...
                }
            ]
        }
    }
    ```

2. **Record your SoC readings** to these keys.

   - Enter the values as **factor of total capacity** of the respective **battery**.

In these examples:

- datetime specifies the timestamp of the measurement.
- key is the measurement key (e.g. battery1-soc-factor).
- value is the numeric measurement value (e.g. SoC as factor of total capacity).

#### Raw HTTP request

```http
PUT http://127.0.0.1:8503/v1/measurement/value?datetime=2025-09-26T16%3A39&key=battery1-soc-factor&value=0.57
PUT http://127.0.0.1:8503/v1/measurement/value?datetime=2025-09-26T16%3A39&key=ev11-soc-factor&value=0.22
```

#### Equivalent curl commands

```bash
curl -X PUT "http://127.0.0.1:8503/v1/measurement/value?datetime=2025-09-26T16%3A39&key=battery1-soc-factor&value=0.57"
curl -X PUT "http://127.0.0.1:8503/v1/measurement/value?datetime=2025-09-26T16%3A39&key=ev11-soc-factor&value=0.22"
```

### Example: Supplying Load Data

To provide your actual load measurements in Akkudoktor-EOS:

1. **Configure the measurement keys** for your load energy meters. For example:

    ```json
    {
        "measurements": {
            "load_emr_keys": ["my_load_meter_reading", "my_other_load_meter_reading"]
        }
    }
    ```

2. **Record your meter readings** to these keys.

   - Enter the values exactly as your energy meters report them, in **kWh**.
   - Use the same approach as when supplying battery or EV SoC data.
