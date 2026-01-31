% SPDX-License-Identifier: Apache-2.0
(measurement-page)=

# Measurements

Measurements are utilized to refine predictions using real data from your system, thereby enhancing
accuracy.

- Household Load Measurement
- Grid Export Measurement
- Grid Import Measurement

## Storing Measurements

EOS stores measurements in a **key-value store**, where the term `measurement key` refers to the
unique identifier used to store and retrieve specific measurement data.

Several endpoints of the EOS REST server allow for the management and retrieval of these
measurements.

The measurement data must be or is provided in one of the following formats:

### 1. DateTimeData

A dictionary with the following structure:

```json
{
    "start_datetime": "2024-01-01 00:00:00",
    "interval": "1 hour",
    "<measurement key>": [value, value, ...],
    "<measurement key>": [value, value, ...],
    ...
}
```

### 2. DateTimeDataFrame

A JSON string created from a [pandas](https://pandas.pydata.org/docs/index.html) dataframe with a
`DatetimeIndex`. Use [pandas.DataFrame.to_json(orient="index")](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.to_json.html#pandas.DataFrame.to_json).
The column name of the data must be the same as the names of the `measurement key`s.

### 3. DateTimeSeries

A JSON string created from a [pandas](https://pandas.pydata.org/docs/index.html) series with a
`DatetimeIndex`. Use [pandas.Series.to_json(orient="index")](https://pandas.pydata.org/docs/reference/api/pandas.Series.to_json.html#pandas.Series.to_json).

Creates a dictionary like this:

```json
{
  "data": {
     "2024-01-01T00:00:00+01:00": 1,
     "2024-01-02T00:00:00+01:00": 2,
     "2024-01-03T00:00:00+01:00": 3,
     ...
  },
  "dtype": "float64",
  "tz": "Europe/Berlin"
}
```

## Load Measurement

The EOS measurement store provides for storing energy meter readings of loads.

The associated `measurement key`s can be configured by:

```json
{
  "measurement": {
    "load_emr_keys": ["load0_emr", "my special load", ...]
  }
}
```

Load measurements can be stored for any datetime. The values between different meter readings are
linearly approximated. Storing values between optimization intervals is generally not useful.

The EOS measurement store automatically sums all given loads to create a total load value series
for specified intervals, usually one hour. This aggregated data can be used for load predictions.

:::{admonition} Warning
:class: warning
Only use **actual meter readings** in **kWh**, not energy consumption.
Example: `112345.77`, `112389.23`, `112412.55`, …
:::

## Grid Export/ Import Measurement

The EOS measurement store also allows for the storage of meter readings for grid import and export.

The associated `measurement key`s can be configured by:

```json
{
  "measurement": {
    "grid_export_emr_keys": ["grid_export_emr", ...],
    "grid_import_emr_keys": ["grid_import_emr", ...],
  }
}
```

:::{admonition} Todo
:class: note
Currently not used. Integrate grid meter readings into the respective predictions.
:::

## Battery/ Electric Vehicle State of Charge (SoC) Measurement

The state of charge (SoC) measurement of batteries and electric vehicle batteries can be stored.

The associated `measurement key` is pre-defined by the device configuration. It can be
determined from the device configuration by the read-only `measurement_key_soc_factor` configuration
option.

## Battery/ Electric Vehicle Power Measurement

The charge/ discharge power measurements of batteries and electric vehicle batteries can be stored.
Charging power is denoted by a negative value, discharging power by a positive value.

The associated `measurement key`s are pre-defined by the device configuration. They can be
determined from the device configuration by read-only configuration options:

- `measurement_key_power_l1_w`
- `measurement_key_power_l2_w`
- `measurement_key_power_l3_w`
- `measurement_key_power_3_phase_sym_w`
