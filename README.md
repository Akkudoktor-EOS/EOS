# Energy System Simulation and Optimization

This project provides a comprehensive solution for simulating and optimizing an energy system based on renewable energy sources. With a focus on photovoltaic (PV) systems, battery storage (batteries), load management (consumer requirements), heat pumps, electric vehicles, and consideration of electricity price data, this system enables forecasting and optimization of energy flow and costs over a specified period.

Documentation can be found at [Akkudoktor-EOS](https://akkudoktor-eos.readthedocs.io/en/latest/).

## Getting Involved

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Installation

The project requires Python 3.11 or newer. Official docker images can be found at [akkudoktor/eos](https://hub.docker.com/r/akkudoktor/eos).

Following sections describe how to locally start the EOS server on `http://localhost:8503`.

### Run from source

Install dependencies in virtual environment:

Linux:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
```

Windows:

```cmd
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -e .
```

Finally, start the EOS server:

Linux:

```bash
.venv/bin/python src/akkudoktoreos/server/eos.py
```

Windows:

```cmd
.venv\Scripts\python src/akkudoktoreos/server/eos.py
```

### Docker

```bash
docker compose up
```

If you are running the EOS container on a system hosting multiple services, such as a Synology NAS, and want to allow external network access to EOS, please ensure that the default exported ports (8503, 8504) are available on the host. On Synology systems, these ports might already be in use (refer to [this guide](https://kb.synology.com/en-me/DSM/tutorial/What_network_ports_are_used_by_Synology_services)). If the ports are occupied, you will need to reconfigure the exported ports accordingly.

## Configuration

This project uses the `EOS.config.json` file to manage configuration settings.

### Default Configuration

A default configuration file `default.config.json` is provided. This file contains all the necessary configuration keys with their default values.

### Custom Configuration

Users can specify a custom configuration directory by setting the environment variable `EOS_DIR`.

- If the directory specified by `EOS_DIR` contains an existing `config.json` file, the application will use this configuration file.
- If the `EOS.config.json` file does not exist in the specified directory, the `default.config.json` file will be copied to the directory as `EOS.config.json`.

### Configuration Updates

If the configuration keys in the `EOS.config.json` file are missing or different from those in `default.config.json`, they will be automatically updated to match the default settings, ensuring that all required keys are present.

## Classes and Functionalities

This project uses various classes to simulate and optimize the components of an energy system. Each class represents a specific aspect of the system, as described below:

- `Battery`: Simulates a battery storage system, including capacity, state of charge, and now charge and discharge losses.

- `PVForecast`: Provides forecast data for photovoltaic generation, based on weather data and historical generation data.

- `Load`: Models the load requirements of a household or business, enabling the prediction of future energy demand.

- `Heatpump`: Simulates a heat pump, including its energy consumption and efficiency under various operating conditions.

- `Strompreis`: Provides information on electricity prices, enabling optimization of energy consumption and generation based on tariff information.

- `EMS`: The Energy Management System (EMS) coordinates the interaction between the various components, performs optimization, and simulates the operation of the entire energy system.

These classes work together to enable a detailed simulation and optimization of the energy system. For each class, specific parameters and settings can be adjusted to test different scenarios and strategies.

### Customization and Extension

Each class is designed to be easily customized and extended to integrate additional functions or improvements. For example, new methods can be added for more accurate modeling of PV system or battery behavior. Developers are invited to modify and extend the system according to their needs.

## Server API

See the Swagger API documentation for detailed information: [EOS OpenAPI Spec](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json)

## Further resources

- [Installation guide (de)](https://meintechblog.de/2024/09/05/andreas-schmitz-joerg-installiert-mein-energieoptimierungssystem/)
