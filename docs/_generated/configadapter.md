## Adapter Configuration

<!-- pyml disable line-length -->
:::{table} adapter
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| homeassistant | `EOS_ADAPTER__HOMEASSISTANT` | `HomeAssistantAdapterCommonSettings` | `rw` | `required` | Home Assistant adapter settings. |
| nodered | `EOS_ADAPTER__NODERED` | `NodeREDAdapterCommonSettings` | `rw` | `required` | NodeRED adapter settings. |
| provider | `EOS_ADAPTER__PROVIDER` | `Optional[list[str]]` | `rw` | `None` | List of adapter provider id(s) of provider(s) to be used. |
| providers | | `list[str]` | `ro` | `N/A` | Available electricity price provider ids. |
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

[HTTP In \\<URL\\>] -> [Function (parse payload)] -> [Debug] -> [HTTP Response]

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
e.g. devices/batteries/0/capacity_wh |
| device_instruction_entity_ids | `Optional[list[str]]` | `rw` | `None` | Entity IDs for device (resource) instructions to be updated by EOS.
The device ids (resource ids) have to be prepended by 'sensor.eos_' to build the entity_id.
E.g. The instruction for device id 'battery1' becomes the entity_id 'sensor.eos_battery1'. |
| device_measurement_entity_ids | `Optional[dict[str, str]]` | `rw` | `None` | Mapping of EOS measurement keys used by device (resource) simulations to Home Assistant entity IDs. |
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
                   "devices/batteries/0/capacity_wh": "sensor.battery1_capacity"
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
                   "ev11_soc_factor": "sensor.ev11_soc_factor",
                   "battery1_soc_factor": "sensor.battery1_soc_factor"
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
                   "devices/batteries/0/capacity_wh": "sensor.battery1_capacity"
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
                   "ev11_soc_factor": "sensor.ev11_soc_factor",
                   "battery1_soc_factor": "sensor.battery1_soc_factor"
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
