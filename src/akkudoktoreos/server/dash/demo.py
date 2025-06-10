import json
from pathlib import Path
from typing import Union

import pandas as pd
import requests
from bokeh.models import ColumnDataSource, LinearAxis, Range1d
from bokeh.plotting import figure
from monsterui.franken import FT, Grid, P

from akkudoktoreos.core.pydantic import PydanticDateTimeDataFrame
from akkudoktoreos.server.dash.bokeh import Bokeh

DIR_DEMODATA = Path(__file__).absolute().parent.joinpath("data")
FILE_DEMOCONFIG = DIR_DEMODATA.joinpath("democonfig.json")
if not FILE_DEMOCONFIG.exists():
    raise ValueError(f"File does not exist: {FILE_DEMOCONFIG}")

# bar width for 1 hour bars (time given in millseconds)
BAR_WIDTH_1HOUR = 1000 * 60 * 60


def DemoPVForecast(predictions: pd.DataFrame, config: dict) -> FT:
    source = ColumnDataSource(predictions)
    provider = config["pvforecast"]["provider"]

    plot = figure(
        x_axis_type="datetime",
        title=f"PV Power Prediction ({provider})",
        x_axis_label="Datetime",
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

    return Bokeh(plot)


def DemoElectricityPriceForecast(predictions: pd.DataFrame, config: dict) -> FT:
    source = ColumnDataSource(predictions)
    provider = config["elecprice"]["provider"]

    plot = figure(
        x_axis_type="datetime",
        y_range=Range1d(
            predictions["elecprice_marketprice_kwh"].min() - 0.1,
            predictions["elecprice_marketprice_kwh"].max() + 0.1,
        ),
        title=f"Electricity Price Prediction ({provider})",
        x_axis_label="Datetime",
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

    return Bokeh(plot)


def DemoWeatherTempAirHumidity(predictions: pd.DataFrame, config: dict) -> FT:
    source = ColumnDataSource(predictions)
    provider = config["weather"]["provider"]

    plot = figure(
        x_axis_type="datetime",
        title=f"Air Temperature and Humidity Prediction ({provider})",
        x_axis_label="Datetime",
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

    return Bokeh(plot)


def DemoWeatherIrradiance(predictions: pd.DataFrame, config: dict) -> FT:
    source = ColumnDataSource(predictions)
    provider = config["weather"]["provider"]

    plot = figure(
        x_axis_type="datetime",
        title=f"Irradiance Prediction ({provider})",
        x_axis_label="Datetime",
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

    return Bokeh(plot)


def DemoLoad(predictions: pd.DataFrame, config: dict) -> FT:
    source = ColumnDataSource(predictions)
    provider = config["load"]["provider"]
    if provider == "LoadAkkudoktor":
        year_energy = config["load"]["provider_settings"]["loadakkudoktor_year_energy"]
        provider = f"{provider}, {year_energy} kWh"

    plot = figure(
        x_axis_type="datetime",
        title=f"Load Prediction ({provider})",
        x_axis_label="Datetime",
        y_axis_label="Load [W]",
        sizing_mode="stretch_width",
        height=400,
    )
    # Add secondary y-axis for stddev
    stddev_min = predictions["load_std"].min()
    stddev_max = predictions["load_std"].max()
    plot.extra_y_ranges["stddev"] = Range1d(start=stddev_min - 5, end=stddev_max + 5)
    y2_axis = LinearAxis(y_range_name="stddev", axis_label="Load Standard Deviation [W]")
    y2_axis.axis_label_text_color = "green"
    plot.add_layout(y2_axis, "left")

    plot.line(
        "date_time",
        "load_mean",
        source=source,
        legend_label="Load mean value",
        color="red",
    )
    plot.line(
        "date_time",
        "load_mean_adjusted",
        source=source,
        legend_label="Load adjusted by measurement",
        color="blue",
    )
    plot.line(
        "date_time",
        "load_std",
        source=source,
        legend_label="Load standard deviation",
        color="green",
        y_range_name="stddev",
    )

    return Bokeh(plot)


def Demo(eos_host: str, eos_port: Union[str, int]) -> str:
    server = f"http://{eos_host}:{eos_port}"

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

    # Set demo configuration
    with FILE_DEMOCONFIG.open("r", encoding="utf-8") as fd:
        democonfig = json.load(fd)
    try:
        result = requests.put(f"{server}/v1/config", json=democonfig, timeout=10)
        result.raise_for_status()
    except requests.exceptions.HTTPError as err:
        detail = result.json()["detail"]
        # Try to reset to original config
        requests.put(f"{server}/v1/config", json=config, timeout=10)
        return P(
            f"Can not set demo configuration on {server}: {err}, {detail}",
            cls="text-center",
        )

    # Update all predictions
    try:
        result = requests.post(f"{server}/v1/prediction/update", timeout=10)
        result.raise_for_status()
    except requests.exceptions.HTTPError as err:
        detail = result.json()["detail"]
        # Try to reset to original config
        requests.put(f"{server}/v1/config", json=config, timeout=10)
        return P(
            f"Can not update predictions on {server}: {err}, {detail}",
            cls="text-center",
        )

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
                "load_mean",
                "load_std",
                "load_mean_adjusted",
            ],
        }
        result = requests.get(f"{server}/v1/prediction/dataframe", params=params, timeout=10)
        result.raise_for_status()
        predictions = PydanticDateTimeDataFrame(**result.json()).to_dataframe()
    except requests.exceptions.HTTPError as err:
        detail = result.json()["detail"]
        return P(
            f"Can not retrieve predictions from {server}: {err}, {detail}",
            cls="text-center",
        )
    except Exception as err:
        return P(
            f"Can not retrieve predictions from {server}: {err}",
            cls="text-center",
        )

    # Reset to original config
    requests.put(f"{server}/v1/config", json=config, timeout=10)

    return Grid(
        DemoPVForecast(predictions, democonfig),
        DemoElectricityPriceForecast(predictions, democonfig),
        DemoWeatherTempAirHumidity(predictions, democonfig),
        DemoWeatherIrradiance(predictions, democonfig),
        DemoLoad(predictions, democonfig),
        cols_max=2,
    )
