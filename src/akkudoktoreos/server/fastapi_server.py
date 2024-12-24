#!/usr/bin/env python3

import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncGenerator, Dict, List, Optional, Union

import httpx
import pandas as pd
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

from akkudoktoreos.config.config import ConfigEOS, SettingsEOS, get_config
from akkudoktoreos.core.pydantic import (
    PydanticBaseModel,
    PydanticDateTimeData,
    PydanticDateTimeDataFrame,
    PydanticDateTimeSeries,
)
from akkudoktoreos.measurement.measurement import get_measurement
from akkudoktoreos.optimization.genetic import (
    OptimizationParameters,
    OptimizeResponse,
    optimization_problem,
)

# Still to be adapted
from akkudoktoreos.prediction.load_aggregator import LoadAggregator
from akkudoktoreos.prediction.load_corrector import LoadPredictionAdjuster
from akkudoktoreos.prediction.load_forecast import LoadForecast
from akkudoktoreos.prediction.prediction import get_prediction
from akkudoktoreos.utils.datetimeutil import to_datetime
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)
config_eos = get_config()
measurement_eos = get_measurement()
prediction_eos = get_prediction()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan manager for the app."""
    # On startup
    if config_eos.server_fasthtml_host and config_eos.server_fasthtml_port:
        try:
            fasthtml_process = start_fasthtml_server()
        except Exception as e:
            logger.error(f"Failed to start FastHTML server. Error: {e}")
            sys.exit(1)
    # Handover to application
    yield
    # On shutdown
    # nothing to do


app = FastAPI(
    title="Akkudoktor-EOS",
    description="This project provides a comprehensive solution for simulating and optimizing an energy system based on renewable energy sources. With a focus on photovoltaic (PV) systems, battery storage (batteries), load management (consumer requirements), heat pumps, electric vehicles, and consideration of electricity price data, this system enables forecasting and optimization of energy flow and costs over a specified period.",
    summary="Comprehensive solution for simulating and optimizing an energy system based on renewable energy sources",
    version="0.0.1",
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
    lifespan=lifespan,
)

# That's the problem
opt_class = optimization_problem()

server_dir = Path(__file__).parent.resolve()


class PdfResponse(FileResponse):
    media_type = "application/pdf"


@app.get("/v1/config")
def fastapi_config_get() -> ConfigEOS:
    """Get the current configuration."""
    return config_eos


@app.put("/v1/config")
def fastapi_config_put(settings: SettingsEOS) -> ConfigEOS:
    """Merge settings into current configuration."""
    config_eos.merge_settings(settings)
    return config_eos


@app.get("/v1/measurement/keys")
def fastapi_measurement_keys_get() -> list[str]:
    """Get a list of available measurement keys."""
    return sorted(measurement_eos.record_keys)


@app.get("/v1/measurement/series")
def fastapi_measurement_series_get(key: str) -> PydanticDateTimeSeries:
    """Get the measurements of given key as series."""
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not available.")
    series = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(series)


@app.put("/v1/measurement/series")
def fastapi_measurement_series_put(
    datetime: Any, key: str, value: Union[float | str]
) -> PydanticDateTimeSeries:
    """Merge the measurement of given key into EOS measurements at given datetime."""
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not available.")
    measurement_eos.update_value(datetime, key, value)
    series = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(series)


@app.put("/v1/measurement/dataframe")
def fastapi_measurement_dataframe_put(data: PydanticDateTimeDataFrame) -> None:
    """Merge the measurement data given as dataframe into EOS measurements."""
    dataframe = data.to_dataframe()
    measurement_eos.import_from_dataframe(dataframe)


@app.put("/v1/measurement/data")
def fastapi_measurement_data_put(data: PydanticDateTimeData) -> None:
    """Merge the measurement data given as datetime data into EOS measurements."""
    datetimedata = data.to_dict()
    measurement_eos.import_from_dict(datetimedata)


@app.get("/v1/prediction/keys")
def fastapi_prediction_keys_get() -> list[str]:
    """Get a list of available prediction keys."""
    return sorted(prediction_eos.record_keys)


@app.get("/v1/prediction/series")
def fastapi_prediction_series_get(key: str) -> PydanticDateTimeSeries:
    """Get prediction for given key as series."""
    if key not in prediction_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not available.")
    series = prediction_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(series)


@app.put("/v1/prediction/series")
def fastapi_prediction_series_put(
    key: str, value: PydanticDateTimeSeries
) -> PydanticDateTimeSeries:
    """Merge prediction given as series into given key."""
    if key not in prediction_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not available.")
    series = value.to_series()
    prediction_eos.key_from_series(key=key, series=series)
    series = prediction_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(series)


@app.get("/strompreis")
def fastapi_strompreis() -> list[float]:
    # Get the current date and the end date based on prediction hours
    marketprice_series = prediction_eos["elecprice_marketprice"]
    # Fetch prices for the specified date range
    return prediction_eos.key_to_array(
        key="elecprice_marketprice",
        start_datetime=prediction_eos.start_datetime,
        end_datetime=prediction_eos.end_datetime,
    ).tolist()


class GesamtlastRequest(PydanticBaseModel):
    year_energy: float
    measured_data: List[Dict[str, Any]]
    hours: int


@app.post("/gesamtlast")
def fastapi_gesamtlast(request: GesamtlastRequest) -> list[float]:
    """Endpoint to handle total load calculation based on the latest measured data."""
    # Request-Daten extrahieren
    year_energy = request.year_energy
    measured_data = request.measured_data
    hours = request.hours

    # Ab hier bleibt der Code unverändert ...
    measured_data_df = pd.DataFrame(measured_data)
    measured_data_df["time"] = pd.to_datetime(measured_data_df["time"])

    # Zeitzonenmanagement
    if measured_data_df["time"].dt.tz is None:
        measured_data_df["time"] = measured_data_df["time"].dt.tz_localize("Europe/Berlin")
    else:
        measured_data_df["time"] = measured_data_df["time"].dt.tz_convert("Europe/Berlin")

    # Zeitzone entfernen
    measured_data_df["time"] = measured_data_df["time"].dt.tz_localize(None)

    # Forecast erstellen
    lf = LoadForecast(
        filepath=server_dir / ".." / "data" / "load_profiles.npz", year_energy=year_energy
    )
    forecast_list = []

    for single_date in pd.date_range(
        measured_data_df["time"].min().date(), measured_data_df["time"].max().date()
    ):
        date_str = single_date.strftime("%Y-%m-%d")
        daily_forecast = lf.get_daily_stats(date_str)
        mean_values = daily_forecast[0]
        fc_hours = [single_date + pd.Timedelta(hours=i) for i in range(24)]
        daily_forecast_df = pd.DataFrame({"time": fc_hours, "Last Pred": mean_values})
        forecast_list.append(daily_forecast_df)

    predicted_data = pd.concat(forecast_list, ignore_index=True)

    adjuster = LoadPredictionAdjuster(measured_data_df, predicted_data, lf)
    adjuster.calculate_weighted_mean()
    adjuster.adjust_predictions()
    future_predictions = adjuster.predict_next_hours(hours)

    leistung_haushalt = future_predictions["Adjusted Pred"].to_numpy()
    gesamtlast = LoadAggregator(prediction_hours=hours)
    gesamtlast.add_load(
        "Haushalt",
        tuple(leistung_haushalt),
    )

    return gesamtlast.calculate_total_load()


@app.get("/gesamtlast_simple")
def fastapi_gesamtlast_simple(year_energy: float) -> list[float]:
    ###############
    # Load Forecast
    ###############
    lf = LoadForecast(
        filepath=server_dir / ".." / "data" / "load_profiles.npz", year_energy=year_energy
    )  # Instantiate LoadForecast with specified parameters
    leistung_haushalt = lf.get_stats_for_date_range(
        prediction_eos.start_datetime, prediction_eos.end_datetime
    )[0]  # Get expected household load for the date range

    prediction_hours = config_eos.prediction_hours if config_eos.prediction_hours else 48
    gesamtlast = LoadAggregator(prediction_hours=prediction_hours)  # Create Gesamtlast instance
    gesamtlast.add_load(
        "Haushalt", tuple(leistung_haushalt)
    )  # Add household to total load calculation

    # ###############
    # # WP (Heat Pump)
    # ##############
    # leistung_wp = wp.simulate_24h(temperature_forecast)  # Simulate heat pump load for 24 hours
    # gesamtlast.hinzufuegen("Heatpump", leistung_wp)  # Add heat pump load to total load calculation

    return gesamtlast.calculate_total_load()


class ForecastResponse(PydanticBaseModel):
    temperature: list[float]
    pvpower: list[float]


@app.get("/pvforecast")
def fastapi_pvprognose(ac_power_measurement: Optional[float] = None) -> ForecastResponse:
    ###############
    # PV Forecast
    ###############
    pvforecast_ac_power = prediction_eos["pvforecast_ac_power"]
    # Fetch prices for the specified date range
    pvforecast_ac_power = pvforecast_ac_power.loc[
        prediction_eos.start_datetime : prediction_eos.end_datetime
    ]
    pvforecastakkudoktor_temp_air = prediction_eos["pvforecastakkudoktor_temp_air"]
    # Fetch prices for the specified date range
    pvforecastakkudoktor_temp_air = pvforecastakkudoktor_temp_air.loc[
        prediction_eos.start_datetime : prediction_eos.end_datetime
    ]

    # Return both forecasts as a JSON response
    return ForecastResponse(
        temperature=pvforecastakkudoktor_temp_air.tolist(), pvpower=pvforecast_ac_power.tolist()
    )


@app.post("/optimize")
def fastapi_optimize(
    parameters: OptimizationParameters,
    start_hour: Annotated[
        Optional[int], Query(description="Defaults to current hour of the day.")
    ] = None,
) -> OptimizeResponse:
    if start_hour is None:
        start_hour = to_datetime().hour

    # TODO: Remove when config and prediction update is done by EMS.
    config_eos.update()
    prediction_eos.update_data()

    # Perform optimization simulation
    result = opt_class.optimierung_ems(parameters=parameters, start_hour=start_hour)
    # print(result)
    return result


@app.get("/visualization_results.pdf", response_class=PdfResponse)
def get_pdf() -> PdfResponse:
    # Endpoint to serve the generated PDF with visualization results
    output_path = config_eos.data_output_path
    if not output_path.is_dir():
        raise ValueError(f"Output path does not exist: {output_path}.")
    file_path = output_path / "visualization_results.pdf"
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="No visualization result available.")
    return PdfResponse(file_path)


@app.get("/site-map", include_in_schema=False)
def site_map() -> RedirectResponse:
    return RedirectResponse(url="/docs")


# Keep the proxy last to handle all requests that are not taken by the Rest API.
# Also keep the single endpoints for delete, get, post, put to assure openapi.json is always build
# the same way for testing.


@app.delete("/{path:path}")
async def proxy_delete(request: Request, path: str) -> Response:
    return await proxy(request, path)


@app.get("/{path:path}")
async def proxy_get(request: Request, path: str) -> Response:
    return await proxy(request, path)


@app.post("/{path:path}")
async def proxy_post(request: Request, path: str) -> Response:
    return await proxy(request, path)


@app.put("/{path:path}")
async def proxy_put(request: Request, path: str) -> Response:
    return await proxy(request, path)


async def proxy(request: Request, path: str) -> Union[Response | RedirectResponse]:
    if config_eos.server_fasthtml_host and config_eos.server_fasthtml_port:
        # Proxy to fasthtml server
        url = f"http://{config_eos.server_fasthtml_host}:{config_eos.server_fasthtml_port}/{path}"
        headers = dict(request.headers)

        data = await request.body()

        async with httpx.AsyncClient() as client:
            if request.method == "GET":
                response = await client.get(url, headers=headers)
            elif request.method == "POST":
                response = await client.post(url, headers=headers, content=data)
            elif request.method == "PUT":
                response = await client.put(url, headers=headers, content=data)
            elif request.method == "DELETE":
                response = await client.delete(url, headers=headers, content=data)

        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
        )
    else:
        # Redirect the root URL to the site map
        return RedirectResponse(url="/docs")


def start_fasthtml_server() -> subprocess.Popen:
    """Start the fasthtml server as a subprocess."""
    server_process = subprocess.Popen(
        [sys.executable, str(server_dir.joinpath("fasthtml_server.py"))],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return server_process


def start_fastapi_server() -> None:
    """Start FastAPI server."""
    try:
        uvicorn.run(
            app,
            host=str(config_eos.server_fastapi_host),
            port=config_eos.server_fastapi_port,
            log_level="debug",
            access_log=True,
        )
    except Exception as e:
        logger.error(
            f"Could not bind to host {config_eos.server_fastapi_host}:{config_eos.server_fastapi_port}. Error: {e}"
        )
        sys.exit(1)


if __name__ == "__main__":
    start_fastapi_server()
