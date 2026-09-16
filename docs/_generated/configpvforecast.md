## PV Forecast Configuration

<!-- pyml disable line-length -->
:::{table} pvforecast
:widths: 10 20 10 5 5 30
:align: left

| Name | Environment Variable | Type | Read-Only | Default | Description |
| ---- | -------------------- | ---- | --------- | ------- | ----------- |
| akkudoktor | `EOS_PVFORECAST__AKKUDOKTOR` | `PVForecastAkkudoktorLocalCommonSettings` | `rw` | `required` | Akkudoktor forecast backend and local calibration settings |
| forecastsolar | `EOS_PVFORECAST__FORECASTSOLAR` | `PVForecastForecastSolarCommonSettings` | `rw` | `required` | ForecastSolar provider settings |
| homeassistant | `EOS_PVFORECAST__HOMEASSISTANT` | `PVForecastHomeAssistantCommonSettings` | `rw` | `required` | Home Assistant provider settings |
| max_planes | `EOS_PVFORECAST__MAX_PLANES` | `Optional[int]` | `rw` | `0` | Maximum number of planes that can be set |
| planes | `EOS_PVFORECAST__PLANES` | `Optional[list[akkudoktoreos.prediction.pvforecast.PVForecastPlaneSetting]]` | `rw` | `None` | Plane configuration. |
| planes_azimuth | | `List[float]` | `ro` | `N/A` | Compute a list of the azimuths per active planes. |
| planes_inverter_paco | | `Any` | `ro` | `N/A` | Compute a list of the maximum power rating of the inverter per active planes. |
| planes_peakpower | | `List[float]` | `ro` | `N/A` | Compute a list of the peak power per active planes. |
| planes_tilt | | `List[float]` | `ro` | `N/A` | Compute a list of the tilts per active planes. |
| planes_userhorizon | | `Any` | `ro` | `N/A` | Compute a list of the user horizon per active planes. |
| provider | `EOS_PVFORECAST__PROVIDER` | `Optional[str]` | `rw` | `None` | PVForecast provider id of provider to be used. |
| providers | | `list[str]` | `ro` | `N/A` | Available PVForecast provider ids. |
| pvforecastimport | `EOS_PVFORECAST__PVFORECASTIMPORT` | `PVForecastImportCommonSettings` | `rw` | `required` | PV forecast import provider settings |
| pvlib | `EOS_PVFORECAST__PVLIB` | `PVForecastPVLibCommonSettings` | `rw` | `required` | PVLib provider settings |
| pvnode | `EOS_PVFORECAST__PVNODE` | `PVForecastPVNodeCommonSettings` | `rw` | `required` | PVNode provider settings |
| solcast | `EOS_PVFORECAST__SOLCAST` | `PVForecastSolcastCommonSettings` | `rw` | `required` | Solcast provider settings |
| vrm | `EOS_PVFORECAST__VRM` | `PVForecastVrmCommonSettings` | `rw` | `required` | Victron Remote Management (VRM) provider settings |
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
           "akkudoktor": {
               "backend": "remote",
               "resolution_minutes": 15,
               "forecast_days": null,
               "past_days": null,
               "weather_models": [
                   "best_match"
               ],
               "transposition_model": "perez",
               "albedo": 0.25,
               "inverter_efficiency": 0.96,
               "temperature_coefficient": -0.36,
               "apply_iam": true,
               "shift_to_interval_start": true,
               "calibration_enabled": false,
               "calibration_days": 30,
               "calibration_reference_days": 30,
               "calibration_outage_filter_enabled": true,
               "calibration_outage_threshold": 0.55,
               "calibration_min_healthy_days": 3,
               "calibration_azimuth_bin_degrees": 45,
               "calibration_prior_kwh": 5.0,
               "calibration_min_factor": 0.5,
               "calibration_max_factor": 1.5
           },
           "pvforecastimport": {
               "import_file_path": null,
               "import_json": null
           },
           "vrm": {
               "token": "your-token",
               "site_id": 12345
           },
           "homeassistant": {
               "entity_id": "sensor.pv_forecast",
               "attribute": "forecast",
               "datetime_key": "datetime",
               "value_key": "watts",
               "value_unit": "W",
               "base_url": null,
               "token": null
           },
           "pvlib": {},
           "pvnode": {
               "api_key": "",
               "site_id": null,
               "forecast_days": 2
           },
           "forecastsolar": {
               "api_key": null
           },
           "solcast": {
               "api_key": "",
               "site_id": ""
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
           "akkudoktor": {
               "backend": "remote",
               "resolution_minutes": 15,
               "forecast_days": null,
               "past_days": null,
               "weather_models": [
                   "best_match"
               ],
               "transposition_model": "perez",
               "albedo": 0.25,
               "inverter_efficiency": 0.96,
               "temperature_coefficient": -0.36,
               "apply_iam": true,
               "shift_to_interval_start": true,
               "calibration_enabled": false,
               "calibration_days": 30,
               "calibration_reference_days": 30,
               "calibration_outage_filter_enabled": true,
               "calibration_outage_threshold": 0.55,
               "calibration_min_healthy_days": 3,
               "calibration_azimuth_bin_degrees": 45,
               "calibration_prior_kwh": 5.0,
               "calibration_min_factor": 0.5,
               "calibration_max_factor": 1.5
           },
           "pvforecastimport": {
               "import_file_path": null,
               "import_json": null
           },
           "vrm": {
               "token": "your-token",
               "site_id": 12345
           },
           "homeassistant": {
               "entity_id": "sensor.pv_forecast",
               "attribute": "forecast",
               "datetime_key": "datetime",
               "value_key": "watts",
               "value_unit": "W",
               "base_url": null,
               "token": null
           },
           "pvlib": {},
           "pvnode": {
               "api_key": "",
               "site_id": null,
               "forecast_days": 2
           },
           "forecastsolar": {
               "api_key": null
           },
           "solcast": {
               "api_key": "",
               "site_id": ""
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
               "PVForecastForecastSolar",
               "PVForecastHomeAssistant",
               "PVForecastImport",
               "PVForecastPVLib",
               "PVForecastPVNode",
               "PVForecastSolcast",
               "PVForecastVrm"
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
:::{table} pvforecast::vrm
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
       "pvforecast": {
           "vrm": {
               "token": "your-token",
               "site_id": 12345
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for the Solcast PV forecast provider

<!-- pyml disable line-length -->
:::{table} pvforecast::solcast
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| api_key | `str` | `rw` | `` | Solcast API key (Bearer auth). Required. |
| site_id | `str` | `rw` | `` | Solcast rooftop site (resource) id. Required. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "pvforecast": {
           "solcast": {
               "api_key": "your-solcast-key",
               "site_id": "abcd-1234-efgh-5678"
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for the pvnode.com PV forecast provider

<!-- pyml disable line-length -->
:::{table} pvforecast::pvnode
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| api_key | `str` | `rw` | `` | pvnode.com API key (Bearer auth). Required. |
| forecast_days | `int` | `rw` | `2` | Forecast horizon in days (1-7, capped by the pvnode plan). |
| site_id | `Optional[str]` | `rw` | `None` | pvnode.com site id of the saved plant ('Anlagen-ID'). When set, the saved (possibly calibrated) site is used. Leave empty to send the configured pvforecast.planes inline instead. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "pvforecast": {
           "pvnode": {
               "api_key": "pvn_live_xxxxxxxxxxxxxxxx",
               "site_id": "abcd-1234",
               "forecast_days": 2
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for pvforecast data calculation with PVLib

<!-- pyml disable line-length -->
:::{table} pvforecast::pvlib
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "pvforecast": {
           "pvlib": {}
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for pvforecast data import from file or JSON string

<!-- pyml disable line-length -->
:::{table} pvforecast::pvforecastimport
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| import_file_path | `Union[str, pathlib.Path, NoneType]` | `rw` | `None` | Path to the file to import PV forecast data from. |
| import_json | `Optional[str]` | `rw` | `None` | JSON string, dictionary of PV forecast value lists. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "pvforecast": {
           "pvforecastimport": {
               "import_file_path": null,
               "import_json": "{\"pvforecast_ac_power\": [0, 8.05, 352.91]}"
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
| albedo | `Optional[float]` | `rw` | `0.2` | Proportion of the light hitting the ground that it reflects back. |
| inverter_model | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| inverter_paco | `Optional[int]` | `rw` | `None` | AC power rating of the inverter [W]. |
| loss | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| module_model | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| modules_per_string | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| mountingplace | `Optional[str]` | `rw` | `building` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| optimal_surface_tilt | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| optimalangles | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| peakpower | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| pvtechchoice | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| strings_per_inverter | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| surface_azimuth | `Optional[float]` | `rw` | `180.0` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| surface_tilt | `Optional[float]` | `rw` | `30.0` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| trackingtype | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| userhorizon | `Optional[List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
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
                   "mountingplace": "building",
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

### Common settings for pvforecast data from a Home Assistant entity

<!-- pyml disable line-length -->
:::{table} pvforecast::homeassistant
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| attribute | `str` | `rw` | `forecast` | Entity attribute holding the forecast list. |
| base_url | `Optional[str]` | `rw` | `None` | Base URL of the Home Assistant instance. Only required when EOS is not running as a Home Assistant add-on (no SUPERVISOR_TOKEN available). |
| datetime_key | `str` | `rw` | `datetime` | Key for the timestamp in each forecast entry. |
| entity_id | `str` | `rw` | `sensor.pv_forecast` | Home Assistant entity providing the PV forecast. |
| token | `Optional[str]` | `rw` | `None` | Long-lived access token for the Home Assistant instance. Only required when EOS is not running as a Home Assistant add-on. |
| value_key | `str` | `rw` | `watts` | Key for the AC power value in each forecast entry. |
| value_unit | `Literal['W', 'kW']` | `rw` | `W` | Unit of the forecast value. Converted to W internally. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "pvforecast": {
           "homeassistant": {
               "entity_id": "sensor.pv1_power_now",
               "attribute": "forecast",
               "datetime_key": "datetime",
               "value_key": "watts",
               "value_unit": "W",
               "base_url": "http://homeassistant.local:8123",
               "token": null
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for the Forecast.Solar PV forecast provider

<!-- pyml disable line-length -->
:::{table} pvforecast::forecastsolar
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| api_key | `Optional[str]` | `rw` | `None` | Forecast.Solar API key. Optional — the public endpoint works without a key (lower rate limit). |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "pvforecast": {
           "forecastsolar": {
               "api_key": null
           }
       }
   }
```
<!-- pyml enable line-length -->

### Common settings for the local (pvlib) PV forecast provider

<!-- pyml disable line-length -->
:::{table} pvforecast::akkudoktor
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| albedo | `float` | `rw` | `0.25` | Ground albedo used for planes that do not set their own. |
| apply_iam | `bool` | `rw` | `True` | Apply the ASHRAE incidence-angle modifier to the beam component. |
| backend | `Literal['remote', 'local']` | `rw` | `remote` | Akkudoktor forecast backend: remote API or local Open-Meteo/pvlib model. |
| calibration_azimuth_bin_degrees | `int` | `rw` | `45` | Width of the solar-azimuth bins for the correction. 0 fits a single global factor only. |
| calibration_days | `int` | `rw` | `30` | Length of the measurement window used to fit the correction. |
| calibration_enabled | `bool` | `rw` | `False` | Correct systematic model error against measured PV production. Requires `measurement.pv_production_emr_keys` to be configured and fed. Fits a global scale factor plus per-solar-azimuth factors, which is what catches near-field shading the horizon profile misses. |
| calibration_max_factor | `float` | `rw` | `1.5` | Upper clamp on any fitted correction factor. |
| calibration_min_factor | `float` | `rw` | `0.5` | Lower clamp on any fitted correction factor. |
| calibration_min_healthy_days | `int` | `rw` | `3` | Minimum number of healthy days used for a fit. Older healthy days from the reference window are added when the recent window contains fewer. |
| calibration_outage_filter_enabled | `bool` | `rw` | `True` | Exclude days whose measured production is far below the recent healthy plant level. This prevents inverter, battery and curtailment events from being learned as permanent PV model losses. |
| calibration_outage_threshold | `float` | `rw` | `0.55` | A day is treated as unavailable when its measured/modelled energy ratio is below this fraction of the robust healthy reference ratio. |
| calibration_prior_kwh | `float` | `rw` | `5.0` | Shrinkage strength: a bin needs this much modelled energy before its own factor outweighs the global one. Higher is more conservative. |
| calibration_reference_days | `int` | `rw` | `30` | Lookback used to distinguish healthy production from outages or curtailment. If the calibration window contains too few healthy days, the most recent healthy days from this reference window are used. |
| forecast_days | `Optional[int]` | `rw` | `None` | Forecast horizon in days (1-16). Leave empty to derive it from `prediction.hours`, which is what keeps the optimizer's tail horizon fed. |
| inverter_efficiency | `float` | `rw` | `0.96` | Nominal inverter efficiency (PVWatts eta_inv_nom). |
| past_days | `Optional[int]` | `rw` | `None` | Days of past data to request (0-92). Leave empty to derive it from `prediction.historic_hours`. |
| resolution_minutes | `int` | `rw` | `15` | Forecast resolution in minutes. 15 requests Open-Meteo's `minutely_15` block (natively resolved over Central Europe and North America, interpolated from hourly elsewhere); 60 requests the `hourly` block. |
| shift_to_interval_start | `bool` | `rw` | `True` | Open-Meteo stamps an interval mean with the interval END. EOS labels an interval by its START, so records are shifted back by one interval. Disable only to compare like-for-like against a provider that does not. |
| temperature_coefficient | `float` | `rw` | `-0.36` | Module power temperature coefficient in %/degC (negative). Matches the `cellCoEff` the akkudoktor.net forecast uses. |
| transposition_model | `str` | `rw` | `perez` | pvlib sky-diffuse transposition model: isotropic, klucher, haydavies, reindl, king or perez. |
| weather_models | `list[str]` | `rw` | `['best_match']` | Open-Meteo weather models to request. Listing more than one turns the input into a poor-man's ensemble: the members are averaged per variable, which is the cheapest reliable way to cut irradiance forecast error. Costs no extra API calls. |
:::
<!-- pyml enable line-length -->

<!-- pyml disable no-emphasis-as-heading -->
**Example Input/Output**
<!-- pyml enable no-emphasis-as-heading -->

<!-- pyml disable line-length -->
```json
   {
       "pvforecast": {
           "akkudoktor": {
               "backend": "remote",
               "resolution_minutes": 15,
               "forecast_days": null,
               "past_days": null,
               "weather_models": [
                   "best_match"
               ],
               "transposition_model": "perez",
               "albedo": 0.25,
               "inverter_efficiency": 0.96,
               "temperature_coefficient": -0.36,
               "apply_iam": true,
               "shift_to_interval_start": true,
               "calibration_enabled": true,
               "calibration_days": 30,
               "calibration_reference_days": 30,
               "calibration_outage_filter_enabled": true,
               "calibration_outage_threshold": 0.55,
               "calibration_min_healthy_days": 3,
               "calibration_azimuth_bin_degrees": 45,
               "calibration_prior_kwh": 5.0,
               "calibration_min_factor": 0.5,
               "calibration_max_factor": 1.5
           }
       }
   }
```
<!-- pyml enable line-length -->
