## Adapter Configuration

<!-- pyml disable line-length -->
:::{table} adapter
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| control | `EOS_ADAPTER__CONTROL` | `ControlAdapterCommonSettings` | `rw` | `required` | Device control adapter settings. |
| homeassistant | `EOS_ADAPTER__HOMEASSISTANT` | `HomeAssistantAdapterCommonSettings` | `rw` | `required` | Home Assistant adapter settings. |
| nodered | `EOS_ADAPTER__NODERED` | `NodeREDAdapterCommonSettings` | `rw` | `required` | NodeRED adapter settings. |
| provider | `EOS_ADAPTER__PROVIDER` | `Optional[list[str]]` | `rw` | `None` | List of adapter provider id(s) of provider(s) to be used. |
| providers | | `list[str]` | `ro` | `N/A` | Available adapter provider ids. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "adapter": {
           "provider": [
               "HomeAssistant"
           ],
           "control": {
               "controllers": {},
               "load_emr_data_keys": null,
               "grid_export_emr_data_keys": null,
               "grid_import_emr_data_keys": null,
               "pv_production_emr_data_keys": null,
               "device_measurement_data_keys": null
           },
           "homeassistant": {
               "config_entity_ids": null,
               "load_emr_entity_ids": null,
               "grid_export_emr_entity_ids": null,
               "grid_import_emr_entity_ids": null,
               "pv_production_emr_entity_ids": null,
               "device_measurement_entity_ids": null,
               "device_instruction_entity_ids": null,
               "solution_entity_ids": null
           },
           "nodered": {
               "host": "127.0.0.1",
               "port": 1880
           }
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
       "adapter": {
           "provider": [
               "HomeAssistant"
           ],
           "control": {
               "controllers": {},
               "load_emr_data_keys": null,
               "grid_export_emr_data_keys": null,
               "grid_import_emr_data_keys": null,
               "pv_production_emr_data_keys": null,
               "device_measurement_data_keys": null
           },
           "homeassistant": {
               "config_entity_ids": null,
               "load_emr_entity_ids": null,
               "grid_export_emr_entity_ids": null,
               "grid_import_emr_entity_ids": null,
               "pv_production_emr_entity_ids": null,
               "device_measurement_entity_ids": null,
               "device_instruction_entity_ids": null,
               "solution_entity_ids": null,
               "homeassistant_entity_ids": [],
               "eos_solution_entity_ids": [],
               "eos_device_instruction_entity_ids": []
           },
           "nodered": {
               "host": "127.0.0.1",
               "port": 1880
           },
           "providers": [
               "Control",
               "HomeAssistant",
               "NodeRED"
           ]
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for the NodeRED adapter

The Node-RED adapter sends to HTTP IN nodes.

This is the example flow:

`[HTTP In <URL>] -> [Function (parse payload)] -> [Debug] -> [HTTP Response]`

There are two URLs that are used:

- GET /eos/data_aquisition
  The GET is issued before the optimization.
- POST /eos/control_dispatch
  The POST is issued after the optimization.

<!-- pyml disable line-length -->
:::{table} adapter::nodered
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| host | `Optional[str]` | `rw` | `127.0.0.1` | Node-RED server IP address. Defaults to 127.0.0.1. |
| port | `Optional[int]` | `rw` | `1880` | Node-RED server IP port number. Defaults to 1880. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "adapter": {
           "nodered": {
               "host": "127.0.0.1",
               "port": 1880
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for the home assistant adapter

<!-- pyml disable line-length -->
:::{table} adapter::homeassistant
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| config_entity_ids | `Optional[dict[str, str]]` | `rw` | `None` | Mapping of EOS config keys to Home Assistant entity IDs.
The config key has to be given by a ‘/’-separated path
e.g. devices/batteries/battery1/profile_adjustment/nominal_capacity_wh
Devices are addressed by their device_id. The setting must exist. |
| device_instruction_entity_ids | `Optional[list[str]]` | `rw` | `None` | Entity IDs for device (resource) instructions to be updated by EOS.
The device ids (resource ids) have to be prepended by 'sensor.eos_' to build the entity_id.
E.g. The instruction for device id 'battery1' becomes the entity_id 'sensor.eos_battery1'. |
| device_measurement_entity_ids | `Optional[dict[str, str]]` | `rw` | `None` | Mapping of EOS device measurement keys to Home Assistant entity IDs. Registered keys such as 'battery1-soc-factor' are accepted; legacy underscore spellings are resolved when they uniquely match a registered key. |
| eos_device_instruction_entity_ids | `list[str]` | `ro` | `N/A` | Entity IDs for energy management instructions available at EOS. |
| eos_solution_entity_ids | `list[str]` | `ro` | `N/A` | Entity IDs for optimization solution available at EOS. |
| grid_export_emr_entity_ids | `Optional[list[str]]` | `rw` | `None` | Entity ID(s) of export to grid energy meter readings [kWh] |
| grid_import_emr_entity_ids | `Optional[list[str]]` | `rw` | `None` | Entity ID(s) of import from grid energy meter readings [kWh] |
| homeassistant_entity_ids | `list[str]` | `ro` | `N/A` | Entity IDs available at Home Assistant. |
| load_emr_entity_ids | `Optional[list[str]]` | `rw` | `None` | Entity ID(s) of load energy meter readings [kWh] |
| pv_production_emr_entity_ids | `Optional[list[str]]` | `rw` | `None` | Entity ID(s) of PV production energy meter readings [kWh] |
| solution_entity_ids | `Optional[list[str]]` | `rw` | `None` | Entity IDs for optimization solution keys to be updated by EOS.
The solution keys have to be prepended by 'sensor.eos_' to build the entity_id.
E.g. solution key 'battery1_idle_op_mode' becomes the entity_id 'sensor.eos_battery1_idle_op_mode'. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "adapter": {
           "homeassistant": {
               "config_entity_ids": {
                   "devices/batteries/battery1/profile_adjustment/nominal_capacity_wh": "sensor.battery1_capacity"
               },
               "load_emr_entity_ids": [
                   "sensor.load_energy_total_kwh"
               ],
               "grid_export_emr_entity_ids": [
                   "sensor.grid_export_energy_total_kwh"
               ],
               "grid_import_emr_entity_ids": [
                   "sensor.grid_import_energy_total_kwh"
               ],
               "pv_production_emr_entity_ids": [
                   "sensor.pv_energy_total_kwh"
               ],
               "device_measurement_entity_ids": {
                   "ev11-soc-factor": "sensor.ev11_soc_factor",
                   "battery1-soc-factor": "sensor.battery1_soc_factor"
               },
               "device_instruction_entity_ids": [
                   "sensor.eos_battery1"
               ],
               "solution_entity_ids": [
                   "sensor.eos_battery1_idle_mode_mode"
               ]
           }
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
       "adapter": {
           "homeassistant": {
               "config_entity_ids": {
                   "devices/batteries/battery1/profile_adjustment/nominal_capacity_wh": "sensor.battery1_capacity"
               },
               "load_emr_entity_ids": [
                   "sensor.load_energy_total_kwh"
               ],
               "grid_export_emr_entity_ids": [
                   "sensor.grid_export_energy_total_kwh"
               ],
               "grid_import_emr_entity_ids": [
                   "sensor.grid_import_energy_total_kwh"
               ],
               "pv_production_emr_entity_ids": [
                   "sensor.pv_energy_total_kwh"
               ],
               "device_measurement_entity_ids": {
                   "ev11-soc-factor": "sensor.ev11_soc_factor",
                   "battery1-soc-factor": "sensor.battery1_soc_factor"
               },
               "device_instruction_entity_ids": [
                   "sensor.eos_battery1"
               ],
               "solution_entity_ids": [
                   "sensor.eos_battery1_idle_mode_mode"
               ],
               "homeassistant_entity_ids": [],
               "eos_solution_entity_ids": [],
               "eos_device_instruction_entity_ids": []
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for the device control adapter

<!-- pyml disable line-length -->
:::{table} adapter::control
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| controllers | `dict[str, Annotated[Union[akkudoktoreos.adapter.controllers.sma.smaenergymeter.SmaEnergyMeterSettings, akkudoktoreos.adapter.controllers.sma.smastpe.SmaStpeSettings, akkudoktoreos.adapter.controllers.byd.bydbatterybox.BydBatteryBoxSettings], FieldInfo(annotation=NoneType, required=True, discriminator='controller_type')]]` | `rw` | `required` | Device controller settings, keyed by device_id. |
| device_measurement_data_keys | `Optional[dict[str, str]]` | `rw` | `None` | Measurements of devices fed by the controllers: maps the measurement key of a device (see `devices.measurement_keys`, e.g. the SoC of a battery) to the shared data key `<device_id>:<key>` of the reading. |
| grid_export_emr_data_keys | `Optional[list[str]]` | `rw` | `None` | Shared data keys `<device_id>:<key>` of the energy meter readings of energy export to grid [kWh] the controllers provide. Each is stored as measurement `<device_id>_<key>` and counts as a grid export energy meter reading. |
| grid_import_emr_data_keys | `Optional[list[str]]` | `rw` | `None` | Shared data keys `<device_id>:<key>` of the energy meter readings of energy import from grid [kWh] the controllers provide. Each is stored as measurement `<device_id>_<key>` and counts as a grid import energy meter reading. |
| load_emr_data_keys | `Optional[list[str]]` | `rw` | `None` | Shared data keys `<device_id>:<key>` of the load energy meter readings [kWh] the controllers provide. Each is stored as measurement `<device_id>_<key>` and counts as a load energy meter reading. |
| pv_production_emr_data_keys | `Optional[list[str]]` | `rw` | `None` | Shared data keys `<device_id>:<key>` of the PV production energy meter readings [kWh] the controllers provide. Each is stored as measurement `<device_id>_<key>` and counts as a PV production energy meter reading. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "adapter": {
           "control": {
               "controllers": {
                   "meter1": {
                       "measurement_max_age_s": 60.0,
                       "diagnostic_log_capacity": 0,
                       "controller_type": "sma:energy-meter",
                       "serial": 1900123456,
                       "interface_ip": "0.0.0.0",
                       "rejoin_after": 60.0
                   }
               },
               "load_emr_data_keys": [
                   "meter2:load_kwh"
               ],
               "grid_export_emr_data_keys": [
                   "meter1:grid_export_kwh"
               ],
               "grid_import_emr_data_keys": [
                   "meter1:grid_import_kwh"
               ],
               "pv_production_emr_data_keys": [
                   "meter3:pv_production_kwh"
               ],
               "device_measurement_data_keys": {
                   "battery1-soc-factor": "inverter1:bat_soc_factor"
               }
           }
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
       "adapter": {
           "control": {
               "controllers": {
                   "meter1": {
                       "measurement_max_age_s": 60.0,
                       "diagnostic_log_capacity": 0,
                       "controller_type": "sma:energy-meter",
                       "serial": 1900123456,
                       "interface_ip": "0.0.0.0",
                       "rejoin_after": 60.0,
                       "shared_data_keys": [
                           "grid_import_kwh",
                           "grid_export_kwh",
                           "grid_import_w",
                           "grid_export_w",
                           "grid_net_w"
                       ]
                   }
               },
               "load_emr_data_keys": [
                   "meter2:load_kwh"
               ],
               "grid_export_emr_data_keys": [
                   "meter1:grid_export_kwh"
               ],
               "grid_import_emr_data_keys": [
                   "meter1:grid_import_kwh"
               ],
               "pv_production_emr_data_keys": [
                   "meter3:pv_production_kwh"
               ],
               "device_measurement_data_keys": {
                   "battery1-soc-factor": "inverter1:bat_soc_factor"
               }
           }
       }
   }
```
<!-- pyml enable line-length -->
