#!/usr/bin/env python3

import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Optional

import matplotlib
import uvicorn
from fastapi.exceptions import HTTPException

# Sets the Matplotlib backend to 'Agg' for rendering plots in environments without a display
matplotlib.use("Agg")

import pandas as pd
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, RedirectResponse

from akkudoktoreos.class_load import LoadForecast
from akkudoktoreos.class_load_container import Gesamtlast
from akkudoktoreos.class_load_corrector import LoadPredictionAdjuster
from akkudoktoreos.class_optimize import (
    OptimizationParameters,
    OptimizeResponse,
    optimization_problem,
)
from akkudoktoreos.class_pv_forecast import ForecastResponse, PVForecast
from akkudoktoreos.class_strompreis import HourlyElectricityPriceForecast
from akkudoktoreos.config import (
    SetupIncomplete,
    get_start_enddate,
    get_working_dir,
    load_config,
)

app = FastAPI(
    title="Akkudoktor-EOS",
    description="This project provides a comprehensive solution for simulating and optimizing an energy system based on renewable energy sources. With a focus on photovoltaic (PV) systems, battery storage (batteries), load management (consumer requirements), heat pumps, electric vehicles, and consideration of electricity price data, this system enables forecasting and optimization of energy flow and costs over a specified period.",
    summary="Comprehensive solution for simulating and optimizing an energy system based on renewable energy sources",
    version="0.0.1",
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
)

working_dir = get_working_dir()
# copy config to working directory. Make this a CLI option later
config = load_config(working_dir, True)
opt_class = optimization_problem(config)
server_dir = Path(__file__).parent.resolve()


class PdfResponse(FileResponse):
    media_type = "application/pdf"


@app.get("/strompreis")
def fastapi_strompreis() -> list[float]:
    # Get the current date and the end date based on prediction hours
    date_now, date = get_start_enddate(config.eos.prediction_hours, startdate=datetime.now().date())
    price_forecast = HourlyElectricityPriceForecast(
        source=f"https://api.akkudoktor.net/prices?start={date_now}&end={date}",
        config=config,
        use_cache=False,
    )
    specific_date_prices = price_forecast.get_price_for_daterange(
        date_now, date
    )  # Fetch prices for the specified date range
    return specific_date_prices.tolist()


@app.post("/gesamtlast")
def fastapi_gesamtlast(
    year_energy: float,
    measured_data: list[dict[str, Any]],
    hours: int = config.eos.prediction_hours,
) -> list[float]:
    """Endpoint to handle total load calculation based on the latest measured data."""
    # Measured data in JSON format
    measured_data_df = pd.DataFrame(measured_data)
    measured_data_df["time"] = pd.to_datetime(measured_data_df["time"])

    # Ensure datetime has timezone info for accurate calculations
    if measured_data_df["time"].dt.tz is None:
        measured_data_df["time"] = measured_data_df["time"].dt.tz_localize("Europe/Berlin")
    else:
        measured_data_df["time"] = measured_data_df["time"].dt.tz_convert("Europe/Berlin")

    # Remove timezone info after conversion to simplify further processing
    measured_data_df["time"] = measured_data_df["time"].dt.tz_localize(None)

    # Instantiate LoadForecast and generate forecast data
    lf = LoadForecast(
        filepath=server_dir / ".." / "data" / "load_profiles.npz", year_energy=year_energy
    )
    forecast_list = []

    # Generate daily forecasts for the date range based on measured data
    for single_date in pd.date_range(
        measured_data_df["time"].min().date(), measured_data_df["time"].max().date()
    ):
        date_str = single_date.strftime("%Y-%m-%d")
        daily_forecast = lf.get_daily_stats(date_str)
        mean_values = daily_forecast[0]
        fc_hours = [single_date + pd.Timedelta(hours=i) for i in range(24)]
        daily_forecast_df = pd.DataFrame({"time": fc_hours, "Last Pred": mean_values})
        forecast_list.append(daily_forecast_df)

    # Concatenate all daily forecasts into a single DataFrame
    predicted_data = pd.concat(forecast_list, ignore_index=True)

    # Create LoadPredictionAdjuster instance to adjust the predictions based on measured data
    adjuster = LoadPredictionAdjuster(measured_data_df, predicted_data, lf)
    adjuster.calculate_weighted_mean()  # Calculate weighted mean for adjustment
    adjuster.adjust_predictions()  # Adjust predictions based on measured data
    future_predictions = adjuster.predict_next_hours(hours)  # Predict future load

    # Extract household power predictions
    leistung_haushalt = future_predictions["Adjusted Pred"].values
    gesamtlast = Gesamtlast(prediction_hours=hours)
    gesamtlast.hinzufuegen(
        "Haushalt", leistung_haushalt
    )  # Add household load to total load calculation

    # Calculate the total load
    last = gesamtlast.gesamtlast_berechnen()  # Compute total load
    return last.tolist()


@app.get("/gesamtlast_simple")
def fastapi_gesamtlast_simple(year_energy: float) -> list[float]:
    date_now, date = get_start_enddate(
        config.eos.prediction_hours, startdate=datetime.now().date()
    )  # Get the current date and prediction end date

    ###############
    # Load Forecast
    ###############
    lf = LoadForecast(
        filepath=server_dir / ".." / "data" / "load_profiles.npz", year_energy=year_energy
    )  # Instantiate LoadForecast with specified parameters
    leistung_haushalt = lf.get_stats_for_date_range(date_now, date)[
        0
    ]  # Get expected household load for the date range

    gesamtlast = Gesamtlast(
        prediction_hours=config.eos.prediction_hours
    )  # Create Gesamtlast instance
    gesamtlast.hinzufuegen(
        "Haushalt", leistung_haushalt
    )  # Add household load to total load calculation

    # ###############
    # # WP (Heat Pump)
    # ##############
    # leistung_wp = wp.simulate_24h(temperature_forecast)  # Simulate heat pump load for 24 hours
    # gesamtlast.hinzufuegen("Heatpump", leistung_wp)  # Add heat pump load to total load calculation

    last = gesamtlast.gesamtlast_berechnen()  # Calculate total load
    print(last)  # Output total load
    return last.tolist()  # Return total load as JSON


@app.get("/pvforecast")
def fastapi_pvprognose(url: str, ac_power_measurement: Optional[float] = None) -> ForecastResponse:
    date_now, date = get_start_enddate(config.eos.prediction_hours, startdate=datetime.now().date())

    ###############
    # PV Forecast
    ###############
    PVforecast = PVForecast(
        prediction_hours=config.eos.prediction_hours, url=url
    )  # Instantiate PVForecast with given parameters
    if ac_power_measurement is not None:
        PVforecast.update_ac_power_measurement(
            date_time=datetime.now(),
            ac_power_measurement=ac_power_measurement,
        )  # Update measurement

    # Get PV forecast and temperature forecast for the specified date range
    pv_forecast = PVforecast.get_pv_forecast_for_date_range(date_now, date)
    temperature_forecast = PVforecast.get_temperature_for_date_range(date_now, date)

    # Return both forecasts as a JSON response
    ret = {
        "temperature": temperature_forecast.tolist(),
        "pvpower": pv_forecast.tolist(),
    }
    return ret


@app.post("/optimize")
def fastapi_optimize(
    parameters: OptimizationParameters,
    start_hour: Annotated[
        Optional[int], Query(description="Defaults to current hour of the day.")
    ] = None,
) -> OptimizeResponse:
    if start_hour is None:
        start_hour = datetime.now().hour

    # Perform optimization simulation
    result = opt_class.optimierung_ems(parameters=parameters, start_hour=start_hour)
    # print(result)
    # convert to JSON (None accepted by dumps)
    return result


@app.get("/visualization_results.pdf", response_class=PdfResponse)
def get_pdf():
    # Endpoint to serve the generated PDF with visualization results
    output_path = config.working_dir / config.directories.output
    if not output_path.is_dir():
        raise SetupIncomplete(f"Output path does not exist: {output_path}.")
    file_path = output_path / "visualization_results.pdf"
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="No visualization result available.")
    return FileResponse(file_path)


@app.get("/site-map", include_in_schema=False)
def site_map():
    return RedirectResponse(url="/docs")


@app.get("/", include_in_schema=False)
def root():
    # Redirect the root URL to the site map
    return RedirectResponse(url="/docs")


if __name__ == "__main__":
    try:
        config.run_setup()
    except Exception as e:
        print(f"Failed to initialize: {e}")
        exit(1)

    # Set host and port from environment variables or defaults
    host = os.getenv("EOS_RUN_HOST", "0.0.0.0")
    port = os.getenv("EOS_RUN_PORT", 8503)
    try:
        uvicorn.run(app, host=host, port=int(port))  # Run the FastAPI application
    except Exception as e:
        print(
            f"Could not bind to host {host}:{port}. Error: {e}"
        )  # Error handling for binding issues
        exit(1)
else:
    # started from cli / dev server
    config.run_setup()
