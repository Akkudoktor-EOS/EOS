# Getting Started

## Installation

The project requires Python 3.10 or newer. Currently there are no official packages or images published.

Following sections describe how to locally start the EOS server on `http://localhost:8503`.

### Run from source

Install the dependencies in a virtual environment:

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        python -m venv .venv
        .venv\Scripts\pip install -r requirements.txt

  .. tab:: Linux

     .. code-block:: bash

        python -m venv .venv
        .venv/bin/pip install -r requirements.txt

```

Start the EOS fastapi server:

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        .venv\Scripts\python src/akkudoktoreos/server/eos.py

  .. tab:: Linux

     .. code-block:: bash

        .venv/bin/python src/akkudoktoreos/server/eos.py

```

### Docker

```{eval-rst}
.. tabs::

  .. tab:: Windows

     .. code-block:: powershell

        docker compose up --build

  .. tab:: Linux

     .. code-block:: bash

        docker compose up --build

```

## Configuration

This project uses the `EOS.config.json` file to manage configuration settings.

### Default Configuration

A default configuration file `default.config.json` is provided. This file contains all the necessary configuration keys with their default values.

### Custom Configuration

Users can specify a custom configuration directory by setting the environment variable `EOS_DIR`.

- If the directory specified by `EOS_DIR` contains an existing `EOS.config.json` file, the application will use this configuration file.
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
