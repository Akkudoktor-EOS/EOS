% SPDX-License-Identifier: Apache-2.0

# Optimization

## Introduction

The `POST /optimize` API endpoint optimizes your energy management system based on various inputs
including electricity prices, battery storage capacity, PV forecast, and temperature data.

## Input Payload

### Sample Request

```json
{
    "ems": {
        "preis_euro_pro_wh_akku": 0.0007,
        "einspeiseverguetung_euro_pro_wh": 0.00007,
        "gesamtlast": [500, 500, ..., 500, 500],
        "pv_prognose_wh": [300, 0, 0, ..., 2160, 1840],
        "strompreis_euro_pro_wh": [0.0003784, 0.0003868, ..., 0.00034102, 0.00033709]
    },
    "pv_akku": {
        "device_id": "battery1",
        "capacity_wh": 12000,
        "charging_efficiency": 0.92,
        "discharging_efficiency": 0.92,
        "max_charge_power_w": 5700,
        "initial_soc_percentage": 66,
        "min_soc_percentage": 5,
        "max_soc_percentage": 100
    },
    "inverter": {
        "device_id": "inverter1",
        "max_power_wh": 15500
        "battery_id": "battery1",
    },
    "eauto": {
        "device_id": "auto1",
        "capacity_wh": 64000,
        "charging_efficiency": 0.88,
        "discharging_efficiency": 0.88,
        "max_charge_power_w": 11040,
        "initial_soc_percentage": 98,
        "min_soc_percentage": 60,
        "max_soc_percentage": 100
    },
    "temperature_forecast": [18.3, 18, ..., 20.16, 19.84],
    "start_solution": null
}
```

## Input Parameters

### Energy Management System (EMS)

#### Battery Cost (`preis_euro_pro_wh_akku`)

- Unit: €/Wh
- Purpose: Represents the residual value of energy stored in the battery
- Impact: Lower values encourage battery depletion, higher values preserve charge at the end of the simulation.

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

- `ac_charge`: Grid charging schedule (0-1)
- `dc_charge`: DC charging schedule (0-1)
- `discharge_allowed`: Discharge permission (0 or 1)

0 (no charge)
1 (charge with full load)

`ac_charge` multiplied by the maximum charge power of the battery results in the planned charging power.

#### EV Charging

- `eautocharge_hours_float`: EV charging schedule (0-1)

#### Results

The `result` object contains detailed information about the optimization outcome.
The length of the array is between 25 and 48 and starts at the current hour and ends at 23:00 tomorrow.

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
