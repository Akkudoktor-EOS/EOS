## PV Forecast Configuration

<!-- pyml disable line-length -->
:::{table} pvforecast
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| max_planes | `EOS_PVFORECAST__MAX_PLANES` | `int | None` | `rw` | `0` | Maximum number of planes that can be set |
| planes | `EOS_PVFORECAST__PLANES` | `list[akkudoktoreos.prediction.pvforecast.PVForecastPlaneSetting] | None` | `rw` | `None` | Plane configuration. |
| planes_azimuth | | `List[float]` | `ro` | `N/A` | Compute a list of the azimuths per active planes. |
| planes_inverter_paco | | `Any` | `ro` | `N/A` | Compute a list of the maximum power rating of the inverter per active planes. |
| planes_peakpower | | `List[float]` | `ro` | `N/A` | Compute a list of the peak power per active planes. |
| planes_tilt | | `List[float]` | `ro` | `N/A` | Compute a list of the tilts per active planes. |
| planes_userhorizon | | `Any` | `ro` | `N/A` | Compute a list of the user horizon per active planes. |
| provider | `EOS_PVFORECAST__PROVIDER` | `str | None` | `rw` | `None` | PVForecast provider id of provider to be used. |
| provider_settings | `EOS_PVFORECAST__PROVIDER_SETTINGS` | `PVForecastCommonProviderSettings` | `rw` | `required` | Provider settings |
| providers | | `list[str]` | `ro` | `N/A` | Available PVForecast provider ids. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "pvforecast": {
           "provider": "PVForecastAkkudoktor",
           "provider_settings": {
               "PVForecastImport": null,
               "PVForecastVrm": null
           },
           "planes": [
               {
                   "surface_tilt": 10.0,
                   "surface_azimuth": 180.0,
                   "userhorizon": [
                       10.0,
                       20.0,
                       30.0
                   ],
                   "peakpower": 5.0,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 0,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 6000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               },
               {
                   "surface_tilt": 20.0,
                   "surface_azimuth": 90.0,
                   "userhorizon": [
                       5.0,
                       15.0,
                       25.0
                   ],
                   "peakpower": 3.5,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 1,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 4000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               }
           ],
           "max_planes": 1
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
       "pvforecast": {
           "provider": "PVForecastAkkudoktor",
           "provider_settings": {
               "PVForecastImport": null,
               "PVForecastVrm": null
           },
           "planes": [
               {
                   "surface_tilt": 10.0,
                   "surface_azimuth": 180.0,
                   "userhorizon": [
                       10.0,
                       20.0,
                       30.0
                   ],
                   "peakpower": 5.0,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 0,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 6000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               },
               {
                   "surface_tilt": 20.0,
                   "surface_azimuth": 90.0,
                   "userhorizon": [
                       5.0,
                       15.0,
                       25.0
                   ],
                   "peakpower": 3.5,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 1,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 4000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               }
           ],
           "max_planes": 1,
           "providers": [
               "PVForecastAkkudoktor",
               "PVForecastVrm",
               "PVForecastImport"
           ],
           "planes_peakpower": [
               5.0,
               3.5
           ],
           "planes_azimuth": [
               180.0,
               90.0
           ],
           "planes_tilt": [
               10.0,
               20.0
           ],
           "planes_userhorizon": [
               [
                   10.0,
                   20.0,
                   30.0
               ],
               [
                   5.0,
                   15.0,
                   25.0
               ]
           ],
           "planes_inverter_paco": [
               6000.0,
               4000.0
           ]
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for PV forecast VRM API

<!-- pyml disable line-length -->
:::{table} pvforecast::provider_settings::PVForecastVrm
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| pvforecast_vrm_idsite | `int` | `rw` | `12345` | VRM-Installation-ID |
| pvforecast_vrm_token | `str` | `rw` | `your-token` | Token for Connecting VRM API |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "pvforecast": {
           "provider_settings": {
               "PVForecastVrm": {
                   "pvforecast_vrm_token": "your-token",
                   "pvforecast_vrm_idsite": 12345
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for pvforecast data import from file or JSON string

<!-- pyml disable line-length -->
:::{table} pvforecast::provider_settings::PVForecastImport
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| import_file_path | `str | pathlib.Path | None` | `rw` | `None` | Path to the file to import PV forecast data from. |
| import_json | `str | None` | `rw` | `None` | JSON string, dictionary of PV forecast value lists. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "pvforecast": {
           "provider_settings": {
               "PVForecastImport": {
                   "import_file_path": null,
                   "import_json": "{\"pvforecast_ac_power\": [0, 8.05, 352.91]}"
               }
           }
       }
   }
```
<!-- pyml enable line-length -->

### PV Forecast Provider Configuration

<!-- pyml disable line-length -->
:::{table} pvforecast::provider_settings
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| PVForecastImport | `akkudoktoreos.prediction.pvforecastimport.PVForecastImportCommonSettings | None` | `rw` | `None` | PVForecastImport settings |
| PVForecastVrm | `akkudoktoreos.prediction.pvforecastvrm.PVForecastVrmCommonSettings | None` | `rw` | `None` | PVForecastVrm settings |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "pvforecast": {
           "provider_settings": {
               "PVForecastImport": null,
               "PVForecastVrm": null
           }
       }
   }
```
<!-- pyml enable line-length -->

### PV Forecast Plane Configuration

<!-- pyml disable line-length -->
:::{table} pvforecast::planes::list
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| albedo | `float | None` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| inverter_model | `str | None` | `rw` | `None` | Model of the inverter of this plane. |
| inverter_paco | `int | None` | `rw` | `None` | AC power rating of the inverter [W]. |
| loss | `float | None` | `rw` | `14.0` | Sum of PV system losses in percent |
| module_model | `str | None` | `rw` | `None` | Model of the PV modules of this plane. |
| modules_per_string | `int | None` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| mountingplace | `str | None` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| optimal_surface_tilt | `bool | None` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| optimalangles | `bool | None` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| peakpower | `float | None` | `rw` | `None` | Nominal power of PV system in kW. |
| pvtechchoice | `str | None` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| strings_per_inverter | `int | None` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| surface_azimuth | `float | None` | `rw` | `180.0` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| surface_tilt | `float | None` | `rw` | `30.0` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| trackingtype | `int | None` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| userhorizon | `List[float] | None` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "pvforecast": {
           "planes": [
               {
                   "surface_tilt": 10.0,
                   "surface_azimuth": 180.0,
                   "userhorizon": [
                       10.0,
                       20.0,
                       30.0
                   ],
                   "peakpower": 5.0,
                   "pvtechchoice": "crystSi",
                   "mountingplace": "free",
                   "loss": 14.0,
                   "trackingtype": 0,
                   "optimal_surface_tilt": false,
                   "optimalangles": false,
                   "albedo": null,
                   "module_model": null,
                   "inverter_model": null,
                   "inverter_paco": 6000,
                   "modules_per_string": 20,
                   "strings_per_inverter": 2
               }
           ]
       }
   }
```
<!-- pyml enable line-length -->
