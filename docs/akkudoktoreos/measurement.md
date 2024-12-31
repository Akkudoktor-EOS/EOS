% SPDX-License-Identifier: Apache-2.0

# Measurements

Measurements are utilized to refine predictions using real data from your system, thereby enhancing
accuracy.

- **Household Load Measurement**
- **Grid Export Measurement**
- **Grid Import Measurement**

## Storing Measurements

EOS stores measurements in a **key-value store**, where the term `measurement key` refers to the
unique identifier used to store and retrieve specific measurement data. Note that the key-value
store is memory-based, meaning that all stored data will be lost upon restarting the EOS REST
server.

:::{admonition} Todo
:class: note
Ensure that measurement data persists across server restarts.
:::

Several endpoints of the EOS REST server allow for the management and retrieval of these
measurements.

The measurement data must be or is provided in one of the following formats:

### 1. DateTimeData

A dictionary with the following structure:

```JSON
    {
        "start_datetime": "2024-01-01 00:00:00",
        "interval": "1 Hour",
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

## Load Measurement

The EOS measurement store provides for storing meter readings of loads. There are currently five loads
foreseen. The associated `measurement key`s are:

- `measurement_load0_mr`: Load0 meter reading [kWh]
- `measurement_load1_mr`: Load1 meter reading [kWh]
- `measurement_load2_mr`: Load2 meter reading [kWh]
- `measurement_load3_mr`: Load3 meter reading [kWh]
- `measurement_load4_mr`: Load4 meter reading [kWh]

For ease of use, you can assign descriptive names to the `measurement key`s to represent your
system's load sources. Use the following `configuration options` to set these names
(e.g., 'Dish Washer', 'Heat Pump'):

- `measurement_load0_name`: Name of the load0 source
- `measurement_load1_name`: Name of the load1 source
- `measurement_load2_name`: Name of the load2 source
- `measurement_load3_name`: Name of the load3 source
- `measurement_load4_name`: Name of the load4 source

Load measurements can be stored for any datetime. The values between different meter readings are
linearly approximated. Since optimization occurs on the hour, storing values between hours is
generally not useful.

The EOS measurement store automatically sums all given loads to create a total load value series
for specified intervals, usually one hour. This aggregated data can be used for load predictions.

## Grid Export/ Import Measurement

The EOS measurement store also allows for the storage of meter readings for grid import and export.
The associated `measurement key`s are:

- `measurement_grid_export_mr`: Export to grid meter reading [kWh]
- `measurement_grid_import_mr`: Import from grid meter reading [kWh]

:::{admonition} Todo
:class: note
Currently not used. Integrate grid meter readings into the respective predictions.
:::
