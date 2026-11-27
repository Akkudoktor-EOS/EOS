## Settings of the device-by-device optimizer

<!-- pyml disable line-length -->
:::{table} optimization
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| horizon | | `int` | `ro` | `N/A` | Number of optimization steps. |
| horizon_hours | `EOS_OPTIMIZATION__HORIZON_HOURS` | `int` | `rw` | `24` | The general time window within which the energy optimization goal shall be achieved [h]. Defaults to 24 hours. |
| interval_sec | `EOS_OPTIMIZATION__INTERVAL_SEC` | `Literal[900, 3600]` | `rw` | `3600` | The optimization interval [sec]. Defaults to 3600 seconds (1 hour) |
| keys | | `list[str]` | `ro` | `N/A` | The keys of the solution. |
| measurement_max_age_seconds | `EOS_OPTIMIZATION__MEASUREMENT_MAX_AGE_SECONDS` | `int` | `rw` | `300` | Maximum age of SoC measurements used for configuration-based optimization [s]. Without an explicit SoC or usable measurement, the initial SoC defaults to 0%. |
| penalties | `EOS_OPTIMIZATION__PENALTIES` | `dict[str, Union[float, int, str]]` | `rw` | `required` | Penalty parameters used in fitness evaluation. |
| search | `EOS_OPTIMIZATION__SEARCH` | `SearchCommonSettings` | `rw` | `required` | OHMRAKEL42 optimizer configuration. |
| self_consumption_interpolator | `EOS_OPTIMIZATION__SELF_CONSUMPTION_INTERPOLATOR` | `<enum 'GridInterpolatorBackend'>` | `rw` | `scipy` | How the inverter's self-consumption probability table is evaluated [scipy | numpy]. 'scipy' (default) uses scipy.interpolate. 'numpy' gives the same values without importing SciPy, which saves about 35 MB RSS after the first optimization run (useful on devices with little RAM); a run takes about 2.5 % longer. |
| tail_horizon_hours | `EOS_OPTIMIZATION__TAIL_HORIZON_HOURS` | `int` | `rw` | `48` | Forecast lookahead after the control horizon [h]. No tail commands are issued. Set 0 to disable. |
| terminal_value_euro_per_kwh | `EOS_OPTIMIZATION__TERMINAL_VALUE_EURO_PER_KWH` | `float` | `rw` | `0.0` | Value assigned to usable battery energy remaining at the end of the optimization horizon [EUR/kWh]. This terminal value is independent of the battery LCOS. Only used with terminal_value_mode = FIXED. Defaults to 0 EUR/kWh. |
| terminal_value_mode | `EOS_OPTIMIZATION__TERMINAL_VALUE_MODE` | `<enum 'TerminalValueMode'>` | `rw` | `AUTO` | How to value the energy left in the battery at the end of the control horizon. AUTO solves the forecast tail with an AUTO continuation proxy at its end (or only the proxy if tail is zero); FIXED uses 'terminal_value_euro_per_kwh'. Defaults to AUTO. |
| terminal_value_window_hours | `EOS_OPTIMIZATION__TERMINAL_VALUE_WINDOW_HOURS` | `int` | `rw` | `24` | Length of the trailing window at the effective tail end the AUTO continuation curve is derived from [h]. One day covers a full load and PV cycle. Defaults to 24 hours. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "optimization": {
           "interval_sec": 3600,
           "horizon_hours": 24,
           "measurement_max_age_seconds": 300,
           "tail_horizon_hours": 48,
           "terminal_value_mode": "AUTO",
           "terminal_value_euro_per_kwh": 0.0,
           "terminal_value_window_hours": 24,
           "penalties": {
               "ev_soc_miss": 10
           },
           "search": {
               "time_budget_seconds": 30.0,
               "effort": "NORMAL"
           },
           "self_consumption_interpolator": "scipy"
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
       "optimization": {
           "interval_sec": 3600,
           "horizon_hours": 24,
           "measurement_max_age_seconds": 300,
           "tail_horizon_hours": 48,
           "terminal_value_mode": "AUTO",
           "terminal_value_euro_per_kwh": 0.0,
           "terminal_value_window_hours": 24,
           "penalties": {
               "ev_soc_miss": 10
           },
           "search": {
               "time_budget_seconds": 30.0,
               "effort": "NORMAL"
           },
           "self_consumption_interpolator": "scipy",
           "horizon": 24,
           "keys": []
       }
   }
```
<!-- pyml enable line-length -->

### Effort limits of the device by device search

<!-- pyml disable line-length -->
:::{table} optimization::search
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| effort | `<enum 'SearchEffort'>` | `rw` | `NORMAL` | Search effort [LOW | NORMAL | HIGH]. Sets the resolution of the state grid and the number of passes over the devices. |
| time_budget_seconds | `Optional[float]` | `rw` | `None` | Time after which the search starts no further moves [s]. The result is never worse than the start plan; a move in progress is finished. None means no limit, the search then ends when no move improves the plan. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "optimization": {
           "search": {
               "time_budget_seconds": null,
               "effort": "NORMAL"
           }
       }
   }
```
<!-- pyml enable line-length -->
