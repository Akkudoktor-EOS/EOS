# Energy System Simulation and Optimization

This project provides a comprehensive solution for simulating and optimizing an energy system based on renewable energy sources. With a focus on photovoltaic (PV) systems, battery storage (batteries), load management (consumer requirements), heat pumps, electric vehicles, and consideration of electricity price data, this system enables forecasting and optimization of energy flow and costs over a specified period.

## Getting Involved

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Installation

Good installation guide:
https://meintechblog.de/2024/09/05/andreas-schmitz-joerg-installiert-mein-energieoptimierungssystem/

The project requires Python 3.10 or newer.

### Quick Start Guide

On Linux (Ubuntu/Debian):

```bash
sudo apt install make
```

On MacOS (requires [Homebrew](https://brew.sh)):

```zsh
brew install make
```

Next, adjust `config.py`.
The server can then be started with `make run`. A full overview of the main shortcuts is given by `make help`.

### Detailed Instructions

All necessary dependencies can be installed via `pip`. Clone the repository and install the required packages with:

```bash
git clone https://github.com/Akkudoktor-EOS/EOS
cd EOS
```

Next, create a virtual environment. This serves to store the Python dependencies, which we will install later using `pip`:

```bash
virtualenv .venv
```

Finally, install the Python dependencies for EOS:

```bash
.venv/bin/pip install -r requirements.txt
```

To always use the Python version from the virtual environment, you should activate it before working in EOS:

```bash
source .venv/bin/activate
```
(for Bash users, the default under Linux) or

```zsh
. .venv/bin/activate
```

## Usage

Adjust `config.py`.
To use the system, run `flask_server.py`, which starts the server:

```bash
./flask_server.py
```

## Classes and Functionalities

This project uses various classes to simulate and optimize the components of an energy system. Each class represents a specific aspect of the system, as described below:

- `PVAkku`: Simulates a battery storage system, including capacity, state of charge, and now charge and discharge losses.

- `PVForecast`: Provides forecast data for photovoltaic generation, based on weather data and historical generation data.

- `Load`: Models the load requirements of a household or business, enabling the prediction of future energy demand.

- `Heatpump`: Simulates a heat pump, including its energy consumption and efficiency under various operating conditions.

- `Strompreis`: Provides information on electricity prices, enabling optimization of energy consumption and generation based on tariff information.

- `EMS`: The Energy Management System (EMS) coordinates the interaction between the various components, performs optimization, and simulates the operation of the entire energy system.

These classes work together to enable a detailed simulation and optimization of the energy system. For each class, specific parameters and settings can be adjusted to test different scenarios and strategies.

### Customization and Extension

Each class is designed to be easily customized and extended to integrate additional functions or improvements. For example, new methods can be added for more accurate modeling of PV system or battery behavior. Developers are invited to modify and extend the system according to their needs.


# Input for the Flask Server (as of 30.07.2024)

Describes the structure and data types of the JSON object sent to the Flask server, with a forecast period of 48 hours.

## JSON Object Fields

### `strompreis_euro_pro_wh`
- **Description**: An array of floats representing the electricity price in euros per watt-hour for different time intervals.
- **Type**: Array
- **Element Type**: Float
- **Length**: 48

### `gesamtlast`
- **Description**: An array of floats representing the total load (consumption) in watts for different time intervals.
- **Type**: Array
- **Element Type**: Float
- **Length**: 48

### `pv_forecast`
- **Description**: An array of floats representing the forecasted photovoltaic output in watts for different time intervals.
- **Type**: Array
- **Element Type**: Float
- **Length**: 48

### `temperature_forecast`
- **Description**: An array of floats representing the temperature forecast in degrees Celsius for different time intervals.
- **Type**: Array
- **Element Type**: Float
- **Length**: 48

### `pv_soc`
- **Description**: An integer representing the state of charge of the PV battery at the **start** of the current hour (not the current state).
- **Type**: Integer

### `pv_akku_cap`
- **Description**: An integer representing the capacity of the photovoltaic battery in watt-hours.
- **Type**: Integer

### `einspeiseverguetung_euro_pro_wh`
- **Description**: A float representing the feed-in compensation in euros per watt-hour.
- **Type**: Float

### `eauto_min_soc`
- **Description**: An integer representing the minimum state of charge (SOC) of the electric vehicle in percentage.
- **Type**: Integer

### `eauto_cap`
- **Description**: An integer representing the capacity of the electric vehicle battery in watt-hours.
- **Type**: Integer

### `eauto_charge_efficiency`
- **Description**: A float representing the charging efficiency of the electric vehicle.
- **Type**: Float

### `eauto_charge_power`
- **Description**: An integer representing the charging power of the electric vehicle in watts.
- **Type**: Integer

### `eauto_soc`
- **Description**: An integer representing the current state of charge (SOC) of the electric vehicle in percentage.
- **Type**: Integer

### `start_solution`
- **Description**: Can be `null` or contain a previous solution (if available).
- **Type**: `null` or object

### `haushaltsgeraet_wh`
- **Description**: An integer representing the energy consumption of a household device in watt-hours.
- **Type**: Integer

### `haushaltsgeraet_dauer`
- **Description**: An integer representing the usage duration of a household device in hours.
- **Type**: Integer



# JSON Output Description

This document describes the structure and data types of the JSON output returned by the Flask server, with a forecast period of 48 hours.

## JSON Output Fields (as of 30.7.2024)

### discharge_hours_bin
An array that indicates for each hour of the forecast period (in this example, 48 hours) whether energy is discharged from the battery or not. The values are either `0` (no discharge) or `1` (discharge).

### eauto_obj
This object contains information related to the electric vehicle and its charging and discharging behavior:

- **charge_array**: Indicates for each hour whether the EV is charging (`0` for no charging, `1` for charging).
  - **Type**: Array
  - **Element Type**: Integer (0 or 1)
  - **Length**: 48
- **discharge_array**: Indicates for each hour whether the EV is discharging (`0` for no discharging, `1` for discharging).
  - **Type**: Array
  - **Element Type**: Integer (0 or 1)
  - **Length**: 48
- **entlade_effizienz**: The discharge efficiency as a float.
  - **Type**: Float
- **hours**: Amount of hours the simulation is done for.
  - **Type**: Integer
- **kapazitaet_wh**: The capacity of the EV’s battery in watt-hours.
  - **Type**: Integer
- **lade_effizienz**: The charging efficiency as a float.
  - **Type**: Float
- **max_ladeleistung_w**: The maximum charging power of the EV in watts.
  - **Type**: Float
- **max_ladeleistung_w**: Max charging power of the EV in Watts.
  - **Type**: Integer
- **soc_wh**: The state of charge of the battery in watt-hours at the start of the simulation.
  - **Type**: Integer
- **start_soc_prozent**: The state of charge of the battery in percentage at the start of the simulation.
  - **Type**: Integer

### eautocharge_hours_float
An array of binary values (0 or 1) that indicates whether the EV will be charged in a certain hour.
- **Type**: Array
- **Element Type**: Integer (0 or 1)
- **Length**: 48

### result
This object contains the results of the simulation and provides insights into various parameters over the entire forecast period:

- **E-Auto_SoC_pro_Stunde**: The state of charge of the EV for each hour.
  - **Type**: Array
  - **Element Type**: Float
  - **Length**: 35
- **Eigenverbrauch_Wh_pro_Stunde**: The self-consumption of the system in watt-hours per hour.
  - **Type**: Array
  - **Element Type**: Float
  - **Length**: 35
- **Einnahmen_Euro_pro_Stunde**: The revenue from grid feed-in or other sources in euros per hour.
  - **Type**: Array
  - **Element Type**: Float
  - **Length**: 35
- **Gesamt_Verluste**: The total losses in watt-hours over the entire period.
  - **Type**: Float
- **Gesamtbilanz_Euro**: The total balance of revenues minus costs in euros.
  - **Type**: Float
- **Gesamteinnahmen_Euro**: The total revenues in euros.
  - **Type**: Float
- **Gesamtkosten_Euro**: The total costs in euros.
  - **Type**: Float
- **Haushaltsgeraet_wh_pro_stunde**: The energy consumption of a household appliance in watt-hours per hour.
  - **Type**: Array
  - **Element Type**: Float
  - **Length**: 35
- **Kosten_Euro_pro_Stunde**: The costs in euros per hour.
  - **Type**: Array
  - **Element Type**: Float
  - **Length**: 35
- **Netzbezug_Wh_pro_Stunde**: The grid energy drawn in watt-hours per hour.
  - **Type**: Array
  - **Element Type**: Float
  - **Length**: 35
- **Netzeinspeisung_Wh_pro_Stunde**: The energy fed into the grid in watt-hours per hour.
  - **Type**: Array
  - **Element Type**: Float
  - **Length**: 35
- **Verluste_Pro_Stunde**: The losses in watt-hours per hour.
  - **Type**: Array
  - **Element Type**: Float
  - **Length**: 35
- **akku_soc_pro_stunde**: The state of charge of the battery (not the EV) in percentage per hour.
  - **Type**: Array
  - **Element Type**: Float
  - **Length**: 35

### simulation_data
An object containing the simulated data.
  - **E-Auto_SoC_pro_Stunde**: An array of floats representing the simulated state of charge of the electric car per hour.
    - **Type**: Array
    - **Element Type**: Float
    - **Length**: 35
  - **Eigenverbrauch_Wh_pro_Stunde**: An array of floats representing the simulated self-consumption in watt-hours per hour.
    - **Type**: Array
    - **Element Type**: Float
    - **Length**: 35
  - **Einnahmen_Euro_pro_Stunde**: An array of floats representing the simulated income in euros per hour.
    - **Type**: Array
    - **Element Type**: Float
    - **Length**: 35
  - **Gesamt_Verluste**: The total simulated losses in watt-hours.
    - **Type**: Float
  - **Gesamtbilanz_Euro**: The total simulated balance in euros.
    - **Type**: Float
  - **Gesamteinnahmen_Euro**: The total simulated income in euros.
    - **Type**: Float
  - **Gesamtkosten_Euro**: The total simulated costs in euros.
    - **Type**: Float
  - **Haushaltsgeraet_wh_pro_stunde**: An array of floats representing the simulated energy consumption of a household appliance in watt-hours per hour.
    - **Type**: Array
    - **Element Type**: Float
    - **Length**: 35
  - **Kosten_Euro_pro_Stunde**: An array of floats representing the simulated costs in euros per hour.
    - **Type**: Array
    - **Element Type**: Float
    - **Length**: 35
  - **Netzbezug_Wh_pro_Stunde**: An array of floats representing the simulated grid consumption in watt-hours per hour.
    - **Type**: Array
    - **Element Type**: Float
    - **Length**: 35
  - **Netzeinspeisung_Wh_pro_Stunde**: An array of floats representing the simulated grid feed-in in watt-hours per hour.
    - **Type**: Array
    - **Element Type**: Float
    - **Length**: 35
  - **Verluste_Pro_Stunde**: An array of floats representing the simulated losses per hour.
    - **Type**: Array
    - **Element Type**: Float
    - **Length**: 35
  - **akku_soc_pro_stunde**: An array of floats representing the simulated state of charge of the battery in percentage per hour.
    - **Type**: Array
    - **Element Type**: Float
    - **Length**: 35

### spuelstart
- **Description**: Can be `null` or contain an object representing the start of washing (if applicable).
- **Type**: null or object

### start_solution
- **Description**: An array of binary values (0 or 1) representing a possible starting solution for the simulation.
- **Type**: Array
- **Element Type**: Integer (0 or 1)
- **Length**: 48
