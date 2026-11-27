## Optional thermal settings; live control remains reserved

Runtime measurements, presence state, manual overrides and actuator targets
are intentionally not persisted in this configuration model.

<!-- pyml disable line-length -->
:::{table} thermal
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| absence_delay_seconds | `EOS_THERMAL__ABSENCE_DELAY_SECONDS` | `int` | `rw` | `1800` | Required continuous valid absence before an absence policy. |
| electric_power_block_seconds | `EOS_THERMAL__ELECTRIC_POWER_BLOCK_SECONDS` | `int | None` | `rw` | `1800` | Optional hard constant-electric-power blocks; null lets electricity follow the physical heat-pump map each slot. Not an actuator dwell time. |
| measurement_max_age_seconds | `EOS_THERMAL__MEASUREMENT_MAX_AGE_SECONDS` | `int` | `rw` | `180` | Maximum age of room measurements in seconds. |
| mode | `EOS_THERMAL__MODE` | `Literal['off', 'shadow', 'control']` | `rw` | `off` | Off preserves normal planning; shadow permits only the separate thermal API. Live control is not implemented. |
| pattern_polish_seconds | `EOS_THERMAL__PATTERN_POLISH_SECONDS` | `float` | `rw` | `0.0` | Seconds of the joint stage spent on exact local improvement of the heat-source on/off pattern (short pauses/bursts); 0 disables it (previous behaviour). |
| plan_ttl_seconds | `EOS_THERMAL__PLAN_TTL_SECONDS` | `int` | `rw` | `1800` | Configured future plan lease in seconds. |
| planning_time_limit_seconds | `EOS_THERMAL__PLANNING_TIME_LIMIT_SECONDS` | `float` | `rw` | `60.0` | Cooperative total optimization budget, including all thermal planning stages; not a hard process timeout or the native-data resolver duration. |
| presence_max_age_seconds | `EOS_THERMAL__PRESENCE_MAX_AGE_SECONDS` | `int` | `rw` | `90` | Maximum age of valid presence data in seconds. |
| solver_process_grace_seconds | `EOS_THERMAL__SOLVER_PROCESS_GRACE_SECONDS` | `float` | `rw` | `5.0` | Startup/transport allowance beyond the cooperative planning budget; then kill worker. |
| solver_process_memory_limit_mb | `EOS_THERMAL__SOLVER_PROCESS_MEMORY_LIMIT_MB` | `int` | `rw` | `1024` | Isolated solver memory limit; Linux address-space cap plus sampled process-tree RSS. |
| tracking | `EOS_THERMAL__TRACKING` | `ThermalTrackingSettings` | `rw` | `required` | Separate disabled-by-default room-target pilot; never executes the full plan. |
| zones | `EOS_THERMAL__ZONES` | `dict[str, akkudoktoreos.thermal.thermalsettings.ThermalZoneSettings]` | `rw` | `required` | None |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "thermal": {
           "mode": "off",
           "zones": {},
           "tracking": {
               "enabled": false,
               "operation": "pilot",
               "zone_id": null,
               "minimum_temperature_c": 18,
               "occupied_tolerance_c": 0.3,
               "max_increase_c": 0.5,
               "maximum_preheat_temperature_c": 30.0,
               "max_decrease_c": 0.5,
               "cold_guard_below_manual_c": 0.25,
               "measured_room_ceiling_above_manual_c": 0.5,
               "lease_seconds": 150,
               "operator_max_age_seconds": 90,
               "room_max_age_seconds": 180,
               "presence_max_age_seconds": 90,
               "source_max_age_seconds": 1800,
               "absence_delay_seconds": 1800
           },
           "planning_time_limit_seconds": 60.0,
           "electric_power_block_seconds": 1800,
           "solver_process_memory_limit_mb": 1024,
           "solver_process_grace_seconds": 5.0,
           "pattern_polish_seconds": 0.0,
           "plan_ttl_seconds": 1800,
           "absence_delay_seconds": 1800,
           "measurement_max_age_seconds": 180,
           "presence_max_age_seconds": 90
       }
   }
```
<!-- pyml enable line-length -->
