## Optional history provider; no fixed occupancy or comfort calendar

<!-- pyml disable line-length -->
:::{table} presence
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| absence_delay_seconds | `EOS_PRESENCE__ABSENCE_DELAY_SECONDS` | `int` | `rw` | `1800` | None |
| absence_probability_threshold | `EOS_PRESENCE__ABSENCE_PROBABILITY_THRESHOLD` | `float` | `rw` | `0.2` | None |
| absence_return_hold_minutes | `EOS_PRESENCE__ABSENCE_RETURN_HOLD_MINUTES` | `int` | `rw` | `120` | After a reported absence ends, comfort is required for this long. |
| absences | `EOS_PRESENCE__ABSENCES` | `list[akkudoktoreos.prediction.presence.PresenceAbsence]` | `rw` | `required` | Reported absences (from/until); they beat the learned presence pattern. |
| allow_provisional_setback | `EOS_PRESENCE__ALLOW_PROVISIONAL_SETBACK` | `bool` | `rw` | `False` | None |
| history_days | `EOS_PRESENCE__HISTORY_DAYS` | `int` | `rw` | `56` | None |
| local_correction_seconds | `EOS_PRESENCE__LOCAL_CORRECTION_SECONDS` | `int` | `rw` | `3600` | None |
| minimum_bucket_coverage | `EOS_PRESENCE__MINIMUM_BUCKET_COVERAGE` | `float` | `rw` | `0.8` | None |
| minimum_support_days | `EOS_PRESENCE__MINIMUM_SUPPORT_DAYS` | `int` | `rw` | `7` | Minimum history for comfort reduction; provisional probabilities appear earlier. |
| provider | `EOS_PRESENCE__PROVIDER` | `Optional[Literal['PresenceHistory']]` | `rw` | `None` | None |
| recency_half_life_days | `EOS_PRESENCE__RECENCY_HALF_LIFE_DAYS` | `float` | `rw` | `21` | None |
| sample_max_age_seconds | `EOS_PRESENCE__SAMPLE_MAX_AGE_SECONDS` | `int` | `rw` | `90` | None |
| zones | `EOS_PRESENCE__ZONES` | `dict[str, akkudoktoreos.prediction.presence.PresenceZoneSettings]` | `rw` | `required` | None |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "presence": {
           "provider": null,
           "zones": {},
           "history_days": 56,
           "minimum_support_days": 7,
           "allow_provisional_setback": false,
           "sample_max_age_seconds": 90,
           "absence_delay_seconds": 1800,
           "local_correction_seconds": 3600,
           "recency_half_life_days": 21.0,
           "minimum_bucket_coverage": 0.8,
           "absence_probability_threshold": 0.2,
           "absences": [],
           "absence_return_hold_minutes": 120
       }
   }
```
<!-- pyml enable line-length -->

### A reported absence (away from ... until ...); it beats the learned pattern

``start``/``end`` are ISO date-times. A value with an offset ("+01:00", "Z") is exact; one
without is read in the installation's time zone. ``end=None`` means "until revoked": the
plan then assumes absence for its whole horizon. Revoke or shorten a running absence by
setting ``end`` to the return time and keep the entry: reported absences are also
excluded from learning for as long as they lie inside the history window.
``zone_ids`` limits the entry to some presence areas (empty = all areas).

<!-- pyml disable line-length -->
:::{table} presence::absences::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| end | `datetime.datetime | None` | `rw` | `None` | None |
| label | `str | None` | `rw` | `None` | None |
| start | `datetime` | `rw` | `required` | None |
| zone_ids | `list[str]` | `rw` | `required` | None |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "presence": {
           "absences": [
               {
                   "start": "2026-12-24 08:00:00+01:00",
                   "end": "2026-12-28 18:00:00+01:00",
                   "zone_ids": [],
                   "label": "Holiday"
               }
           ]
       }
   }
```
<!-- pyml enable line-length -->
