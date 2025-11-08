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

:::{table} general
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| version | `EOS_GENERAL__VERSION` | `str` | `rw` | `0.1.0+dev` | Configuration file version. Used to check compatibility. |
| data_folder_path | `EOS_GENERAL__DATA_FOLDER_PATH` | `Optional[pathlib.Path]` | `rw` | `None` | Path to EOS data directory. |
| data_output_subpath | `EOS_GENERAL__DATA_OUTPUT_SUBPATH` | `Optional[pathlib.Path]` | `rw` | `output` | Sub-path for the EOS output data directory. |
| latitude | `EOS_GENERAL__LATITUDE` | `Optional[float]` | `rw` | `52.52` | Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°) |
| longitude | `EOS_GENERAL__LONGITUDE` | `Optional[float]` | `rw` | `13.405` | Longitude in decimal degrees, within -180 to 180 (°) |
| timezone | | `Optional[str]` | `ro` | `N/A` | Compute timezone based on latitude and longitude. |
| data_output_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Compute data_output_path based on data_folder_path. |
| config_folder_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Path to EOS configuration directory. |
| config_file_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Path to EOS configuration file. |
:::

### Example Input

```{eval-rst}
.. code-block:: json

   {
       "general": {
           "version": "0.1.0+dev",
           "data_folder_path": null,
           "data_output_subpath": "output",
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
           "version": "0.1.0+dev",
           "data_folder_path": null,
           "data_output_subpath": "output",
           "latitude": 52.52,
           "longitude": 13.405,
           "timezone": "Europe/Berlin",
           "data_output_path": null,
           "config_folder_path": "/home/user/.config/net.akkudoktoreos.net",
           "config_file_path": "/home/user/.config/net.akkudoktoreos.net/EOS.config.json"
       }
   }
```

## Cache Configuration

:::{table} cache
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| subpath | `EOS_CACHE__SUBPATH` | `Optional[pathlib.Path]` | `rw` | `cache` | Sub-path for the EOS cache data directory. |
| cleanup_interval | `EOS_CACHE__CLEANUP_INTERVAL` | `float` | `rw` | `300` | Intervall in seconds for EOS file cache cleanup. |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "cache": {
           "subpath": "cache",
           "cleanup_interval": 300.0
       }
   }
```

## Energy Management Configuration

:::{table} ems
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| startup_delay | `EOS_EMS__STARTUP_DELAY` | `float` | `rw` | `5` | Startup delay in seconds for EOS energy management runs. |
| interval | `EOS_EMS__INTERVAL` | `Optional[float]` | `rw` | `None` | Intervall in seconds between EOS energy management runs. |
| mode | `EOS_EMS__MODE` | `Optional[akkudoktoreos.core.emsettings.EnergyManagementMode]` | `rw` | `None` | Energy management mode [OPTIMIZATION | PREDICTION]. |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "ems": {
           "startup_delay": 5.0,
           "interval": 300.0,
           "mode": "OPTIMIZATION"
       }
   }
```

## Logging Configuration

:::{table} logging
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| console_level | `EOS_LOGGING__CONSOLE_LEVEL` | `Optional[str]` | `rw` | `None` | Logging level when logging to console. |
| file_level | `EOS_LOGGING__FILE_LEVEL` | `Optional[str]` | `rw` | `None` | Logging level when logging to file. |
| file_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Computed log file path based on data output path. |
:::

### Example Input

```{eval-rst}
.. code-block:: json

   {
       "logging": {
           "console_level": "TRACE",
           "file_level": "TRACE"
       }
   }
```

### Example Output

```{eval-rst}
.. code-block:: json

   {
       "logging": {
           "console_level": "TRACE",
           "file_level": "TRACE",
           "file_path": "/home/user/.local/share/net.akkudoktoreos.net/output/eos.log"
       }
   }
```

## Base configuration for devices simulation settings

:::{table} devices
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| batteries | `EOS_DEVICES__BATTERIES` | `Optional[list[akkudoktoreos.devices.devices.BatteriesCommonSettings]]` | `rw` | `None` | List of battery devices |
| max_batteries | `EOS_DEVICES__MAX_BATTERIES` | `Optional[int]` | `rw` | `None` | Maximum number of batteries that can be set |
| electric_vehicles | `EOS_DEVICES__ELECTRIC_VEHICLES` | `Optional[list[akkudoktoreos.devices.devices.BatteriesCommonSettings]]` | `rw` | `None` | List of electric vehicle devices |
| max_electric_vehicles | `EOS_DEVICES__MAX_ELECTRIC_VEHICLES` | `Optional[int]` | `rw` | `None` | Maximum number of electric vehicles that can be set |
| inverters | `EOS_DEVICES__INVERTERS` | `Optional[list[akkudoktoreos.devices.devices.InverterCommonSettings]]` | `rw` | `None` | List of inverters |
| max_inverters | `EOS_DEVICES__MAX_INVERTERS` | `Optional[int]` | `rw` | `None` | Maximum number of inverters that can be set |
| home_appliances | `EOS_DEVICES__HOME_APPLIANCES` | `Optional[list[akkudoktoreos.devices.devices.HomeApplianceCommonSettings]]` | `rw` | `None` | List of home appliances |
| max_home_appliances | `EOS_DEVICES__MAX_HOME_APPLIANCES` | `Optional[int]` | `rw` | `None` | Maximum number of home_appliances that can be set |
| measurement_keys | | `Optional[list[str]]` | `ro` | `N/A` | Return the measurement keys for the resource/ device stati that are measurements. |
:::

### Example Input

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "batteries": [
               {
                   "device_id": "battery1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.0,
                   "max_charge_power_w": 5000,
                   "min_charge_power_w": 50,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "battery1-soc-factor",
                   "measurement_key_power_l1_w": "battery1-power-l1-w",
                   "measurement_key_power_l2_w": "battery1-power-l2-w",
                   "measurement_key_power_l3_w": "battery1-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "battery1-power-3-phase-sym-w",
                   "measurement_keys": [
                       "battery1-soc-factor",
                       "battery1-power-l1-w",
                       "battery1-power-l2-w",
                       "battery1-power-l3-w",
                       "battery1-power-3-phase-sym-w"
                   ]
               }
           ],
           "max_batteries": 1,
           "electric_vehicles": [
               {
                   "device_id": "battery1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.0,
                   "max_charge_power_w": 5000,
                   "min_charge_power_w": 50,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "battery1-soc-factor",
                   "measurement_key_power_l1_w": "battery1-power-l1-w",
                   "measurement_key_power_l2_w": "battery1-power-l2-w",
                   "measurement_key_power_l3_w": "battery1-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "battery1-power-3-phase-sym-w",
                   "measurement_keys": [
                       "battery1-soc-factor",
                       "battery1-power-l1-w",
                       "battery1-power-l2-w",
                       "battery1-power-l3-w",
                       "battery1-power-3-phase-sym-w"
                   ]
               }
           ],
           "max_electric_vehicles": 1,
           "inverters": [],
           "max_inverters": 1,
           "home_appliances": [],
           "max_home_appliances": 1
       }
   }
```

### Example Output

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "batteries": [
               {
                   "device_id": "battery1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.0,
                   "max_charge_power_w": 5000,
                   "min_charge_power_w": 50,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "battery1-soc-factor",
                   "measurement_key_power_l1_w": "battery1-power-l1-w",
                   "measurement_key_power_l2_w": "battery1-power-l2-w",
                   "measurement_key_power_l3_w": "battery1-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "battery1-power-3-phase-sym-w",
                   "measurement_keys": [
                       "battery1-soc-factor",
                       "battery1-power-l1-w",
                       "battery1-power-l2-w",
                       "battery1-power-l3-w",
                       "battery1-power-3-phase-sym-w"
                   ]
               }
           ],
           "max_batteries": 1,
           "electric_vehicles": [
               {
                   "device_id": "battery1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.0,
                   "max_charge_power_w": 5000,
                   "min_charge_power_w": 50,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "battery1-soc-factor",
                   "measurement_key_power_l1_w": "battery1-power-l1-w",
                   "measurement_key_power_l2_w": "battery1-power-l2-w",
                   "measurement_key_power_l3_w": "battery1-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "battery1-power-3-phase-sym-w",
                   "measurement_keys": [
                       "battery1-soc-factor",
                       "battery1-power-l1-w",
                       "battery1-power-l2-w",
                       "battery1-power-l3-w",
                       "battery1-power-3-phase-sym-w"
                   ]
               }
           ],
           "max_electric_vehicles": 1,
           "inverters": [],
           "max_inverters": 1,
           "home_appliances": [],
           "max_home_appliances": 1,
           "measurement_keys": [
               "battery1-soc-factor",
               "battery1-power-l1-w",
               "battery1-power-l2-w",
               "battery1-power-l3-w",
               "battery1-power-3-phase-sym-w",
               "battery1-soc-factor",
               "battery1-power-l1-w",
               "battery1-power-l2-w",
               "battery1-power-l3-w",
               "battery1-power-3-phase-sym-w"
           ]
       }
   }
```

### Home Appliance devices base settings

:::{table} devices::home_appliances::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| device_id | `str` | `rw` | `<unknown>` | ID of device |
| consumption_wh | `int` | `rw` | `required` | Energy consumption [Wh]. |
| duration_h | `int` | `rw` | `required` | Usage duration in hours [0 ... 24]. |
| time_windows | `Optional[akkudoktoreos.utils.datetimeutil.TimeWindowSequence]` | `rw` | `None` | Sequence of allowed time windows. Defaults to optimization general time window. |
| measurement_keys | `Optional[list[str]]` | `ro` | `N/A` | Measurement keys for the home appliance stati that are measurements. |
:::

#### Example Input

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "home_appliances": [
               {
                   "device_id": "battery1",
                   "consumption_wh": 2000,
                   "duration_h": 1,
                   "time_windows": {
                       "windows": [
                           {
                               "start_time": "10:00:00.000000 Europe/Berlin",
                               "duration": "2 hours",
                               "day_of_week": null,
                               "date": null,
                               "locale": null
                           }
                       ]
                   }
               },
               {
                   "device_id": "ev1",
                   "consumption_wh": 2000,
                   "duration_h": 1,
                   "time_windows": {
                       "windows": [
                           {
                               "start_time": "10:00:00.000000 Europe/Berlin",
                               "duration": "2 hours",
                               "day_of_week": null,
                               "date": null,
                               "locale": null
                           }
                       ]
                   }
               },
               {
                   "device_id": "inverter1",
                   "consumption_wh": 2000,
                   "duration_h": 1,
                   "time_windows": {
                       "windows": [
                           {
                               "start_time": "10:00:00.000000 Europe/Berlin",
                               "duration": "2 hours",
                               "day_of_week": null,
                               "date": null,
                               "locale": null
                           }
                       ]
                   }
               },
               {
                   "device_id": "dishwasher",
                   "consumption_wh": 2000,
                   "duration_h": 1,
                   "time_windows": {
                       "windows": [
                           {
                               "start_time": "10:00:00.000000 Europe/Berlin",
                               "duration": "2 hours",
                               "day_of_week": null,
                               "date": null,
                               "locale": null
                           }
                       ]
                   }
               }
           ]
       }
   }
```

#### Example Output

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "home_appliances": [
               {
                   "device_id": "battery1",
                   "consumption_wh": 2000,
                   "duration_h": 1,
                   "time_windows": {
                       "windows": [
                           {
                               "start_time": "10:00:00.000000 Europe/Berlin",
                               "duration": "2 hours",
                               "day_of_week": null,
                               "date": null,
                               "locale": null
                           }
                       ]
                   },
                   "measurement_keys": []
               },
               {
                   "device_id": "ev1",
                   "consumption_wh": 2000,
                   "duration_h": 1,
                   "time_windows": {
                       "windows": [
                           {
                               "start_time": "10:00:00.000000 Europe/Berlin",
                               "duration": "2 hours",
                               "day_of_week": null,
                               "date": null,
                               "locale": null
                           }
                       ]
                   },
                   "measurement_keys": []
               },
               {
                   "device_id": "inverter1",
                   "consumption_wh": 2000,
                   "duration_h": 1,
                   "time_windows": {
                       "windows": [
                           {
                               "start_time": "10:00:00.000000 Europe/Berlin",
                               "duration": "2 hours",
                               "day_of_week": null,
                               "date": null,
                               "locale": null
                           }
                       ]
                   },
                   "measurement_keys": []
               },
               {
                   "device_id": "dishwasher",
                   "consumption_wh": 2000,
                   "duration_h": 1,
                   "time_windows": {
                       "windows": [
                           {
                               "start_time": "10:00:00.000000 Europe/Berlin",
                               "duration": "2 hours",
                               "day_of_week": null,
                               "date": null,
                               "locale": null
                           }
                       ]
                   },
                   "measurement_keys": []
               }
           ]
       }
   }
```

### Inverter devices base settings

:::{table} devices::inverters::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| device_id | `str` | `rw` | `<unknown>` | ID of device |
| max_power_w | `Optional[float]` | `rw` | `None` | Maximum power [W]. |
| battery_id | `Optional[str]` | `rw` | `None` | ID of battery controlled by this inverter. |
| measurement_keys | `Optional[list[str]]` | `ro` | `N/A` | Measurement keys for the inverter stati that are measurements. |
:::

#### Example Input

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "inverters": [
               {
                   "device_id": "battery1",
                   "max_power_w": 10000.0,
                   "battery_id": null
               },
               {
                   "device_id": "ev1",
                   "max_power_w": 10000.0,
                   "battery_id": "battery1"
               },
               {
                   "device_id": "inverter1",
                   "max_power_w": 10000.0,
                   "battery_id": "battery1"
               },
               {
                   "device_id": "dishwasher",
                   "max_power_w": 10000.0,
                   "battery_id": "battery1"
               }
           ]
       }
   }
```

#### Example Output

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "inverters": [
               {
                   "device_id": "battery1",
                   "max_power_w": 10000.0,
                   "battery_id": null,
                   "measurement_keys": []
               },
               {
                   "device_id": "ev1",
                   "max_power_w": 10000.0,
                   "battery_id": "battery1",
                   "measurement_keys": []
               },
               {
                   "device_id": "inverter1",
                   "max_power_w": 10000.0,
                   "battery_id": "battery1",
                   "measurement_keys": []
               },
               {
                   "device_id": "dishwasher",
                   "max_power_w": 10000.0,
                   "battery_id": "battery1",
                   "measurement_keys": []
               }
           ]
       }
   }
```

### Battery devices base settings

:::{table} devices::batteries::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| device_id | `str` | `rw` | `<unknown>` | ID of device |
| capacity_wh | `int` | `rw` | `8000` | Capacity [Wh]. |
| charging_efficiency | `float` | `rw` | `0.88` | Charging efficiency [0.01 ... 1.00]. |
| discharging_efficiency | `float` | `rw` | `0.88` | Discharge efficiency [0.01 ... 1.00]. |
| levelized_cost_of_storage_kwh | `float` | `rw` | `0.0` | Levelized cost of storage (LCOS), the average lifetime cost of delivering one kWh [€/kWh]. |
| max_charge_power_w | `Optional[float]` | `rw` | `5000` | Maximum charging power [W]. |
| min_charge_power_w | `Optional[float]` | `rw` | `50` | Minimum charging power [W]. |
| charge_rates | `Optional[numpydantic.vendor.npbase_meta_classes.NDArray]` | `rw` | `[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]` | Charge rates as factor of maximum charging power [0.00 ... 1.00]. None triggers fallback to default charge-rates. |
| min_soc_percentage | `int` | `rw` | `0` | Minimum state of charge (SOC) as percentage of capacity [%]. This is the target SoC for charging |
| max_soc_percentage | `int` | `rw` | `100` | Maximum state of charge (SOC) as percentage of capacity [%]. |
| measurement_key_soc_factor | `str` | `ro` | `N/A` | Measurement key for the battery state of charge (SoC) as factor of total capacity [0.0 ... 1.0]. |
| measurement_key_power_l1_w | `str` | `ro` | `N/A` | Measurement key for the L1 power the battery is charged or discharged with [W]. |
| measurement_key_power_l2_w | `str` | `ro` | `N/A` | Measurement key for the L2 power the battery is charged or discharged with [W]. |
| measurement_key_power_l3_w | `str` | `ro` | `N/A` | Measurement key for the L3 power the battery is charged or discharged with [W]. |
| measurement_key_power_3_phase_sym_w | `str` | `ro` | `N/A` | Measurement key for the symmetric 3 phase power the battery is charged or discharged with [W]. |
| measurement_keys | `Optional[list[str]]` | `ro` | `N/A` | Measurement keys for the battery stati that are measurements.

Battery SoC, power. |
:::

#### Example Input

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "batteries": [
               {
                   "device_id": "battery1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.12,
                   "max_charge_power_w": 5000.0,
                   "min_charge_power_w": 50.0,
                   "charge_rates": "[0.   0.25 0.5  0.75 1.  ]",
                   "min_soc_percentage": 10,
                   "max_soc_percentage": 100
               },
               {
                   "device_id": "ev1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.12,
                   "max_charge_power_w": 5000.0,
                   "min_charge_power_w": 50.0,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 10,
                   "max_soc_percentage": 100
               },
               {
                   "device_id": "inverter1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.12,
                   "max_charge_power_w": 5000.0,
                   "min_charge_power_w": 50.0,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 10,
                   "max_soc_percentage": 100
               },
               {
                   "device_id": "dishwasher",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.12,
                   "max_charge_power_w": 5000.0,
                   "min_charge_power_w": 50.0,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 10,
                   "max_soc_percentage": 100
               }
           ]
       }
   }
```

#### Example Output

```{eval-rst}
.. code-block:: json

   {
       "devices": {
           "batteries": [
               {
                   "device_id": "battery1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.12,
                   "max_charge_power_w": 5000.0,
                   "min_charge_power_w": 50.0,
                   "charge_rates": "[0.   0.25 0.5  0.75 1.  ]",
                   "min_soc_percentage": 10,
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "battery1-soc-factor",
                   "measurement_key_power_l1_w": "battery1-power-l1-w",
                   "measurement_key_power_l2_w": "battery1-power-l2-w",
                   "measurement_key_power_l3_w": "battery1-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "battery1-power-3-phase-sym-w",
                   "measurement_keys": [
                       "battery1-soc-factor",
                       "battery1-power-l1-w",
                       "battery1-power-l2-w",
                       "battery1-power-l3-w",
                       "battery1-power-3-phase-sym-w"
                   ]
               },
               {
                   "device_id": "ev1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.12,
                   "max_charge_power_w": 5000.0,
                   "min_charge_power_w": 50.0,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 10,
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "ev1-soc-factor",
                   "measurement_key_power_l1_w": "ev1-power-l1-w",
                   "measurement_key_power_l2_w": "ev1-power-l2-w",
                   "measurement_key_power_l3_w": "ev1-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "ev1-power-3-phase-sym-w",
                   "measurement_keys": [
                       "ev1-soc-factor",
                       "ev1-power-l1-w",
                       "ev1-power-l2-w",
                       "ev1-power-l3-w",
                       "ev1-power-3-phase-sym-w"
                   ]
               },
               {
                   "device_id": "inverter1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.12,
                   "max_charge_power_w": 5000.0,
                   "min_charge_power_w": 50.0,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 10,
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "inverter1-soc-factor",
                   "measurement_key_power_l1_w": "inverter1-power-l1-w",
                   "measurement_key_power_l2_w": "inverter1-power-l2-w",
                   "measurement_key_power_l3_w": "inverter1-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "inverter1-power-3-phase-sym-w",
                   "measurement_keys": [
                       "inverter1-soc-factor",
                       "inverter1-power-l1-w",
                       "inverter1-power-l2-w",
                       "inverter1-power-l3-w",
                       "inverter1-power-3-phase-sym-w"
                   ]
               },
               {
                   "device_id": "dishwasher",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.12,
                   "max_charge_power_w": 5000.0,
                   "min_charge_power_w": 50.0,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 10,
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "dishwasher-soc-factor",
                   "measurement_key_power_l1_w": "dishwasher-power-l1-w",
                   "measurement_key_power_l2_w": "dishwasher-power-l2-w",
                   "measurement_key_power_l3_w": "dishwasher-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "dishwasher-power-3-phase-sym-w",
                   "measurement_keys": [
                       "dishwasher-soc-factor",
                       "dishwasher-power-l1-w",
                       "dishwasher-power-l2-w",
                       "dishwasher-power-l3-w",
                       "dishwasher-power-3-phase-sym-w"
                   ]
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
| load_emr_keys | `EOS_MEASUREMENT__LOAD_EMR_KEYS` | `Optional[list[str]]` | `rw` | `None` | The keys of the measurements that are energy meter readings of a load [kWh]. |
| grid_export_emr_keys | `EOS_MEASUREMENT__GRID_EXPORT_EMR_KEYS` | `Optional[list[str]]` | `rw` | `None` | The keys of the measurements that are energy meter readings of energy export to grid [kWh]. |
| grid_import_emr_keys | `EOS_MEASUREMENT__GRID_IMPORT_EMR_KEYS` | `Optional[list[str]]` | `rw` | `None` | The keys of the measurements that are energy meter readings of energy import from grid [kWh]. |
| pv_production_emr_keys | `EOS_MEASUREMENT__PV_PRODUCTION_EMR_KEYS` | `Optional[list[str]]` | `rw` | `None` | The keys of the measurements that are PV production energy meter readings [kWh]. |
| keys | | `list[str]` | `ro` | `N/A` | The keys of the measurements that can be stored. |
:::

### Example Input

```{eval-rst}
.. code-block:: json

   {
       "measurement": {
           "load_emr_keys": [
               "load0_emr"
           ],
           "grid_export_emr_keys": [
               "grid_export_emr"
           ],
           "grid_import_emr_keys": [
               "grid_import_emr"
           ],
           "pv_production_emr_keys": [
               "pv1_emr"
           ]
       }
   }
```

### Example Output

```{eval-rst}
.. code-block:: json

   {
       "measurement": {
           "load_emr_keys": [
               "load0_emr"
           ],
           "grid_export_emr_keys": [
               "grid_export_emr"
           ],
           "grid_import_emr_keys": [
               "grid_import_emr"
           ],
           "pv_production_emr_keys": [
               "pv1_emr"
           ],
           "keys": [
               "grid_export_emr",
               "grid_import_emr",
               "load0_emr",
               "pv1_emr"
           ]
       }
   }
```

## General Optimization Configuration

:::{table} optimization
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| horizon_hours | `EOS_OPTIMIZATION__HORIZON_HOURS` | `Optional[int]` | `rw` | `24` | The general time window within which the energy optimization goal shall be achieved [h]. Defaults to 24 hours. |
| interval | `EOS_OPTIMIZATION__INTERVAL` | `Optional[int]` | `rw` | `3600` | The optimization interval [sec]. |
| algorithm | `EOS_OPTIMIZATION__ALGORITHM` | `Optional[str]` | `rw` | `GENETIC` | The optimization algorithm. |
| genetic | `EOS_OPTIMIZATION__GENETIC` | `Optional[akkudoktoreos.optimization.optimization.GeneticCommonSettings]` | `rw` | `None` | Genetic optimization algorithm configuration. |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "optimization": {
           "horizon_hours": 24,
           "interval": 3600,
           "algorithm": "GENETIC",
           "genetic": {
               "individuals": 400,
               "generations": 400,
               "seed": null,
               "penalties": {
                   "ev_soc_miss": 10
               }
           }
       }
   }
```

### General Genetic Optimization Algorithm Configuration

:::{table} optimization::genetic
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| individuals | `Optional[int]` | `rw` | `300` | Number of individuals (solutions) to generate for the (initial) generation [>= 10]. Defaults to 300. |
| generations | `Optional[int]` | `rw` | `400` | Number of generations to evaluate the optimal solution [>= 10]. Defaults to 400. |
| seed | `Optional[int]` | `rw` | `None` | Fixed seed for genetic algorithm. Defaults to 'None' which means random seed. |
| penalties | `Optional[dict[str, Union[float, int, str]]]` | `rw` | `None` | A dictionary of penalty function parameters consisting of a penalty function parameter name and the associated value. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "optimization": {
           "genetic": {
               "individuals": 300,
               "generations": 400,
               "seed": null,
               "penalties": {
                   "ev_soc_miss": 10
               }
           }
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
| charges_kwh | `EOS_ELECPRICE__CHARGES_KWH` | `Optional[float]` | `rw` | `None` | Electricity price charges [€/kWh]. Will be added to variable market price. |
| vat_rate | `EOS_ELECPRICE__VAT_RATE` | `Optional[float]` | `rw` | `1.19` | VAT rate factor applied to electricity price when charges are used. |
| provider_settings | `EOS_ELECPRICE__PROVIDER_SETTINGS` | `ElecPriceCommonProviderSettings` | `rw` | `required` | Provider settings |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "elecprice": {
           "provider": "ElecPriceAkkudoktor",
           "charges_kwh": 0.21,
           "vat_rate": 1.19,
           "provider_settings": {
               "ElecPriceImport": null
           }
       }
   }
```

### Common settings for elecprice data import from file or JSON String

:::{table} elecprice::provider_settings::ElecPriceImport
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
               "ElecPriceImport": {
                   "import_file_path": null,
                   "import_json": "{\"elecprice_marketprice_wh\": [0.0003384, 0.0003318, 0.0003284]}"
               }
           }
       }
   }
```

### Electricity Price Prediction Provider Configuration

:::{table} elecprice::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| ElecPriceImport | `Optional[akkudoktoreos.prediction.elecpriceimport.ElecPriceImportCommonSettings]` | `rw` | `None` | ElecPriceImport settings |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "elecprice": {
           "provider_settings": {
               "ElecPriceImport": null
           }
       }
   }
```

## Feed In Tariff Prediction Configuration

:::{table} feedintariff
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| provider | `EOS_FEEDINTARIFF__PROVIDER` | `Optional[str]` | `rw` | `None` | Feed in tariff provider id of provider to be used. |
| provider_settings | `EOS_FEEDINTARIFF__PROVIDER_SETTINGS` | `FeedInTariffCommonProviderSettings` | `rw` | `required` | Provider settings |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "feedintariff": {
           "provider": "FeedInTariffFixed",
           "provider_settings": {
               "FeedInTariffFixed": null,
               "FeedInTariffImport": null
           }
       }
   }
```

### Common settings for feed in tariff data import from file or JSON string

:::{table} feedintariff::provider_settings::FeedInTariffImport
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| import_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import feed in tariff data from. |
| import_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of feed in tariff forecast value lists. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "feedintariff": {
           "provider_settings": {
               "FeedInTariffImport": {
                   "import_file_path": null,
                   "import_json": "{\"fead_in_tariff_wh\": [0.000078, 0.000078, 0.000023]}"
               }
           }
       }
   }
```

### Common settings for elecprice fixed price

:::{table} feedintariff::provider_settings::FeedInTariffFixed
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| feed_in_tariff_kwh | `Optional[float]` | `rw` | `None` | Electricity price feed in tariff [€/kWH]. |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "feedintariff": {
           "provider_settings": {
               "FeedInTariffFixed": {
                   "feed_in_tariff_kwh": 0.078
               }
           }
       }
   }
```

### Feed In Tariff Prediction Provider Configuration

:::{table} feedintariff::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| FeedInTariffFixed | `Optional[akkudoktoreos.prediction.feedintarifffixed.FeedInTariffFixedCommonSettings]` | `rw` | `None` | FeedInTariffFixed settings |
| FeedInTariffImport | `Optional[akkudoktoreos.prediction.feedintariffimport.FeedInTariffImportCommonSettings]` | `rw` | `None` | FeedInTariffImport settings |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "feedintariff": {
           "provider_settings": {
               "FeedInTariffFixed": null,
               "FeedInTariffImport": null
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
| provider_settings | `EOS_LOAD__PROVIDER_SETTINGS` | `LoadCommonProviderSettings` | `rw` | `required` | Provider settings |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "load": {
           "provider": "LoadAkkudoktor",
           "provider_settings": {
               "LoadAkkudoktor": null,
               "LoadVrm": null,
               "LoadImport": null
           }
       }
   }
```

### Common settings for load data import from file or JSON string

:::{table} load::provider_settings::LoadImport
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
               "LoadImport": {
                   "import_file_path": null,
                   "import_json": "{\"load0_mean\": [676.71, 876.19, 527.13]}"
               }
           }
       }
   }
```

### Common settings for VRM API

:::{table} load::provider_settings::LoadVrm
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| load_vrm_token | `str` | `rw` | `your-token` | Token for Connecting VRM API |
| load_vrm_idsite | `int` | `rw` | `12345` | VRM-Installation-ID |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "load": {
           "provider_settings": {
               "LoadVrm": {
                   "load_vrm_token": "your-token",
                   "load_vrm_idsite": 12345
               }
           }
       }
   }
```

### Common settings for load data import from file

:::{table} load::provider_settings::LoadAkkudoktor
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| loadakkudoktor_year_energy_kwh | `Optional[float]` | `rw` | `None` | Yearly energy consumption (kWh). |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "load": {
           "provider_settings": {
               "LoadAkkudoktor": {
                   "loadakkudoktor_year_energy_kwh": 40421.0
               }
           }
       }
   }
```

### Load Prediction Provider Configuration

:::{table} load::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| LoadAkkudoktor | `Optional[akkudoktoreos.prediction.loadakkudoktor.LoadAkkudoktorCommonSettings]` | `rw` | `None` | LoadAkkudoktor settings |
| LoadVrm | `Optional[akkudoktoreos.prediction.loadvrm.LoadVrmCommonSettings]` | `rw` | `None` | LoadVrm settings |
| LoadImport | `Optional[akkudoktoreos.prediction.loadimport.LoadImportCommonSettings]` | `rw` | `None` | LoadImport settings |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "load": {
           "provider_settings": {
               "LoadAkkudoktor": null,
               "LoadVrm": null,
               "LoadImport": null
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
| provider_settings | `EOS_PVFORECAST__PROVIDER_SETTINGS` | `PVForecastCommonProviderSettings` | `rw` | `required` | Provider settings |
| planes | `EOS_PVFORECAST__PLANES` | `Optional[list[akkudoktoreos.prediction.pvforecast.PVForecastPlaneSetting]]` | `rw` | `None` | Plane configuration. |
| max_planes | `EOS_PVFORECAST__MAX_PLANES` | `Optional[int]` | `rw` | `0` | Maximum number of planes that can be set |
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
           "provider_settings": {
               "PVForecastImport": null,
               "PVForecastVrm": null
           },
           "planes": [
               {
                   "surface_tilt": 10.0,
                   "surface_azimuth": 180.0,
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
                   "surface_azimuth": 90.0,
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
           "max_planes": 1
       }
   }
```

### Example Output

```{eval-rst}
.. code-block:: json

   {
       "pvforecast": {
           "provider": "PVForecastAkkudoktor",
           "provider_settings": {
               "PVForecastImport": null,
               "PVForecastVrm": null
           },
           "planes": [
               {
                   "surface_tilt": 10.0,
                   "surface_azimuth": 180.0,
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
                   "surface_azimuth": 90.0,
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
           "max_planes": 1,
           "planes_peakpower": [
               5.0,
               3.5
           ],
           "planes_azimuth": [
               180.0,
               90.0
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

### PV Forecast Plane Configuration

:::{table} pvforecast::planes::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| surface_tilt | `Optional[float]` | `rw` | `30.0` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| surface_azimuth | `Optional[float]` | `rw` | `180.0` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
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
| inverter_paco | `Optional[int]` | `rw` | `None` | AC power rating of the inverter [W]. |
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
                   "surface_azimuth": 180.0,
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
                   "surface_azimuth": 90.0,
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

### Common settings for VRM API

:::{table} pvforecast::provider_settings::PVForecastVrm
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| pvforecast_vrm_token | `str` | `rw` | `your-token` | Token for Connecting VRM API |
| pvforecast_vrm_idsite | `int` | `rw` | `12345` | VRM-Installation-ID |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "pvforecast": {
           "provider_settings": {
               "PVForecastVrm": {
                   "pvforecast_vrm_token": "your-token",
                   "pvforecast_vrm_idsite": 12345
               }
           }
       }
   }
```

### Common settings for pvforecast data import from file or JSON string

:::{table} pvforecast::provider_settings::PVForecastImport
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
               "PVForecastImport": {
                   "import_file_path": null,
                   "import_json": "{\"pvforecast_ac_power\": [0, 8.05, 352.91]}"
               }
           }
       }
   }
```

### PV Forecast Provider Configuration

:::{table} pvforecast::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| PVForecastImport | `Optional[akkudoktoreos.prediction.pvforecastimport.PVForecastImportCommonSettings]` | `rw` | `None` | PVForecastImport settings |
| PVForecastVrm | `Optional[akkudoktoreos.prediction.pvforecastvrm.PVForecastVrmCommonSettings]` | `rw` | `None` | PVForecastVrm settings |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "pvforecast": {
           "provider_settings": {
               "PVForecastImport": null,
               "PVForecastVrm": null
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
| provider | `EOS_WEATHER__PROVIDER` | `Optional[str]` | `rw` | `None` | Weather provider id of provider to be used. |
| provider_settings | `EOS_WEATHER__PROVIDER_SETTINGS` | `WeatherCommonProviderSettings` | `rw` | `required` | Provider settings |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "weather": {
           "provider": "WeatherImport",
           "provider_settings": {
               "WeatherImport": null
           }
       }
   }
```

### Common settings for weather data import from file or JSON string

:::{table} weather::provider_settings::WeatherImport
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
               "WeatherImport": {
                   "import_file_path": null,
                   "import_json": "{\"weather_temp_air\": [18.3, 17.8, 16.9]}"
               }
           }
       }
   }
```

### Weather Forecast Provider Configuration

:::{table} weather::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| WeatherImport | `Optional[akkudoktoreos.prediction.weatherimport.WeatherImportCommonSettings]` | `rw` | `None` | WeatherImport settings |
:::

#### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "weather": {
           "provider_settings": {
               "WeatherImport": null
           }
       }
   }
```

## Server Configuration

:::{table} server
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| host | `EOS_SERVER__HOST` | `Optional[str]` | `rw` | `127.0.0.1` | EOS server IP address. Defaults to 127.0.0.1. |
| port | `EOS_SERVER__PORT` | `Optional[int]` | `rw` | `8503` | EOS server IP port number. Defaults to 8503. |
| verbose | `EOS_SERVER__VERBOSE` | `Optional[bool]` | `rw` | `False` | Enable debug output |
| startup_eosdash | `EOS_SERVER__STARTUP_EOSDASH` | `Optional[bool]` | `rw` | `True` | EOS server to start EOSdash server. Defaults to True. |
| eosdash_host | `EOS_SERVER__EOSDASH_HOST` | `Optional[str]` | `rw` | `None` | EOSdash server IP address. Defaults to EOS server IP address. |
| eosdash_port | `EOS_SERVER__EOSDASH_PORT` | `Optional[int]` | `rw` | `None` | EOSdash server IP port number. Defaults to EOS server IP port number + 1. |
:::

### Example Input/Output

```{eval-rst}
.. code-block:: json

   {
       "server": {
           "host": "127.0.0.1",
           "port": 8503,
           "verbose": false,
           "startup_eosdash": true,
           "eosdash_host": "127.0.0.1",
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
           "version": "0.1.0+dev",
           "data_folder_path": null,
           "data_output_subpath": "output",
           "latitude": 52.52,
           "longitude": 13.405
       },
       "cache": {
           "subpath": "cache",
           "cleanup_interval": 300.0
       },
       "ems": {
           "startup_delay": 5.0,
           "interval": 300.0,
           "mode": "OPTIMIZATION"
       },
       "logging": {
           "console_level": "TRACE",
           "file_level": "TRACE"
       },
       "devices": {
           "batteries": [
               {
                   "device_id": "battery1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.0,
                   "max_charge_power_w": 5000,
                   "min_charge_power_w": 50,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "battery1-soc-factor",
                   "measurement_key_power_l1_w": "battery1-power-l1-w",
                   "measurement_key_power_l2_w": "battery1-power-l2-w",
                   "measurement_key_power_l3_w": "battery1-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "battery1-power-3-phase-sym-w",
                   "measurement_keys": [
                       "battery1-soc-factor",
                       "battery1-power-l1-w",
                       "battery1-power-l2-w",
                       "battery1-power-l3-w",
                       "battery1-power-3-phase-sym-w"
                   ]
               }
           ],
           "max_batteries": 1,
           "electric_vehicles": [
               {
                   "device_id": "battery1",
                   "capacity_wh": 8000,
                   "charging_efficiency": 0.88,
                   "discharging_efficiency": 0.88,
                   "levelized_cost_of_storage_kwh": 0.0,
                   "max_charge_power_w": 5000,
                   "min_charge_power_w": 50,
                   "charge_rates": "[0.  0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1. ]",
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100,
                   "measurement_key_soc_factor": "battery1-soc-factor",
                   "measurement_key_power_l1_w": "battery1-power-l1-w",
                   "measurement_key_power_l2_w": "battery1-power-l2-w",
                   "measurement_key_power_l3_w": "battery1-power-l3-w",
                   "measurement_key_power_3_phase_sym_w": "battery1-power-3-phase-sym-w",
                   "measurement_keys": [
                       "battery1-soc-factor",
                       "battery1-power-l1-w",
                       "battery1-power-l2-w",
                       "battery1-power-l3-w",
                       "battery1-power-3-phase-sym-w"
                   ]
               }
           ],
           "max_electric_vehicles": 1,
           "inverters": [],
           "max_inverters": 1,
           "home_appliances": [],
           "max_home_appliances": 1
       },
       "measurement": {
           "load_emr_keys": [
               "load0_emr"
           ],
           "grid_export_emr_keys": [
               "grid_export_emr"
           ],
           "grid_import_emr_keys": [
               "grid_import_emr"
           ],
           "pv_production_emr_keys": [
               "pv1_emr"
           ]
       },
       "optimization": {
           "horizon_hours": 24,
           "interval": 3600,
           "algorithm": "GENETIC",
           "genetic": {
               "individuals": 400,
               "generations": 400,
               "seed": null,
               "penalties": {
                   "ev_soc_miss": 10
               }
           }
       },
       "prediction": {
           "hours": 48,
           "historic_hours": 48
       },
       "elecprice": {
           "provider": "ElecPriceAkkudoktor",
           "charges_kwh": 0.21,
           "vat_rate": 1.19,
           "provider_settings": {
               "ElecPriceImport": null
           }
       },
       "feedintariff": {
           "provider": "FeedInTariffFixed",
           "provider_settings": {
               "FeedInTariffFixed": null,
               "FeedInTariffImport": null
           }
       },
       "load": {
           "provider": "LoadAkkudoktor",
           "provider_settings": {
               "LoadAkkudoktor": null,
               "LoadVrm": null,
               "LoadImport": null
           }
       },
       "pvforecast": {
           "provider": "PVForecastAkkudoktor",
           "provider_settings": {
               "PVForecastImport": null,
               "PVForecastVrm": null
           },
           "planes": [
               {
                   "surface_tilt": 10.0,
                   "surface_azimuth": 180.0,
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
                   "surface_azimuth": 90.0,
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
           "max_planes": 1
       },
       "weather": {
           "provider": "WeatherImport",
           "provider_settings": {
               "WeatherImport": null
           }
       },
       "server": {
           "host": "127.0.0.1",
           "port": 8503,
           "verbose": false,
           "startup_eosdash": true,
           "eosdash_host": "127.0.0.1",
           "eosdash_port": 8504
       },
       "utils": {}
   }
```
