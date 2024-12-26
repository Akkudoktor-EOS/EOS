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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from akkudoktoreos.config.config import ConfigEOS, SettingsEOS, get_config
from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.core.pydantic import (
    PydanticBaseModel,
    PydanticDateTimeData,
    PydanticDateTimeDataFrame,
    PydanticDateTimeSeries,
)
from akkudoktoreos.measurement.measurement import get_measurement
from akkudoktoreos.optimization.genetic import (
    OptimizationParameters,
    OptimizationProblem,
    OptimizationResponse,
)
from akkudoktoreos.prediction.prediction import get_prediction
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)
config_eos = get_config()
measurement_eos = get_measurement()
prediction_eos = get_prediction()
ems_eos = get_ems()

ERROR_PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Energy Optimization System (EOS) Error</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background-color: #f5f5f5;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            padding: 20px;
            box-sizing: border-box;
        }
        .error-container {
            background: white;
            padding: 2rem;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
            max-width: 500px;
            width: 100%;
            text-align: center;
        }
        .error-code {
            font-size: 4rem;
            font-weight: bold;
            color: #e53e3e;
            margin: 0;
        }
        .error-title {
            font-size: 1.5rem;
            color: #2d3748;
            margin: 1rem 0;
        }
        .error-message {
            color: #4a5568;
            margin-bottom: 1.5rem;
        }
        .error-details {
            background: #f7fafc;
            padding: 1rem;
            border-radius: 4px;
            margin-bottom: 1.5rem;
            text-align: left;
            font-family: monospace;
            white-space: pre-wrap;
            word-break: break-word;
        }
        .back-button {
            background: #3182ce;
            color: white;
            border: none;
            padding: 0.75rem 1.5rem;
            border-radius: 4px;
            text-decoration: none;
            display: inline-block;
            transition: background-color 0.2s;
        }
        .back-button:hover {
            background: #2c5282;
        }
    </style>
</head>
<body>
    <div class="error-container">
        <h1 class="error-code">STATUS_CODE</h1>
        <h2 class="error-title">ERROR_TITLE</h2>
        <p class="error-message">ERROR_MESSAGE</p>
        <div class="error-details">ERROR_DETAILS</div>
        <a href="/docs" class="back-button">Back to Home</a>
    </div>
</body>
</html>
"""


def create_error_page(
    status_code: str, error_title: str, error_message: str, error_details: str
) -> str:
    """Create an error page by replacing placeholders in the template."""
    return (
        ERROR_PAGE_TEMPLATE.replace("STATUS_CODE", status_code)
        .replace("ERROR_TITLE", error_title)
        .replace("ERROR_MESSAGE", error_message)
        .replace("ERROR_DETAILS", error_details)
    )


def start_fasthtml_server() -> subprocess.Popen:
    """Start the fasthtml server as a subprocess."""
    server_process = subprocess.Popen(
        [sys.executable, str(server_dir.joinpath("fasthtml_server.py"))],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return server_process


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan manager for the app."""
    # On startup
    if (
        config_eos.server_fastapi_startup_server_fasthtml
        and config_eos.server_fasthtml_host
        and config_eos.server_fasthtml_port
    ):
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
opt_class = OptimizationProblem(verbose=bool(config_eos.server_fastapi_verbose))
server_dir = Path(__file__).parent.resolve()


class PdfResponse(FileResponse):
    media_type = "application/pdf"


@app.get("/v1/config")
def fastapi_config_get() -> ConfigEOS:
    """Get the current configuration."""
    return config_eos


@app.put("/v1/config")
def fastapi_config_put(
    settings: SettingsEOS,
    save: Optional[bool] = None,
) -> ConfigEOS:
    """Merge settings into current configuration.

    Args:
        settings (SettingsEOS): The settings to merge into the current configuration.
        save (Optional[bool]): Save the resulting configuration to the configuration file.
            Defaults to False.
    """
    config_eos.merge_settings(settings)
    if save:
        try:
            config_eos.to_config_file()
        except:
            raise HTTPException(
                status_code=404,
                detail=f"Cannot save configuration to file '{config_eos.config_file_path}'.",
            )
    return config_eos


@app.get("/v1/measurement/keys")
def fastapi_measurement_keys_get() -> list[str]:
    """Get a list of available measurement keys."""
    return sorted(measurement_eos.record_keys)


@app.get("/v1/measurement/load-mr/series/by-name")
def fastapi_measurement_load_mr_series_by_name_get(name: str) -> PydanticDateTimeSeries:
    """Get the meter reading of given load name as series."""
    key = measurement_eos.name_to_key(name=name, topic="measurement_load")
    if key is None:
        raise HTTPException(
            status_code=404, detail=f"Measurement load with name '{name}' not available."
        )
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not available.")
    pdseries = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.put("/v1/measurement/load-mr/value/by-name")
def fastapi_measurement_load_mr_value_by_name_put(
    datetime: Any, name: str, value: Union[float | str]
) -> PydanticDateTimeSeries:
    """Merge the meter reading of given load name and value into EOS measurements at given datetime."""
    key = measurement_eos.name_to_key(name=name, topic="measurement_load")
    if key is None:
        raise HTTPException(
            status_code=404, detail=f"Measurement load with name '{name}' not available."
        )
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not available.")
    measurement_eos.update_value(datetime, key, value)
    pdseries = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.put("/v1/measurement/load-mr/series/by-name")
def fastapi_measurement_load_mr_series_by_name_put(
    name: str, series: PydanticDateTimeSeries
) -> PydanticDateTimeSeries:
    """Merge the meter readings series of given load name into EOS measurements at given datetime."""
    key = measurement_eos.name_to_key(name=name, topic="measurement_load")
    if key is None:
        raise HTTPException(
            status_code=404, detail=f"Measurement load with name '{name}' not available."
        )
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not available.")
    pdseries = series.to_series()  # make pandas series from PydanticDateTimeSeries
    measurement_eos.key_from_series(key=key, series=pdseries)
    pdseries = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.get("/v1/measurement/series")
def fastapi_measurement_series_get(key: str) -> PydanticDateTimeSeries:
    """Get the measurements of given key as series."""
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not available.")
    pdseries = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.put("/v1/measurement/value")
def fastapi_measurement_value_put(
    datetime: Any, key: str, value: Union[float | str]
) -> PydanticDateTimeSeries:
    """Merge the measurement of given key and value into EOS measurements at given datetime."""
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not available.")
    measurement_eos.update_value(datetime, key, value)
    pdseries = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.put("/v1/measurement/series")
def fastapi_measurement_series_put(
    key: str, series: PydanticDateTimeSeries
) -> PydanticDateTimeSeries:
    """Merge measurement given as series into given key."""
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not available.")
    pdseries = series.to_series()  # make pandas series from PydanticDateTimeSeries
    measurement_eos.key_from_series(key=key, series=pdseries)
    pdseries = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


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
def fastapi_prediction_series_get(
    key: str,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
) -> PydanticDateTimeSeries:
    """Get prediction for given key within given date range as series.

    Args:
        start_datetime: Starting datetime (inclusive).
            Defaults to start datetime of latest prediction.
        end_datetime: Ending datetime (exclusive).
            Defaults to end datetime of latest prediction.
    """
    if key not in prediction_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not available.")
    if start_datetime is None:
        start_datetime = prediction_eos.start_datetime
    else:
        start_datetime = to_datetime(start_datetime)
    if end_datetime is None:
        end_datetime = prediction_eos.end_datetime
    else:
        end_datetime = to_datetime(end_datetime)
    pdseries = prediction_eos.key_to_series(
        key=key, start_datetime=start_datetime, end_datetime=end_datetime
    )
    return PydanticDateTimeSeries.from_series(pdseries)


@app.get("/v1/prediction/list")
def fastapi_prediction_list_get(
    key: str,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    interval: Optional[str] = None,
) -> List[Any]:
    """Get prediction for given key within given date range as value list.

    Args:
        start_datetime: Starting datetime (inclusive).
            Defaults to start datetime of latest prediction.
        end_datetime: Ending datetime (exclusive).
            Defaults to end datetime of latest prediction.
        interval: Time duration for each interval
            Defaults to 1 hour.
    """
    if key not in prediction_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not available.")
    if start_datetime is None:
        start_datetime = prediction_eos.start_datetime
    else:
        start_datetime = to_datetime(start_datetime)
    if end_datetime is None:
        end_datetime = prediction_eos.end_datetime
    else:
        end_datetime = to_datetime(end_datetime)
    if interval is None:
        interval = to_duration("1 hour")
    else:
        interval = to_duration(interval)
    prediction_list = prediction_eos.key_to_array(
        key=key,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        interval=interval,
    ).tolist()
    return prediction_list


@app.get("/strompreis")
def fastapi_strompreis() -> list[float]:
    """Deprecated: Electricity Market Price Prediction per Wh (€/Wh).

    Note:
        Use '/v1/prediction/list?key=elecprice_marketprice_wh' or
            '/v1/prediction/list?key=elecprice_marketprice_kwh' instead.
    """
    settings = SettingsEOS(
        elecprice_provider="ElecPriceAkkudoktor",
    )
    config_eos.merge_settings(settings=settings)
    ems_eos.set_start_datetime()  # Set energy management start datetime to current hour.

    # Create electricity price forecast
    prediction_eos.update_data(force_update=True)

    # Get the current date and the end date based on prediction hours
    # Fetch prices for the specified date range
    return prediction_eos.key_to_array(
        key="elecprice_marketprice_wh",
        start_datetime=prediction_eos.start_datetime,
        end_datetime=prediction_eos.end_datetime,
    ).tolist()


class GesamtlastRequest(PydanticBaseModel):
    year_energy: float
    measured_data: List[Dict[str, Any]]
    hours: int


@app.post("/gesamtlast")
def fastapi_gesamtlast(request: GesamtlastRequest) -> list[float]:
    """Deprecated: Total Load Prediction with adjustment.

    Endpoint to handle total load prediction adjusted by latest measured data.

    Note:
        Use '/v1/prediction/list?key=load_mean_adjusted' instead.
        Load energy meter readings to be added to EOS measurement by:
        '/v1/measurement/load-mr/value/by-name' or
        '/v1/measurement/value'
    """
    settings = SettingsEOS(
        prediction_hours=request.hours,
        load_provider="LoadAkkudoktor",
        loadakkudoktor_year_energy=request.year_energy,
    )
    config_eos.merge_settings(settings=settings)
    ems_eos.set_start_datetime()  # Set energy management start datetime to current hour.

    # Insert measured data into EOS measurement
    # Convert from energy per interval to dummy energy meter readings
    measurement_key = "measurement_load0_mr"
    measurement_eos.key_delete_by_datetime(key=measurement_key)  # delete all load0_mr measurements
    energy = {}
    for data_dict in request.measured_data:
        for date_time, value in data_dict.items():
            dt_str = to_datetime(date_time, as_string=True)
            energy[dt_str] = value
    energy_mr = 0
    for i, key in enumerate(sorted(energy)):
        energy_mr += energy[key]
        dt = to_datetime(key)
        if i == 0:
            # first element, add start value before
            dt_before = dt - to_duration("1 hour")
            measurement_eos.update_value(date=dt_before, key=measurement_key, value=0.0)
        measurement_eos.update_value(date=dt, key=measurement_key, value=energy_mr)

    # Create load forecast
    prediction_eos.update_data(force_update=True)

    prediction_list = prediction_eos.key_to_array(
        key="load_mean_adjusted",
        start_datetime=prediction_eos.start_datetime,
        end_datetime=prediction_eos.end_datetime,
    ).tolist()
    return prediction_list


@app.get("/gesamtlast_simple")
def fastapi_gesamtlast_simple(year_energy: float) -> list[float]:
    """Deprecated: Total Load Prediction.

    Endpoint to handle total load prediction.

    Note:
        Use '/v1/prediction/list?key=load_mean' instead.
    """
    settings = SettingsEOS(
        load_provider="LoadAkkudoktor",
        loadakkudoktor_year_energy=year_energy,
    )
    config_eos.merge_settings(settings=settings)
    ems_eos.set_start_datetime()  # Set energy management start datetime to current hour.

    # Create load forecast
    prediction_eos.update_data(force_update=True)

    prediction_list = prediction_eos.key_to_array(
        key="load_mean",
        start_datetime=prediction_eos.start_datetime,
        end_datetime=prediction_eos.end_datetime,
    ).tolist()
    return prediction_list


class ForecastResponse(PydanticBaseModel):
    temperature: list[Optional[float]]
    pvpower: list[float]


@app.get("/pvforecast")
def fastapi_pvforecast() -> ForecastResponse:
    ###############
    # PV Forecast
    ###############
    prediction_key = "pvforecast_ac_power"
    pvforecast_ac_power = prediction_eos.get(prediction_key)
    if pvforecast_ac_power is None:
        raise HTTPException(status_code=404, detail=f"Prediction not available: {prediction_key}")

    # On empty Series.loc TypeError: Cannot compare tz-naive and tz-aware datetime-like objects
    if len(pvforecast_ac_power) == 0:
        pvforecast_ac_power = pd.Series()
    else:
        # Fetch prices for the specified date range
        pvforecast_ac_power = pvforecast_ac_power.loc[
            prediction_eos.start_datetime : prediction_eos.end_datetime
        ]

    prediction_key = "pvforecastakkudoktor_temp_air"
    pvforecastakkudoktor_temp_air = prediction_eos.get(prediction_key)
    if pvforecastakkudoktor_temp_air is None:
        raise HTTPException(status_code=404, detail=f"Prediction not available: {prediction_key}")

    # On empty Series.loc TypeError: Cannot compare tz-naive and tz-aware datetime-like objects
    if len(pvforecastakkudoktor_temp_air) == 0:
        pvforecastakkudoktor_temp_air = pd.Series()
    else:
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
) -> OptimizationResponse:
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
    if output_path is None or not output_path.is_dir():
        raise HTTPException(status_code=404, detail=f"Output path does not exist: {output_path}.")
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


async def proxy(request: Request, path: str) -> Union[Response | RedirectResponse | HTMLResponse]:
    if config_eos.server_fasthtml_host and config_eos.server_fasthtml_port:
        # Proxy to fasthtml server
        url = f"http://{config_eos.server_fasthtml_host}:{config_eos.server_fasthtml_port}/{path}"
        headers = dict(request.headers)

        data = await request.body()

        try:
            async with httpx.AsyncClient() as client:
                if request.method == "GET":
                    response = await client.get(url, headers=headers)
                elif request.method == "POST":
                    response = await client.post(url, headers=headers, content=data)
                elif request.method == "PUT":
                    response = await client.put(url, headers=headers, content=data)
                elif request.method == "DELETE":
                    response = await client.delete(url, headers=headers, content=data)
        except Exception as e:
            error_page = create_error_page(
                status_code="404",
                error_title="Page Not Found",
                error_message=f"""<pre>
Application server not reachable: '{url}'
Did you start the application server
or set 'server_fastapi_startup_server_fasthtml'?
If there is no application server intended please
set 'server_fasthtml_host' or 'server_fasthtml_port' to None.
</pre>
""",
                error_details=f"{e}",
            )
            return HTMLResponse(content=error_page, status_code=404)

        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
        )
    else:
        # Redirect the root URL to the site map
        return RedirectResponse(url="/docs")


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
