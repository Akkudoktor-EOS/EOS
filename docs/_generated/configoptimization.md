## General Optimization Configuration

<!-- pyml disable line-length -->
:::{table} optimization
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| algorithm | `EOS_OPTIMIZATION__ALGORITHM` | `str | None` | `rw` | `GENETIC` | The optimization algorithm. |
| genetic | `EOS_OPTIMIZATION__GENETIC` | `akkudoktoreos.optimization.optimization.GeneticCommonSettings | None` | `rw` | `None` | Genetic optimization algorithm configuration. |
| horizon_hours | `EOS_OPTIMIZATION__HORIZON_HOURS` | `int | None` | `rw` | `24` | The general time window within which the energy optimization goal shall be achieved [h]. Defaults to 24 hours. |
| interval | `EOS_OPTIMIZATION__INTERVAL` | `int | None` | `rw` | `3600` | The optimization interval [sec]. |
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
           "horizon_hours": 24,
           "interval": 3600,
           "algorithm": "GENETIC",
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
           "genetic": {
               "individuals": 400,
               "generations": 400,
               "seed": null,
               "penalties": {
                   "ev_soc_miss": 10
               }
           },
           "keys": []
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
| generations | `int | None` | `rw` | `400` | Number of generations to evaluate the optimal solution [>= 10]. Defaults to 400. |
| individuals | `int | None` | `rw` | `300` | Number of individuals (solutions) to generate for the (initial) generation [>= 10]. Defaults to 300. |
| penalties | `dict[str, float | int | str] | None` | `rw` | `None` | A dictionary of penalty function parameters consisting of a penalty function parameter name and the associated value. |
| seed | `int | None` | `rw` | `None` | Fixed seed for genetic algorithm. Defaults to 'None' which means random seed. |
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
