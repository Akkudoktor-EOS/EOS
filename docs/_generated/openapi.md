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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_optimize_optimize_post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_optimize_optimize_post)

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

**Links**: [local](http://localhost:8503/docs#/default/fastapi_pvforecast_pvforecast_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_pvforecast_pvforecast_get)

Fastapi Pvforecast

```
Deprecated: PV Forecast Prediction.

Endpoint to handle PV forecast prediction.

PVForecast starts at 00.00.00 today and is provided for 48 hours.
If no forecast values are available the missing ones at the start of the series are
filled with the first available forecast value.

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
Write the provided settings into the current settings.

The existing settings are completely overwritten. Note that for any setting
value that is None, the configuration will fall back to values from other sources such as
environment variables, the EOS configuration file, or default values.

Args:
    settings (SettingsEOS): The settings to write into the current settings.

Returns:
    configuration (ConfigEOS): The current configuration after the write.
```

**Parameters**:

- `server_eos_host` (query, optional): EOS server IP address.

- `server_eos_port` (query, optional): EOS server IP port number.

- `server_eos_verbose` (query, optional): Enable debug output

- `server_eos_startup_eosdash` (query, optional): EOS server to start EOSdash server.

- `server_eosdash_host` (query, optional): EOSdash server IP address.

- `server_eosdash_port` (query, optional): EOSdash server IP port number.

- `weatherimport_file_path` (query, optional): Path to the file to import weather data from.

- `weatherimport_json` (query, optional): JSON string, dictionary of weather forecast value lists.

- `weather_provider` (query, optional): Weather provider id of provider to be used.

- `pvforecastimport_file_path` (query, optional): Path to the file to import PV forecast data from.

- `pvforecastimport_json` (query, optional): JSON string, dictionary of PV forecast value lists.

- `pvforecast_provider` (query, optional): PVForecast provider id of provider to be used.

- `pvforecast0_surface_tilt` (query, optional): Tilt angle from horizontal plane. Ignored for two-axis tracking.

- `pvforecast0_surface_azimuth` (query, optional): Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).

- `pvforecast0_userhorizon` (query, optional): Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.

- `pvforecast0_peakpower` (query, optional): Nominal power of PV system in kW.

- `pvforecast0_pvtechchoice` (query, optional): PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'.

- `pvforecast0_mountingplace` (query, optional): Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.

- `pvforecast0_loss` (query, optional): Sum of PV system losses in percent

- `pvforecast0_trackingtype` (query, optional): Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.

- `pvforecast0_optimal_surface_tilt` (query, optional): Calculate the optimum tilt angle. Ignored for two-axis tracking.

- `pvforecast0_optimalangles` (query, optional): Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.

- `pvforecast0_albedo` (query, optional): Proportion of the light hitting the ground that it reflects back.

- `pvforecast0_module_model` (query, optional): Model of the PV modules of this plane.

- `pvforecast0_inverter_model` (query, optional): Model of the inverter of this plane.

- `pvforecast0_inverter_paco` (query, optional): AC power rating of the inverter. [W]

- `pvforecast0_modules_per_string` (query, optional): Number of the PV modules of the strings of this plane.

- `pvforecast0_strings_per_inverter` (query, optional): Number of the strings of the inverter of this plane.

- `pvforecast1_surface_tilt` (query, optional): Tilt angle from horizontal plane. Ignored for two-axis tracking.

- `pvforecast1_surface_azimuth` (query, optional): Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).

- `pvforecast1_userhorizon` (query, optional): Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.

- `pvforecast1_peakpower` (query, optional): Nominal power of PV system in kW.

- `pvforecast1_pvtechchoice` (query, optional): PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'.

- `pvforecast1_mountingplace` (query, optional): Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.

- `pvforecast1_loss` (query, optional): Sum of PV system losses in percent

- `pvforecast1_trackingtype` (query, optional): Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.

- `pvforecast1_optimal_surface_tilt` (query, optional): Calculate the optimum tilt angle. Ignored for two-axis tracking.

- `pvforecast1_optimalangles` (query, optional): Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.

- `pvforecast1_albedo` (query, optional): Proportion of the light hitting the ground that it reflects back.

- `pvforecast1_module_model` (query, optional): Model of the PV modules of this plane.

- `pvforecast1_inverter_model` (query, optional): Model of the inverter of this plane.

- `pvforecast1_inverter_paco` (query, optional): AC power rating of the inverter. [W]

- `pvforecast1_modules_per_string` (query, optional): Number of the PV modules of the strings of this plane.

- `pvforecast1_strings_per_inverter` (query, optional): Number of the strings of the inverter of this plane.

- `pvforecast2_surface_tilt` (query, optional): Tilt angle from horizontal plane. Ignored for two-axis tracking.

- `pvforecast2_surface_azimuth` (query, optional): Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).

- `pvforecast2_userhorizon` (query, optional): Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.

- `pvforecast2_peakpower` (query, optional): Nominal power of PV system in kW.

- `pvforecast2_pvtechchoice` (query, optional): PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'.

- `pvforecast2_mountingplace` (query, optional): Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.

- `pvforecast2_loss` (query, optional): Sum of PV system losses in percent

- `pvforecast2_trackingtype` (query, optional): Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.

- `pvforecast2_optimal_surface_tilt` (query, optional): Calculate the optimum tilt angle. Ignored for two-axis tracking.

- `pvforecast2_optimalangles` (query, optional): Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.

- `pvforecast2_albedo` (query, optional): Proportion of the light hitting the ground that it reflects back.

- `pvforecast2_module_model` (query, optional): Model of the PV modules of this plane.

- `pvforecast2_inverter_model` (query, optional): Model of the inverter of this plane.

- `pvforecast2_inverter_paco` (query, optional): AC power rating of the inverter. [W]

- `pvforecast2_modules_per_string` (query, optional): Number of the PV modules of the strings of this plane.

- `pvforecast2_strings_per_inverter` (query, optional): Number of the strings of the inverter of this plane.

- `pvforecast3_surface_tilt` (query, optional): Tilt angle from horizontal plane. Ignored for two-axis tracking.

- `pvforecast3_surface_azimuth` (query, optional): Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).

- `pvforecast3_userhorizon` (query, optional): Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.

- `pvforecast3_peakpower` (query, optional): Nominal power of PV system in kW.

- `pvforecast3_pvtechchoice` (query, optional): PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'.

- `pvforecast3_mountingplace` (query, optional): Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.

- `pvforecast3_loss` (query, optional): Sum of PV system losses in percent

- `pvforecast3_trackingtype` (query, optional): Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.

- `pvforecast3_optimal_surface_tilt` (query, optional): Calculate the optimum tilt angle. Ignored for two-axis tracking.

- `pvforecast3_optimalangles` (query, optional): Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.

- `pvforecast3_albedo` (query, optional): Proportion of the light hitting the ground that it reflects back.

- `pvforecast3_module_model` (query, optional): Model of the PV modules of this plane.

- `pvforecast3_inverter_model` (query, optional): Model of the inverter of this plane.

- `pvforecast3_inverter_paco` (query, optional): AC power rating of the inverter. [W]

- `pvforecast3_modules_per_string` (query, optional): Number of the PV modules of the strings of this plane.

- `pvforecast3_strings_per_inverter` (query, optional): Number of the strings of the inverter of this plane.

- `pvforecast4_surface_tilt` (query, optional): Tilt angle from horizontal plane. Ignored for two-axis tracking.

- `pvforecast4_surface_azimuth` (query, optional): Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).

- `pvforecast4_userhorizon` (query, optional): Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.

- `pvforecast4_peakpower` (query, optional): Nominal power of PV system in kW.

- `pvforecast4_pvtechchoice` (query, optional): PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'.

- `pvforecast4_mountingplace` (query, optional): Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.

- `pvforecast4_loss` (query, optional): Sum of PV system losses in percent

- `pvforecast4_trackingtype` (query, optional): Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.

- `pvforecast4_optimal_surface_tilt` (query, optional): Calculate the optimum tilt angle. Ignored for two-axis tracking.

- `pvforecast4_optimalangles` (query, optional): Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.

- `pvforecast4_albedo` (query, optional): Proportion of the light hitting the ground that it reflects back.

- `pvforecast4_module_model` (query, optional): Model of the PV modules of this plane.

- `pvforecast4_inverter_model` (query, optional): Model of the inverter of this plane.

- `pvforecast4_inverter_paco` (query, optional): AC power rating of the inverter. [W]

- `pvforecast4_modules_per_string` (query, optional): Number of the PV modules of the strings of this plane.

- `pvforecast4_strings_per_inverter` (query, optional): Number of the strings of the inverter of this plane.

- `pvforecast5_surface_tilt` (query, optional): Tilt angle from horizontal plane. Ignored for two-axis tracking.

- `pvforecast5_surface_azimuth` (query, optional): Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).

- `pvforecast5_userhorizon` (query, optional): Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.

- `pvforecast5_peakpower` (query, optional): Nominal power of PV system in kW.

- `pvforecast5_pvtechchoice` (query, optional): PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'.

- `pvforecast5_mountingplace` (query, optional): Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.

- `pvforecast5_loss` (query, optional): Sum of PV system losses in percent

- `pvforecast5_trackingtype` (query, optional): Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.

- `pvforecast5_optimal_surface_tilt` (query, optional): Calculate the optimum tilt angle. Ignored for two-axis tracking.

- `pvforecast5_optimalangles` (query, optional): Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.

- `pvforecast5_albedo` (query, optional): Proportion of the light hitting the ground that it reflects back.

- `pvforecast5_module_model` (query, optional): Model of the PV modules of this plane.

- `pvforecast5_inverter_model` (query, optional): Model of the inverter of this plane.

- `pvforecast5_inverter_paco` (query, optional): AC power rating of the inverter. [W]

- `pvforecast5_modules_per_string` (query, optional): Number of the PV modules of the strings of this plane.

- `pvforecast5_strings_per_inverter` (query, optional): Number of the strings of the inverter of this plane.

- `load_import_file_path` (query, optional): Path to the file to import load data from.

- `load_import_json` (query, optional): JSON string, dictionary of load forecast value lists.

- `loadakkudoktor_year_energy` (query, optional): Yearly energy consumption (kWh).

- `load_provider` (query, optional): Load provider id of provider to be used.

- `elecpriceimport_file_path` (query, optional): Path to the file to import elecprice data from.

- `elecpriceimport_json` (query, optional): JSON string, dictionary of electricity price forecast value lists.

- `elecprice_provider` (query, optional): Electricity price provider id of provider to be used.

- `elecprice_charges_kwh` (query, optional): Electricity price charges (€/kWh).

- `prediction_hours` (query, optional): Number of hours into the future for predictions

- `prediction_historic_hours` (query, optional): Number of hours into the past for historical predictions data

- `latitude` (query, optional): Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°). Maybe used with !secret.

- `longitude` (query, optional): Longitude in decimal degrees, within -180 to 180 (°). Maybe used with !secret.

- `optimization_hours` (query, optional): Number of hours into the future for optimizations.

- `optimization_penalty` (query, optional): Penalty factor used in optimization.

- `optimization_ev_available_charge_rates_percent` (query, optional): Charge rates available for the EV in percent of maximum charge.

- `measurement_load0_name` (query, optional): Name of the load0 source (e.g. 'Household', 'Heat Pump')

- `measurement_load1_name` (query, optional): Name of the load1 source (e.g. 'Household', 'Heat Pump')

- `measurement_load2_name` (query, optional): Name of the load2 source (e.g. 'Household', 'Heat Pump')

- `measurement_load3_name` (query, optional): Name of the load3 source (e.g. 'Household', 'Heat Pump')

- `measurement_load4_name` (query, optional): Name of the load4 source (e.g. 'Household', 'Heat Pump')

- `battery_provider` (query, optional): Id of Battery simulation provider.

- `battery_capacity` (query, optional): Battery capacity [Wh].

- `battery_initial_soc` (query, optional): Battery initial state of charge [%].

- `battery_soc_min` (query, optional): Battery minimum state of charge [%].

- `battery_soc_max` (query, optional): Battery maximum state of charge [%].

- `battery_charging_efficiency` (query, optional): Battery charging efficiency [%].

- `battery_discharging_efficiency` (query, optional): Battery discharging efficiency [%].

- `battery_max_charging_power` (query, optional): Battery maximum charge power [W].

- `bev_provider` (query, optional): Id of Battery Electric Vehicle simulation provider.

- `bev_capacity` (query, optional): Battery Electric Vehicle capacity [Wh].

- `bev_initial_soc` (query, optional): Battery Electric Vehicle initial state of charge [%].

- `bev_soc_max` (query, optional): Battery Electric Vehicle maximum state of charge [%].

- `bev_charging_efficiency` (query, optional): Battery Electric Vehicle charging efficiency [%].

- `bev_discharging_efficiency` (query, optional): Battery Electric Vehicle discharging efficiency [%].

- `bev_max_charging_power` (query, optional): Battery Electric Vehicle maximum charge power [W].

- `dishwasher_provider` (query, optional): Id of Dish Washer simulation provider.

- `dishwasher_consumption` (query, optional): Dish Washer energy consumption [Wh].

- `dishwasher_duration` (query, optional): Dish Washer usage duration [h].

- `inverter_provider` (query, optional): Id of PV Inverter simulation provider.

- `inverter_power_max` (query, optional): Inverter maximum power [W].

- `logging_level_default` (query, optional): EOS default logging level.

- `data_folder_path` (query, optional): Path to EOS data directory.

- `data_output_subpath` (query, optional): Sub-path for the EOS output data directory.

- `data_cache_subpath` (query, optional): Sub-path for the EOS cache data directory.

**Responses**:

- **200**: Successful Response

- **422**: Validation Error

---

## GET /v1/config/file

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_file_get_v1_config_file_get), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_file_get_v1_config_file_get)

Fastapi Config File Get

```
Get the settings as defined by the EOS configuration file.

Returns:
    settings (SettingsEOS): The settings defined by the EOS configuration file.
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

## POST /v1/config/update

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_update_post_v1_config_update_post), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_update_post_v1_config_update_post)

Fastapi Config Update Post

```
Update the configuration from the EOS configuration file.

Returns:
    configuration (ConfigEOS): The current configuration after update.
```

**Responses**:

- **200**: Successful Response

---

## PUT /v1/config/value

**Links**: [local](http://localhost:8503/docs#/default/fastapi_config_value_put_v1_config_value_put), [eos](https://petstore3.swagger.io/?url=https://raw.githubusercontent.com/Akkudoktor-EOS/EOS/refs/heads/main/openapi.json#/default/fastapi_config_value_put_v1_config_value_put)

Fastapi Config Value Put

```
Set the configuration option in the settings.

Args:
    key (str): configuration key
    value (Any): configuration value

Returns:
    configuration (ConfigEOS): The current configuration after the write.
```

**Parameters**:

- `key` (query, required): configuration key

- `value` (query, required): configuration value

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
