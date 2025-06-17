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
**device simulations**, which model the system’s energy behavior over a defined time period divided
into fixed intervals.

The quality of each solution (its *fitness*) is determined by how well it performs during
simulation, based on objectives such as minimizing electricity costs, maximizing self-consumption,
or meeting battery charge targets.

Through an iterative process of selection, crossover, and mutation, the algorithm gradually evolves
more effective solutions. The final result is an optimized control strategy that balances multiple
system goals within the constraints of the input data and configuration.

:::{note}
You don’t need to understand the internal workings of the genetic algorithm to benefit from
automatic optimization. EOS handles everything behind the scenes based on your configuration.
However, advanced users can fine-tune the optimization behavior using additional settings like
population size, penalties, and random seed.
:::

## Energy Management Plan

Whenever the optimization is run, the energy management plan is updated. The energy management plan
provides a list of energy management instructions in chronological order. The instructions lean on
to the S2 standard to have maximum flexibility and stay completely independent from any manufacturer.

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

## Configuration

### Energy management configuration

The energy management is run on configured intervals with some startup delay after server start.
Both values are given in seconds.

:::{admonition} Note
:class: note
If no interval is configured (`None`, `null`) there will be only one energy management run at
startup.
:::

The energy management can be run in two modes:

- **OPTIMIZATION**: A full optimization is done. This includes update of predictions.
- **PREDICTION**: Only the predictions are updated.

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

#### Simulation Time Configuration

- **hours**:
    Specifies how many hours into the future the optimization should cover.

- **interval**: Defines the time step in seconds between control actions
    (e.g. `3600` for one hour, `900` for 15 minutes).

:::{warning}
**Current Limitation**

At present, the `hours` and `interval` settings are **not used** by the genetic algorithm. Instead:

- The time period is fixed to the number of **prediction hours**.
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

- **penalties** (`dict`):
  Defines how penalties are applied to solutions that violate constraints (e.g., undercharged
  batteries). Penalty function parameter values influence the fitness score, discouraging
  undesirable solutions.

:::{note}
**Supported Penalty Functions**

Currently, the only supported penalty function parameter is:

- `ev_soc_miss`:
  Applies a penalty when the **state of charge (SOC)** of the electric vehicle battery falls below
  the required minimum. This encourages the optimizer to ensure sufficient EV charging.
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
        "hours": 48,
        "interval": 3600,
        "genetic" : {
            "individuals": 300,
            "generations": 400,
            "seed": null,
            "penalties": {
                "ev_soc_miss": 10
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
                "battery_id": "bat1"
            }
        ]
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
    }
}
```

#### Battery simulation configuration

**Example:**

```json
{
    "devices": {
        "max_batteries": 1,
        "batteries": [
            {
                "device_id": "bat1",
                "capacity_wh": 10000,
                "charging_efficiency": 0.88,
                "discharging_efficiency": 0.88,
                "levelized_cost_of_storage_kwh": 0.12,
                "max_charge_power_w": 10000,
                "min_charge_power_w": 50,
                "charge_rates": null,
                "min_soc_percentage": 5,
                "max_soc_percentage": 95
            }
        ]
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
                "duration_h": 3
            }
        ]
    }
}
```

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
