## Base configuration for devices simulation settings

<!-- pyml disable line-length -->
:::{table} devices
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| batteries | `EOS_DEVICES__BATTERIES` | `Optional[list[akkudoktoreos.devices.devices.BatteriesCommonSettings]]` | `rw` | `None` | List of battery devices |
| electric_vehicles | `EOS_DEVICES__ELECTRIC_VEHICLES` | `Optional[list[akkudoktoreos.devices.devices.BatteriesCommonSettings]]` | `rw` | `None` | List of electric vehicle devices |
| home_appliances | `EOS_DEVICES__HOME_APPLIANCES` | `Optional[list[akkudoktoreos.devices.devices.HomeApplianceCommonSettings]]` | `rw` | `None` | List of home appliances |
| inverters | `EOS_DEVICES__INVERTERS` | `Optional[list[akkudoktoreos.devices.devices.InverterCommonSettings]]` | `rw` | `None` | List of inverters |
| max_batteries | `EOS_DEVICES__MAX_BATTERIES` | `Optional[int]` | `rw` | `None` | Maximum number of batteries that can be set |
| max_electric_vehicles | `EOS_DEVICES__MAX_ELECTRIC_VEHICLES` | `Optional[int]` | `rw` | `None` | Maximum number of electric vehicles that can be set |
| max_home_appliances | `EOS_DEVICES__MAX_HOME_APPLIANCES` | `Optional[int]` | `rw` | `None` | Maximum number of home_appliances that can be set |
| max_inverters | `EOS_DEVICES__MAX_INVERTERS` | `Optional[int]` | `rw` | `None` | Maximum number of inverters that can be set |
| measurement_keys | | `Optional[list[str]]` | `ro` | `N/A` | Return the measurement keys for the resource/ device stati that are measurements. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
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
                   "charge_rates": [
                       0.0,
                       0.1,
                       0.2,
                       0.3,
                       0.4,
                       0.5,
                       0.6,
                       0.7,
                       0.8,
                       0.9,
                       1.0
                   ],
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100
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
                   "charge_rates": [
                       0.0,
                       0.1,
                       0.2,
                       0.3,
                       0.4,
                       0.5,
                       0.6,
                       0.7,
                       0.8,
                       0.9,
                       1.0
                   ],
                   "min_soc_percentage": 0,
                   "max_soc_percentage": 100
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
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
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
                   "charge_rates": [
                       0.0,
                       0.1,
                       0.2,
                       0.3,
                       0.4,
                       0.5,
                       0.6,
                       0.7,
                       0.8,
                       0.9,
                       1.0
                   ],
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
                   "charge_rates": [
                       0.0,
                       0.1,
                       0.2,
                       0.3,
                       0.4,
                       0.5,
                       0.6,
                       0.7,
                       0.8,
                       0.9,
                       1.0
                   ],
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
<!-- pyml enable line-length -->

### Inverter devices base settings

<!-- pyml disable line-length -->
:::{table} devices::inverters::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| ac_to_dc_efficiency | `float` | `rw` | `1.0` | Efficiency of AC to DC conversion for grid-to-battery AC charging (0-1). Set to 0 to disable AC charging. Default 1.0 (no additional inverter loss). |
| battery_id | `Optional[str]` | `rw` | `None` | ID of battery controlled by this inverter. |
| dc_to_ac_efficiency | `float` | `rw` | `1.0` | Efficiency of DC to AC conversion for battery discharging to AC load/grid (0-1). Default 1.0 (no additional inverter loss). |
| device_id | `str` | `rw` | `<unknown>` | ID of device |
| max_ac_charge_power_w | `Optional[float]` | `rw` | `None` | Maximum AC charging power in watts. null means no additional limit. Set to 0 to disable AC charging. |
| max_power_w | `Optional[float]` | `rw` | `None` | Maximum power [W]. |
| measurement_keys | `Optional[list[str]]` | `ro` | `N/A` | Measurement keys for the inverter stati that are measurements. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "devices": {
           "inverters": [
               {
                   "device_id": "battery1",
                   "max_power_w": 10000.0,
                   "battery_id": null,
                   "ac_to_dc_efficiency": 0.95,
                   "dc_to_ac_efficiency": 0.95,
                   "max_ac_charge_power_w": null
               }
           ]
       }
   }
```
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "devices": {
           "inverters": [
               {
                   "device_id": "battery1",
                   "max_power_w": 10000.0,
                   "battery_id": null,
                   "ac_to_dc_efficiency": 0.95,
                   "dc_to_ac_efficiency": 0.95,
                   "max_ac_charge_power_w": null,
                   "measurement_keys": []
               }
           ]
       }
   }
```
<!-- pyml enable line-length -->

### Model defining a daily or date time window with optional localization support

Represents a time interval starting at `start_time` and lasting for `duration`.
Can restrict applicability to a specific day of the week or a specific calendar date.
Supports day names in multiple languages via locale-aware parsing.

Timezone contract:

``start_time`` is always **naive** (no ``tzinfo``).  It is interpreted as a
local wall-clock time in whatever timezone the caller's ``date_time`` or
``reference_date`` carries.  When those arguments are timezone-aware the
window boundaries are evaluated in that timezone; when they are naive,
arithmetic is performed as-is (no timezone conversion occurs).

``date``, being a calendar ``Date`` object, is inherently timezone-free.

This design avoids the ambiguity that arises when a stored ``start_time``
carries its own timezone that differs from the caller's timezone, and keeps
the model serialisable without timezone state.

<!-- pyml disable line-length -->
:::{table} devices::home_appliances::list::time_windows::windows::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| date | `Optional[pydantic_extra_types.pendulum_dt.Date]` | `rw` | `None` | Optional specific calendar date for the time window. Naive — matched against the local date of the datetime passed to contains(). Overrides `day_of_week` if set. |
| day_of_week | `Union[int, str, NoneType]` | `rw` | `None` | Optional day of the week restriction. Can be specified as integer (0=Monday to 6=Sunday) or localized weekday name. If None, applies every day unless `date` is set. |
| duration | `Duration` | `rw` | `required` | Duration of the time window starting from `start_time`. |
| locale | `Optional[str]` | `rw` | `None` | Locale used to parse weekday names in `day_of_week` when given as string. If not set, Pendulum's default locale is used. Examples: 'en', 'de', 'fr', etc. |
| start_time | `Time` | `rw` | `required` | Naive start time of the time window (time of day, no timezone). Interpreted in the timezone of the datetime passed to contains() or earliest_start_time(). |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "devices": {
           "home_appliances": [
               {
                   "time_windows": {
                       "windows": [
                           {
                               "start_time": "00:00:00.000000",
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
<!-- pyml enable line-length -->

### Model representing a sequence of time windows with collective operations

Manages multiple TimeWindow objects and provides methods to work with them
as a cohesive unit for scheduling and availability checking.

<!-- pyml disable line-length -->
:::{table} devices::home_appliances::list::time_windows
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| windows | `list[akkudoktoreos.config.configabc.TimeWindow]` | `rw` | `required` | List of TimeWindow objects that make up this sequence. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "devices": {
           "home_appliances": [
               {
                   "time_windows": {
                       "windows": []
                   }
               }
           ]
       }
   }
```
<!-- pyml enable line-length -->

### Home Appliance devices base settings

<!-- pyml disable line-length -->
:::{table} devices::home_appliances::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| consumption_wh | `int` | `rw` | `required` | Energy consumption [Wh]. |
| device_id | `str` | `rw` | `<unknown>` | ID of device |
| duration_h | `int` | `rw` | `required` | Usage duration in hours [0 ... 24]. |
| measurement_keys | `Optional[list[str]]` | `ro` | `N/A` | Measurement keys for the home appliance stati that are measurements. |
| time_windows | `Optional[akkudoktoreos.config.configabc.TimeWindowSequence]` | `rw` | `None` | Sequence of allowed time windows. Defaults to optimization general time window. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
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
                               "start_time": "10:00:00.000000",
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
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
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
                               "start_time": "10:00:00.000000",
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
<!-- pyml enable line-length -->

### Battery devices base settings

<!-- pyml disable line-length -->
:::{table} devices::batteries::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| capacity_wh | `int` | `rw` | `8000` | Capacity [Wh]. |
| charge_rates | `Optional[list[float]]` | `rw` | `[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]` | Charge rates as factor of maximum charging power [0.00 ... 1.00]. None triggers fallback to default charge-rates. |
| charging_efficiency | `float` | `rw` | `0.88` | Charging efficiency [0.01 ... 1.00]. |
| device_id | `str` | `rw` | `<unknown>` | ID of device |
| discharging_efficiency | `float` | `rw` | `0.88` | Discharge efficiency [0.01 ... 1.00]. |
| levelized_cost_of_storage_kwh | `float` | `rw` | `0.0` | Levelized cost of storage (LCOS), the average lifetime cost of delivering one kWh [€/kWh]. |
| max_charge_power_w | `Optional[float]` | `rw` | `5000` | Maximum charging power [W]. |
| max_soc_percentage | `int` | `rw` | `100` | Maximum state of charge (SOC) as percentage of capacity [%]. |
| measurement_key_power_3_phase_sym_w | `str` | `ro` | `N/A` | Measurement key for the symmetric 3 phase power the battery is charged or discharged with [W]. |
| measurement_key_power_l1_w | `str` | `ro` | `N/A` | Measurement key for the L1 power the battery is charged or discharged with [W]. |
| measurement_key_power_l2_w | `str` | `ro` | `N/A` | Measurement key for the L2 power the battery is charged or discharged with [W]. |
| measurement_key_power_l3_w | `str` | `ro` | `N/A` | Measurement key for the L3 power the battery is charged or discharged with [W]. |
| measurement_key_soc_factor | `str` | `ro` | `N/A` | Measurement key for the battery state of charge (SoC) as factor of total capacity [0.0 ... 1.0]. |
| measurement_keys | `Optional[list[str]]` | `ro` | `N/A` | Measurement keys for the battery stati that are measurements. |
| min_charge_power_w | `Optional[float]` | `rw` | `50` | Minimum charging power [W]. |
| min_soc_percentage | `int` | `rw` | `0` | Minimum state of charge (SOC) as percentage of capacity [%]. This is the target SoC for charging |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
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
                   "charge_rates": [
                       0.0,
                       0.25,
                       0.5,
                       0.75,
                       1.0
                   ],
                   "min_soc_percentage": 10,
                   "max_soc_percentage": 100
               }
           ]
       }
   }
```
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
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
                   "charge_rates": [
                       0.0,
                       0.25,
                       0.5,
                       0.75,
                       1.0
                   ],
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
               }
           ]
       }
   }
```
<!-- pyml enable line-length -->
