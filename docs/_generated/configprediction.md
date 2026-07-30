## General Prediction Configuration

<!-- pyml disable line-length -->
:::{table} prediction
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| historic_hours | `EOS_PREDICTION__HISTORIC_HOURS` | `int | None` | `rw` | `48` | Number of hours into the past for historical predictions data |
| hours | `EOS_PREDICTION__HOURS` | `int | None` | `rw` | `48` | Number of hours into the future for predictions |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "prediction": {
           "hours": 48,
           "historic_hours": 48
       }
   }
```
<!-- pyml enable line-length -->
