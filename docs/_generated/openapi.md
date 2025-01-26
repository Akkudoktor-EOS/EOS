# Akkudoktor-EOS

**Version**: `0.0.1`

**Description**: This project provides a comprehensive solution for simulating and optimizing an energy system based on renewable energy sources. With a focus on photovoltaic (PV) systems, battery storage (batteries), load management (consumer requirements), heat pumps, electric vehicles, and consideration of electricity price data, this system enables forecasting and optimization of energy flow and costs over a specified period.

**Base URL**: `No base URL provided.`

**Endpoints**:

## POST /gesamtlast

**Links**: [local](http://localhost:8503/docs#/default/fastapi_gesamtlast_gesamtlast_post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_gesamtlast_gesamtlast_post)

Fastapi Gesamtlast

```
Deprecated: Total Load Prediction with adjustment.

Endpoint to handle total load prediction adjusted by latest measured data.

Total load prediction starts at 00.00.00 today and is provided for 48 hours.
If no prediction values are available the missing ones at the start of the series are
filled with the first available prediction value.

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

## GET /gesamtlast_simple

**Links**: [local](http://localhost:8503/docs#/default/fastapi_gesamtlast_simple_gesamtlast_simple_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_gesamtlast_simple_gesamtlast_simple_get)

Fastapi Gesamtlast Simple

```
Deprecated: Total Load Prediction.

Endpoint to handle total load prediction.

Total load prediction starts at 00.00.00 today and is provided for 48 hours.
If no prediction values are available the missing ones at the start of the series are
filled with the first available prediction value.

Args:
    year_energy (float): Yearly energy consumption in Wh.

Note:
    Set LoadAkkudoktor as provider, then update data with
    '/v1/prediction/update'
    and then request data with
    '/v1/prediction/list?key=load_mean' instead.
```

**Parameters**:

- `year_energy` (query, required): No description provided.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## POST /optimize

**Links**: [local](http://localhost:8503/docs#/default/fastapi_optimize_optimize_post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_optimize_optimize_post)

Fastapi Optimize

**Parameters**:

- `start_hour` (query, optional): Defaults to current hour of the day.

- `ngen` (query, optional): No description provided.

**Request Body**:

- `application/json`: {
  "$ref": "#/components/schemas/OptimizationParameters"
}

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## GET /pvforecast

**Links**: [local](http://localhost:8503/docs#/default/fastapi_pvforecast_pvforecast_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_pvforecast_pvforecast_get)

Fastapi Pvforecast

```
Deprecated: PV Forecast Prediction.

Endpoint to handle PV forecast prediction.

PVForecast starts at 00.00.00 today and is provided for 48 hours.
If no forecast values are available the missing ones at the start of the series are
filled with the first available forecast value.

Note:
    Set PVForecastAkkudoktor as provider, then update data with
    '/v1/prediction/update'
    and then request data with
    '/v1/prediction/list?key=pvforecast_ac_power' and
    '/v1/prediction/list?key=pvforecastakkudoktor_temp_air' instead.
```

**Responses**:

- **200**: Successful Response

---

## GET /strompreis

**Links**: [local](http://localhost:8503/docs#/default/fastapi_strompreis_strompreis_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_strompreis_strompreis_get)

Fastapi Strompreis

```
Deprecated: Electricity Market Price Prediction per Wh (€/Wh).

Electricity prices start at 00.00.00 today and are provided for 48 hours.
If no prices are available the missing ones at the start of the series are
filled with the first available price.

Note:
    Electricity price charges are added.

Note:
    Set ElecPriceAkkudoktor as provider, then update data with
    '/v1/prediction/update'
    and then request data with
    '/v1/prediction/list?key=elecprice_marketprice_wh' or
    '/v1/prediction/list?key=elecprice_marketprice_kwh' instead.
```

**Responses**:

- **200**: Successful Response

---

## GET /v1/config

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_get_v1_config_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_get_v1_config_get)

Fastapi Config Get

```
Get the current configuration.

Returns:
    configuration (ConfigEOS): The current configuration.
```

**Responses**:

- **200**: Successful Response

---

## PUT /v1/config

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_put_v1_config_put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_put_v1_config_put)

Fastapi Config Put

```
Update the current config with the provided settings.

Note that for any setting value that is None or unset, the configuration will fall back to
values from other sources such as environment variables, the EOS configuration file, or default
values.

Args:
    settings (SettingsEOS): The settings to write into the current settings.

Returns:
    configuration (ConfigEOS): The current configuration after the write.
```

**Request Body**:

- `application/json`: {
  "$ref": "#/components/schemas/SettingsEOS"
}

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## PUT /v1/config/file

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_file_put_v1_config_file_put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_file_put_v1_config_file_put)

Fastapi Config File Put

```
Save the current configuration to the EOS configuration file.

Returns:
    configuration (ConfigEOS): The current configuration that was saved.
```

**Responses**:

- **200**: Successful Response

---

## PUT /v1/config/reset

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_update_post_v1_config_reset_put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_update_post_v1_config_reset_put)

Fastapi Config Update Post

```
Reset the configuration to the EOS configuration file.

Returns:
    configuration (ConfigEOS): The current configuration after update.
```

**Responses**:

- **200**: Successful Response

---

## PUT /v1/measurement/data

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_data_put_v1_measurement_data_put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_data_put_v1_measurement_data_put)

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

## PUT /v1/measurement/dataframe

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_dataframe_put_v1_measurement_dataframe_put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_dataframe_put_v1_measurement_dataframe_put)

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

## GET /v1/measurement/keys

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_keys_get_v1_measurement_keys_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_keys_get_v1_measurement_keys_get)

Fastapi Measurement Keys Get

```
Get a list of available measurement keys.
```

**Responses**:

- **200**: Successful Response

---

## GET /v1/measurement/load-mr/series/by-name

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_load_mr_series_by_name_get_v1_measurement_load-mr_series_by-name_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_load_mr_series_by_name_get_v1_measurement_load-mr_series_by-name_get)

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

## PUT /v1/measurement/load-mr/series/by-name

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_load_mr_series_by_name_put_v1_measurement_load-mr_series_by-name_put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_load_mr_series_by_name_put_v1_measurement_load-mr_series_by-name_put)

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

## PUT /v1/measurement/load-mr/value/by-name

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_load_mr_value_by_name_put_v1_measurement_load-mr_value_by-name_put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_load_mr_value_by_name_put_v1_measurement_load-mr_value_by-name_put)

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

## GET /v1/measurement/series

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_series_get_v1_measurement_series_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_series_get_v1_measurement_series_get)

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

## PUT /v1/measurement/series

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_series_put_v1_measurement_series_put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_series_put_v1_measurement_series_put)

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

## PUT /v1/measurement/value

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_value_put_v1_measurement_value_put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_value_put_v1_measurement_value_put)

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

## GET /v1/prediction/keys

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_keys_get_v1_prediction_keys_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_keys_get_v1_prediction_keys_get)

Fastapi Prediction Keys Get

```
Get a list of available prediction keys.
```

**Responses**:

- **200**: Successful Response

---

## GET /v1/prediction/list

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_list_get_v1_prediction_list_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_list_get_v1_prediction_list_get)

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

## GET /v1/prediction/providers

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_providers_get_v1_prediction_providers_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_providers_get_v1_prediction_providers_get)

Fastapi Prediction Providers Get

```
Get a list of available prediction providers.

Args:
    enabled (bool): Return enabled/disabled providers. If unset, return all providers.
```

**Parameters**:

- `enabled` (query, optional): No description provided.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## GET /v1/prediction/series

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_series_get_v1_prediction_series_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_series_get_v1_prediction_series_get)

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

## POST /v1/prediction/update

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_update_v1_prediction_update_post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_update_v1_prediction_update_post)

Fastapi Prediction Update

```
Update predictions for all providers.

Args:
    force_update: Update data even if it is already cached.
        Defaults to False.
    force_enable: Update data even if provider is disabled.
        Defaults to False.
```

**Parameters**:

- `force_update` (query, optional): No description provided.

- `force_enable` (query, optional): No description provided.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## POST /v1/prediction/update/{provider_id}

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_update_provider_v1_prediction_update__provider_id__post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_update_provider_v1_prediction_update__provider_id__post)

Fastapi Prediction Update Provider

```
Update predictions for given provider ID.

Args:
    provider_id: ID of provider to update.
    force_update: Update data even if it is already cached.
        Defaults to False.
    force_enable: Update data even if provider is disabled.
        Defaults to False.
```

**Parameters**:

- `provider_id` (path, required): No description provided.

- `force_update` (query, optional): No description provided.

- `force_enable` (query, optional): No description provided.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## GET /visualization_results.pdf

**Links**: [local](http://localhost:8503/docs#/default/get_pdf_visualization_results_pdf_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/get_pdf_visualization_results_pdf_get)

Get Pdf

**Responses**:

- **200**: Successful Response

---
