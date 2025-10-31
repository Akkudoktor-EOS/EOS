from typing import Optional, Union

import pandas as pd
import requests
from bokeh.models import ColumnDataSource, LinearAxis, Range1d
from bokeh.plotting import figure
from monsterui.franken import FT, Grid, P

from akkudoktoreos.core.pydantic import PydanticDateTimeDataFrame
from akkudoktoreos.server.dash.bokeh import Bokeh, bokey_apply_theme_to_plot
from akkudoktoreos.server.dash.components import Error

# bar width for 1 hour bars (time given in millseconds)
BAR_WIDTH_1HOUR = 1000 * 60 * 60


def PVForecast(predictions: pd.DataFrame, config: dict, date_time_tz: str, dark: bool) -> FT:
    source = ColumnDataSource(predictions)
    provider = config["pvforecast"]["provider"]

    plot = figure(
        x_axis_type="datetime",
        title=f"PV Power Prediction ({provider})",
        x_axis_label=f"Datetime [localtime {date_time_tz}]",
        y_axis_label="Power [W]",
        sizing_mode="stretch_width",
        height=400,
    )
    plot.vbar(
        x="date_time",
        top="pvforecast_ac_power",
        source=source,
        width=BAR_WIDTH_1HOUR * 0.8,
        legend_label="AC Power",
        color="lightblue",
    )
    plot.toolbar.autohide = True
    bokey_apply_theme_to_plot(plot, dark)

    return Bokeh(plot)


def ElectricityPriceForecast(
    predictions: pd.DataFrame, config: dict, date_time_tz: str, dark: bool
) -> FT:
    source = ColumnDataSource(predictions)
    provider = config["elecprice"]["provider"]

    plot = figure(
        x_axis_type="datetime",
        y_range=Range1d(
            predictions["elecprice_marketprice_kwh"].min() - 0.1,
            predictions["elecprice_marketprice_kwh"].max() + 0.1,
        ),
        title=f"Electricity Price Prediction ({provider})",
        x_axis_label=f"Datetime [localtime {date_time_tz}]",
        y_axis_label="Price [€/kWh]",
        sizing_mode="stretch_width",
        height=400,
    )
    plot.vbar(
        x="date_time",
        top="elecprice_marketprice_kwh",
        source=source,
        width=BAR_WIDTH_1HOUR * 0.8,
        legend_label="Market Price",
        color="lightblue",
    )
    plot.toolbar.autohide = True
    bokey_apply_theme_to_plot(plot, dark)

    return Bokeh(plot)


def WeatherTempAirHumidityForecast(
    predictions: pd.DataFrame, config: dict, date_time_tz: str, dark: bool
) -> FT:
    source = ColumnDataSource(predictions)
    provider = config["weather"]["provider"]

    plot = figure(
        x_axis_type="datetime",
        title=f"Air Temperature and Humidity Prediction ({provider})",
        x_axis_label=f"Datetime [localtime {date_time_tz}]",
        y_axis_label="Temperature [°C]",
        sizing_mode="stretch_width",
        height=400,
    )
    # Add secondary y-axis for humidity
    plot.extra_y_ranges["humidity"] = Range1d(start=-5, end=105)
    y2_axis = LinearAxis(y_range_name="humidity", axis_label="Relative Humidity [%]")
    y2_axis.axis_label_text_color = "green"
    plot.add_layout(y2_axis, "left")

    plot.line(
        "date_time", "weather_temp_air", source=source, legend_label="Air Temperature", color="blue"
    )
    plot.line(
        "date_time",
        "weather_relative_humidity",
        source=source,
        legend_label="Relative Humidity [%]",
        color="green",
        y_range_name="humidity",
    )
    plot.toolbar.autohide = True
    bokey_apply_theme_to_plot(plot, dark)

    return Bokeh(plot)


def WeatherIrradianceForecast(
    predictions: pd.DataFrame, config: dict, date_time_tz: str, dark: bool
) -> FT:
    source = ColumnDataSource(predictions)
    provider = config["weather"]["provider"]

    plot = figure(
        x_axis_type="datetime",
        title=f"Irradiance Prediction ({provider})",
        x_axis_label=f"Datetime [localtime {date_time_tz}]",
        y_axis_label="Irradiance [W/m2]",
        sizing_mode="stretch_width",
        height=400,
    )
    plot.line(
        "date_time",
        "weather_ghi",
        source=source,
        legend_label="Global Horizontal Irradiance",
        color="red",
    )
    plot.line(
        "date_time",
        "weather_dni",
        source=source,
        legend_label="Direct Normal Irradiance",
        color="green",
    )
    plot.line(
        "date_time",
        "weather_dhi",
        source=source,
        legend_label="Diffuse Horizontal Irradiance",
        color="blue",
    )
    plot.toolbar.autohide = True
    bokey_apply_theme_to_plot(plot, dark)

    return Bokeh(plot)


def LoadForecast(predictions: pd.DataFrame, config: dict, date_time_tz: str, dark: bool) -> FT:
    source = ColumnDataSource(predictions)
    provider = config["load"]["provider"]
    if provider == "LoadAkkudoktorAdjusted":
        year_energy = config["load"]["provider_settings"]["LoadAkkudoktor"][
            "loadakkudoktor_year_energy_kwh"
        ]
        provider = f"{provider}, {year_energy} kWh"

    plot = figure(
        title=f"Load Prediction ({provider})",
        x_axis_type="datetime",
        x_axis_label=f"Datetime [localtime {date_time_tz}]",
        y_axis_label="Load [W]",
        sizing_mode="stretch_width",
        height=400,
    )
    # Add secondary y-axis for stddev
    stddev_min = predictions["loadakkudoktor_std_power_w"].min()
    stddev_max = predictions["loadakkudoktor_std_power_w"].max()
    plot.extra_y_ranges["stddev"] = Range1d(start=stddev_min - 5, end=stddev_max + 5)
    y2_axis = LinearAxis(y_range_name="stddev", axis_label="Load Standard Deviation [W]")
    y2_axis.axis_label_text_color = "green"
    plot.add_layout(y2_axis, "left")

    plot.line(
        "date_time",
        "loadforecast_power_w",
        source=source,
        legend_label="Load forcast value (adjusted by measurement)",
        color="red",
    )
    plot.line(
        "date_time",
        "loadakkudoktor_mean_power_w",
        source=source,
        legend_label="Load mean value",
        color="blue",
    )
    plot.line(
        "date_time",
        "loadakkudoktor_std_power_w",
        source=source,
        legend_label="Load standard deviation",
        color="green",
        y_range_name="stddev",
    )
    plot.toolbar.autohide = True
    bokey_apply_theme_to_plot(plot, dark)

    return Bokeh(plot)


def Prediction(eos_host: str, eos_port: Union[str, int], data: Optional[dict] = None) -> str:
    server = f"http://{eos_host}:{eos_port}"

    dark = False
    if data and data.get("dark", None) == "true":
        dark = True

    # Get current configuration from server
    try:
        result = requests.get(f"{server}/v1/config", timeout=10)
        result.raise_for_status()
    except requests.exceptions.HTTPError as err:
        detail = result.json()["detail"]
        return P(
            f"Can not retrieve configuration from {server}: {err}, {detail}",
            cls="text-center",
        )
    config = result.json()

    # Get Forecasts
    try:
        params = {
            "keys": [
                "pvforecast_ac_power",
                "elecprice_marketprice_kwh",
                "weather_relative_humidity",
                "weather_temp_air",
                "weather_ghi",
                "weather_dni",
                "weather_dhi",
                "loadforecast_power_w",
                "loadakkudoktor_std_power_w",
                "loadakkudoktor_mean_power_w",
            ],
        }
        result = requests.get(f"{server}/v1/prediction/dataframe", params=params, timeout=10)
        result.raise_for_status()
        predictions = PydanticDateTimeDataFrame(**result.json()).to_dataframe()
    except requests.exceptions.HTTPError as err:
        detail = result.json()["detail"]
        return Error(f"Can not retrieve predictions from {server}: {err}, {detail}")
    except Exception as err:
        return Error(f"Can not retrieve predictions from {server}: {err}")

    # Remove time offset from UTC to get naive local time and make bokeh plot in local time
    date_time_tz = predictions["date_time"].dt.tz
    predictions["date_time"] = pd.to_datetime(predictions["date_time"]).dt.tz_localize(None)

    return Grid(
        PVForecast(predictions, config, date_time_tz, dark),
        ElectricityPriceForecast(predictions, config, date_time_tz, dark),
        WeatherTempAirHumidityForecast(predictions, config, date_time_tz, dark),
        WeatherIrradianceForecast(predictions, config, date_time_tz, dark),
        LoadForecast(predictions, config, date_time_tz, dark),
        cols_max=2,
    )
