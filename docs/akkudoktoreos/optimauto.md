% SPDX-License-Identifier: Apache-2.0

# Automatic Optimization

## Introduction

EOS periodically retrieves forecasts and observations and uses OHMRAKEL42 to prepare the
energy management plan. To start a run with the stored configuration, use
POST /v1/energy-management/run. See [OHMRAKEL42 optimization](optimohmrakel42.md)
for the search, the objective and the instruction formats.

## Energy Management Plan

Whenever the optimization is run, the energy management plan is updated. The energy management plan
provides a list of energy management instructions in chronological order. The instructions lean on
to the [S2 standard](https://docs.s2standard.org/) to have maximum flexibility and stay completely
independent from any manufacturer.

### Battery Instructions

Stationary batteries can charge from PV, charge from the grid, discharge to cover demand,
or export under direct marketing. Each instruction carries a mode and its factor.
For SELF_CONSUMPTION, the factor caps discharge as a share of rated maximum
discharge power. EOS plans binary discharge unless the battery's adapter supports
the discharge ceiling and is configured accordingly.

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

The energy management is run on configured intervals with the interval configured in seconds.

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
        "interval": 300.0,
        "mode": "OPTIMIZATION"
    }
}
```

### Optimization configuration

- **horizon_hours** sets the length of the control plan in hours.
- **interval_sec** sets the control slot to 3600 seconds (one hour) or 900 seconds (15 minutes).
- **tail_horizon_hours** sets the extra forecast lookahead for valuing stored energy.
- **penalties** sets the prices of missed vehicle and full-charge targets and unprofitable
  AC charging.
- **search** sets search effort and the optional time budget.

### Device simulation configuration

OHMRAKEL42 plans the configured inverters, stationary batteries, electric vehicles,
home appliances and heat sources together. An inverter models the PV and grid flows.
Each stationary battery needs an assigned inverter.

#### Inverter simulation configuration

**Example:**

```json
{
    "devices": {
        "max_inverters": 1,
        "inverters": {
            "inv1": {
                "inverter_profile": "generic-hybrid",
                "profile_adjustment": {
                    "nominal_power_w": 10000,
                    "max_ac_charge_power_w": 5000
                },
                "battery_id": "bat1"
            }
        }
    }
}
```

The technical data of the inverter comes from an **inverter profile** (see
[](#inverter-profiles)): the efficiency curves for battery discharge (DC→AC) and for charging
the battery from the grid (AC→DC), the AC charge power limit of the model and the power derating
over the ambient temperature. The inverter settings select the profile and describe how the unit
differs from it:

- `inverter_profile`: Name of the inverter profile. Default `generic-hybrid`.
- `profile_adjustment.nominal_power_w`: Nominal AC power of the installed inverter [W]. Replaces
  the nominal power of the profile; required for a generic profile such as `generic-hybrid`.
- `profile_adjustment.max_power_w`: AC power limit of the installation [W], e.g. a configured
  output limit. Can only lower the (derated) nominal power. Default `null`.
- `profile_adjustment.max_ac_charge_power_w`: AC charge power limit of the installation [W]. Can
  only lower the limit of the profile. Set to `0` to disable AC charging. Default `null`.
- `dc_to_ac_efficiency_reference_load_fraction`: Load fraction of the maximum power at which the
  DC→AC efficiency curve is evaluated where stored energy is valued without a specific discharge,
  e.g. the energy left in the battery at the end of the horizon and the AC charge break-even.
  Choose the typical load the battery serves. Default `0.06`.

The efficiency curves are over the load fraction, the AC power relative to the nominal power.
Real inverters are much less efficient at low load, e.g. when the battery serves a small
night-time base load. The efficiency is always the one at the AC power of the conversion: if the
battery limits a discharge (low state of charge or its power limit), the conversion is evaluated
at that lower load, not at the requested one; charging from the grid draws the AC energy at the
efficiency of the AC load actually drawn.

**Power derating:** inverters reduce their power when they get hot. EOS reads the ambient
temperature of the inverter from the measurement `<device_id>-temperature-degc` (e.g.
`inv1-temperature-degc`) at the start of each optimization and limits the maximum power and the
AC charge power by the derating of the profile at that temperature. Without a measurement in the
last hour EOS assumes 45 °C, where the `generic-hybrid` profile is not derated yet.

The resolved values are shown as read-only fields of the inverter configuration
(`effective_nominal_power_w`, `effective_max_power_w`, `effective_max_ac_charge_power_w`,
`effective_dc_to_ac_efficiency`, `effective_ac_to_dc_efficiency`), evaluated at 45 °C. If the
configured inverter profile does not exist or is invalid, the optimization uses `generic-hybrid`
with the profile adjustment instead and logs an error naming the inverter.

:::{admonition} Note
:class: note
Configurations of earlier versions are migrated automatically. `max_power_w` becomes
`profile_adjustment.nominal_power_w` and `max_ac_charge_power_w` moves into the profile
adjustment; an `ac_to_dc_efficiency` of `0` keeps AC charging disabled. The former fields
`ac_to_dc_efficiency`, `dc_to_ac_efficiency`, `dc_to_ac_efficiency_curve` and
`ac_charge_limits_total_charge` come from the inverter profile now and are dropped (logged as
error), so the efficiencies of the profile apply instead of the former default `1.0` (lossless).
To keep other values, save an own inverter profile and select it.
:::

#### Electric vehicle simulation configuration

**Example:**

```json
{
    "devices": {
        "max_electric_vehicles": 1,
        "electric_vehicles": {
            "ev1": {
                "device_id": "ev1",
                "battery_profile": "generic-lfp",
                "profile_adjustment": {
                    "nominal_capacity_wh": 50000,
                    "max_charge_power_w": 10000
                },
                "charge_rates": [0.0, 0.25, 0.5, 0.75, 1.0],
                "min_soc_percentage": 10,
                "max_soc_percentage": 80
            }
        }
    },
    "measurement": {
        "electric_vehicle_soc_keys": ["ev1_soc"]
    }
}
```

#### Battery simulation configuration

**Example:**

```json
{
    "devices": {
        "max_batteries": 1,
        "batteries": {
            "battery1": {
                "device_id": "battery1",
                "battery_profile": "generic-lfp",
                "profile_adjustment": {
                    "nominal_capacity_wh": 8000,
                    "max_charge_power_w": 8000
                },
                "levelized_cost_of_storage_amt_kwh": 0.12,
                "min_charge_power_w": 50,
                "charge_rates": null,
                "min_soc_percentage": 5,
                "max_soc_percentage": 95
            }
        }
    }
}
```

##### Periodic full charge

LFP batteries have an almost flat voltage curve in the middle of their range. The battery
management system can only recalibrate the state of charge close to full charge, so many
manufacturers expect a full charge every few weeks, and some batteries start one on their own
at an arbitrary time. A periodic full charge lets EOS plan it instead, preferably from PV
surplus or at a cheap price.

The feature is off by default . The interval and the detection threshold are
properties of the battery model and come from the battery profile; the `profile_adjustment` of a
stationary battery replaces them for this unit. The target and the use of the grid are battery
settings:

- `full_charge_interval_days` (battery profile or `profile_adjustment`): maximum number of days
  between two full charges. `null` disables the feature; the bundled `generic-lfp` profile sets
  none. For LFP batteries 21 to 25 days are a reasonable start; check what the manufacturer
  requires.
- `full_charge_detect_percentage` (battery profile or `profile_adjustment`, profile default `99`):
  the measured state of charge from which the battery counts as full. Many battery management
  systems never report exactly 100 %.
- `full_charge_soc_percentage` (default `100`): the target. It may be above `max_soc_percentage`,
  which keeps applying on all other days, but is capped by the technical maximum SoC of the
  battery profile.
- `full_charge_allow_grid` (default `false`): with `false`, EOS only raises the maximum state of
  charge while the full charge is due, so that PV surplus can fill the battery; the target is not
  enforced. With `true`, the optimizer has to reach the target and charges from the grid if
  needed, at the cheapest time before the deadline.

The effective values are available as `effective_full_charge_interval_days` and
`effective_full_charge_detect_percentage`. The cell chemistry is the `chemistry` of the battery
profile.

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
                "battery_profile": "generic-lfp",
                "profile_adjustment": {
                    "nominal_capacity_wh": 10000,
                    "full_charge_interval_days": 21
                },
                "max_soc_percentage": 90,
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
            "batteries": {
                "battery1": {
                "device_id": "battery1",
                "profile_adjustment": {"nominal_capacity_wh": 8000},  ...
                "measurement_key_soc_factor": "battery1-soc-factor", ...
                }
            },
            "electric_vehicles": {
                "ev11": {
                "device_id": "ev11",
                "profile_adjustment": {"nominal_capacity_wh": 8000},  ...
                "measurement_key_soc_factor": "ev11-soc-factor", ...
                }
            }
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
