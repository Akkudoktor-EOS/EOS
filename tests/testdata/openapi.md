# Akkudoktor-EOS

**Version**: `0.0.1`

**Description**: This project provides a comprehensive solution for simulating and optimizing an energy system based on renewable energy sources. With a focus on photovoltaic (PV) systems, battery storage (batteries), load management (consumer requirements), heat pumps, electric vehicles, and consideration of electricity price data, this system enables forecasting and optimization of energy flow and costs over a specified period.

**Base URL**: `No base URL provided.`

**Endpoints**:

## POST /gesamtlast

**Links**: [local](http://localhost:8503/docs#/default/fastapi_gesamtlast_gesamtlast_post), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_gesamtlast_gesamtlast_post)

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

## GET /gesamtlast_simple

**Links**: [local](http://localhost:8503/docs#/default/fastapi_gesamtlast_simple_gesamtlast_simple_get), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_gesamtlast_simple_gesamtlast_simple_get)

Fastapi Gesamtlast Simple

```
Deprecated: Total Load Prediction.

Endpoint to handle total load prediction.

Note:
    Set LoadAkkudoktor as load_provider, then update data with
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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_optimize_optimize_post), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_optimize_optimize_post)

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

## GET /pvforecast

**Links**: [local](http://localhost:8503/docs#/default/fastapi_pvforecast_pvforecast_get), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_pvforecast_pvforecast_get)

Fastapi Pvforecast

```
Deprecated: PV Forecast Prediction.

Endpoint to handle PV forecast prediction.

Note:
    Set PVForecastAkkudoktor as pvforecast_provider, then update data with
    '/v1/prediction/update'
    and then request data with
    '/v1/prediction/list?key=pvforecast_ac_power' and
    '/v1/prediction/list?key=pvforecastakkudoktor_temp_air' instead.
```

**Responses**:

- **200**: Successful Response

---

## GET /strompreis

**Links**: [local](http://localhost:8503/docs#/default/fastapi_strompreis_strompreis_get), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_strompreis_strompreis_get)

Fastapi Strompreis

```
Deprecated: Electricity Market Price Prediction per Wh (€/Wh).

Note:
    Set ElecPriceAkkudoktor as elecprice_provider, then update data with
    '/v1/prediction/update'
    and then request data with
    '/v1/prediction/list?key=elecprice_marketprice_wh' or
    '/v1/prediction/list?key=elecprice_marketprice_kwh' instead.
```

**Responses**:

- **200**: Successful Response

---

## GET /v1/config

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_get_v1_config_get), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_get_v1_config_get)

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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_put_v1_config_put), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_put_v1_config_put)

Fastapi Config Put

```
Merge the provided settings into the current settings.

If `force` is True, the existing settings are completely overwritten. Note that for any setting
value that is None, the configuration will fall back to values from other sources such as
environment variables, the EOS configuration file, or default values.

If `force` is False, only the non-None values from the provided settings will be merged into
the existing settings, giving priority to the new values.

Args:
    settings (SettingsEOS): The settings to merge into the current settings.
    force (Optional[bool]): If True, overwrites the existing settings completely.
                            If False, merges the new settings with the existing ones, giving
                            priority to the new values. Defaults to False.

Returns:
    configuration (ConfigEOS): The current configuration after the merge.
```

**Parameters**:

- `settings` (query, required): settings

- `force` (query, optional): force

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## GET /v1/config/file

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_file_get_v1_config_file_get), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_file_get_v1_config_file_get)

Fastapi Config File Get

```
Get the settings as defined by the EOS configuration file.

Args:
    update (Optional[bool]): If True, additionally update the configuration by the settings of
        the EOS configuration file. If False, only read the settings and provide it. Defaults to
        False.

Returns:
    settings (SettingsEOS): The settings defined by the EOS configuration file.
```

**Parameters**:

- `update` (query, optional): update

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## PUT /v1/config/file

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_file_put_v1_config_file_put), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_file_put_v1_config_file_put)

Fastapi Config File Put

```
Save the current configuration to the EOS configuration file.

Returns:
    configuration (ConfigEOS): The current configuration that was saved.
```

**Responses**:

- **200**: Successful Response

---

## PUT /v1/measurement/data

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_data_put_v1_measurement_data_put), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_data_put_v1_measurement_data_put)

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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_dataframe_put_v1_measurement_dataframe_put), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_dataframe_put_v1_measurement_dataframe_put)

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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_keys_get_v1_measurement_keys_get), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_keys_get_v1_measurement_keys_get)

Fastapi Measurement Keys Get

```
Get a list of available measurement keys.
```

**Responses**:

- **200**: Successful Response

---

## GET /v1/measurement/load-mr/series/by-name

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_load_mr_series_by_name_get_v1_measurement_load-mr_series_by-name_get), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_load_mr_series_by_name_get_v1_measurement_load-mr_series_by-name_get)

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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_load_mr_series_by_name_put_v1_measurement_load-mr_series_by-name_put), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_load_mr_series_by_name_put_v1_measurement_load-mr_series_by-name_put)

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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_load_mr_value_by_name_put_v1_measurement_load-mr_value_by-name_put), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_load_mr_value_by_name_put_v1_measurement_load-mr_value_by-name_put)

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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_series_get_v1_measurement_series_get), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_series_get_v1_measurement_series_get)

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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_series_put_v1_measurement_series_put), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_series_put_v1_measurement_series_put)

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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_value_put_v1_measurement_value_put), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_value_put_v1_measurement_value_put)

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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_keys_get_v1_prediction_keys_get), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_keys_get_v1_prediction_keys_get)

Fastapi Prediction Keys Get

```
Get a list of available prediction keys.
```

**Responses**:

- **200**: Successful Response

---

## GET /v1/prediction/list

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_list_get_v1_prediction_list_get), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_list_get_v1_prediction_list_get)

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

## GET /v1/prediction/series

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_series_get_v1_prediction_series_get), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_series_get_v1_prediction_series_get)

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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_update_v1_prediction_update_post), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_update_v1_prediction_update_post)

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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_update_provider_v1_prediction_update__provider_id__post), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_update_provider_v1_prediction_update__provider_id__post)

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

**Links**: [local](http://localhost:8503/docs#/default/get_pdf_visualization_results_pdf_get), [swagger](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/get_pdf_visualization_results_pdf_get)

Get Pdf

**Responses**:

- **200**: Successful Response

---