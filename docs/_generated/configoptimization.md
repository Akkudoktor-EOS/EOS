## General Optimization Configuration

<!-- pyml disable line-length -->
:::{table} optimization
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| algorithm | `EOS_OPTIMIZATION__ALGORITHM` | `str` | `rw` | `GENETIC` | The optimization algorithm. Defaults to GENETIC |
| genetic | `EOS_OPTIMIZATION__GENETIC` | `GeneticCommonSettings` | `rw` | `required` | Genetic optimization algorithm configuration. |
| horizon | | `int` | `ro` | `N/A` | Number of optimization steps. |
| horizon_hours | `EOS_OPTIMIZATION__HORIZON_HOURS` | `int` | `rw` | `24` | The general time window within which the energy optimization goal shall be achieved [h]. Defaults to 24 hours. |
| interval | `EOS_OPTIMIZATION__INTERVAL` | `int` | `rw` | `3600` | The optimization interval (slot length) [sec]. The genetic optimizer supports 3600 (1 hour) and 900 (15 min); other values fall back to 3600. Defaults to 3600 seconds (1 hour). |
| keys | | `list[str]` | `ro` | `N/A` | The keys of the solution. |
| terminal_value_euro_per_kwh | `EOS_OPTIMIZATION__TERMINAL_VALUE_EURO_PER_KWH` | `float` | `rw` | `0.0` | Value assigned to usable battery energy remaining at the end of the optimization horizon [EUR/kWh]. This terminal value is independent of the battery LCOS. Only used with terminal_value_mode = FIXED. Defaults to 0 EUR/kWh. |
| terminal_value_mode | `EOS_OPTIMIZATION__TERMINAL_VALUE_MODE` | `<enum 'TerminalValueMode'>` | `rw` | `AUTO` | How to value the energy left in the battery at the end of the optimization horizon. AUTO derives a concave value curve from the trailing horizon window and needs no configuration; FIXED uses 'terminal_value_euro_per_kwh'. Defaults to AUTO. |
| terminal_value_window_hours | `EOS_OPTIMIZATION__TERMINAL_VALUE_WINDOW_HOURS` | `int` | `rw` | `24` | Length of the trailing horizon window the AUTO terminal value curve is derived from [h]. One day covers a full load and PV cycle. Defaults to 24 hours. |
| visualize_pdf | `EOS_OPTIMIZATION__VISUALIZE_PDF` | `bool` | `rw` | `True` | Generate the PDF visualization after each optimization run. Disable for headless setups (e.g. Node-RED integration) to save several seconds per run. Defaults to True. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "optimization": {
           "horizon_hours": 24,
           "interval": 3600,
           "algorithm": "GENETIC",
           "visualize_pdf": true,
           "terminal_value_mode": "AUTO",
           "terminal_value_euro_per_kwh": 0.0,
           "terminal_value_window_hours": 24,
           "genetic": {
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
           "horizon_hours": 24,
           "interval": 3600,
           "algorithm": "GENETIC",
           "visualize_pdf": true,
           "terminal_value_mode": "AUTO",
           "terminal_value_euro_per_kwh": 0.0,
           "terminal_value_window_hours": 24,
           "genetic": {
               "individuals": 400,
               "generations": 400,
               "seed": null,
               "penalties": {
                   "ev_soc_miss": 10
               }
           },
           "keys": [],
           "horizon": 24
       }
   }
```
<!-- pyml enable line-length -->

### General Genetic Optimization Algorithm Configuration

<!-- pyml disable line-length -->
:::{table} optimization::genetic
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| generations | `Optional[int]` | `rw` | `400` | Number of generations to evolve [>= 10]. Defaults to 400. |
| individuals | `Optional[int]` | `rw` | `300` | Number of individuals (solutions) in the population [>= 10]. Defaults to 300. |
| penalties | `dict[str, Union[float, int, str]]` | `rw` | `required` | Penalty parameters used in fitness evaluation. |
| seed | `Optional[int]` | `rw` | `None` | Random seed for reproducibility. None = random. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "optimization": {
           "genetic": {
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
