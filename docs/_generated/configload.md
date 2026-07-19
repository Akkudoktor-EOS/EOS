## Load Prediction Configuration

<!-- pyml disable line-length -->
:::{table} load
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| loadakkudoktor | `EOS_LOAD__LOADAKKUDOKTOR` | `LoadAkkudoktorCommonSettings` | `rw` | `required` | LoadAkkudoktor provider settings. |
| loadimport | `EOS_LOAD__LOADIMPORT` | `LoadImportCommonSettings` | `rw` | `required` | LoadImport provider settings. |
| provider | `EOS_LOAD__PROVIDER` | `Optional[str]` | `rw` | `None` | Load provider id of provider to be used. |
| providers | | `list[str]` | `ro` | `N/A` | Available load provider ids. |
| vrm | `EOS_LOAD__VRM` | `LoadVrmCommonSettings` | `rw` | `required` | Victron Remote Management (VRM) provider settings. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "load": {
           "provider": "LoadAkkudoktor",
           "loadakkudoktor": {
               "loadakkudoktor_year_energy_kwh": null
           },
           "loadimport": {
               "import_file_path": null,
               "import_json": null
           },
           "vrm": {
               "token": "your-token",
               "site_id": 12345
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
       "load": {
           "provider": "LoadAkkudoktor",
           "loadakkudoktor": {
               "loadakkudoktor_year_energy_kwh": null
           },
           "loadimport": {
               "import_file_path": null,
               "import_json": null
           },
           "vrm": {
               "token": "your-token",
               "site_id": 12345
           },
           "providers": [
               "LoadAkkudoktor",
               "LoadAkkudoktorAdjusted",
               "LoadVrm",
               "LoadImport"
           ]
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for load forecast VRM API

<!-- pyml disable line-length -->
:::{table} load::vrm
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| site_id | `int` | `rw` | `12345` | VRM-Installation-ID |
| token | `str` | `rw` | `your-token` | Access token for connecting to the Victron Remote Management (VRM) API |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "load": {
           "vrm": {
               "token": "your-token",
               "site_id": 12345
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for load data import from file or JSON string

<!-- pyml disable line-length -->
:::{table} load::loadimport
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| import_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import load data from. |
| import_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of load forecast value lists. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "load": {
           "loadimport": {
               "import_file_path": null,
               "import_json": "{\"loadforecast_power_w\": [676.71, 876.19, 527.13]}"
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for load data import from file

<!-- pyml disable line-length -->
:::{table} load::loadakkudoktor
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| loadakkudoktor_year_energy_kwh | `Optional[float]` | `rw` | `None` | Yearly energy consumption (kWh). |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "load": {
           "loadakkudoktor": {
               "loadakkudoktor_year_energy_kwh": 40421.0
           }
       }
   }
```
<!-- pyml enable line-length -->
