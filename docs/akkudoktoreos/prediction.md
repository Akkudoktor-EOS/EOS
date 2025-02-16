% SPDX-License-Identifier: Apache-2.0

# Predictions

Predictions, along with simulations and measurements, form the foundation upon which energy
optimization is executed. In EOS, a standard set of predictions is managed, including:

- Household Load Prediction
- Electricity Price Prediction
- PV Power Prediction
- Weather Prediction

## Storing Predictions

EOS stores predictions in a **key-value store**, where the term `prediction key` refers to the
unique key used to retrieve specific prediction data. The key-value store is in memory. Stored
data is lost on re-start of the EOS REST server.

## Prediction Providers

Most predictions can be sourced from various providers. The specific provider to use is configured
in the EOS configuration and can be set by prediction type. For example:

```python
{
  "weather": {
    "provider": "ClearOutside"
  }
}
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

```python
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
`DatetimeIndex`. Use
[pandas.DataFrame.to_json(orient="index")](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_json.html#pandas.DataFrame.to_json).
The column name of the data must be the same as the names of the `prediction key`s.

#### 3. DateTimeSeries

A JSON string created from a [pandas](https://pandas.pydata.org/docs/index.html) series with a
`DatetimeIndex`. Use
[pandas.Series.to_json(orient="index")](https://pandas.pydata.org/docs/reference/api/pandas.Series.to_json.html#pandas.Series.to_json).

## Adjusted Predictions

Certain prediction keys include an `_adjusted` suffix, such as `load_total_adjusted`. These
predictions are adjusted by real data from your system's measurements if given to enhance accuracy.

For example, the load prediction provider `LoadAkkudoktor` takes generic load data assembled by
Akkudoktor.net, maps that to the yearly energy consumption given in the configuration option
`loadakkudoktor_year_energy`, and finally adjusts the predicted load by the `loads`
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

In a standard configuration, the [**REST API**](http://localhost:8503/docs) of a running EOS instance
is available at [http://localhost:8503/docs](http://localhost:8503/docs). This link provides access to
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

- `elecprice`: Electricity price configuration.

  - `provider`: Electricity price provider id of provider to be used.

    - `ElecPriceAkkudoktor`: Retrieves from Akkudoktor.net.
    - `ElecPriceImport`: Imports from a file or JSON string.

  - `charges_kwh`: Electricity price charges (€/kWh).
  - `provider_settings.import_file_path`: Path to the file to import electricity price forecast data from.
  - `provider_settings.import_json`: JSON string, dictionary of electricity price forecast value lists.

### ElecPriceAkkudoktor Provider

The `ElecPriceAkkudoktor` provider retrieves electricity prices directly from **Akkudoktor.net**,
which supplies price data for the next 24 hours. For periods beyond 24 hours, the provider generates
prices by extrapolating historical price data combined with the most recent actual prices obtained
from Akkudoktor.net. Electricity price charges given in the `charges_kwh` configuration
option are added.

### ElecPriceImport Provider

The `ElecPriceImport` provider is designed to import electricity prices from a file or a JSON
string. An external entity should update the file or JSON string whenever new prediction data
becomes available.

The prediction key for the electricity price forecast data is:

- `elecprice_marketprice_wh`: Electricity market price per Wh (€/Wh).

The electricity proce forecast data must be provided in one of the formats described in
<project:#prediction-import-providers>. The data source can be given in the
`import_file_path` or `import_json` configuration option.

The data may additionally or solely be provided by the
**PUT** `/v1/prediction/import/ElecPriceImport` endpoint.

## Load Prediction

Prediction keys:

- `load_mean`: Predicted load mean value (W).
- `load_std`: Predicted load standard deviation (W).
- `load_mean_adjusted`: Predicted load mean value adjusted by load measurement (W).

Configuration options:

- `load`: Load configuration.

  - `provider`: Load provider id of provider to be used.

    - `LoadAkkudoktor`: Retrieves from local database.
    - `LoadImport`: Imports from a file or JSON string.

  - `provider_settings.loadakkudoktor_year_energy`: Yearly energy consumption (kWh).
  - `provider_settings.loadimport_file_path`: Path to the file to import load forecast data from.
  - `provider_settings.loadimport_json`: JSON string, dictionary of load forecast value lists.

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
<project:#prediction-import-providers>. The data source can be given in the `loadimport_file_path`
or `loadimport_json` configuration option.

The data may additionally or solely be provided by the
**PUT** `/v1/prediction/import/LoadImport` endpoint.

## PV Power Prediction

Prediction keys:

- `pvforecast_ac_power`: Total DC power (W).
- `pvforecast_dc_power`: Total AC power (W).

Configuration options:

- `general`: General configuration.

  - `latitude`: Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°)"
  - `longitude`: Longitude in decimal degrees, within -180 to 180 (°)

- `pvforecast`: PV forecast configuration.

  - `provider`: PVForecast provider id of provider to be used.

    - `PVForecastAkkudoktor`: Retrieves from Akkudoktor.net.
    - `PVForecastImport`: Imports from a file or JSON string.

  - `planes[].surface_tilt`: Tilt angle from horizontal plane. Ignored for two-axis tracking.
  - `planes[].surface_azimuth`: Orientation (azimuth angle) of the (fixed) plane.
                                Clockwise from north (north=0, east=90, south=180, west=270).
  - `planes[].userhorizon`: Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.
  - `planes[].peakpower`: Nominal power of PV system in kW.
  - `planes[].pvtechchoice`: PV technology. One of 'crystSi', 'CIS', 'CdTe', 'Unknown'.
  - `planes[].mountingplace`: Type of mounting for PV system. Options are 'free' for free-standing and 'building' for building-integrated.
  - `planes[].loss`: Sum of PV system losses in percent
  - `planes[].trackingtype`: Type of suntracking.
                             0=fixed,
                             1=single horizontal axis aligned north-south,
                             2=two-axis tracking,
                             3=vertical axis tracking,
                             4=single horizontal axis aligned east-west,
                             5=single inclined axis aligned north-south.
  - `planes[].optimal_surface_tilt`: Calculate the optimum tilt angle. Ignored for two-axis tracking.
  - `planes[].optimalangles`: Calculate the optimum tilt and azimuth angles. Ignored for two-axis tracking.
  - `planes[].albedo`: Proportion of the light hitting the ground that it reflects back.
  - `planes[].module_model`: Model of the PV modules of this plane.
  - `planes[].inverter_model`: Model of the inverter of this plane.
  - `planes[].inverter_paco`: AC power rating of the inverter. [W]
  - `planes[].modules_per_string`: Number of the PV modules of the strings of this plane.
  - `planes[].strings_per_inverter`: Number of the strings of the inverter of this plane.
  - `provider_settings.import_file_path`: Path to the file to import PV forecast data from.
  - `provider_settings.import_json`: JSON string, dictionary of PV forecast value lists.

------

Some of the planes configuration options directly follow the
[PVGIS](https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/getting-started-pvgis/pvgis-user-manual_en)
nomenclature.

Detailed definitions taken from **PVGIS**:

- `pvtechchoice`

The performance of PV modules depends on the temperature and on the solar irradiance, but the exact
dependence varies between different types of PV modules. At the moment we can estimate the losses
due to temperature and irradiance effects for the following types of modules: crystalline silicon
cells; thin film modules made from CIS or CIGS and thin film modules made from Cadmium Telluride
(CdTe).

For other technologies (especially various amorphous technologies), this correction cannot be
calculated here. If you choose one of the first three options here the calculation of performance
will take into account the temperature dependence of the performance of the chosen technology. If
you choose the other option (other/unknown), the calculation will assume a loss of 8% of power due
to temperature effects (a generic value which has found to be reasonable for temperate climates).

PV power output also depends on the spectrum of the solar radiation. PVGIS can calculate how the
variations of the spectrum of sunlight affects the overall energy production from a PV system. At
the moment this calculation can be done for crystalline silicon and CdTe modules. Note that this
calculation is not yet available when using the NSRDB solar radiation database.

- `peakpower`

This is the power that the manufacturer declares that the PV array can produce under standard test
conditions (STC), which are a constant 1000W of solar irradiation per square meter in the plane of
the array, at an array temperature of 25°C. The peak power should be entered in kilowatt-peak (kWp).
If you do not know the declared peak power of your modules but instead know the area of the modules
and the declared conversion efficiency (in percent), you can calculate the peak power as
power = area * efficiency / 100.

Bifacial modules: PVGIS doesn't make specific calculations for bifacial modules at present. Users
who wish to explore the possible benefits of this technology can input the power value for Bifacial
Nameplate Irradiance. This can also be can also be estimated from the front side peak power P_STC
value and the bifaciality factor, φ (if reported in the module data sheet) as:
P_BNPI  = P_STC \* (1 + φ \* 0.135). NB this bifacial approach  is not appropriate for BAPV or BIPV
installations or for modules mounting on a N-S axis i.e. facing E-W.

- `loss`

The estimated system losses are all the losses in the system, which cause the power actually
delivered to the electricity grid to be lower than the power produced by the PV modules. There are
several causes for this loss, such as losses in cables, power inverters, dirt (sometimes snow) on
the modules and so on. Over the years the modules also tend to lose a bit of their power, so the
average yearly output over the lifetime of the system will be a few percent lower than the output
in the first years.

We have given a default value of 14% for the overall losses. If you have a good idea that your value
will be different (maybe due to a really high-efficiency inverter) you may reduce this value a little.

- `mountingplace`

For fixed (non-tracking) systems, the way the modules are mounted will have an influence on the
temperature of the module, which in turn affects the efficiency. Experiments have shown that if the
movement of air behind the modules is restricted, the modules can get considerably hotter
(up to 15°C at 1000W/m2 of sunlight).

In PVGIS there are two possibilities: free-standing, meaning that the modules are mounted on a rack
with air flowing freely behind the modules; and building- integrated, which means that the modules
are completely built into the structure of the wall or roof of a building, with no air movement
behind the modules.

Some types of mounting are in between these two extremes, for instance if the modules are mounted on
a roof with curved roof tiles, allowing air to move behind the modules. In such cases, the
performance will be somewhere between the results of the two calculations that are possible here.

- `userhorizon`

Elevation of horizon in degrees, at equally spaced azimuth clockwise from north. In the user horizon
data each number represents the horizon height in degrees in a certain compass direction around the
point of interest. The horizon heights should be given in a clockwise direction starting at North;
that is, from North, going to East, South, West, and back to North. The values are assumed to
represent equal angular distance around the horizon. For instance, if you have 36 values, the first
point is due north, the next is 10 degrees east of north, and so on, until the last point, 10
degrees west of north.

------

Most of the configuration options are in line with the
[PVLib](https://pvlib-python.readthedocs.io/en/stable/_modules/pvlib/iotools/pvgis.html)
definition for PVGIS data.

Detailed definitions from **PVLib** for PVGIS data.

- `surface_tilt`:

Tilt angle from horizontal plane.

- `surface_azimuth`

Orientation (azimuth angle) of the (fixed) plane. Clockwise from north (north=0, east=90, south=180,
west=270). This is offset 180 degrees from the convention used by PVGIS.

------

### PVForecastAkkudoktor Provider

The `PVForecastAkkudoktor` provider retrieves the PV power forecast data directly from
**Akkudoktor.net**.

The following prediction configuration options of the PV system must be set:

- `general.latitude`: Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°)"
- `general.longitude`: Longitude in decimal degrees, within -180 to 180 (°)

For each plane of the PV system the following configuration options must be set:

- `pvforecast.planes[].surface_tilt`: Tilt angle from horizontal plane. Ignored for two-axis tracking.
- `pvforecast.planes[].surface_azimuth`: Orientation (azimuth angle) of the (fixed) plane.
                                         Clockwise from north (north=0, east=90, south=180, west=270).
- `pvforecast.planes[].userhorizon`: Elevation of horizon in degrees, at equally spaced azimuth clockwise from north.
- `pvforecast.planes[].inverter_paco`: AC power rating of the inverter. [W]
- `pvforecast.planes[].peakpower`: Nominal power of PV system in kW.

Example:

```Python
{
  "general": {
    "latitude": 50.1234,
    "longitude": 9.7654,
  },
  "pvforecast": {
    "provider": "PVForecastAkkudoktor",
    "planes": [
      {
        "peakpower": 5.0,
        "surface_azimuth": -10,
        "surface_tilt": 7,
        "userhorizon": [20, 27, 22, 20],
        "inverter_paco": 10000,
      },
      {
        "peakpower": 4.8,
        "surface_azimuth": -90,
        "surface_tilt": 7,
        "userhorizon": [30, 30, 30, 50],
        "inverter_paco": 10000,
      },
      {
        "peakpower": 1.4,
        "surface_azimuth": -40,
        "surface_tilt": 60,
        "userhorizon": [60, 30, 0, 30],
        "inverter_paco": 2000,
      },
      {
        "peakpower": 1.6,
        "surface_azimuth": 5,
        "surface_tilt": 45,
        "userhorizon": [45, 25, 30, 60],
        "inverter_paco": 1400,
      }
    ]
  }
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
<project:#prediction-import-providers>. The data source can be given in the
`import_file_path` or `import_json` configuration option.

The data may additionally or solely be provided by the
**PUT** `/v1/prediction/import/PVForecastImport` endpoint.

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

- `weather`: General weather configuration.

  - `provider`: Load provider id of provider to be used.

    - `BrightSky`: Retrieves from [BrightSky](https://api.brightsky.dev).
    - `ClearOutside`: Retrieves from [ClearOutside](https://clearoutside.com/forecast).
    - `LoadImport`: Imports from a file or JSON string.

  - `provider_settings.import_file_path`: Path to the file to import weatherforecast data from.
  - `provider_settings.import_json`: JSON string, dictionary of weather forecast value lists.

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

The prediction keys for the weather forecast data are:

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
<project:#prediction-import-providers>. The data source can be given in the
`import_file_path` or `import_json` configuration option.

The data may additionally or solely be provided by the
**PUT** `/v1/prediction/import/WeatherImport` endpoint.
