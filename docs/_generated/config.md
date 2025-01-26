# Configuration Table

## Settings for common configuration

General configuration to set directories of cache and output files and system location (latitude
and longitude).
Validators ensure each parameter is within a specified range. A computed property, `timezone`,
determines the time zone based on latitude and longitude.

Attributes:
    latitude (Optional[float]): Latitude in degrees, must be between -90 and 90.
    longitude (Optional[float]): Longitude in degrees, must be between -180 and 180.

Properties:
    timezone (Optional[str]): Computed time zone string based on the specified latitude
        and longitude.

Validators:
    validate_latitude (float): Ensures `latitude` is within the range -90 to 90.
    validate_longitude (float): Ensures `longitude` is within the range -180 to 180.

:::{table} general
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| data_folder_path | `EOS_GENERAL__DATA_FOLDER_PATH` | `Optional[pathlib.Path]` | `rw` | `None` | Path to EOS data directory. |
| data_output_subpath | `EOS_GENERAL__DATA_OUTPUT_SUBPATH` | `Optional[pathlib.Path]` | `rw` | `output` | Sub-path for the EOS output data directory. |
| data_cache_subpath | `EOS_GENERAL__DATA_CACHE_SUBPATH` | `Optional[pathlib.Path]` | `rw` | `cache` | Sub-path for the EOS cache data directory. |
| latitude | `EOS_GENERAL__LATITUDE` | `Optional[float]` | `rw` | `52.52` | Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°) |
| longitude | `EOS_GENERAL__LONGITUDE` | `Optional[float]` | `rw` | `13.405` | Longitude in decimal degrees, within -180 to 180 (°) |
| timezone | | `Optional[str]` | `ro` | `N/A` | Compute timezone based on latitude and longitude. |
| data_output_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Compute data_output_path based on data_folder_path. |
| data_cache_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Compute data_cache_path based on data_folder_path. |
| config_folder_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Path to EOS configuration directory. |
| config_file_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Path to EOS configuration file. |
:::

### Example Input

```{eval-rst}
.. code-block:: json

   {
       "general": {
           "data_folder_path": null,
           "data_output_subpath": "output",
           "data_cache_subpath": "cache",
           "latitude": 52.52,
           "longitude": 13.405
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
           "latitude": 52.52,
           "longitude": 13.405,
           "timezone": "Europe/Berlin",
           "data_output_path": null,
           "data_cache_path": null,
           "config_folder_path": "/home/user/.config/net.akkudoktoreos.net",
           "config_file_path": "/home/user/.config/net.akkudoktoreos.net/EOS.config.json"
       }
   }
```

## Logging Configuration

:::{table} logging
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| level | `EOS_LOGGING__LEVEL` | `Optional[str]` | `rw` | `None` | EOS default logging level. |
| root_level | | `str` | `ro` | `N/A` | Root logger logging level. |
:::

### Example Input

```{eval-rst}
.. code-block:: json

   {
       "logging": {
           "level": "INFO"
       }
   }
```

### Example Output

```{eval-rst}
.. code-block:: json

   {
       "logging": {
           "level": "INFO",
           "root_level": "INFO"
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

:::{table} devices::home_appliances::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| device_id | `str` | `rw` | `required` | ID of home appliance |
| hours | `Optional[int]` | `rw` | `None` | Number of prediction hours. Defaults to global config prediction hours. |
| consumption_wh | `int` | `rw` | `required` | An integer representing the energy consumption of a household device in watt-hours. |
| duration_h | `int` | `rw` | `required` | An integer representing the usage duration of a household device in hours. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "home_appliances": [
               {
                   "device_id": "dishwasher",
                   "hours": null,
                   "consumption_wh": 2000,
                   "duration_h": 3
               }
           ]
       }
   }
```

### Inverter Device Simulation Configuration

:::{table} devices::inverters::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| device_id | `str` | `rw` | `required` | ID of inverter |
| hours | `Optional[int]` | `rw` | `None` | Number of prediction hours. Defaults to global config prediction hours. |
| max_power_wh | `float` | `rw` | `required` | - |
| battery_id | `Optional[str]` | `rw` | `None` | ID of battery |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "inverters": [
               {
                   "device_id": "inverter1",
                   "hours": null,
                   "max_power_wh": 10000.0,
                   "battery_id": null
               }
           ]
       }
   }
```

### Battery Device Simulation Configuration

:::{table} devices::batteries::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| device_id | `str` | `rw` | `required` | ID of battery |
| hours | `Optional[int]` | `rw` | `None` | Number of prediction hours. Defaults to global config prediction hours. |
| capacity_wh | `int` | `rw` | `required` | An integer representing the capacity of the battery in watt-hours. |
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
           "batteries": [
               {
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
           ]
       }
   }
```

## Measurement Configuration

:::{table} measurement
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| load0_name | `EOS_MEASUREMENT__LOAD0_NAME` | `Optional[str]` | `rw` | `None` | Name of the load0 source |
| load1_name | `EOS_MEASUREMENT__LOAD1_NAME` | `Optional[str]` | `rw` | `None` | Name of the load1 source |
| load2_name | `EOS_MEASUREMENT__LOAD2_NAME` | `Optional[str]` | `rw` | `None` | Name of the load2 source |
| load3_name | `EOS_MEASUREMENT__LOAD3_NAME` | `Optional[str]` | `rw` | `None` | Name of the load3 source |
| load4_name | `EOS_MEASUREMENT__LOAD4_NAME` | `Optional[str]` | `rw` | `None` | Name of the load4 source |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "measurement": {
           "load0_name": "Household",
           "load1_name": null,
           "load2_name": null,
           "load3_name": null,
           "load4_name": null
       }
   }
```

## General Optimization Configuration

Attributes:
    hours (int): Number of hours for optimizations.

:::{table} optimization
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| hours | `EOS_OPTIMIZATION__HOURS` | `Optional[int]` | `rw` | `48` | Number of hours into the future for optimizations. |
| penalty | `EOS_OPTIMIZATION__PENALTY` | `Optional[int]` | `rw` | `10` | Penalty factor used in optimization. |
| ev_available_charge_rates_percent | `EOS_OPTIMIZATION__EV_AVAILABLE_CHARGE_RATES_PERCENT` | `Optional[List[float]]` | `rw` | `[0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]` | Charge rates available for the EV in percent of maximum charge. |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "optimization": {
           "hours": 48,
           "penalty": 10,
           "ev_available_charge_rates_percent": [
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
parameters such as the forecast duration (in hours).
Validators ensure each parameter is within a specified range.

Attributes:
    hours (Optional[int]): Number of hours into the future for predictions.
        Must be non-negative.
    historic_hours (Optional[int]): Number of hours into the past for historical data.
        Must be non-negative.

Validators:
    validate_hours (int): Ensures `hours` is a non-negative integer.
    validate_historic_hours (int): Ensures `historic_hours` is a non-negative integer.

:::{table} prediction
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| hours | `EOS_PREDICTION__HOURS` | `Optional[int]` | `rw` | `48` | Number of hours into the future for predictions |
| historic_hours | `EOS_PREDICTION__HISTORIC_HOURS` | `Optional[int]` | `rw` | `48` | Number of hours into the past for historical predictions data |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "prediction": {
           "hours": 48,
           "historic_hours": 48
       }
   }
```

## Electricity Price Prediction Configuration

:::{table} elecprice
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| provider | `EOS_ELECPRICE__PROVIDER` | `Optional[str]` | `rw` | `None` | Electricity price provider id of provider to be used. |
| charges_kwh | `EOS_ELECPRICE__CHARGES_KWH` | `Optional[float]` | `rw` | `None` | Electricity price charges (€/kWh). |
| provider_settings | `EOS_ELECPRICE__PROVIDER_SETTINGS` | `Optional[akkudoktoreos.prediction.elecpriceimport.ElecPriceImportCommonSettings]` | `rw` | `None` | Provider settings |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "elecprice": {
           "provider": "ElecPriceAkkudoktor",
           "charges_kwh": 0.21,
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
| import_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import elecprice data from. |
| import_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of electricity price forecast value lists. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "elecprice": {
           "provider_settings": {
               "import_file_path": null,
               "import_json": "{\"elecprice_marketprice_wh\": [0.0003384, 0.0003318, 0.0003284]}"
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
| provider | `EOS_LOAD__PROVIDER` | `Optional[str]` | `rw` | `None` | Load provider id of provider to be used. |
| provider_settings | `EOS_LOAD__PROVIDER_SETTINGS` | `Union[akkudoktoreos.prediction.loadakkudoktor.LoadAkkudoktorCommonSettings, akkudoktoreos.prediction.loadimport.LoadImportCommonSettings, NoneType]` | `rw` | `None` | Provider settings |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "load": {
           "provider": "LoadAkkudoktor",
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
| import_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import load data from. |
| import_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of load forecast value lists. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "load": {
           "provider_settings": {
               "import_file_path": null,
               "import_json": "{\"load0_mean\": [676.71, 876.19, 527.13]}"
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
| provider | `EOS_PVFORECAST__PROVIDER` | `Optional[str]` | `rw` | `None` | PVForecast provider id of provider to be used. |
| planes | `EOS_PVFORECAST__PLANES` | `Optional[list[akkudoktoreos.prediction.pvforecast.PVForecastPlaneSetting]]` | `rw` | `None` | Plane configuration. |
| provider_settings | `EOS_PVFORECAST__PROVIDER_SETTINGS` | `Optional[akkudoktoreos.prediction.pvforecastimport.PVForecastImportCommonSettings]` | `rw` | `None` | Provider settings |
| planes_peakpower | | `List[float]` | `ro` | `N/A` | Compute a list of the peak power per active planes. |
| planes_azimuth | | `List[float]` | `ro` | `N/A` | Compute a list of the azimuths per active planes. |
| planes_tilt | | `List[float]` | `ro` | `N/A` | Compute a list of the tilts per active planes. |
| planes_userhorizon | | `Any` | `ro` | `N/A` | Compute a list of the user horizon per active planes. |
| planes_inverter_paco | | `Any` | `ro` | `N/A` | Compute a list of the maximum power rating of the inverter per active planes. |
:::

### Example Input

```{eval-rst}
.. code-block:: json

   {
       "pvforecast": {
           "provider": "PVForecastAkkudoktor",
           "planes": [
               {
                   "surface_tilt": 10.0,
                   "surface_azimuth": 10.0,
                   "userhorizon": [
                       10.0,
                       20.0,
                       30.0
                   ],
                   "peakpower": 5.0,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 0,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 6000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               },
               {
                   "surface_tilt": 20.0,
                   "surface_azimuth": 20.0,
                   "userhorizon": [
                       5.0,
                       15.0,
                       25.0
                   ],
                   "peakpower": 3.5,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 1,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 4000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               }
           ],
           "provider_settings": null
       }
   }
```

### Example Output

```{eval-rst}
.. code-block:: json

   {
       "pvforecast": {
           "provider": "PVForecastAkkudoktor",
           "planes": [
               {
                   "surface_tilt": 10.0,
                   "surface_azimuth": 10.0,
                   "userhorizon": [
                       10.0,
                       20.0,
                       30.0
                   ],
                   "peakpower": 5.0,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 0,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 6000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               },
               {
                   "surface_tilt": 20.0,
                   "surface_azimuth": 20.0,
                   "userhorizon": [
                       5.0,
                       15.0,
                       25.0
                   ],
                   "peakpower": 3.5,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 1,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 4000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               }
           ],
           "provider_settings": null,
           "planes_peakpower": [
               5.0,
               3.5
           ],
           "planes_azimuth": [
               10.0,
               20.0
           ],
           "planes_tilt": [
               10.0,
               20.0
           ],
           "planes_userhorizon": [
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
           "planes_inverter_paco": [
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
| import_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import PV forecast data from. |
| import_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of PV forecast value lists. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "pvforecast": {
           "provider_settings": {
               "import_file_path": null,
               "import_json": "{\"pvforecast_ac_power\": [0, 8.05, 352.91]}"
           }
       }
   }
```

### PV Forecast Plane Configuration

:::{table} pvforecast::planes::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| surface_tilt | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| surface_azimuth | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| userhorizon | `Optional[List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| peakpower | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| pvtechchoice | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| mountingplace | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| loss | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| trackingtype | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| optimal_surface_tilt | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| optimalangles | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| albedo | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| module_model | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| inverter_model | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| inverter_paco | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| modules_per_string | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| strings_per_inverter | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "pvforecast": {
           "planes": [
               {
                   "surface_tilt": 10.0,
                   "surface_azimuth": 10.0,
                   "userhorizon": [
                       10.0,
                       20.0,
                       30.0
                   ],
                   "peakpower": 5.0,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 0,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 6000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               },
               {
                   "surface_tilt": 20.0,
                   "surface_azimuth": 20.0,
                   "userhorizon": [
                       5.0,
                       15.0,
                       25.0
                   ],
                   "peakpower": 3.5,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 1,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 4000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               }
           ]
       }
   }
```

## Weather Forecast Configuration

:::{table} weather
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| provider | `EOS_WEATHER__PROVIDER` | `Optional[str]` | `rw` | `None` | Weather provider id of provider to be used. |
| provider_settings | `EOS_WEATHER__PROVIDER_SETTINGS` | `Optional[akkudoktoreos.prediction.weatherimport.WeatherImportCommonSettings]` | `rw` | `None` | Provider settings |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "weather": {
           "provider": "WeatherImport",
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
| import_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import weather data from. |
| import_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of weather forecast value lists. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "weather": {
           "provider_settings": {
               "import_file_path": null,
               "import_json": "{\"weather_temp_air\": [18.3, 17.8, 16.9]}"
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
| host | `EOS_SERVER__HOST` | `Optional[pydantic.networks.IPvAnyAddress]` | `rw` | `0.0.0.0` | EOS server IP address. |
| port | `EOS_SERVER__PORT` | `Optional[int]` | `rw` | `8503` | EOS server IP port number. |
| verbose | `EOS_SERVER__VERBOSE` | `Optional[bool]` | `rw` | `False` | Enable debug output |
| startup_eosdash | `EOS_SERVER__STARTUP_EOSDASH` | `Optional[bool]` | `rw` | `True` | EOS server to start EOSdash server. |
| eosdash_host | `EOS_SERVER__EOSDASH_HOST` | `Optional[pydantic.networks.IPvAnyAddress]` | `rw` | `0.0.0.0` | EOSdash server IP address. |
| eosdash_port | `EOS_SERVER__EOSDASH_PORT` | `Optional[int]` | `rw` | `8504` | EOSdash server IP port number. |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "server": {
           "host": "0.0.0.0",
           "port": 8503,
           "verbose": false,
           "startup_eosdash": true,
           "eosdash_host": "0.0.0.0",
           "eosdash_port": 8504
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

## Full example Config

```{eval-rst}
.. code-block:: json

   {
       "general": {
           "data_folder_path": null,
           "data_output_subpath": "output",
           "data_cache_subpath": "cache",
           "latitude": 52.52,
           "longitude": 13.405
       },
       "logging": {
           "level": "INFO"
       },
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
       },
       "measurement": {
           "load0_name": "Household",
           "load1_name": null,
           "load2_name": null,
           "load3_name": null,
           "load4_name": null
       },
       "optimization": {
           "hours": 48,
           "penalty": 10,
           "ev_available_charge_rates_percent": [
               0.0,
               0.375,
               0.5,
               0.625,
               0.75,
               0.875,
               1.0
           ]
       },
       "prediction": {
           "hours": 48,
           "historic_hours": 48
       },
       "elecprice": {
           "provider": "ElecPriceAkkudoktor",
           "charges_kwh": 0.21,
           "provider_settings": null
       },
       "load": {
           "provider": "LoadAkkudoktor",
           "provider_settings": null
       },
       "pvforecast": {
           "provider": "PVForecastAkkudoktor",
           "planes": [
               {
                   "surface_tilt": 10.0,
                   "surface_azimuth": 10.0,
                   "userhorizon": [
                       10.0,
                       20.0,
                       30.0
                   ],
                   "peakpower": 5.0,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 0,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 6000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               },
               {
                   "surface_tilt": 20.0,
                   "surface_azimuth": 20.0,
                   "userhorizon": [
                       5.0,
                       15.0,
                       25.0
                   ],
                   "peakpower": 3.5,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 1,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 4000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               }
           ],
           "provider_settings": null
       },
       "weather": {
           "provider": "WeatherImport",
           "provider_settings": null
       },
       "server": {
           "host": "0.0.0.0",
           "port": 8503,
           "verbose": false,
           "startup_eosdash": true,
           "eosdash_host": "0.0.0.0",
           "eosdash_port": 8504
       },
       "utils": {}
   }
```
