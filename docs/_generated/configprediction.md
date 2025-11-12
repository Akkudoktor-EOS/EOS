## General Prediction Configuration

This class provides configuration for prediction settings, allowing users to specify
parameters such as the forecast duration (in hours).
Validators ensure each parameter is within a specified range.

Attributes:
    hours (Optional[int]): Number of hours into the future for predictions.
        Must be non-negative.
    historic_hours (Optional[int]): Number of hours into the past for historical data.
        Must be non-negative.

Validators:
    validate_hours (int): Ensures `hours` is a non-negative integer.
    validate_historic_hours (int): Ensures `historic_hours` is a non-negative integer.

<!-- pyml disable line-length -->
:::{table} prediction
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| historic_hours | `EOS_PREDICTION__HISTORIC_HOURS` | `Optional[int]` | `rw` | `48` | Number of hours into the past for historical predictions data |
| hours | `EOS_PREDICTION__HOURS` | `Optional[int]` | `rw` | `48` | Number of hours into the future for predictions |
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
