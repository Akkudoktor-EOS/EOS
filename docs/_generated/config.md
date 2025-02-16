# Configuration Table

## General Configuration Values

:::{table} General Configuration Values
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `config_default_file_path` | `<class 'pathlib._local.Path'>` | `ro` | `N/A` | Compute the default config file path. |
| `config_file_path` | `Optional[pathlib._local.Path]` | `ro` | `N/A` | Path to EOS configuration file. |
| `config_folder_path` | `Optional[pathlib._local.Path]` | `ro` | `N/A` | Path to EOS configuration directory. |
| `config_keys` | `List[str]` | `ro` | `N/A` | Returns the keys of all fields in the configuration. |
| `config_keys_read_only` | `List[str]` | `ro` | `N/A` | Returns the keys of all read only fields in the configuration. |
| `data_cache_path` | `Optional[pathlib._local.Path]` | `ro` | `N/A` | Compute data_cache_path based on data_folder_path. |
| `data_cache_subpath` | `Optional[pathlib._local.Path]` | `rw` | `cache` | Sub-path for the EOS cache data directory. |
| `data_folder_path` | `Optional[pathlib._local.Path]` | `rw` | `None` | Path to EOS data directory. |
| `data_output_path` | `Optional[pathlib._local.Path]` | `ro` | `N/A` | Compute data_output_path based on data_folder_path. |
| `data_output_subpath` | `Optional[pathlib._local.Path]` | `rw` | `output` | Sub-path for the EOS output data directory. |
| `latitude` | `Optional[float]` | `rw` | `None` | Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°) |
| `longitude` | `Optional[float]` | `rw` | `None` | Longitude in decimal degrees, within -180 to 180 (°) |
| `package_root_path` | `<class 'pathlib._local.Path'>` | `ro` | `N/A` | Compute the package root path. |
| `timezone` | `Optional[str]` | `ro` | `N/A` | Compute timezone based on latitude and longitude. |
:::

## Battery Device Simulation Configuration

:::{table} Battery Device Simulation Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `battery_capacity` | `Optional[int]` | `rw` | `None` | Battery capacity [Wh]. |
| `battery_charging_efficiency` | `Optional[float]` | `rw` | `None` | Battery charging efficiency [%]. |
| `battery_discharging_efficiency` | `Optional[float]` | `rw` | `None` | Battery discharging efficiency [%]. |
| `battery_initial_soc` | `Optional[int]` | `rw` | `None` | Battery initial state of charge [%]. |
| `battery_max_charging_power` | `Optional[int]` | `rw` | `None` | Battery maximum charge power [W]. |
| `battery_provider` | `Optional[str]` | `rw` | `None` | Id of Battery simulation provider. |
| `battery_soc_max` | `Optional[int]` | `rw` | `None` | Battery maximum state of charge [%]. |
| `battery_soc_min` | `Optional[int]` | `rw` | `None` | Battery minimum state of charge [%]. |
:::

## Battery Electric Vehicle Device Simulation Configuration

:::{table} Battery Electric Vehicle Device Simulation Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `bev_capacity` | `Optional[int]` | `rw` | `None` | Battery Electric Vehicle capacity [Wh]. |
| `bev_charging_efficiency` | `Optional[float]` | `rw` | `None` | Battery Electric Vehicle charging efficiency [%]. |
| `bev_discharging_efficiency` | `Optional[float]` | `rw` | `None` | Battery Electric Vehicle discharging efficiency [%]. |
| `bev_initial_soc` | `Optional[int]` | `rw` | `None` | Battery Electric Vehicle initial state of charge [%]. |
| `bev_max_charging_power` | `Optional[int]` | `rw` | `None` | Battery Electric Vehicle maximum charge power [W]. |
| `bev_provider` | `Optional[str]` | `rw` | `None` | Id of Battery Electric Vehicle simulation provider. |
| `bev_soc_max` | `Optional[int]` | `rw` | `None` | Battery Electric Vehicle maximum state of charge [%]. |
:::

## Dishwasher Device Simulation Configuration

:::{table} Dishwasher Device Simulation Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `dishwasher_consumption` | `Optional[int]` | `rw` | `None` | Dish Washer energy consumption [Wh]. |
| `dishwasher_duration` | `Optional[int]` | `rw` | `None` | Dish Washer usage duration [h]. |
| `dishwasher_provider` | `Optional[str]` | `rw` | `None` | Id of Dish Washer simulation provider. |
:::

## Electricity Price Prediction Configuration

:::{table} Electricity Price Prediction Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `elecprice_charges_kwh` | `Optional[float]` | `rw` | `None` | Electricity price charges (€/kWh). |
| `elecprice_provider` | `Optional[str]` | `rw` | `None` | Electricity price provider id of provider to be used. |
| `elecpriceimport_file_path` | `Union[str, pathlib._local.Path, NoneType]` | `rw` | `None` | Path to the file to import elecprice data from. |
| `elecpriceimport_json` | `Optional[str]` | `rw` | `None` | JSON string, dictionary of electricity price forecast value lists. |
:::

## General Optimization Configuration

:::{table} General Optimization Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `optimization_ev_available_charge_rates_percent` | `Optional[typing.List[float]]` | `rw` | `[0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]` | Charge rates available for the EV in percent of maximum charge. |
| `optimization_hours` | `Optional[int]` | `rw` | `24` | Number of hours into the future for optimizations. |
| `optimization_penalty` | `Optional[int]` | `rw` | `10` | Penalty factor used in optimization. |
:::

## General Prediction Configuration

:::{table} General Prediction Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `prediction_historic_hours` | `Optional[int]` | `rw` | `48` | Number of hours into the past for historical predictions data |
| `prediction_hours` | `Optional[int]` | `rw` | `48` | Number of hours into the future for predictions |
:::

## Inverter Device Simulation Configuration

:::{table} Inverter Device Simulation Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `inverter_power_max` | `Optional[float]` | `rw` | `None` | Inverter maximum power [W]. |
| `inverter_provider` | `Optional[str]` | `rw` | `None` | Id of PV Inverter simulation provider. |
:::

## Load Prediction Configuration

:::{table} Load Prediction Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `load_import_file_path` | `Union[str, pathlib._local.Path, NoneType]` | `rw` | `None` | Path to the file to import load data from. |
| `load_import_json` | `Optional[str]` | `rw` | `None` | JSON string, dictionary of load forecast value lists. |
| `load_provider` | `Optional[str]` | `rw` | `None` | Load provider id of provider to be used. |
| `loadakkudoktor_year_energy` | `Optional[float]` | `rw` | `None` | Yearly energy consumption (kWh). |
:::

## Logging Configuration

:::{table} Logging Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `logging_level_default` | `Optional[str]` | `rw` | `None` | EOS default logging level. |
| `logging_level_root` | `<class 'str'>` | `ro` | `N/A` | Root logger logging level. |
:::

## Measurement Configuration

:::{table} Measurement Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `measurement_load0_name` | `Optional[str]` | `rw` | `None` | Name of the load0 source (e.g. 'Household', 'Heat Pump') |
| `measurement_load1_name` | `Optional[str]` | `rw` | `None` | Name of the load1 source (e.g. 'Household', 'Heat Pump') |
| `measurement_load2_name` | `Optional[str]` | `rw` | `None` | Name of the load2 source (e.g. 'Household', 'Heat Pump') |
| `measurement_load3_name` | `Optional[str]` | `rw` | `None` | Name of the load3 source (e.g. 'Household', 'Heat Pump') |
| `measurement_load4_name` | `Optional[str]` | `rw` | `None` | Name of the load4 source (e.g. 'Household', 'Heat Pump') |
:::

## PV Forecast Configuration

:::{table} PV Forecast Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `pvforecast0_albedo` | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| `pvforecast0_inverter_model` | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| `pvforecast0_inverter_paco` | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| `pvforecast0_loss` | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| `pvforecast0_module_model` | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| `pvforecast0_modules_per_string` | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| `pvforecast0_mountingplace` | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| `pvforecast0_optimal_surface_tilt` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| `pvforecast0_optimalangles` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| `pvforecast0_peakpower` | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| `pvforecast0_pvtechchoice` | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| `pvforecast0_strings_per_inverter` | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| `pvforecast0_surface_azimuth` | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| `pvforecast0_surface_tilt` | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| `pvforecast0_trackingtype` | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| `pvforecast0_userhorizon` | `Optional[typing.List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| `pvforecast1_albedo` | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| `pvforecast1_inverter_model` | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| `pvforecast1_inverter_paco` | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| `pvforecast1_loss` | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| `pvforecast1_module_model` | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| `pvforecast1_modules_per_string` | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| `pvforecast1_mountingplace` | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| `pvforecast1_optimal_surface_tilt` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| `pvforecast1_optimalangles` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| `pvforecast1_peakpower` | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| `pvforecast1_pvtechchoice` | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| `pvforecast1_strings_per_inverter` | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| `pvforecast1_surface_azimuth` | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| `pvforecast1_surface_tilt` | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| `pvforecast1_trackingtype` | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| `pvforecast1_userhorizon` | `Optional[typing.List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| `pvforecast2_albedo` | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| `pvforecast2_inverter_model` | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| `pvforecast2_inverter_paco` | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| `pvforecast2_loss` | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| `pvforecast2_module_model` | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| `pvforecast2_modules_per_string` | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| `pvforecast2_mountingplace` | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| `pvforecast2_optimal_surface_tilt` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| `pvforecast2_optimalangles` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| `pvforecast2_peakpower` | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| `pvforecast2_pvtechchoice` | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| `pvforecast2_strings_per_inverter` | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| `pvforecast2_surface_azimuth` | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| `pvforecast2_surface_tilt` | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| `pvforecast2_trackingtype` | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| `pvforecast2_userhorizon` | `Optional[typing.List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| `pvforecast3_albedo` | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| `pvforecast3_inverter_model` | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| `pvforecast3_inverter_paco` | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| `pvforecast3_loss` | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| `pvforecast3_module_model` | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| `pvforecast3_modules_per_string` | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| `pvforecast3_mountingplace` | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| `pvforecast3_optimal_surface_tilt` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| `pvforecast3_optimalangles` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| `pvforecast3_peakpower` | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| `pvforecast3_pvtechchoice` | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| `pvforecast3_strings_per_inverter` | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| `pvforecast3_surface_azimuth` | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| `pvforecast3_surface_tilt` | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| `pvforecast3_trackingtype` | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| `pvforecast3_userhorizon` | `Optional[typing.List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| `pvforecast4_albedo` | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| `pvforecast4_inverter_model` | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| `pvforecast4_inverter_paco` | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| `pvforecast4_loss` | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| `pvforecast4_module_model` | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| `pvforecast4_modules_per_string` | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| `pvforecast4_mountingplace` | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| `pvforecast4_optimal_surface_tilt` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| `pvforecast4_optimalangles` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| `pvforecast4_peakpower` | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| `pvforecast4_pvtechchoice` | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| `pvforecast4_strings_per_inverter` | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| `pvforecast4_surface_azimuth` | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| `pvforecast4_surface_tilt` | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| `pvforecast4_trackingtype` | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| `pvforecast4_userhorizon` | `Optional[typing.List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| `pvforecast5_albedo` | `Optional[float]` | `rw` | `None` | Proportion of the light hitting the ground that it reflects back. |
| `pvforecast5_inverter_model` | `Optional[str]` | `rw` | `None` | Model of the inverter of this plane. |
| `pvforecast5_inverter_paco` | `Optional[int]` | `rw` | `None` | AC power rating of the inverter. [W] |
| `pvforecast5_loss` | `Optional[float]` | `rw` | `14.0` | Sum of PV system losses in percent |
| `pvforecast5_module_model` | `Optional[str]` | `rw` | `None` | Model of the PV modules of this plane. |
| `pvforecast5_modules_per_string` | `Optional[int]` | `rw` | `None` | Number of the PV modules of the strings of this plane. |
| `pvforecast5_mountingplace` | `Optional[str]` | `rw` | `free` | Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated. |
| `pvforecast5_optimal_surface_tilt` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt angle. Ignored for two-axis tracking. |
| `pvforecast5_optimalangles` | `Optional[bool]` | `rw` | `False` | Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking. |
| `pvforecast5_peakpower` | `Optional[float]` | `rw` | `None` | Nominal power of PV system in kW. |
| `pvforecast5_pvtechchoice` | `Optional[str]` | `rw` | `crystSi` | PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'. |
| `pvforecast5_strings_per_inverter` | `Optional[int]` | `rw` | `None` | Number of the strings of the inverter of this plane. |
| `pvforecast5_surface_azimuth` | `Optional[float]` | `rw` | `None` | Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270). |
| `pvforecast5_surface_tilt` | `Optional[float]` | `rw` | `None` | Tilt angle from horizontal plane. Ignored for two-axis tracking. |
| `pvforecast5_trackingtype` | `Optional[int]` | `rw` | `None` | Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south. |
| `pvforecast5_userhorizon` | `Optional[typing.List[float]]` | `rw` | `None` | Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. |
| `pvforecast_planes` | `List[str]` | `ro` | `N/A` | Compute a list of active planes. |
| `pvforecast_planes_azimuth` | `List[float]` | `ro` | `N/A` | Compute a list of the azimuths per active planes. |
| `pvforecast_planes_inverter_paco` | `Any` | `ro` | `N/A` | Compute a list of the maximum power rating of the inverter per active planes. |
| `pvforecast_planes_peakpower` | `List[float]` | `ro` | `N/A` | Compute a list of the peak power per active planes. |
| `pvforecast_planes_tilt` | `List[float]` | `ro` | `N/A` | Compute a list of the tilts per active planes. |
| `pvforecast_planes_userhorizon` | `Any` | `ro` | `N/A` | Compute a list of the user horizon per active planes. |
| `pvforecast_provider` | `Optional[str]` | `rw` | `None` | PVForecast provider id of provider to be used. |
| `pvforecastimport_file_path` | `Union[str, pathlib._local.Path, NoneType]` | `rw` | `None` | Path to the file to import PV forecast data from. |
| `pvforecastimport_json` | `Optional[str]` | `rw` | `None` | JSON string, dictionary of PV forecast value lists. |
:::

## Server Configuration

:::{table} Server Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `server_eos_host` | `Optional[pydantic.networks.IPvAnyAddress]` | `rw` | `127.0.0.1` | EOS server IP address. |
| `server_eos_port` | `Optional[int]` | `rw` | `8503` | EOS server IP port number. |
| `server_eos_startup_eosdash` | `Optional[bool]` | `rw` | `True` | EOS server to start EOSdash server. |
| `server_eos_verbose` | `Optional[bool]` | `rw` | `False` | Enable debug output |
| `server_eosdash_host` | `Optional[pydantic.networks.IPvAnyAddress]` | `rw` | `127.0.0.1` | EOSdash server IP address. |
| `server_eosdash_port` | `Optional[int]` | `rw` | `8504` | EOSdash server IP port number. |
:::

## Weather Forecast Configuration

:::{table} Weather Forecast Configuration
:widths: 10 10 5 5 30
:align: left

| Name | Type | Read-Only | Default | Description |
| ---- | ---- | --------- | ------- | ----------- |
| `weather_provider` | `Optional[str]` | `rw` | `None` | Weather provider id of provider to be used. |
| `weatherimport_file_path` | `Union[str, pathlib._local.Path, NoneType]` | `rw` | `None` | Path to the file to import weather data from. |
| `weatherimport_json` | `Optional[str]` | `rw` | `None` | JSON string, dictionary of weather forecast value lists. |
:::
