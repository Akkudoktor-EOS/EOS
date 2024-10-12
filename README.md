# Energy System Simulation and Optimization

This project provides a comprehensive solution for simulating and optimizing an energy system based on renewable energy sources. With a focus on photovoltaic (PV) systems, battery storage (batteries), load management (consumer requirements), heat pumps, electric vehicles, and consideration of electricity price data, this system enables forecasting and optimization of energy flow and costs over a specified period.

## Getting Involved

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Installation

Good installation guide:
<https://meintechblog.de/2024/09/05/andreas-schmitz-joerg-installiert-mein-energieoptimierungssystem/>

The project requires Python 3.10 or newer.

## Configuration

This project uses a `config.json` file to manage configuration settings.

### Default Configuration

A default configuration file `default.config.json` is provided. This file contains all the necessary configuration keys with their default values.

### Custom Configuration

Users can specify a custom configuration directory by setting the environment variable `EOS_DIR`.

- If the directory specified by `EOS_DIR` contains an existing `config.json` file, the application will use this configuration file.
- If the `config.json` file does not exist in the specified directory, the `default.config.json` file will be copied to the directory as `config.json`.

### Configuration Updates

If the configuration keys in the `config.json` file are missing or different from those in `default.config.json`, they will be automatically updated to match the default settings, ensuring that all required keys are present.

### Quick Start Guide

On Linux (Ubuntu/Debian):

```bash
sudo apt install make
```

On MacOS (requires [Homebrew](https://brew.sh)):

```zsh
brew install make
```

The server can be started with `make run`. A full overview of the main shortcuts is given by `make help`.

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

To use the system, run `fastapi_server.py`, which starts the server:

```bash
./fastapi_server.py
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

# Server API

See the Swagger documentation for detailed information: [EOS OpenAPI Spec](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json)
>>>>>>> 4b4cbf2 (Move API doc from README to pydantic model classes (swagger))
