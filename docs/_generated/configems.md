## Energy Management Configuration

<!-- pyml disable line-length -->
:::{table} ems
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| interval | `EOS_EMS__INTERVAL` | `float` | `rw` | `300.0` | Intervall between EOS energy management runs [seconds]. |
| mode | `EOS_EMS__MODE` | `akkudoktoreos.core.emsettings.EnergyManagementMode | None` | `rw` | `None` | Energy management mode [OPTIMIZATION | PREDICTION]. |
| startup_delay | `EOS_EMS__STARTUP_DELAY` | `float` | `rw` | `5` | Startup delay in seconds for EOS energy management runs. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "ems": {
           "startup_delay": 5.0,
           "interval": 300.0,
           "mode": "OPTIMIZATION"
       }
   }
```
<!-- pyml enable line-length -->
