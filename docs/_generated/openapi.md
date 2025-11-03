# Akkudoktor-EOS

**Version**: `v0.1.0+dev`

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
    Use '/v1/prediction/list?key=loadforecast_power_w' instead.
    Load energy meter readings to be added to EOS measurement by:
    '/v1/measurement/value' or
    '/v1/measurement/series' or
    '/v1/measurement/dataframe' or
    '/v1/measurement/data'
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
    '/v1/prediction/list?key=loadforecast_power_w' instead.
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

```
Deprecated: Optimize.

Endpoint to handle optimization.

Note:
    Use automatic optimization instead.
```

**Parameters**:

- `start_hour` (query, optional): Defaults to current hour of the day.

- `ngen` (query, optional): Number of indivuals to generate for genetic algorithm.

**Request Body**:

- `application/json`: {
  "$ref": "#/components/schemas/GeneticOptimizationParameters"
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

## GET /v1/admin/cache

**Links**: [local](http://localhost:8503/docs#/default/fastapi_admin_cache_get_v1_admin_cache_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_admin_cache_get_v1_admin_cache_get)

Fastapi Admin Cache Get

```
Current cache management data.

Returns:
    data (dict): The management data.
```

**Responses**:

- **200**: Successful Response

---

## POST /v1/admin/cache/clear

**Links**: [local](http://localhost:8503/docs#/default/fastapi_admin_cache_clear_post_v1_admin_cache_clear_post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_admin_cache_clear_post_v1_admin_cache_clear_post)

Fastapi Admin Cache Clear Post

```
Clear the cache.

Deletes all cache files.

Returns:
    data (dict): The management data after cleanup.
```

**Responses**:

- **200**: Successful Response

---

## POST /v1/admin/cache/clear-expired

**Links**: [local](http://localhost:8503/docs#/default/fastapi_admin_cache_clear_expired_post_v1_admin_cache_clear-expired_post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_admin_cache_clear_expired_post_v1_admin_cache_clear-expired_post)

Fastapi Admin Cache Clear Expired Post

```
Clear the cache from expired data.

Deletes expired cache files.

Returns:
    data (dict): The management data after cleanup.
```

**Responses**:

- **200**: Successful Response

---

## POST /v1/admin/cache/load

**Links**: [local](http://localhost:8503/docs#/default/fastapi_admin_cache_load_post_v1_admin_cache_load_post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_admin_cache_load_post_v1_admin_cache_load_post)

Fastapi Admin Cache Load Post

```
Load cache management data.

Returns:
    data (dict): The management data that was loaded.
```

**Responses**:

- **200**: Successful Response

---

## POST /v1/admin/cache/save

**Links**: [local](http://localhost:8503/docs#/default/fastapi_admin_cache_save_post_v1_admin_cache_save_post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_admin_cache_save_post_v1_admin_cache_save_post)

Fastapi Admin Cache Save Post

```
Save the current cache management data.

Returns:
    data (dict): The management data that was saved.
```

**Responses**:

- **200**: Successful Response

---

## POST /v1/admin/server/restart

**Links**: [local](http://localhost:8503/docs#/default/fastapi_admin_server_restart_post_v1_admin_server_restart_post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_admin_server_restart_post_v1_admin_server_restart_post)

Fastapi Admin Server Restart Post

```
Restart the server.

Restart EOS properly by starting a new instance before exiting the old one.
```

**Responses**:

- **200**: Successful Response

---

## POST /v1/admin/server/shutdown

**Links**: [local](http://localhost:8503/docs#/default/fastapi_admin_server_shutdown_post_v1_admin_server_shutdown_post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_admin_server_shutdown_post_v1_admin_server_shutdown_post)

Fastapi Admin Server Shutdown Post

```
Shutdown the server.
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

## GET /v1/config/backup

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_backup_get_v1_config_backup_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_backup_get_v1_config_backup_get)

Fastapi Config Backup Get

```
Get the EOS configuration backup identifiers and backup metadata.

Returns:
    dict[str, dict[str, Any]]: Mapping of backup identifiers to metadata.
```

**Responses**:

- **200**: Successful Response

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

## POST /v1/config/reset

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_reset_post_v1_config_reset_post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_reset_post_v1_config_reset_post)

Fastapi Config Reset Post

```
Reset the configuration to the EOS configuration file.

Returns:
    configuration (ConfigEOS): The current configuration after update.
```

**Responses**:

- **200**: Successful Response

---

## PUT /v1/config/revert

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_revert_put_v1_config_revert_put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_revert_put_v1_config_revert_put)

Fastapi Config Revert Put

```
Revert the configuration to a EOS configuration backup.

Returns:
    configuration (ConfigEOS): The current configuration after revert.
```

**Parameters**:

- `backup_id` (query, required): EOS configuration backup ID.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## GET /v1/config/{path}

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_get_key_v1_config__path__get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_get_key_v1_config__path__get)

Fastapi Config Get Key

```
Get the value of a nested key or index in the config model.

Args:
    path (str): The nested path to the key (e.g., "general/latitude" or "optimize/nested_list/0").

Returns:
    value (Any): The value of the selected nested key.
```

**Parameters**:

- `path` (path, required): The nested path to the configuration key (e.g., general/latitude).

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## PUT /v1/config/{path}

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_put_key_v1_config__path__put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_put_key_v1_config__path__put)

Fastapi Config Put Key

```
Update a nested key or index in the config model.

Args:
    path (str): The nested path to the key (e.g., "general/latitude" or "optimize/nested_list/0").
    value (Any): The new value to assign to the key or index at path.

Returns:
    configuration (ConfigEOS): The current configuration after the update.
```

**Parameters**:

- `path` (path, required): The nested path to the configuration key (e.g., general/latitude).

**Request Body**:

- `application/json`: {
  "anyOf": [
    {},
    {
      "type": "null"
    }
  ],
  "description": "The value to assign to the specified configuration path (can be None).",
  "title": "Value"
}

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## GET /v1/energy-management/optimization/solution

**Links**: [local](http://localhost:8503/docs#/default/fastapi_energy_management_optimization_solution_get_v1_energy-management_optimization_solution_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_energy_management_optimization_solution_get_v1_energy-management_optimization_solution_get)

Fastapi Energy Management Optimization Solution Get

```
Get the latest solution of the optimization.
```

**Responses**:

- **200**: Successful Response

---

## GET /v1/energy-management/plan

**Links**: [local](http://localhost:8503/docs#/default/fastapi_energy_management_plan_get_v1_energy-management_plan_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_energy_management_plan_get_v1_energy-management_plan_get)

Fastapi Energy Management Plan Get

```
Get the latest energy management plan.
```

**Responses**:

- **200**: Successful Response

---

## GET /v1/health

**Links**: [local](http://localhost:8503/docs#/default/fastapi_health_get_v1_health_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_health_get_v1_health_get)

Fastapi Health Get

```
Health check endpoint to verify that the EOS server is alive.
```

**Responses**:

- **200**: Successful Response

---

## GET /v1/logging/log

**Links**: [local](http://localhost:8503/docs#/default/fastapi_logging_get_log_v1_logging_log_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_logging_get_log_v1_logging_log_get)

Fastapi Logging Get Log

```
Get structured log entries from the EOS log file.

Filters and returns log entries based on the specified query parameters. The log
file is expected to contain newline-delimited JSON entries.

Args:
    limit (int): Maximum number of entries to return.
    level (Optional[str]): Filter logs by severity level (e.g., DEBUG, INFO).
    contains (Optional[str]): Return only logs that include this string in the message.
    regex (Optional[str]): Return logs that match this regular expression in the message.
    from_time (Optional[str]): ISO 8601 timestamp to filter logs not older than this.
    to_time (Optional[str]): ISO 8601 timestamp to filter logs not newer than this.
    tail (bool): If True, fetch the most recent log entries (like `tail`).

Returns:
    JSONResponse: A JSON list of log entries.
```

**Parameters**:

- `limit` (query, optional): Maximum number of log entries to return.

- `level` (query, optional): Filter by log level (e.g., INFO, ERROR).

- `contains` (query, optional): Filter logs containing this substring.

- `regex` (query, optional): Filter logs by matching regex in message.

- `from_time` (query, optional): Start time (ISO format) for filtering logs.

- `to_time` (query, optional): End time (ISO format) for filtering logs.

- `tail` (query, optional): If True, returns the most recent lines (tail mode).

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

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

## GET /v1/measurement/series

**Links**: [local](http://localhost:8503/docs#/default/fastapi_measurement_series_get_v1_measurement_series_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_measurement_series_get_v1_measurement_series_get)

Fastapi Measurement Series Get

```
Get the measurements of given key as series.
```

**Parameters**:

- `key` (query, required): Measurement key.

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

- `key` (query, required): Measurement key.

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

- `key` (query, required): Measurement key.

- `value` (query, required): No description provided.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## GET /v1/prediction/dataframe

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_dataframe_get_v1_prediction_dataframe_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_dataframe_get_v1_prediction_dataframe_get)

Fastapi Prediction Dataframe Get

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

- `keys` (query, required): Prediction keys.

- `start_datetime` (query, optional): Starting datetime (inclusive).

- `end_datetime` (query, optional): Ending datetime (exclusive).

- `interval` (query, optional): Time duration for each interval. Defaults to 1 hour.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## PUT /v1/prediction/import/{provider_id}

**Links**: [local](http://localhost:8503/docs#/default/fastapi_prediction_import_provider_v1_prediction_import__provider_id__put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_prediction_import_provider_v1_prediction_import__provider_id__put)

Fastapi Prediction Import Provider

```
Import prediction for given provider ID.

Args:
    provider_id: ID of provider to update.
    data: Prediction data.
    force_enable: Update data even if provider is disabled.
        Defaults to False.
```

**Parameters**:

- `provider_id` (path, required): Provider ID.

- `force_enable` (query, optional): No description provided.

**Request Body**:

- `application/json`: {
  "anyOf": [
    {
      "$ref": "#/components/schemas/PydanticDateTimeDataFrame"
    },
    {
      "$ref": "#/components/schemas/PydanticDateTimeData"
    },
    {
      "type": "object",
      "additionalProperties": true
    },
    {
      "type": "null"
    }
  ],
  "title": "Data"
}

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

- `interval` (query, optional): Time duration for each interval. Defaults to 1 hour.

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

## GET /v1/resource/status

**Links**: [local](http://localhost:8503/docs#/default/fastapi_devices_status_get_v1_resource_status_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_devices_status_get_v1_resource_status_get)

Fastapi Devices Status Get

```
Get the latest status of a resource/ device.

Return:
    latest_status: The latest status of a resource/ device.
```

**Parameters**:

- `resource_id` (query, required): Resource ID.

- `actuator_id` (query, optional): Actuator ID.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## PUT /v1/resource/status

**Links**: [local](http://localhost:8503/docs#/default/fastapi_devices_status_put_v1_resource_status_put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_devices_status_put_v1_resource_status_put)

Fastapi Devices Status Put

```
Update the status of a resource/ device.

Return:
    latest_status: The latest status of a resource/ device.
```

**Parameters**:

- `resource_id` (query, required): Resource ID.

- `actuator_id` (query, optional): Actuator ID.

**Request Body**:

- `application/json`: {
  "anyOf": [
    {
      "$ref": "#/components/schemas/PowerMeasurement-Input"
    },
    {
      "$ref": "#/components/schemas/EnergyMeasurement-Input"
    },
    {
      "$ref": "#/components/schemas/PPBCPowerProfileStatus-Input"
    },
    {
      "$ref": "#/components/schemas/OMBCStatus"
    },
    {
      "$ref": "#/components/schemas/FRBCActuatorStatus"
    },
    {
      "$ref": "#/components/schemas/FRBCEnergyStatus-Input"
    },
    {
      "$ref": "#/components/schemas/FRBCStorageStatus"
    },
    {
      "$ref": "#/components/schemas/FRBCTimerStatus"
    },
    {
      "$ref": "#/components/schemas/DDBCActuatorStatus"
    }
  ],
  "description": "Resource Status.",
  "title": "Status"
}

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
