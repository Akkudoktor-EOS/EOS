## Measurement Configuration

<!-- pyml disable line-length -->
:::{table} measurement
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| channels | `EOS_MEASUREMENT__CHANNELS` | `dict[str, akkudoktoreos.measurement.measurement.MeasurementChannelSettings]` | `rw` | `required` | Typed raw measurement channels keyed by measurement key. |
| emr_max_gap_seconds | `EOS_MEASUREMENT__EMR_MAX_GAP_SECONDS` | `Optional[int]` | `rw` | `10800` | Longest gap between two readings of a `*_emr_keys` meter [s] whose energy is spread evenly over the gap. A longer gap has no energy value (unknown) instead. A meter with its own `channels` entry uses that entry's `max_gap_seconds`. None spreads every gap evenly. |
| energy_context_seconds | `EOS_MEASUREMENT__ENERGY_CONTEXT_SECONDS` | `int` | `rw` | `86400` | None |
| grid_export_emr_keys | `EOS_MEASUREMENT__GRID_EXPORT_EMR_KEYS` | `Optional[list[str]]` | `rw` | `None` | The keys of the measurements that are energy meter readings of energy export to grid [kWh]. |
| grid_import_emr_keys | `EOS_MEASUREMENT__GRID_IMPORT_EMR_KEYS` | `Optional[list[str]]` | `rw` | `None` | The keys of the measurements that are energy meter readings of energy import from grid [kWh]. |
| historic_hours | `EOS_MEASUREMENT__HISTORIC_HOURS` | `Optional[int]` | `rw` | `17520` | Number of hours into the past for measurement data |
| household | `EOS_MEASUREMENT__HOUSEHOLD` | `Optional[akkudoktoreos.measurement.household.HouseholdSettings]` | `rw` | `None` | Optional household energy balance definition. The input keys are measurement keys: of `measurement.keys`, of the devices (`devices.measurement_keys`) or energy meter readings of device controllers (`adapter.control.*_emr_data_keys`, stored as `<device_id>_<key>`). |
| keys | | `list[str]` | `ro` | `N/A` | The keys of the measurements that can be stored. |
| load_emr_keys | `EOS_MEASUREMENT__LOAD_EMR_KEYS` | `Optional[list[str]]` | `rw` | `None` | The keys of the measurements that are energy meter readings of a load [kWh]. |
| pv_production_emr_keys | `EOS_MEASUREMENT__PV_PRODUCTION_EMR_KEYS` | `Optional[list[str]]` | `rw` | `None` | The keys of the measurements that are PV production energy meter readings [kWh]. |
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
           "channels": {},
           "household": null,
           "energy_context_seconds": 86400,
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
           "emr_max_gap_seconds": 10800
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
           "channels": {},
           "household": null,
           "energy_context_seconds": 86400,
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
           "emr_max_gap_seconds": 10800,
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
