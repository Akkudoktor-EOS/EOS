## Load Prediction Configuration

<!-- pyml disable line-length -->
:::{table} load
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| provider | `EOS_LOAD__PROVIDER` | `str | None` | `rw` | `None` | Load provider id of provider to be used. |
| provider_settings | `EOS_LOAD__PROVIDER_SETTINGS` | `LoadCommonProviderSettings` | `rw` | `required` | Provider settings |
| providers | | `list[str]` | `ro` | `N/A` | Available load provider ids. |
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
           "provider_settings": {
               "LoadAkkudoktor": null,
               "LoadVrm": null,
               "LoadImport": null
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
           "provider_settings": {
               "LoadAkkudoktor": null,
               "LoadVrm": null,
               "LoadImport": null
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

### Common settings for load data import from file or JSON string

<!-- pyml disable line-length -->
:::{table} load::provider_settings::LoadImport
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| import_file_path | `str | pathlib.Path | None` | `rw` | `None` | Path to the file to import load data from. |
| import_json | `str | None` | `rw` | `None` | JSON string, dictionary of load forecast value lists. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "load": {
           "provider_settings": {
               "LoadImport": {
                   "import_file_path": null,
                   "import_json": "{\"load0_mean\": [676.71, 876.19, 527.13]}"
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for load forecast VRM API

<!-- pyml disable line-length -->
:::{table} load::provider_settings::LoadVrm
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| load_vrm_idsite | `int` | `rw` | `12345` | VRM-Installation-ID |
| load_vrm_token | `str` | `rw` | `your-token` | Token for Connecting VRM API |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "load": {
           "provider_settings": {
               "LoadVrm": {
                   "load_vrm_token": "your-token",
                   "load_vrm_idsite": 12345
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for load data import from file

<!-- pyml disable line-length -->
:::{table} load::provider_settings::LoadAkkudoktor
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| loadakkudoktor_year_energy_kwh | `float | None` | `rw` | `None` | Yearly energy consumption (kWh). |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "load": {
           "provider_settings": {
               "LoadAkkudoktor": {
                   "loadakkudoktor_year_energy_kwh": 40421.0
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### Load Prediction Provider Configuration

<!-- pyml disable line-length -->
:::{table} load::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| LoadAkkudoktor | `akkudoktoreos.prediction.loadakkudoktor.LoadAkkudoktorCommonSettings | None` | `rw` | `None` | LoadAkkudoktor settings |
| LoadImport | `akkudoktoreos.prediction.loadimport.LoadImportCommonSettings | None` | `rw` | `None` | LoadImport settings |
| LoadVrm | `akkudoktoreos.prediction.loadvrm.LoadVrmCommonSettings | None` | `rw` | `None` | LoadVrm settings |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "load": {
           "provider_settings": {
               "LoadAkkudoktor": null,
               "LoadVrm": null,
               "LoadImport": null
           }
       }
   }
```
<!-- pyml enable line-length -->
