## Settings for common configuration

General configuration to set directories of cache and output files and system location (latitude
and longitude).
Validators ensure each parameter is within a specified range. A computed property, `timezone`,
determines the time zone based on latitude and longitude.

Attributes:
    latitude (Optional[float]): Latitude in degrees, must be between -90 and 90.
    longitude (Optional[float]): Longitude in degrees, must be between -180 and 180.

Properties:
    timezone (Optional[str]): Computed time zone string based on the specified latitude
        and longitude.

<!-- pyml disable line-length -->
:::{table} general
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| config_file_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | None |
| config_folder_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | None |
| data_folder_path | `EOS_GENERAL__DATA_FOLDER_PATH` | `Optional[pathlib.Path]` | `rw` | `None` | Path to EOS data directory. |
| data_output_path | | `Optional[pathlib.Path]` | `ro` | `N/A` | None |
| data_output_subpath | `EOS_GENERAL__DATA_OUTPUT_SUBPATH` | `Optional[pathlib.Path]` | `rw` | `output` | Sub-path for the EOS output data directory. |
| latitude | `EOS_GENERAL__LATITUDE` | `Optional[float]` | `rw` | `52.52` | Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°) |
| longitude | `EOS_GENERAL__LONGITUDE` | `Optional[float]` | `rw` | `13.405` | Longitude in decimal degrees, within -180 to 180 (°) |
| timezone | | `Optional[str]` | `ro` | `N/A` | None |
| version | `EOS_GENERAL__VERSION` | `str` | `rw` | `0.2.0+dev` | Configuration file version. Used to check compatibility. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "general": {
           "version": "0.2.0+dev",
           "data_folder_path": null,
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
           "version": "0.2.0+dev",
           "data_folder_path": null,
           "data_output_subpath": "output",
           "latitude": 52.52,
           "longitude": 13.405,
           "timezone": "Europe/Berlin",
           "data_output_path": null,
           "config_folder_path": "/home/user/.config/net.akkudoktoreos.net",
           "config_file_path": "/home/user/.config/net.akkudoktoreos.net/EOS.config.json"
       }
   }
```
<!-- pyml enable line-length -->
