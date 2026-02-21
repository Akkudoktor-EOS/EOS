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
        "max_charge_power_w": 5000,
        "initial_soc_percentage": 80,
        "min_soc_percentage": 15
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
        "min_soc_percentage": 0
    },
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

#### Battery Cost (`preis_euro_pro_wh_akku`)

- Unit: €/Wh
- Purpose: Represents the residual value of energy stored in the battery
- Impact: Lower values encourage battery depletion, higher values preserve charge at the end of the
  simulation.

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
- `max_charge_power_w`: Maximum charging power in W

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
`buy_price / η_round_trip`

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
- `break_even_price = charge_price / η_round_trip`
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
- `min_soc_percentage`: Minimum allowed SoC (%)
- `max_soc_percentage`: Maximum allowed SoC (%)

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
- `discharge_allowed`: Discharge permission (0 or 1)

0 (no charge)
1 (charge with full load)

`ac_charge` multiplied by the maximum charge power of the battery results in the planned charging
power.

#### EV Charging

- `eautocharge_hours_float`: EV charging schedule (0.0-1.0)

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
