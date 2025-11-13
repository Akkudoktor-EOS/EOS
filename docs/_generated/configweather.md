## Weather Forecast Configuration

<!-- pyml disable line-length -->
:::{table} weather
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| provider | `EOS_WEATHER__PROVIDER` | `Optional[str]` | `rw` | `None` | Weather provider id of provider to be used. |
| provider_settings | `EOS_WEATHER__PROVIDER_SETTINGS` | `WeatherCommonProviderSettings` | `rw` | `required` | Provider settings |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "weather": {
           "provider": "WeatherImport",
           "provider_settings": {
               "WeatherImport": null
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for weather data import from file or JSON string

<!-- pyml disable line-length -->
:::{table} weather::provider_settings::WeatherImport
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| import_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import weather data from. |
| import_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of weather forecast value lists. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "weather": {
           "provider_settings": {
               "WeatherImport": {
                   "import_file_path": null,
                   "import_json": "{\"weather_temp_air\": [18.3, 17.8, 16.9]}"
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### Weather Forecast Provider Configuration

<!-- pyml disable line-length -->
:::{table} weather::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| WeatherImport | `Optional[akkudoktoreos.prediction.weatherimport.WeatherImportCommonSettings]` | `rw` | `None` | WeatherImport settings |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "weather": {
           "provider_settings": {
               "WeatherImport": null
           }
       }
   }
```
<!-- pyml enable line-length -->
