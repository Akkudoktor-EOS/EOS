## Weather Forecast Configuration

<!-- pyml disable line-length -->
:::{table} weather
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| provider | `EOS_WEATHER__PROVIDER` | `str | None` | `rw` | `None` | Weather provider id of provider to be used. |
| providers | | `list[str]` | `ro` | `N/A` | Available weather provider ids. |
| weatherimport | `EOS_WEATHER__WEATHERIMPORT` | `WeatherImportCommonSettings` | `rw` | `required` | Weather import provider settings |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "weather": {
           "provider": "WeatherImport",
           "weatherimport": {
               "import_file_path": null,
               "import_json": null
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
       "weather": {
           "provider": "WeatherImport",
           "weatherimport": {
               "import_file_path": null,
               "import_json": null
           },
           "providers": [
               "BrightSky",
               "ClearOutside",
               "OpenMeteo",
               "WeatherImport"
           ]
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for weather data import from file or JSON string

<!-- pyml disable line-length -->
:::{table} weather::weatherimport
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| import_file_path | `str | pathlib.Path | None` | `rw` | `None` | Path to the file to import weather data from. |
| import_json | `str | None` | `rw` | `None` | JSON string, dictionary of weather forecast value lists. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "weather": {
           "weatherimport": {
               "import_file_path": null,
               "import_json": "{\"weather_temp_air\": [18.3, 17.8, 16.9]}"
           }
       }
   }
```
<!-- pyml enable line-length -->
