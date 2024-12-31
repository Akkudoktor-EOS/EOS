% SPDX-License-Identifier: Apache-2.0

# Predictions

Predictions, along with simulations and measurements, form the foundation upon which energy
optimization is executed. In EOS, a standard set of predictions is managed, including:

- **Household Load Prediction**
- **Electricity Price Prediction**
- **PV Energy Prediction**
- **Weather Prediction**

## Storing Predictions

EOS stores predictions in a **key-value store**, where the term `prediction key` refers to the
unique key used to retrieve specific prediction data. The key-value store is in memory. Stored
data is lost on re-start of the EOS REST server.

## Prediction Providers

Most predictions can be sourced from various providers. The specific provider to use is configured
in the EOS configuration. For example:

```plaintext
weather_provider = "ClearOutside"
```

Some providers offer multiple prediction keys. For instance, a weather provider might provide data
to prediction keys like:

- `weather_temp_air` (air temperature)
- `weather_wind_speed` (wind speed)

## Adjusted Predictions

Certain prediction keys include an `_adjusted` suffix, such as `load_total_adjusted`. These
predictions are adjusted by real data from your system's measurements if given to enhance accuracy.

For example, the load prediction provider `LoadAkkudoktor` takes generic load data assembled by
Akkudoktor.net, maps that to the yearly energy consumption given in the configuration option
`loadakkudoktor_year_energy`, and finally adjusts the predicted load by the `measurement_loads`
of your system.

## Prediction Updates

Predictions are updated at the start of each energy management run, i.e., when EOS performs
optimization. Key considerations for updates include:

- Predictions sourced from online providers are usually rate-limited to one retrieval per hour.
- Only predictions with a configured provider are updated.
- Some providers may not support all generic prediction keys, leading to potential gaps
  in updated predictions even after update.

## Accessing Predictions

Prediction data can be accessed using the EOS **REST API** via the `/v1/prediction/<...>` endpoints.

In a standard configuration, the [**REST API**](http://0.0.0.0:8503/docs) of a running EOS instance
is available at [http://0.0.0.0:8503/docs](http://0.0.0.0:8503/docs).

This link provides access to the API documentation and allows you to explore available endpoints
interactively.

To view all available prediction keys, use the **GET** `/v1/prediction/keys` endpoint.

If no keys are displayed, or if the ones you need are missing, it indicates that your configuration
lacks the necessary prediction provider settings. You can configure prediction providers by using
the **PUT** `/v1/config` endpoint. You may save your configuration to the EOS configuration file.

## Electricity Price Prediction

Prediction keys:

- `elecprice_marketprice_wh`: Electricity market price per Wh (€/Wh).
- `elecprice_marketprice_kwh`: Electricity market price per kWh (€/kWh).

Configuration options:

- `elecprice_provider`: Electricity price provider id of provider to be used.

  - `ElecPriceAkkudoktor`: Retrieves from Akkudoktor.net.
  - `ElecPriceImport`: Imports from a file or JSON string.

- `elecprice_charges_kwh`: Electricity price charges (€/kWh).

- `elecpriceimport_file_path`: Path to the file to import elecprice data from.
- `elecpriceimport_json`: JSON string, dictionary of electricity price forecast value lists.

### ElecPriceAkkudoktor Provider

The `ElecPriceAkkudoktor` provider retrieves electricity prices directly from **Akkudoktor.net**,
which supplies price data for the next 24 hours. For periods beyond 24 hours, the provider generates
prices by extrapolating historical price data combined with the most recent actual prices obtained
from Akkudoktor.net.

### ElecPriceImport Provider

The `ElecPriceImport` provider is designed to import electricity prices from a file or a JSON
string. An external entity should update the file or JSON string whenever new prediction data
becomes available.

The price data (in €/Wh) must be provided in one of the following formats:

#### 1. DateTimeData

A dictionary with the following structure:

```JSON
    {
        "start_datetime": "2024-01-01 00:00:00",
        "interval": "1 Hour",
        "elecprice_marketprice_wh": [0.00033, 0.000325, 0.000295]
    }
```

#### 2. DateTimeDataFrame

A JSON string created from a [pandas](https://pandas.pydata.org/docs/index.html) dataframe with a
`DatetimeIndex`. Use [pandas.DataFrame.to_json(orient="index")](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_json.html#pandas.DataFrame.to_json).
The column name of the data must be `elecprice_marketprice_wh`.

#### 3. DateTimeSeries

A JSON string created from a [pandas](https://pandas.pydata.org/docs/index.html) series with a
`DatetimeIndex`. Use [pandas.Series.to_json(orient="index")](https://pandas.pydata.org/docs/reference/api/pandas.Series.to_json.html#pandas.Series.to_json).

## Load Prediction

Prediction keys:

Configuration options:
