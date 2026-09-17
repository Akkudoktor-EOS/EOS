## General Optimization Configuration

<!-- pyml disable line-length -->
:::{table} optimization
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| algorithm | `EOS_OPTIMIZATION__ALGORITHM` | `<enum 'OptimizationAlgorithm'>` | `rw` | `required` | Optimization algorithm [GENETIC | GENETIC0]. Defaults to GENETIC. |
| algorithms | | `list[str]` | `ro` | `N/A` | Available optimization algorithms. |
| genetic | `EOS_OPTIMIZATION__GENETIC` | `GeneticCommonSettings` | `rw` | `required` | GENETIC optimization algorithm configuration. |
| genetic0 | `EOS_OPTIMIZATION__GENETIC0` | `Genetic0CommonSettings` | `rw` | `required` | GENETIC0 optimization algorithm configuration. |
| keys | | `list[str]` | `ro` | `N/A` | The keys of the solution. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "optimization": {
           "algorithm": "GENETIC",
           "genetic": {
               "interval_sec": 3600,
               "horizon_hours": 24,
               "individuals": 400,
               "generations": 400,
               "seed": null,
               "measurement_max_age_seconds": 300,
               "tail_horizon_hours": 48,
               "terminal_value_mode": "AUTO",
               "terminal_value_euro_per_kwh": 0.0,
               "terminal_value_window_hours": 24,
               "penalties": {
                   "ev_soc_miss": 10
               }
           },
           "genetic0": {
               "horizon_hours": 24,
               "individuals": 400,
               "generations": 400,
               "seed": null,
               "penalties": {
                   "ev_soc_miss": 10
               }
           }
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
           "algorithm": "GENETIC",
           "genetic": {
               "interval_sec": 3600,
               "horizon_hours": 24,
               "individuals": 400,
               "generations": 400,
               "seed": null,
               "measurement_max_age_seconds": 300,
               "tail_horizon_hours": 48,
               "terminal_value_mode": "AUTO",
               "terminal_value_euro_per_kwh": 0.0,
               "terminal_value_window_hours": 24,
               "penalties": {
                   "ev_soc_miss": 10
               },
               "horizon": 24
           },
           "genetic0": {
               "horizon_hours": 24,
               "individuals": 400,
               "generations": 400,
               "seed": null,
               "penalties": {
                   "ev_soc_miss": 10
               },
               "interval_sec": 3600,
               "horizon": 24
           },
           "algorithms": [
               "GENETIC",
               "GENETIC0"
           ],
           "keys": []
       }
   }
```
<!-- pyml enable line-length -->

### GENETIC0 Optimization Algorithm Configuration

<!-- pyml disable line-length -->
:::{table} optimization::genetic0
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| generations | `Optional[int]` | `rw` | `400` | Number of generations to evolve [>= 10]. Defaults to 400. |
| horizon | `int` | `ro` | `N/A` | Number of optimization steps. |
| horizon_hours | `int` | `rw` | `24` | The general time window within which the energy optimization goal shall be achieved [h]. Defaults to 24 hours. |
| individuals | `Optional[int]` | `rw` | `300` | Number of individuals (solutions) in the population [>= 10]. Defaults to 300. |
| interval_sec | `int` | `ro` | `N/A` | The optimization interval [sec]. Fixed to 1 hour (3600 seconds). |
| penalties | `dict[str, Union[float, int, str]]` | `rw` | `required` | Penalty parameters used in fitness evaluation. |
| seed | `Optional[int]` | `rw` | `None` | Random seed for reproducibility. None = random. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "optimization": {
           "genetic0": {
               "horizon_hours": 24,
               "individuals": 300,
               "generations": 400,
               "seed": null,
               "penalties": {
                   "ev_soc_miss": 10
               }
           }
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
           "genetic0": {
               "horizon_hours": 24,
               "individuals": 300,
               "generations": 400,
               "seed": null,
               "penalties": {
                   "ev_soc_miss": 10
               },
               "interval_sec": 3600,
               "horizon": 24
           }
       }
   }
```
<!-- pyml enable line-length -->

### GENETIC Optimization Algorithm Configuration

<!-- pyml disable line-length -->
:::{table} optimization::genetic
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| generations | `Optional[int]` | `rw` | `400` | Number of generations to evolve [>= 10]. Defaults to 400. |
| horizon | `int` | `ro` | `N/A` | Number of optimization steps. |
| horizon_hours | `int` | `rw` | `24` | The general time window within which the energy optimization goal shall be achieved [h]. Defaults to 24 hours. |
| individuals | `Optional[int]` | `rw` | `300` | Number of individuals (solutions) in the population [>= 10]. Defaults to 300. |
| interval_sec | `Literal[900, 3600]` | `rw` | `3600` | The optimization interval [sec]. Defaults to 3600 seconds (1 hour) |
| measurement_max_age_seconds | `int` | `rw` | `300` | Maximum age of SoC measurements for configuration-based optimization [s]. |
| penalties | `dict[str, Union[float, int, str]]` | `rw` | `required` | Penalty parameters used in fitness evaluation. |
| seed | `Optional[int]` | `rw` | `None` | Random seed for reproducibility. None = random. |
| tail_horizon_hours | `int` | `rw` | `48` | Forecast lookahead after the control horizon [h]. No tail commands are issued. Set 0 to disable. |
| terminal_value_euro_per_kwh | `float` | `rw` | `0.0` | Value assigned to usable battery energy remaining at the end of the optimization horizon [EUR/kWh]. This terminal value is independent of the battery LCOS. Only used with terminal_value_mode = FIXED. Defaults to 0 EUR/kWh. |
| terminal_value_mode | `<enum 'TerminalValueMode'>` | `rw` | `AUTO` | How to value the energy left in the battery at the end of the control horizon. AUTO solves the forecast tail with an AUTO continuation proxy at its end (or only the proxy if tail is zero); FIXED uses 'terminal_value_euro_per_kwh'. Defaults to AUTO. |
| terminal_value_window_hours | `int` | `rw` | `24` | Length of the trailing window at the effective tail end the AUTO continuation curve is derived from [h]. One day covers a full load and PV cycle. Defaults to 24 hours. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "optimization": {
           "genetic": {
               "interval_sec": 3600,
               "horizon_hours": 24,
               "individuals": 300,
               "generations": 400,
               "seed": null,
               "measurement_max_age_seconds": 300,
               "tail_horizon_hours": 48,
               "terminal_value_mode": "AUTO",
               "terminal_value_euro_per_kwh": 0.0,
               "terminal_value_window_hours": 24,
               "penalties": {
                   "ev_soc_miss": 10
               }
           }
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
           "genetic": {
               "interval_sec": 3600,
               "horizon_hours": 24,
               "individuals": 300,
               "generations": 400,
               "seed": null,
               "measurement_max_age_seconds": 300,
               "tail_horizon_hours": 48,
               "terminal_value_mode": "AUTO",
               "terminal_value_euro_per_kwh": 0.0,
               "terminal_value_window_hours": 24,
               "penalties": {
                   "ev_soc_miss": 10
               },
               "horizon": 24
           }
       }
   }
```
<!-- pyml enable line-length -->
