# Energy System Simulation and Optimization

This project provides a comprehensive solution for simulating and optimizing an energy system based on renewable energy sources. With a focus on photovoltaic (PV) systems, battery storage (batteries), load management (consumer requirements), heat pumps, electric vehicles, and consideration of electricity price data, this system enables forecasting and optimization of energy flow and costs over a specified period.

## Getting Involved

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Installation

The project requires Python 3.10 or newer. Currently there are no official packages or images published.

Following sections describe how to locally start the EOS server on `http://localhost:8503`.

### Run from source

Install dependencies in virtual environment:

Linux:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Windows:

```bash
python -m venv .venv
 .venv\Scripts\pip install -r requirements.txt
```

Finally, start EOS fastapi server:

Linux:

```bash
.venv/bin/fastapi run --port 8503 src/akkudoktoreos/server/fastapi_server.py
```

Windows:

```
 .venv\Scripts\fastapi run --port 8503 src/akkudoktoreos/server/fastapi_server.py
```

### Docker

```bash
docker compose up --build
```

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

See the Swagger API documentation for detailed information: [EOS OpenAPI Spec](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/docs/akkudoktoreos/openapi.json)

### Akkudoktor-EOS

**Version**: `0.0.1`

**Description**: This project provides a comprehensive solution for simulating and optimizing an energy system based on renewable energy sources. With a focus on photovoltaic (PV) systems, battery storage (batteries), load management (consumer requirements), heat pumps, electric vehicles, and consideration of electricity price data, this system enables forecasting and optimization of energy flow and costs over a specified period.

**Base URL**: `No base URL provided.`

#### Endpoints

##### `POST /gesamtlast`

Fastapi Gesamtlast

```
Deprecated: Total Load Prediction with adjustment.

Endpoint to handle total load prediction adjusted by latest measured data.

Note:
    Use '/v1/prediction/list?key=load_mean_adjusted' instead.
    Load energy meter readings to be added to EOS measurement by:
    '/v1/measurement/load-mr/value/by-name' or
    '/v1/measurement/value'
```

**Request Body**:

- `application/json`: {
  "$ref": "#/components/schemas/GesamtlastRequest"
}

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `GET /gesamtlast_simple`

Fastapi Gesamtlast Simple

```
Deprecated: Total Load Prediction.

Endpoint to handle total load prediction.

Note:
    Use '/v1/prediction/list?key=load_mean' instead.
```

**Parameters**:

- `year_energy` (query, required): No description provided.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `POST /optimize`

Fastapi Optimize

**Parameters**:

- `start_hour` (query, optional): Defaults to current hour of the day.

**Request Body**:

- `application/json`: {
  "$ref": "#/components/schemas/OptimizationParameters"
}

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `GET /pvforecast`

Fastapi Pvforecast

**Responses**:

- **200**: Successful Response

---

##### `GET /strompreis`

Fastapi Strompreis

```
Deprecated: Electricity Market Price Prediction per Wh (€/Wh).

Note:
    Use '/v1/prediction/list?key=elecprice_marketprice_wh' or
        '/v1/prediction/list?key=elecprice_marketprice_kwh' instead.
```

**Responses**:

- **200**: Successful Response

---

##### `GET /v1/config`

Fastapi Config Get

```
Get the current configuration.
```

**Responses**:

- **200**: Successful Response

---

##### `PUT /v1/config`

Fastapi Config Put

```
Merge settings into current configuration.

Args:
    settings (SettingsEOS): The settings to merge into the current configuration.
    save (Optional[bool]): Save the resulting configuration to the configuration file.
        Defaults to False.
```

**Parameters**:

- `save` (query, optional): No description provided.

**Request Body**:

- `application/json`: {
  "$ref": "#/components/schemas/SettingsEOS"
}

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `PUT /v1/measurement/data`

Fastapi Measurement Data Put

```
Merge the measurement data given as datetime data into EOS measurements.
```

**Request Body**:

- `application/json`: {
  "$ref": "#/components/schemas/PydanticDateTimeData"
}

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `PUT /v1/measurement/dataframe`

Fastapi Measurement Dataframe Put

```
Merge the measurement data given as dataframe into EOS measurements.
```

**Request Body**:

- `application/json`: {
  "$ref": "#/components/schemas/PydanticDateTimeDataFrame"
}

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `GET /v1/measurement/keys`

Fastapi Measurement Keys Get

```
Get a list of available measurement keys.
```

**Responses**:

- **200**: Successful Response

---

##### `GET /v1/measurement/load-mr/series/by-name`

Fastapi Measurement Load Mr Series By Name Get

```
Get the meter reading of given load name as series.
```

**Parameters**:

- `name` (query, required): Load name.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `PUT /v1/measurement/load-mr/series/by-name`

Fastapi Measurement Load Mr Series By Name Put

```
Merge the meter readings series of given load name into EOS measurements at given datetime.
```

**Parameters**:

- `name` (query, required): Load name.

**Request Body**:

- `application/json`: {
  "$ref": "#/components/schemas/PydanticDateTimeSeries"
}

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `PUT /v1/measurement/load-mr/value/by-name`

Fastapi Measurement Load Mr Value By Name Put

```
Merge the meter reading of given load name and value into EOS measurements at given datetime.
```

**Parameters**:

- `datetime` (query, required): Datetime.

- `name` (query, required): Load name.

- `value` (query, required): No description provided.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `GET /v1/measurement/series`

Fastapi Measurement Series Get

```
Get the measurements of given key as series.
```

**Parameters**:

- `key` (query, required): Prediction key.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `PUT /v1/measurement/series`

Fastapi Measurement Series Put

```
Merge measurement given as series into given key.
```

**Parameters**:

- `key` (query, required): Prediction key.

**Request Body**:

- `application/json`: {
  "$ref": "#/components/schemas/PydanticDateTimeSeries"
}

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `PUT /v1/measurement/value`

Fastapi Measurement Value Put

```
Merge the measurement of given key and value into EOS measurements at given datetime.
```

**Parameters**:

- `datetime` (query, required): Datetime.

- `key` (query, required): Prediction key.

- `value` (query, required): No description provided.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `GET /v1/prediction/keys`

Fastapi Prediction Keys Get

```
Get a list of available prediction keys.
```

**Responses**:

- **200**: Successful Response

---

##### `GET /v1/prediction/list`

Fastapi Prediction List Get

```
Get prediction for given key within given date range as value list.

Args:
    key (str): Prediction key
    start_datetime (Optional[str]): Starting datetime (inclusive).
        Defaults to start datetime of latest prediction.
    end_datetime (Optional[str]: Ending datetime (exclusive).
        Defaults to end datetime of latest prediction.
    interval (Optional[str]): Time duration for each interval.
        Defaults to 1 hour.
```

**Parameters**:

- `key` (query, required): Prediction key.

- `start_datetime` (query, optional): Starting datetime (inclusive).

- `end_datetime` (query, optional): Ending datetime (exclusive).

- `interval` (query, optional): Time duration for each interval.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `GET /v1/prediction/series`

Fastapi Prediction Series Get

```
Get prediction for given key within given date range as series.

Args:
    key (str): Prediction key
    start_datetime (Optional[str]): Starting datetime (inclusive).
        Defaults to start datetime of latest prediction.
    end_datetime (Optional[str]: Ending datetime (exclusive).
        Defaults to end datetime of latest prediction.
```

**Parameters**:

- `key` (query, required): Prediction key.

- `start_datetime` (query, optional): Starting datetime (inclusive).

- `end_datetime` (query, optional): Ending datetime (exclusive).

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

##### `GET /visualization_results.pdf`

Get Pdf

**Responses**:

- **200**: Successful Response

---


## Further resources

- [Installation guide (de)](https://meintechblog.de/2024/09/05/andreas-schmitz-joerg-installiert-mein-energieoptimierungssystem/)
