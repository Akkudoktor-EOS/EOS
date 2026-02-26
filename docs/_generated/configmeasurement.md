## Measurement Configuration

<!-- pyml disable line-length -->
:::{table} measurement
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| grid_export_emr_keys | `EOS_MEASUREMENT__GRID_EXPORT_EMR_KEYS` | `list[str] | None` | `rw` | `None` | The keys of the measurements that are energy meter readings of energy export to grid [kWh]. |
| grid_import_emr_keys | `EOS_MEASUREMENT__GRID_IMPORT_EMR_KEYS` | `list[str] | None` | `rw` | `None` | The keys of the measurements that are energy meter readings of energy import from grid [kWh]. |
| historic_hours | `EOS_MEASUREMENT__HISTORIC_HOURS` | `int | None` | `rw` | `17520` | Number of hours into the past for measurement data |
| keys | | `list[str]` | `ro` | `N/A` | The keys of the measurements that can be stored. |
| load_emr_keys | `EOS_MEASUREMENT__LOAD_EMR_KEYS` | `list[str] | None` | `rw` | `None` | The keys of the measurements that are energy meter readings of a load [kWh]. |
| pv_production_emr_keys | `EOS_MEASUREMENT__PV_PRODUCTION_EMR_KEYS` | `list[str] | None` | `rw` | `None` | The keys of the measurements that are PV production energy meter readings [kWh]. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "measurement": {
           "historic_hours": 17520,
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
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "measurement": {
           "historic_hours": 17520,
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
<!-- pyml enable line-length -->
