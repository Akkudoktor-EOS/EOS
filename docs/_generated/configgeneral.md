## General settings

<!-- pyml disable line-length -->
:::{table} general
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| config_file_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Path to EOS configuration file. |
| config_folder_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Path to EOS configuration directory. |
| data_folder_path | `EOS_GENERAL__DATA_FOLDER_PATH` | `Path` | `rw` | `required` | Path to EOS data folder. |
| data_output_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | Computed data_output_path based on data_folder_path. |
| data_output_subpath | `EOS_GENERAL__DATA_OUTPUT_SUBPATH` | `Optional[pathlib.Path]` | `rw` | `output` | Sub-path for the EOS output data folder. |
| home_assistant_addon | `EOS_GENERAL__HOME_ASSISTANT_ADDON` | `bool` | `rw` | `required` | EOS is running as home assistant add-on. |
| latitude | `EOS_GENERAL__LATITUDE` | `Optional[float]` | `rw` | `52.52` | Latitude in decimal degrees between -90 and 90. North is positive (ISO 19115) (°) |
| longitude | `EOS_GENERAL__LONGITUDE` | `Optional[float]` | `rw` | `13.405` | Longitude in decimal degrees within -180 to 180 (°) |
| timezone | | `Optional[str]` | `ro` | `N/A` | Computed timezone based on latitude and longitude. |
| version | `EOS_GENERAL__VERSION` | `str` | `rw` | `0.2.0.dev2602240695620513` | Configuration file version. Used to check compatibility. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "general": {
           "version": "0.2.0.dev2602240695620513",
           "data_folder_path": "/home/user/.local/share/net.akkudoktoreos.net",
           "data_output_subpath": "output",
           "latitude": 52.52,
           "longitude": 13.405
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
       "general": {
           "version": "0.2.0.dev2602240695620513",
           "data_folder_path": "/home/user/.local/share/net.akkudoktoreos.net",
           "data_output_subpath": "output",
           "latitude": 52.52,
           "longitude": 13.405,
           "timezone": "Europe/Berlin",
           "data_output_path": "/home/user/.local/share/net.akkudoktoreos.net/output",
           "config_folder_path": "/home/user/.config/net.akkudoktoreos.net",
           "config_file_path": "/home/user/.config/net.akkudoktoreos.net/EOS.config.json"
       }
   }
```
<!-- pyml enable line-length -->
