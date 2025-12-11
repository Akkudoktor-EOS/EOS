## Adapter Configuration

<!-- pyml disable line-length -->
:::{table} adapter
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| homeassistant | `EOS_ADAPTER__HOMEASSISTANT` | `HomeAssistantAdapterCommonSettings` | `rw` | `required` | Home Assistant adapter settings. |
| nodered | `EOS_ADAPTER__NODERED` | `NodeREDAdapterCommonSettings` | `rw` | `required` | NodeRED adapter settings. |
| provider | `EOS_ADAPTER__PROVIDER` | `Union[Literal['HomeAssistant', 'NodeRED'], list[Literal['HomeAssistant', 'NodeRED']], NoneType]` | `rw` | `None` | Adapter provider id(s) of provider(s) to be used [HomeAssistant, NodeRED, None]. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "adapter": {
           "provider": "HomeAssistant",
           "homeassistant": {
               "entity_id_pv_production_emr_kwh": null,
               "entity_id_battery_soc_factor": null,
               "entity_id_ev_soc_factor": null,
               "measurement_entity_ids": {},
               "optimization_solution_entity_ids": {}
           },
           "nodered": {
               "host": "127.0.0.1",
               "port": 1880
           }
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
| entity_id_battery_soc_factor | `Optional[list[str]]` | `rw` | `None` | Entity ID(s) of battery SoC factor [0.0..1.0] |
| entity_id_ev_soc_factor | `Optional[list[str]]` | `rw` | `None` | Entity ID(s) of electric vehicle battery SoC factor [0.0..1.0] |
| entity_id_pv_production_emr_kwh | `Optional[list[str]]` | `rw` | `None` | Entity ID(s) of PV production energy meter reading [kWh] |
| measurement_entity_ids | `HomeAssistantEntityIdMapping` | `rw` | `required` | Mapping of EOS measurement keys to Home Assistant entity IDs |
| optimization_solution_entity_ids | `HomeAssistantEntityIdMapping` | `rw` | `required` | Mapping of EOS optimization solution to Home Assistant entity IDs |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "adapter": {
           "homeassistant": {
               "entity_id_pv_production_emr_kwh": [
                   "sensor.pv_energy_total_kwh"
               ],
               "entity_id_battery_soc_factor": [
                   "sensor.battery_soc_factor"
               ],
               "entity_id_ev_soc_factor": [
                   "sensor.ev_soc_factor"
               ],
               "measurement_entity_ids": {
                   "pv_production": "sensor.pv_energy_total_kwh",
                   "battery_soc": "sensor.battery_state_of_charge",
                   "grid_import": "sensor.grid_import_kwh",
                   "grid_export": "sensor.grid_export_kwh"
               },
               "optimization_solution_entity_ids": {
                   "battery_operation_mode_id": "sensor.battery_operation_mode_id",
                   "battery_operation_mode_factor": "sensor.battery_operation_mode_factor"
               }
           }
       }
   }
```
<!-- pyml enable line-length -->
