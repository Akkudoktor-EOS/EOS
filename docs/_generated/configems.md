## Energy Management Configuration

<!-- pyml disable line-length -->
:::{table} ems
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| interval | `EOS_EMS__INTERVAL` | `float` | `rw` | `300.0` | Intervall between EOS energy management runs [seconds]. |
| mode | `EOS_EMS__MODE` | `<enum 'EnergyManagementMode'>` | `rw` | `required` | Energy management mode [DISABLED | PREDICTION | OPTIMIZATION]. Defaults to DISABLED. |
| modes | | `list[str]` | `ro` | `N/A` | Available energy management modes. |
| notify_url | `EOS_EMS__NOTIFY_URL` | `Optional[str]` | `rw` | `None` | URL that receives an HTTP POST with a small JSON event after every completed optimization, so a client can fetch the new solution right away instead of polling for it. None = off. |
| start_on_interval_boundary | `EOS_EMS__START_ON_INTERVAL_BOUNDARY` | `bool` | `rw` | `False` | Start energy management runs on the wall-clock multiples of 'interval' (for 900 s: every quarter hour, matching a 15 minute market) instead of 'interval' seconds after the previous run ended. A run that takes longer than 'interval' is followed by one at the next boundary after it ended. |
| startup_delay | `EOS_EMS__STARTUP_DELAY` | `float` | `rw` | `5` | Startup delay in seconds for EOS energy management runs. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "ems": {
           "startup_delay": 5.0,
           "interval": 300.0,
           "mode": "OPTIMIZATION",
           "start_on_interval_boundary": false,
           "notify_url": null
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
       "ems": {
           "startup_delay": 5.0,
           "interval": 300.0,
           "mode": "OPTIMIZATION",
           "start_on_interval_boundary": false,
           "notify_url": null,
           "modes": [
               "DISABLED",
               "PREDICTION",
               "OPTIMIZATION"
           ]
       }
   }
```
<!-- pyml enable line-length -->
