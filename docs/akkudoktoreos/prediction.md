% SPDX-License-Identifier: Apache-2.0

# Predictions

Predictions, along with simulations and measurements, form the foundation upon which energy
optimization is executed. In EOS, a standard set of predictions is managed, including:

- **Household Load Prediction**
- **Electricity Price Prediction**
- **PV Power Prediction**
- **Weather Prediction**

## Storing Predictions

EOS stores predictions in a **key-value store**, where the term `prediction key` refers to the
unique key used to retrieve specific prediction data. The key-value store is in memory. Stored
data is lost on re-start of the EOS REST server.

## Prediction Providers

Most predictions can be sourced from various providers. The specific provider to use is configured
in the EOS configuration. For example:

```plaintext
weather_provider = "ClearOutside"
```

Some providers offer multiple prediction keys. For instance, a weather provider might provide data
to prediction keys like:

- `weather_temp_air` (air temperature)
- `weather_wind_speed` (wind speed)

### Prediction Import Providers

The prediction import providers are designed to import prediction data from a file or a JSON
string. An external entity should update the file or JSON string whenever new prediction data
becomes available.

The prediction data must be provided in one of the following formats:

#### 1. DateTimeData

A dictionary with the following structure:

```JSON
    {
        "start_datetime": "2024-01-01 00:00:00",
        "interval": "1 Hour",
        "<prediction key>": [value, value, ...],
        "<prediction key>": [value, value, ...],
        ...
    }
```

#### 2. DateTimeDataFrame

A JSON string created from a [pandas](https://pandas.pydata.org/docs/index.html) dataframe with a
`DatetimeIndex`. Use [pandas.DataFrame.to_json(orient="index")](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_json.html#pandas.DataFrame.to_json).
The column name of the data must be the same as the names of the `prediction key`s.

#### 3. DateTimeSeries

A JSON string created from a [pandas](https://pandas.pydata.org/docs/index.html) series with a
`DatetimeIndex`. Use [pandas.Series.to_json(orient="index")](https://pandas.pydata.org/docs/reference/api/pandas.Series.to_json.html#pandas.Series.to_json).

## Adjusted Predictions

Certain prediction keys include an `_adjusted` suffix, such as `load_total_adjusted`. These
predictions are adjusted by real data from your system's measurements if given to enhance accuracy.

For example, the load prediction provider `LoadAkkudoktor` takes generic load data assembled by
Akkudoktor.net, maps that to the yearly energy consumption given in the configuration option
`loadakkudoktor_year_energy`, and finally adjusts the predicted load by the `measurement_loads`
of your system.

## Prediction Updates

Predictions are updated at the start of each energy management run, i.e., when EOS performs
optimization. Key considerations for updates include:

- Predictions sourced from online providers are usually rate-limited to one retrieval per hour.
- Only predictions with a configured provider are updated.
- Some providers may not support all generic prediction keys, leading to potential gaps
  in updated predictions even after update.

## Accessing Predictions

Prediction data can be accessed using the EOS **REST API** via the `/v1/prediction/<...>` endpoints.

In a standard configuration, the [**REST API**](http://0.0.0.0:8503/docs) of a running EOS instance
is available at [http://0.0.0.0:8503/docs](http://0.0.0.0:8503/docs). This link provides access to
the API documentation and allows you to explore available endpoints interactively.

To view all available prediction keys, use the **GET** `/v1/prediction/keys` endpoint.

If no keys are displayed, or if the ones you need are missing, it indicates that your configuration
lacks the necessary prediction provider settings. You can configure prediction providers by using
the **PUT** `/v1/config` endpoint. You may save your configuration to the EOS configuration file.

## Electricity Price Prediction

Prediction keys:

- `elecprice_marketprice_wh`: Electricity market price per Wh (€/Wh).
- `elecprice_marketprice_kwh`: Electricity market price per kWh (€/kWh).

Configuration options:

- `elecprice_provider`: Electricity price provider id of provider to be used.

  - `ElecPriceAkkudoktor`: Retrieves from Akkudoktor.net.
  - `ElecPriceImport`: Imports from a file or JSON string.

- `elecprice_charges_kwh`: Electricity price charges (€/kWh).
- `elecpriceimport_file_path`: Path to the file to import electricity price forecast data from.
- `elecpriceimport_json`: JSON string, dictionary of electricity price forecast value lists.

### ElecPriceAkkudoktor Provider

The `ElecPriceAkkudoktor` provider retrieves electricity prices directly from **Akkudoktor.net**,
which supplies price data for the next 24 hours. For periods beyond 24 hours, the provider generates
prices by extrapolating historical price data combined with the most recent actual prices obtained
from Akkudoktor.net. Electricity price charges given in the `elecprice_charges_kwh` configuration
option are added.

### ElecPriceImport Provider

The `ElecPriceImport` provider is designed to import electricity prices from a file or a JSON
string. An external entity should update the file or JSON string whenever new prediction data
becomes available.

The prediction key for the electricity price forecast data is:

- `elecprice_marketprice_wh`: Electricity market price per Wh (€/Wh).

The electricity proce forecast data must be provided in one of the formats described in
<project:#prediction-import-providers>. The data source must be given in the
`elecpriceimport_file_path` or `elecpriceimport_json` configuration option.

## Load Prediction

Prediction keys:

- `load_mean`: Predicted load mean value (W).
- `load_std`: Predicted load standard deviation (W).
- `load_mean_adjusted`: Predicted load mean value adjusted by load measurement (W).

Configuration options:

- `load_provider`: Load provider id of provider to be used.

  - `LoadAkkudoktor`: Retrieves from local database.
  - `LoadImport`: Imports from a file or JSON string.

- `loadakkudoktor_year_energy`: Yearly energy consumption (kWh).
- `loadimport_file_path`: Path to the file to import load forecast data from.
- `loadimport_json`: JSON string, dictionary of load forecast value lists.

### LoadAkkudoktor Provider

The `LoadAkkudoktor` provider retrieves generic load data from a local database and tailors it to
align with the annual energy consumption specified in the `loadakkudoktor_year_energy` configuration
option.

### LoadImport Provider

The `LoadImport` provider is designed to import load forecast data from a file or a JSON
string. An external entity should update the file or JSON string whenever new prediction data
becomes available.

The prediction keys for the load forecast data are:

- `load_mean`: Predicted load mean value (W).
- `load_std`: Predicted load standard deviation (W).
- `load_mean_adjusted`: Predicted load mean value adjusted by load measurement (W).

The load forecast data must be provided in one of the formats described in
<project:#prediction-import-providers>. The data source must be given in the `loadimport_file_path`
or `loadimport_json` configuration option.

## PV Power Prediction

Prediction keys:

- `pvforecast_ac_power`: Total DC power (W).
- `pvforecast_dc_power`: Total AC power (W).

Configuration options:

- `pvforecast_provider`: PVForecast provider id of provider to be used.

  - `PVForecastAkkudoktor`: Retrieves from Akkudoktor.net.
  - `PVForecastImport`: Imports from a file or JSON string.

- `latitude`: Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°)"
- `longitude`: Longitude in decimal degrees, within -180 to 180 (°)
- `pvforecast<0..5>_surface_tilt`: Tilt angle from horizontal plane. Ignored for two-axis tracking.
- `pvforecast<0..5>_surface_azimuth`: Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).
- `pvforecast<0..5>_userhorizon`: Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.
- `pvforecast<0..5>_peakpower`: Nominal power of PV system in kW.
- `pvforecast<0..5>_pvtechchoice`: PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'.
- `pvforecast<0..5>_mountingplace`: Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.
- `pvforecast<0..5>_loss`: Sum of PV system losses in percent
- `pvforecast<0..5>_trackingtype`: Type of suntracking. 0=fixed, 1=single horizontal axis aligned north-south, 2=two-axis tracking, 3=vertical axis tracking, 4=single horizontal axis aligned east-west, 5=single inclined axis aligned north-south.
- `pvforecast<0..5>_optimal_surface_tilt`: Calculate the optimum tilt angle. Ignored for two-axis tracking.
- `pvforecast<0..5>_optimalangles`: Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.
- `pvforecast<0..5>_albedo`: Proportion of the light hitting the ground that it reflects back.
- `pvforecast<0..5>_module_model`: Model of the PV modules of this plane.
- `pvforecast<0..5>_inverter_model`: Model of the inverter of this plane.
- `pvforecast<0..5>_inverter_paco`: AC power rating of the inverter. [W]
- `pvforecast<0..5>_modules_per_string`: Number of the PV modules of the strings of this plane.
- `pvforecast<0..5>_strings_per_inverter`: Number of the strings of the inverter of this plane.
- `pvforecastimport_file_path`: Path to the file to import PV forecast data from.
- `pvforecastimport_json`: JSON string, dictionary of PV forecast value lists.

### PVForecastAkkudoktor Provider

The `PVForecastAkkudoktor` provider retrieves the PV power forecast data directly from
**Akkudoktor.net**.

The following general configuration options of the PV system must be set:

- `latitude`: Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°)"
- `longitude`: Longitude in decimal degrees, within -180 to 180 (°)

For each plane `<0..5>` of the PV system the following configuration options must be set:

- `pvforecast<0..5>_surface_tilt`: Tilt angle from horizontal plane. Ignored for two-axis tracking.
- `pvforecast<0..5>_surface_azimuth`: Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180, west=270).
- `pvforecast<0..5>_userhorizon`: Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.
- `pvforecast<0..5>_inverter_paco`: AC power rating of the inverter. [W]
- `pvforecast<0..5>_peakpower`: Nominal power of PV system in kW.

Example:

```Python
{
  "latitude": 50.1234,
  "longitude": 9.7654,
  "pvforecast_provider": "PVForecastAkkudoktor",
  "pvforecast0_peakpower": 5.0,
  "pvforecast0_surface_azimuth": -10,
  "pvforecast0_surface_tilt": 7,
  "pvforecast0_userhorizon": [20, 27, 22, 20],
  "pvforecast0_inverter_paco": 10000,
  "pvforecast1_peakpower": 4.8,
  "pvforecast1_surface_azimuth": -90,
  "pvforecast1_surface_tilt": 7,
  "pvforecast1_userhorizon": [30, 30, 30, 50],
  "pvforecast1_inverter_paco": 10000,
  "pvforecast2_peakpower": 1.4,
  "pvforecast2_surface_azimuth": -40,
  "pvforecast2_surface_tilt": 60,
  "pvforecast2_userhorizon": [60, 30, 0, 30],
  "pvforecast2_inverter_paco": 2000,
  "pvforecast3_peakpower": 1.6,
  "pvforecast3_surface_azimuth": 5,
  "pvforecast3_surface_tilt": 45,
  "pvforecast3_userhorizon": [45, 25, 30, 60],
  "pvforecast3_inverter_paco": 1400,
  "pvforecast4_peakpower": None,
}
```

### PVForecastImport Provider

The `PVForecastImport` provider is designed to import PV forecast data from a file or a JSON
string. An external entity should update the file or JSON string whenever new prediction data
becomes available.

The prediction keys for the PV forecast data are:

- `pvforecast_ac_power`: Total DC power (W).
- `pvforecast_dc_power`: Total AC power (W).

The PV forecast data must be provided in one of the formats described in
<project:#prediction-import-providers>. The data source must be given in the
`pvforecastimport_file_path` or `pvforecastimport_json` configuration option.

## Weather Prediction

Prediction keys:

- `weather_dew_point`: Dew Point (°C)
- `weather_dhi`: Diffuse Horizontal Irradiance (W/m2)
- `weather_dni`: Direct Normal Irradiance (W/m2)
- `weather_feels_like`: Feels Like (°C)
- `weather_fog`: Fog (%)
- `weather_frost_chance`: Chance of Frost
- `weather_ghi`: Global Horizontal Irradiance (W/m2)
- `weather_high_clouds`: High Clouds (% Sky Obscured)
- `weather_low_clouds`: Low Clouds (% Sky Obscured)
- `weather_medium_clouds`: Medium Clouds (% Sky Obscured)
- `weather_ozone`: Ozone (du)
- `weather_precip_amt`: Precipitation Amount (mm)
- `weather_precip_prob`: Precipitation Probability (%)
- `weather_preciptable_water`: Precipitable Water (cm)
- `weather_precip_type`: Precipitation Type
- `weather_pressure`: Pressure (mb)
- `weather_relative_humidity`: Relative Humidity (%)
- `weather_temp_air`: Temperature (°C)
- `weather_total_clouds`: Total Clouds (% Sky Obscured)
- `weather_visibility`: Visibility (m)
- `weather_wind_direction`: "Wind Direction (°)
- `weather_wind_speed`: Wind Speed (kmph)

Configuration options:

- `weather_provider`: Load provider id of provider to be used.

  - `BrightSky`: Retrieves from https://api.brightsky.dev.
  - `ClearOutside`: Retrieves from https://clearoutside.com/forecast.
  - `LoadImport`: Imports from a file or JSON string.

- `weatherimport_file_path`: Path to the file to import weatherforecast data from.
- `weatherimport_json`: JSON string, dictionary of weather forecast value lists.

### BrightSky Provider

The `BrightSky` provider retrieves the PV power forecast data directly from
[**BrightSky**](https://api.brightsky.dev).

The provider provides forecast data for the following prediction keys:

- `weather_dew_point`: Dew Point (°C)
- `weather_ghi`: Global Horizontal Irradiance (W/m2)
- `weather_precip_amt`: Precipitation Amount (mm)
- `weather_precip_prob`: Precipitation Probability (%)
- `weather_pressure`: Pressure (mb)
- `weather_relative_humidity`: Relative Humidity (%)
- `weather_temp_air`: Temperature (°C)
- `weather_total_clouds`: Total Clouds (% Sky Obscured)
- `weather_visibility`: Visibility (m)
- `weather_wind_direction`: "Wind Direction (°)
- `weather_wind_speed`: Wind Speed (kmph)

### ClearOutside Provider

The `ClearOutside` provider retrieves the PV power forecast data directly from
[**ClearOutside**](https://clearoutside.com/forecast).

The provider provides forecast data for the following prediction keys:

- `weather_dew_point`: Dew Point (°C)
- `weather_dhi`: Diffuse Horizontal Irradiance (W/m2)
- `weather_dni`: Direct Normal Irradiance (W/m2)
- `weather_feels_like`: Feels Like (°C)
- `weather_fog`: Fog (%)
- `weather_frost_chance`: Chance of Frost
- `weather_ghi`: Global Horizontal Irradiance (W/m2)
- `weather_high_clouds`: High Clouds (% Sky Obscured)
- `weather_low_clouds`: Low Clouds (% Sky Obscured)
- `weather_medium_clouds`: Medium Clouds (% Sky Obscured)
- `weather_ozone`: Ozone (du)
- `weather_precip_amt`: Precipitation Amount (mm)
- `weather_precip_prob`: Precipitation Probability (%)
- `weather_preciptable_water`: Precipitable Water (cm)
- `weather_precip_type`: Precipitation Type
- `weather_pressure`: Pressure (mb)
- `weather_relative_humidity`: Relative Humidity (%)
- `weather_temp_air`: Temperature (°C)
- `weather_total_clouds`: Total Clouds (% Sky Obscured)
- `weather_visibility`: Visibility (m)
- `weather_wind_direction`: "Wind Direction (°)
- `weather_wind_speed`: Wind Speed (kmph)

### WeatherImport Provider

The `WeatherImport` provider is designed to import weather forecast data from a file or a JSON
string. An external entity should update the file or JSON string whenever new prediction data
becomes available.

The prediction keys for the PV forecast data are:

- `weather_dew_point`: Dew Point (°C)
- `weather_dhi`: Diffuse Horizontal Irradiance (W/m2)
- `weather_dni`: Direct Normal Irradiance (W/m2)
- `weather_feels_like`: Feels Like (°C)
- `weather_fog`: Fog (%)
- `weather_frost_chance`: Chance of Frost
- `weather_ghi`: Global Horizontal Irradiance (W/m2)
- `weather_high_clouds`: High Clouds (% Sky Obscured)
- `weather_low_clouds`: Low Clouds (% Sky Obscured)
- `weather_medium_clouds`: Medium Clouds (% Sky Obscured)
- `weather_ozone`: Ozone (du)
- `weather_precip_amt`: Precipitation Amount (mm)
- `weather_precip_prob`: Precipitation Probability (%)
- `weather_preciptable_water`: Precipitable Water (cm)
- `weather_precip_type`: Precipitation Type
- `weather_pressure`: Pressure (mb)
- `weather_relative_humidity`: Relative Humidity (%)
- `weather_temp_air`: Temperature (°C)
- `weather_total_clouds`: Total Clouds (% Sky Obscured)
- `weather_visibility`: Visibility (m)
- `weather_wind_direction`: "Wind Direction (°)
- `weather_wind_speed`: Wind Speed (kmph)

The PV forecast data must be provided in one of the formats described in
<project:#prediction-import-providers>. The data source must be given in the
`weatherimport_file_path` or `pvforecastimport_json` configuration option.
