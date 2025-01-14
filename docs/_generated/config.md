# Configuration Table

## Settings for common configuration

General configuration to set directories of cache and output files.

:::{table} general
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| data_folder_path | `EOS_GENERAL__DATA_FOLDER_PATH` | `Optional[pathlib.Path]` | `rw` | `None` | Path to EOS data directory. |
| data_output_subpath | `EOS_GENERAL__DATA_OUTPUT_SUBPATH` | `Optional[pathlib.Path]` | `rw` | `output` | Sub-path for the EOS output data directory. |
| data_cache_subpath | `EOS_GENERAL__DATA_CACHE_SUBPATH` | `Optional[pathlib.Path]` | `rw` | `cache` | Sub-path for the EOS cache data directory. |
| data_output_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Compute data_output_path based on data_folder_path. |
| data_cache_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Compute data_cache_path based on data_folder_path. |
:::

### Example Input

```{eval-rst}
.. code-block:: json

   {
       "general": {
           "data_folder_path": null,
           "data_output_subpath": "output",
           "data_cache_subpath": "cache"
       }
   }
```

### Example Output

```{eval-rst}
.. code-block:: json

   {
       "general": {
           "data_folder_path": null,
           "data_output_subpath": "output",
           "data_cache_subpath": "cache",
           "data_output_path": null,
           "data_cache_path": null
       }
   }
```

## Logging Configuration

:::{table} logging
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| logging_level_default | `EOS_LOGGING__LOGGING_LEVEL_DEFAULT` | `Optional[str]` | `rw` | `None` | EOS default logging level. |
| logging_level_root | | `str` | `ro` | `N/A` | Root logger logging level. |
:::

### Example Input

```{eval-rst}
.. code-block:: json

   {
       "logging": {
           "logging_level_default": "INFO"
       }
   }
```

### Example Output

```{eval-rst}
.. code-block:: json

   {
       "logging": {
           "logging_level_default": "INFO",
           "logging_level_root": "INFO"
       }
   }
```

## Base configuration for devices simulation settings

:::{table} devices
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| batteries | `EOS_DEVICES__BATTERIES` | `Optional[list[akkudoktoreos.devices.battery.BaseBatteryParameters]]` | `rw` | `None` | List of battery/ev devices |
| inverters | `EOS_DEVICES__INVERTERS` | `Optional[list[akkudoktoreos.devices.inverter.InverterParameters]]` | `rw` | `None` | List of inverters |
| home_appliances | `EOS_DEVICES__HOME_APPLIANCES` | `Optional[list[akkudoktoreos.devices.generic.HomeApplianceParameters]]` | `rw` | `None` | List of home appliances |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "batteries": [
               {
                   "device_id": "battery1",
                   "hours": null,
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "max_charge_power_w": 5000,
                   "initial_soc_percentage": 0,
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100
               }
           ],
           "inverters": [],
           "home_appliances": []
       }
   }
```

### Home Appliance Device Simulation Configuration

:::{table} devices::home_appliances
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| device_id | `str` | `rw` | `PydanticUndefined` | ID of home appliance |
| hours | `Optional[int]` | `rw` | `None` | Number of prediction hours. Defaults to global config prediction hours. |
| consumption_wh | `int` | `rw` | `PydanticUndefined` | An integer representing the energy consumption of a household device in watt-hours. |
| duration_h | `int` | `rw` | `PydanticUndefined` | An integer representing the usage duration of a household device in hours. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "home_appliances": {
               "device_id": "dishwasher",
               "hours": null,
               "consumption_wh": 2000,
               "duration_h": 3
           }
       }
   }
```

### Inverter Device Simulation Configuration

:::{table} devices::inverters
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| device_id | `str` | `rw` | `PydanticUndefined` | ID of inverter |
| hours | `Optional[int]` | `rw` | `None` | Number of prediction hours. Defaults to global config prediction hours. |
| max_power_wh | `float` | `rw` | `PydanticUndefined` | - |
| battery | `Optional[str]` | `rw` | `None` | ID of battery |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "inverters": {
               "device_id": "inverter1",
               "hours": null,
               "max_power_wh": 10000.0,
               "battery": "battery1"
           }
       }
   }
```

### Battery Device Simulation Configuration

:::{table} devices::batteries
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| device_id | `str` | `rw` | `PydanticUndefined` | ID of battery |
| hours | `Optional[int]` | `rw` | `None` | Number of prediction hours. Defaults to global config prediction hours. |
| capacity_wh | `int` | `rw` | `PydanticUndefined` | An integer representing the capacity of the battery in watt-hours. |
| charging_efficiency | `float` | `rw` | `0.88` | A float representing the charging efficiency of the battery. |
| discharging_efficiency | `float` | `rw` | `0.88` | A float representing the discharge efficiency of the battery. |
| max_charge_power_w | `Optional[float]` | `rw` | `5000` | Maximum charging power in watts. |
| initial_soc_percentage | `int` | `rw` | `0` | An integer representing the state of charge of the battery at the **start** of the current hour (not the current state). |
| min_soc_percentage | `int` | `rw` | `0` | An integer representing the minimum state of charge (SOC) of the battery in percentage. |
| max_soc_percentage | `int` | `rw` | `100` | An integer representing the maximum state of charge (SOC) of the battery in percentage. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "batteries": {
               "device_id": "battery1",
               "hours": null,
               "capacity_wh": 8000,
               "charging_efficiency": 0.88,
               "discharging_efficiency": 0.88,
               "max_charge_power_w": 5000.0,
               "initial_soc_percentage": 42,
               "min_soc_percentage": 10,
               "max_soc_percentage": 100
           }
       }
   }
```

## Measurement Configuration

:::{table} measurement
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| measurement_load0_name | `EOS_MEASUREMENT__MEASUREMENT_LOAD0_NAME` | `Optional[str]` | `rw` | `None` | Name of the load0 source |
| measurement_load1_name | `EOS_MEASUREMENT__MEASUREMENT_LOAD1_NAME` | `Optional[str]` | `rw` | `None` | Name of the load1 source |
| measurement_load2_name | `EOS_MEASUREMENT__MEASUREMENT_LOAD2_NAME` | `Optional[str]` | `rw` | `None` | Name of the load2 source |
| measurement_load3_name | `EOS_MEASUREMENT__MEASUREMENT_LOAD3_NAME` | `Optional[str]` | `rw` | `None` | Name of the load3 source |
| measurement_load4_name | `EOS_MEASUREMENT__MEASUREMENT_LOAD4_NAME` | `Optional[str]` | `rw` | `None` | Name of the load4 source |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "measurement": {
           "measurement_load0_name": "Household",
           "measurement_load1_name": null,
           "measurement_load2_name": null,
           "measurement_load3_name": null,
           "measurement_load4_name": null
       }
   }
```

## General Optimization Configuration

Attributes:
    optimization_hours (int): Number of hours for optimizations.

:::{table} optimization
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| optimization_hours | `EOS_OPTIMIZATION__OPTIMIZATION_HOURS` | `Optional[int]` | `rw` | `48` | Number of hours into the future for optimizations. |
| optimization_penalty | `EOS_OPTIMIZATION__OPTIMIZATION_PENALTY` | `Optional[int]` | `rw` | `10` | Penalty factor used in optimization. |
| optimization_ev_available_charge_rates_percent | `EOS_OPTIMIZATION__OPTIMIZATION_EV_AVAILABLE_CHARGE_RATES_PERCENT` | `Optional[typing.List[float]]` | `rw` | `[0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]` | Charge rates available for the EV in percent of maximum charge. |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "optimization": {
           "optimization_hours": 48,
           "optimization_penalty": 10,
           "optimization_ev_available_charge_rates_percent": [
               0.0,
               0.375,
               0.5,
               0.625,
               0.75,
               0.875,
               1.0
           ]
       }
   }
```

## General Prediction Configuration

This class provides configuration for prediction settings, allowing users to specify
parameters such as the forecast duration (in hours) and location (latitude and longitude).
Validators ensure each parameter is within a specified range. A computed property, `timezone`,
determines the time zone based on latitude and longitude.

Attributes:
    prediction_hours (Optional[int]): Number of hours into the future for predictions.
        Must be non-negative.
    prediction_historic_hours (Optional[int]): Number of hours into the past for historical data.
        Must be non-negative.
    latitude (Optional[float]): Latitude in degrees, must be between -90 and 90.
    longitude (Optional[float]): Longitude in degrees, must be between -180 and 180.

Properties:
    timezone (Optional[str]): Computed time zone string based on the specified latitude
        and longitude.

Validators:
    validate_prediction_hours (int): Ensures `prediction_hours` is a non-negative integer.
    validate_prediction_historic_hours (int): Ensures `prediction_historic_hours` is a non-negative integer.
    validate_latitude (float): Ensures `latitude` is within the range -90 to 90.
    validate_longitude (float): Ensures `longitude` is within the range -180 to 180.

:::{table} prediction
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| prediction_hours | `EOS_PREDICTION__PREDICTION_HOURS` | `Optional[int]` | `rw` | `48` | Number of hours into the future for predictions |
| prediction_historic_hours | `EOS_PREDICTION__PREDICTION_HISTORIC_HOURS` | `Optional[int]` | `rw` | `48` | Number of hours into the past for historical predictions data |
| latitude | `EOS_PREDICTION__LATITUDE` | `Optional[float]` | `rw` | `52.52` | Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°) |
| longitude | `EOS_PREDICTION__LONGITUDE` | `Optional[float]` | `rw` | `13.405` | Longitude in decimal degrees, within -180 to 180 (°) |
| timezone | | `Optional[str]` | `ro` | `N/A` | Compute timezone based on latitude and longitude. |
:::

### Example Input

```{eval-rst}
.. code-block:: json

   {
       "prediction": {
           "prediction_hours": 48,
           "prediction_historic_hours": 48,
           "latitude": 52.52,
           "longitude": 13.405
       }
   }
```

### Example Output

```{eval-rst}
.. code-block:: json

   {
       "prediction": {
           "prediction_hours": 48,
           "prediction_historic_hours": 48,
           "latitude": 52.52,
           "longitude": 13.405,
           "timezone": "Europe/Berlin"
       }
   }
```

## Electricity Price Prediction Configuration

:::{table} elecprice
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| elecprice_provider | `EOS_ELECPRICE__ELECPRICE_PROVIDER` | `Optional[str]` | `rw` | `None` | Electricity price provider id of provider to be used. |
| elecprice_charges_kwh | `EOS_ELECPRICE__ELECPRICE_CHARGES_KWH` | `Optional[float]` | `rw` | `None` | Electricity price charges (€/kWh). |
| provider_settings | `EOS_ELECPRICE__PROVIDER_SETTINGS` | `Optional[akkudoktoreos.prediction.elecpriceimport.ElecPriceImportCommonSettings]` | `rw` | `None` | Provider settings |
| elecpriceimport_file_path | `EOS_ELECPRICE__PROVIDER_SETTINGS__ELECPRICEIMPORT_FILE_PATH` | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import elecprice data from. |
| elecpriceimport_json | `EOS_ELECPRICE__PROVIDER_SETTINGS__ELECPRICEIMPORT_JSON` | `Optional[str]` | `rw` | `None` | JSON string, dictionary of electricity price forecast value lists. |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "elecprice": {
           "elecprice_provider": "ElecPriceAkkudoktor",
           "elecprice_charges_kwh": 0.21,
           "provider_settings": null
       }
   }
```

### Common settings for elecprice data import from file or JSON String

:::{table} elecprice::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| elecpriceimport_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import elecprice data from. |
| elecpriceimport_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of electricity price forecast value lists. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "elecprice": {
           "provider_settings": {
               "elecpriceimport_file_path": null,
               "elecpriceimport_json": "{\"elecprice_marketprice_wh\": [0.0003384, 0.0003318, 0.0003284]}"
           }
       }
   }
```

## Load Prediction Configuration

:::{table} load
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| load_provider | `EOS_LOAD__LOAD_PROVIDER` | `Optional[str]` | `rw` | `None` | Load provider id of provider to be used. |
| provider_settings | `EOS_LOAD__PROVIDER_SETTINGS` | `Union[akkudoktoreos.prediction.loadakkudoktor.LoadAkkudoktorCommonSettings, akkudoktoreos.prediction.loadimport.LoadImportCommonSettings, NoneType]` | `rw` | `None` | Provider settings |
| loadakkudoktor_year_energy | `EOS_LOAD__PROVIDER_SETTINGS__LOADAKKUDOKTOR_YEAR_ENERGY` | `Optional[float]` | `rw` | `None` | Yearly energy consumption (kWh). |
| load_import_file_path | `EOS_LOAD__PROVIDER_SETTINGS__LOAD_IMPORT_FILE_PATH` | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import load data from. |
| load_import_json | `EOS_LOAD__PROVIDER_SETTINGS__LOAD_IMPORT_JSON` | `Optional[str]` | `rw` | `None` | JSON string, dictionary of load forecast value lists. |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "load": {
           "load_provider": "LoadAkkudoktor",
           "provider_settings": null
       }
   }
```

### Common settings for load data import from file or JSON string

:::{table} load::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| load_import_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import load data from. |
| load_import_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of load forecast value lists. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "load": {
           "provider_settings": {
               "load_import_file_path": null,
               "load_import_json": "{\"load0_mean\": [676.71, 876.19, 527.13]}"
           }
       }
   }
```

### Common settings for load data import from file

:::{table} load::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| loadakkudoktor_year_energy | `Optional[float]` | `rw` | `None` | Yearly energy consumption (kWh). |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "load": {
           "provider_settings": {
               "loadakkudoktor_year_energy": 40421.0
           }
       }
   }
```

## PV Forecast Configuration

:::{table} pvforecast
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| pvforecast_provider | `EOS_PVFORECAST__PVFORECAST_PROVIDER` | `Optional[str]` | `rw` | `None` | PVForecast provider id of provider to be used. |
| pvforecast0_surface_tilt | `EOS_PVFORECAST__PVFORECAST0_SURFACE_TILT` | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| pvforecast0_surface_azimuth | `EOS_PVFORECAST__PVFORECAST0_SURFACE_AZIMUTH` | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| pvforecast0_userhorizon | `EOS_PVFORECAST__PVFORECAST0_USERHORIZON` | `Optional[typing.List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| pvforecast0_peakpower | `EOS_PVFORECAST__PVFORECAST0_PEAKPOWER` | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| pvforecast0_pvtechchoice | `EOS_PVFORECAST__PVFORECAST0_PVTECHCHOICE` | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| pvforecast0_mountingplace | `EOS_PVFORECAST__PVFORECAST0_MOUNTINGPLACE` | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| pvforecast0_loss | `EOS_PVFORECAST__PVFORECAST0_LOSS` | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| pvforecast0_trackingtype | `EOS_PVFORECAST__PVFORECAST0_TRACKINGTYPE` | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| pvforecast0_optimal_surface_tilt | `EOS_PVFORECAST__PVFORECAST0_OPTIMAL_SURFACE_TILT` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| pvforecast0_optimalangles | `EOS_PVFORECAST__PVFORECAST0_OPTIMALANGLES` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| pvforecast0_albedo | `EOS_PVFORECAST__PVFORECAST0_ALBEDO` | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| pvforecast0_module_model | `EOS_PVFORECAST__PVFORECAST0_MODULE_MODEL` | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| pvforecast0_inverter_model | `EOS_PVFORECAST__PVFORECAST0_INVERTER_MODEL` | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| pvforecast0_inverter_paco | `EOS_PVFORECAST__PVFORECAST0_INVERTER_PACO` | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| pvforecast0_modules_per_string | `EOS_PVFORECAST__PVFORECAST0_MODULES_PER_STRING` | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| pvforecast0_strings_per_inverter | `EOS_PVFORECAST__PVFORECAST0_STRINGS_PER_INVERTER` | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| pvforecast1_surface_tilt | `EOS_PVFORECAST__PVFORECAST1_SURFACE_TILT` | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| pvforecast1_surface_azimuth | `EOS_PVFORECAST__PVFORECAST1_SURFACE_AZIMUTH` | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| pvforecast1_userhorizon | `EOS_PVFORECAST__PVFORECAST1_USERHORIZON` | `Optional[typing.List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| pvforecast1_peakpower | `EOS_PVFORECAST__PVFORECAST1_PEAKPOWER` | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| pvforecast1_pvtechchoice | `EOS_PVFORECAST__PVFORECAST1_PVTECHCHOICE` | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| pvforecast1_mountingplace | `EOS_PVFORECAST__PVFORECAST1_MOUNTINGPLACE` | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| pvforecast1_loss | `EOS_PVFORECAST__PVFORECAST1_LOSS` | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| pvforecast1_trackingtype | `EOS_PVFORECAST__PVFORECAST1_TRACKINGTYPE` | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| pvforecast1_optimal_surface_tilt | `EOS_PVFORECAST__PVFORECAST1_OPTIMAL_SURFACE_TILT` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| pvforecast1_optimalangles | `EOS_PVFORECAST__PVFORECAST1_OPTIMALANGLES` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| pvforecast1_albedo | `EOS_PVFORECAST__PVFORECAST1_ALBEDO` | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| pvforecast1_module_model | `EOS_PVFORECAST__PVFORECAST1_MODULE_MODEL` | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| pvforecast1_inverter_model | `EOS_PVFORECAST__PVFORECAST1_INVERTER_MODEL` | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| pvforecast1_inverter_paco | `EOS_PVFORECAST__PVFORECAST1_INVERTER_PACO` | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| pvforecast1_modules_per_string | `EOS_PVFORECAST__PVFORECAST1_MODULES_PER_STRING` | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| pvforecast1_strings_per_inverter | `EOS_PVFORECAST__PVFORECAST1_STRINGS_PER_INVERTER` | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| pvforecast2_surface_tilt | `EOS_PVFORECAST__PVFORECAST2_SURFACE_TILT` | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| pvforecast2_surface_azimuth | `EOS_PVFORECAST__PVFORECAST2_SURFACE_AZIMUTH` | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| pvforecast2_userhorizon | `EOS_PVFORECAST__PVFORECAST2_USERHORIZON` | `Optional[typing.List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| pvforecast2_peakpower | `EOS_PVFORECAST__PVFORECAST2_PEAKPOWER` | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| pvforecast2_pvtechchoice | `EOS_PVFORECAST__PVFORECAST2_PVTECHCHOICE` | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| pvforecast2_mountingplace | `EOS_PVFORECAST__PVFORECAST2_MOUNTINGPLACE` | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| pvforecast2_loss | `EOS_PVFORECAST__PVFORECAST2_LOSS` | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| pvforecast2_trackingtype | `EOS_PVFORECAST__PVFORECAST2_TRACKINGTYPE` | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| pvforecast2_optimal_surface_tilt | `EOS_PVFORECAST__PVFORECAST2_OPTIMAL_SURFACE_TILT` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| pvforecast2_optimalangles | `EOS_PVFORECAST__PVFORECAST2_OPTIMALANGLES` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| pvforecast2_albedo | `EOS_PVFORECAST__PVFORECAST2_ALBEDO` | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| pvforecast2_module_model | `EOS_PVFORECAST__PVFORECAST2_MODULE_MODEL` | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| pvforecast2_inverter_model | `EOS_PVFORECAST__PVFORECAST2_INVERTER_MODEL` | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| pvforecast2_inverter_paco | `EOS_PVFORECAST__PVFORECAST2_INVERTER_PACO` | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| pvforecast2_modules_per_string | `EOS_PVFORECAST__PVFORECAST2_MODULES_PER_STRING` | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| pvforecast2_strings_per_inverter | `EOS_PVFORECAST__PVFORECAST2_STRINGS_PER_INVERTER` | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| pvforecast3_surface_tilt | `EOS_PVFORECAST__PVFORECAST3_SURFACE_TILT` | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| pvforecast3_surface_azimuth | `EOS_PVFORECAST__PVFORECAST3_SURFACE_AZIMUTH` | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| pvforecast3_userhorizon | `EOS_PVFORECAST__PVFORECAST3_USERHORIZON` | `Optional[typing.List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| pvforecast3_peakpower | `EOS_PVFORECAST__PVFORECAST3_PEAKPOWER` | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| pvforecast3_pvtechchoice | `EOS_PVFORECAST__PVFORECAST3_PVTECHCHOICE` | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| pvforecast3_mountingplace | `EOS_PVFORECAST__PVFORECAST3_MOUNTINGPLACE` | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| pvforecast3_loss | `EOS_PVFORECAST__PVFORECAST3_LOSS` | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| pvforecast3_trackingtype | `EOS_PVFORECAST__PVFORECAST3_TRACKINGTYPE` | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| pvforecast3_optimal_surface_tilt | `EOS_PVFORECAST__PVFORECAST3_OPTIMAL_SURFACE_TILT` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| pvforecast3_optimalangles | `EOS_PVFORECAST__PVFORECAST3_OPTIMALANGLES` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| pvforecast3_albedo | `EOS_PVFORECAST__PVFORECAST3_ALBEDO` | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| pvforecast3_module_model | `EOS_PVFORECAST__PVFORECAST3_MODULE_MODEL` | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| pvforecast3_inverter_model | `EOS_PVFORECAST__PVFORECAST3_INVERTER_MODEL` | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| pvforecast3_inverter_paco | `EOS_PVFORECAST__PVFORECAST3_INVERTER_PACO` | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| pvforecast3_modules_per_string | `EOS_PVFORECAST__PVFORECAST3_MODULES_PER_STRING` | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| pvforecast3_strings_per_inverter | `EOS_PVFORECAST__PVFORECAST3_STRINGS_PER_INVERTER` | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| pvforecast4_surface_tilt | `EOS_PVFORECAST__PVFORECAST4_SURFACE_TILT` | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| pvforecast4_surface_azimuth | `EOS_PVFORECAST__PVFORECAST4_SURFACE_AZIMUTH` | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| pvforecast4_userhorizon | `EOS_PVFORECAST__PVFORECAST4_USERHORIZON` | `Optional[typing.List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| pvforecast4_peakpower | `EOS_PVFORECAST__PVFORECAST4_PEAKPOWER` | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| pvforecast4_pvtechchoice | `EOS_PVFORECAST__PVFORECAST4_PVTECHCHOICE` | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| pvforecast4_mountingplace | `EOS_PVFORECAST__PVFORECAST4_MOUNTINGPLACE` | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| pvforecast4_loss | `EOS_PVFORECAST__PVFORECAST4_LOSS` | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| pvforecast4_trackingtype | `EOS_PVFORECAST__PVFORECAST4_TRACKINGTYPE` | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| pvforecast4_optimal_surface_tilt | `EOS_PVFORECAST__PVFORECAST4_OPTIMAL_SURFACE_TILT` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| pvforecast4_optimalangles | `EOS_PVFORECAST__PVFORECAST4_OPTIMALANGLES` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| pvforecast4_albedo | `EOS_PVFORECAST__PVFORECAST4_ALBEDO` | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| pvforecast4_module_model | `EOS_PVFORECAST__PVFORECAST4_MODULE_MODEL` | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| pvforecast4_inverter_model | `EOS_PVFORECAST__PVFORECAST4_INVERTER_MODEL` | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| pvforecast4_inverter_paco | `EOS_PVFORECAST__PVFORECAST4_INVERTER_PACO` | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| pvforecast4_modules_per_string | `EOS_PVFORECAST__PVFORECAST4_MODULES_PER_STRING` | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| pvforecast4_strings_per_inverter | `EOS_PVFORECAST__PVFORECAST4_STRINGS_PER_INVERTER` | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| pvforecast5_surface_tilt | `EOS_PVFORECAST__PVFORECAST5_SURFACE_TILT` | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| pvforecast5_surface_azimuth | `EOS_PVFORECAST__PVFORECAST5_SURFACE_AZIMUTH` | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| pvforecast5_userhorizon | `EOS_PVFORECAST__PVFORECAST5_USERHORIZON` | `Optional[typing.List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| pvforecast5_peakpower | `EOS_PVFORECAST__PVFORECAST5_PEAKPOWER` | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| pvforecast5_pvtechchoice | `EOS_PVFORECAST__PVFORECAST5_PVTECHCHOICE` | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| pvforecast5_mountingplace | `EOS_PVFORECAST__PVFORECAST5_MOUNTINGPLACE` | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| pvforecast5_loss | `EOS_PVFORECAST__PVFORECAST5_LOSS` | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| pvforecast5_trackingtype | `EOS_PVFORECAST__PVFORECAST5_TRACKINGTYPE` | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| pvforecast5_optimal_surface_tilt | `EOS_PVFORECAST__PVFORECAST5_OPTIMAL_SURFACE_TILT` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| pvforecast5_optimalangles | `EOS_PVFORECAST__PVFORECAST5_OPTIMALANGLES` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| pvforecast5_albedo | `EOS_PVFORECAST__PVFORECAST5_ALBEDO` | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| pvforecast5_module_model | `EOS_PVFORECAST__PVFORECAST5_MODULE_MODEL` | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| pvforecast5_inverter_model | `EOS_PVFORECAST__PVFORECAST5_INVERTER_MODEL` | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| pvforecast5_inverter_paco | `EOS_PVFORECAST__PVFORECAST5_INVERTER_PACO` | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| pvforecast5_modules_per_string | `EOS_PVFORECAST__PVFORECAST5_MODULES_PER_STRING` | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| pvforecast5_strings_per_inverter | `EOS_PVFORECAST__PVFORECAST5_STRINGS_PER_INVERTER` | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| provider_settings | `EOS_PVFORECAST__PROVIDER_SETTINGS` | `Optional[akkudoktoreos.prediction.pvforecastimport.PVForecastImportCommonSettings]` | `rw` | `None` | Provider settings |
| pvforecastimport_file_path | `EOS_PVFORECAST__PROVIDER_SETTINGS__PVFORECASTIMPORT_FILE_PATH` | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import PV forecast data from. |
| pvforecastimport_json | `EOS_PVFORECAST__PROVIDER_SETTINGS__PVFORECASTIMPORT_JSON` | `Optional[str]` | `rw` | `None` | JSON string, dictionary of PV forecast value lists. |
| pvforecast_planes | | `List[str]` | `ro` | `N/A` | Compute a list of active planes. |
| pvforecast_planes_peakpower | | `List[float]` | `ro` | `N/A` | Compute a list of the peak power per active planes. |
| pvforecast_planes_azimuth | | `List[float]` | `ro` | `N/A` | Compute a list of the azimuths per active planes. |
| pvforecast_planes_tilt | | `List[float]` | `ro` | `N/A` | Compute a list of the tilts per active planes. |
| pvforecast_planes_userhorizon | | `Any` | `ro` | `N/A` | Compute a list of the user horizon per active planes. |
| pvforecast_planes_inverter_paco | | `Any` | `ro` | `N/A` | Compute a list of the maximum power rating of the inverter per active planes. |
:::

### Example Input

```{eval-rst}
.. code-block:: json

   {
       "pvforecast": {
           "pvforecast_provider": "PVForecastAkkudoktor",
           "pvforecast0_surface_tilt": 10.0,
           "pvforecast0_surface_azimuth": 10.0,
           "pvforecast0_userhorizon": [
               10.0,
               20.0,
               30.0
           ],
           "pvforecast0_peakpower": 5.0,
           "pvforecast0_pvtechchoice": "crystSi",
           "pvforecast0_mountingplace": "free",
           "pvforecast0_loss": 14.0,
           "pvforecast0_trackingtype": 0,
           "pvforecast0_optimal_surface_tilt": false,
           "pvforecast0_optimalangles": false,
           "pvforecast0_albedo": null,
           "pvforecast0_module_model": null,
           "pvforecast0_inverter_model": null,
           "pvforecast0_inverter_paco": 6000,
           "pvforecast0_modules_per_string": 20,
           "pvforecast0_strings_per_inverter": 2,
           "pvforecast1_surface_tilt": 20.0,
           "pvforecast1_surface_azimuth": 20.0,
           "pvforecast1_userhorizon": [
               5.0,
               15.0,
               25.0
           ],
           "pvforecast1_peakpower": 3.5,
           "pvforecast1_pvtechchoice": "crystSi",
           "pvforecast1_mountingplace": "free",
           "pvforecast1_loss": 14.0,
           "pvforecast1_trackingtype": null,
           "pvforecast1_optimal_surface_tilt": false,
           "pvforecast1_optimalangles": false,
           "pvforecast1_albedo": null,
           "pvforecast1_module_model": null,
           "pvforecast1_inverter_model": null,
           "pvforecast1_inverter_paco": 4000,
           "pvforecast1_modules_per_string": 20,
           "pvforecast1_strings_per_inverter": 2,
           "pvforecast2_surface_tilt": null,
           "pvforecast2_surface_azimuth": null,
           "pvforecast2_userhorizon": null,
           "pvforecast2_peakpower": null,
           "pvforecast2_pvtechchoice": null,
           "pvforecast2_mountingplace": null,
           "pvforecast2_loss": null,
           "pvforecast2_trackingtype": null,
           "pvforecast2_optimal_surface_tilt": null,
           "pvforecast2_optimalangles": null,
           "pvforecast2_albedo": null,
           "pvforecast2_module_model": null,
           "pvforecast2_inverter_model": null,
           "pvforecast2_inverter_paco": null,
           "pvforecast2_modules_per_string": null,
           "pvforecast2_strings_per_inverter": null,
           "pvforecast3_surface_tilt": null,
           "pvforecast3_surface_azimuth": null,
           "pvforecast3_userhorizon": null,
           "pvforecast3_peakpower": null,
           "pvforecast3_pvtechchoice": null,
           "pvforecast3_mountingplace": null,
           "pvforecast3_loss": null,
           "pvforecast3_trackingtype": null,
           "pvforecast3_optimal_surface_tilt": null,
           "pvforecast3_optimalangles": null,
           "pvforecast3_albedo": null,
           "pvforecast3_module_model": null,
           "pvforecast3_inverter_model": null,
           "pvforecast3_inverter_paco": null,
           "pvforecast3_modules_per_string": null,
           "pvforecast3_strings_per_inverter": null,
           "pvforecast4_surface_tilt": null,
           "pvforecast4_surface_azimuth": null,
           "pvforecast4_userhorizon": null,
           "pvforecast4_peakpower": null,
           "pvforecast4_pvtechchoice": null,
           "pvforecast4_mountingplace": null,
           "pvforecast4_loss": null,
           "pvforecast4_trackingtype": null,
           "pvforecast4_optimal_surface_tilt": null,
           "pvforecast4_optimalangles": null,
           "pvforecast4_albedo": null,
           "pvforecast4_module_model": null,
           "pvforecast4_inverter_model": null,
           "pvforecast4_inverter_paco": null,
           "pvforecast4_modules_per_string": null,
           "pvforecast4_strings_per_inverter": null,
           "pvforecast5_surface_tilt": null,
           "pvforecast5_surface_azimuth": null,
           "pvforecast5_userhorizon": null,
           "pvforecast5_peakpower": null,
           "pvforecast5_pvtechchoice": null,
           "pvforecast5_mountingplace": null,
           "pvforecast5_loss": null,
           "pvforecast5_trackingtype": null,
           "pvforecast5_optimal_surface_tilt": null,
           "pvforecast5_optimalangles": null,
           "pvforecast5_albedo": null,
           "pvforecast5_module_model": null,
           "pvforecast5_inverter_model": null,
           "pvforecast5_inverter_paco": null,
           "pvforecast5_modules_per_string": null,
           "pvforecast5_strings_per_inverter": null,
           "provider_settings": null
       }
   }
```

### Example Output

```{eval-rst}
.. code-block:: json

   {
       "pvforecast": {
           "pvforecast_provider": "PVForecastAkkudoktor",
           "pvforecast0_surface_tilt": 10.0,
           "pvforecast0_surface_azimuth": 10.0,
           "pvforecast0_userhorizon": [
               10.0,
               20.0,
               30.0
           ],
           "pvforecast0_peakpower": 5.0,
           "pvforecast0_pvtechchoice": "crystSi",
           "pvforecast0_mountingplace": "free",
           "pvforecast0_loss": 14.0,
           "pvforecast0_trackingtype": 0,
           "pvforecast0_optimal_surface_tilt": false,
           "pvforecast0_optimalangles": false,
           "pvforecast0_albedo": null,
           "pvforecast0_module_model": null,
           "pvforecast0_inverter_model": null,
           "pvforecast0_inverter_paco": 6000,
           "pvforecast0_modules_per_string": 20,
           "pvforecast0_strings_per_inverter": 2,
           "pvforecast1_surface_tilt": 20.0,
           "pvforecast1_surface_azimuth": 20.0,
           "pvforecast1_userhorizon": [
               5.0,
               15.0,
               25.0
           ],
           "pvforecast1_peakpower": 3.5,
           "pvforecast1_pvtechchoice": "crystSi",
           "pvforecast1_mountingplace": "free",
           "pvforecast1_loss": 14.0,
           "pvforecast1_trackingtype": null,
           "pvforecast1_optimal_surface_tilt": false,
           "pvforecast1_optimalangles": false,
           "pvforecast1_albedo": null,
           "pvforecast1_module_model": null,
           "pvforecast1_inverter_model": null,
           "pvforecast1_inverter_paco": 4000,
           "pvforecast1_modules_per_string": 20,
           "pvforecast1_strings_per_inverter": 2,
           "pvforecast2_surface_tilt": null,
           "pvforecast2_surface_azimuth": null,
           "pvforecast2_userhorizon": null,
           "pvforecast2_peakpower": null,
           "pvforecast2_pvtechchoice": null,
           "pvforecast2_mountingplace": null,
           "pvforecast2_loss": null,
           "pvforecast2_trackingtype": null,
           "pvforecast2_optimal_surface_tilt": null,
           "pvforecast2_optimalangles": null,
           "pvforecast2_albedo": null,
           "pvforecast2_module_model": null,
           "pvforecast2_inverter_model": null,
           "pvforecast2_inverter_paco": null,
           "pvforecast2_modules_per_string": null,
           "pvforecast2_strings_per_inverter": null,
           "pvforecast3_surface_tilt": null,
           "pvforecast3_surface_azimuth": null,
           "pvforecast3_userhorizon": null,
           "pvforecast3_peakpower": null,
           "pvforecast3_pvtechchoice": null,
           "pvforecast3_mountingplace": null,
           "pvforecast3_loss": null,
           "pvforecast3_trackingtype": null,
           "pvforecast3_optimal_surface_tilt": null,
           "pvforecast3_optimalangles": null,
           "pvforecast3_albedo": null,
           "pvforecast3_module_model": null,
           "pvforecast3_inverter_model": null,
           "pvforecast3_inverter_paco": null,
           "pvforecast3_modules_per_string": null,
           "pvforecast3_strings_per_inverter": null,
           "pvforecast4_surface_tilt": null,
           "pvforecast4_surface_azimuth": null,
           "pvforecast4_userhorizon": null,
           "pvforecast4_peakpower": null,
           "pvforecast4_pvtechchoice": null,
           "pvforecast4_mountingplace": null,
           "pvforecast4_loss": null,
           "pvforecast4_trackingtype": null,
           "pvforecast4_optimal_surface_tilt": null,
           "pvforecast4_optimalangles": null,
           "pvforecast4_albedo": null,
           "pvforecast4_module_model": null,
           "pvforecast4_inverter_model": null,
           "pvforecast4_inverter_paco": null,
           "pvforecast4_modules_per_string": null,
           "pvforecast4_strings_per_inverter": null,
           "pvforecast5_surface_tilt": null,
           "pvforecast5_surface_azimuth": null,
           "pvforecast5_userhorizon": null,
           "pvforecast5_peakpower": null,
           "pvforecast5_pvtechchoice": null,
           "pvforecast5_mountingplace": null,
           "pvforecast5_loss": null,
           "pvforecast5_trackingtype": null,
           "pvforecast5_optimal_surface_tilt": null,
           "pvforecast5_optimalangles": null,
           "pvforecast5_albedo": null,
           "pvforecast5_module_model": null,
           "pvforecast5_inverter_model": null,
           "pvforecast5_inverter_paco": null,
           "pvforecast5_modules_per_string": null,
           "pvforecast5_strings_per_inverter": null,
           "provider_settings": null,
           "pvforecast_planes": [
               "pvforecast0",
               "pvforecast1"
           ],
           "pvforecast_planes_peakpower": [
               5.0,
               3.5
           ],
           "pvforecast_planes_azimuth": [
               10.0,
               20.0
           ],
           "pvforecast_planes_tilt": [
               10.0,
               20.0
           ],
           "pvforecast_planes_userhorizon": [
               [
                   10.0,
                   20.0,
                   30.0
               ],
               [
                   5.0,
                   15.0,
                   25.0
               ]
           ],
           "pvforecast_planes_inverter_paco": [
               6000.0,
               4000.0
           ]
       }
   }
```

### Common settings for pvforecast data import from file or JSON string

:::{table} pvforecast::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| pvforecastimport_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import PV forecast data from. |
| pvforecastimport_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of PV forecast value lists. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "pvforecast": {
           "provider_settings": {
               "pvforecastimport_file_path": null,
               "pvforecastimport_json": "{\"pvforecast_ac_power\": [0, 8.05, 352.91]}"
           }
       }
   }
```

## Weather Forecast Configuration

:::{table} weather
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| weather_provider | `EOS_WEATHER__WEATHER_PROVIDER` | `Optional[str]` | `rw` | `None` | Weather provider id of provider to be used. |
| provider_settings | `EOS_WEATHER__PROVIDER_SETTINGS` | `Optional[akkudoktoreos.prediction.weatherimport.WeatherImportCommonSettings]` | `rw` | `None` | Provider settings |
| weatherimport_file_path | `EOS_WEATHER__PROVIDER_SETTINGS__WEATHERIMPORT_FILE_PATH` | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import weather data from. |
| weatherimport_json | `EOS_WEATHER__PROVIDER_SETTINGS__WEATHERIMPORT_JSON` | `Optional[str]` | `rw` | `None` | JSON string, dictionary of weather forecast value lists. |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "weather": {
           "weather_provider": "WeatherImport",
           "provider_settings": null
       }
   }
```

### Common settings for weather data import from file or JSON string

:::{table} weather::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| weatherimport_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import weather data from. |
| weatherimport_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of weather forecast value lists. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "weather": {
           "provider_settings": {
               "weatherimport_file_path": null,
               "weatherimport_json": "{\"weather_temp_air\": [18.3, 17.8, 16.9]}"
           }
       }
   }
```

## Server Configuration

Attributes:
    To be added

:::{table} server
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| server_eos_host | `EOS_SERVER__SERVER_EOS_HOST` | `Optional[pydantic.networks.IPvAnyAddress]` | `rw` | `0.0.0.0` | EOS server IP address. |
| server_eos_port | `EOS_SERVER__SERVER_EOS_PORT` | `Optional[int]` | `rw` | `8503` | EOS server IP port number. |
| server_eos_verbose | `EOS_SERVER__SERVER_EOS_VERBOSE` | `Optional[bool]` | `rw` | `False` | Enable debug output |
| server_eos_startup_eosdash | `EOS_SERVER__SERVER_EOS_STARTUP_EOSDASH` | `Optional[bool]` | `rw` | `True` | EOS server to start EOSdash server. |
| server_eosdash_host | `EOS_SERVER__SERVER_EOSDASH_HOST` | `Optional[pydantic.networks.IPvAnyAddress]` | `rw` | `0.0.0.0` | EOSdash server IP address. |
| server_eosdash_port | `EOS_SERVER__SERVER_EOSDASH_PORT` | `Optional[int]` | `rw` | `8504` | EOSdash server IP port number. |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "server": {
           "server_eos_host": "0.0.0.0",
           "server_eos_port": 8503,
           "server_eos_verbose": false,
           "server_eos_startup_eosdash": true,
           "server_eosdash_host": "0.0.0.0",
           "server_eosdash_port": 8504
       }
   }
```

## Utils Configuration

:::{table} utils
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "utils": {}
   }
```
