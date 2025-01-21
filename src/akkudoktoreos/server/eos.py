#!/usr/bin/env python3

import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncGenerator, Dict, List, Optional, Union

import httpx
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from akkudoktoreos.config.config import ConfigEOS, SettingsEOS, get_config
from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.core.logging import get_logger
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
from akkudoktoreos.prediction.prediction import get_prediction
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

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


def start_eosdash() -> subprocess.Popen:
    """Start the fasthtml server as a subprocess."""
    server_process = subprocess.Popen(
        [sys.executable, str(server_dir.joinpath("eosdash.py"))],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return server_process


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan manager for the app."""
    # On startup
    if (
        config_eos.server_eos_startup_eosdash
        and config_eos.server_eosdash_host
        and config_eos.server_eosdash_port
    ):
        try:
            fasthtml_process = start_eosdash()
        except Exception as e:
            logger.error(f"Failed to start EOSdash server. Error: {e}")
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
opt_class = optimization_problem(verbose=bool(config_eos.server_eos_verbose))

server_dir = Path(__file__).parent.resolve()


class PdfResponse(FileResponse):
    media_type = "application/pdf"


@app.put("/v1/config/value")
def fastapi_config_value_put(
    key: Annotated[str, Query(description="configuration key")],
    value: Annotated[Any, Query(description="configuration value")],
) -> ConfigEOS:
    """Set the configuration option in the settings.

    Args:
        key (str): configuration key
        value (Any): configuration value

    Returns:
        configuration (ConfigEOS): The current configuration after the write.
    """
    if key not in config_eos.config_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    if key in config_eos.config_keys_read_only:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is read only.")
    try:
        setattr(config_eos, key, value)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error on update of configuration: {e}")
    return config_eos


@app.post("/v1/config/update")
def fastapi_config_update_post() -> ConfigEOS:
    """Update the configuration from the EOS configuration file.

    Returns:
        configuration (ConfigEOS): The current configuration after update.
    """
    try:
        _, config_file_path = config_eos.from_config_file()
    except:
        raise HTTPException(
            status_code=404,
            detail=f"Cannot update configuration from file '{config_file_path}'.",
        )
    return config_eos


@app.get("/v1/config/file")
def fastapi_config_file_get() -> SettingsEOS:
    """Get the settings as defined by the EOS configuration file.

    Returns:
        settings (SettingsEOS): The settings defined by the EOS configuration file.
    """
    try:
        settings, config_file_path = config_eos.settings_from_config_file()
    except:
        raise HTTPException(
            status_code=404,
            detail=f"Cannot read configuration from file '{config_file_path}'.",
        )
    return settings


@app.put("/v1/config/file")
def fastapi_config_file_put() -> ConfigEOS:
    """Save the current configuration to the EOS configuration file.

    Returns:
        configuration (ConfigEOS): The current configuration that was saved.
    """
    try:
        config_eos.to_config_file()
    except:
        raise HTTPException(
            status_code=404,
            detail=f"Cannot save configuration to file '{config_eos.config_file_path}'.",
        )
    return config_eos


@app.get("/v1/config")
def fastapi_config_get() -> ConfigEOS:
    """Get the current configuration.

    Returns:
        configuration (ConfigEOS): The current configuration.
    """
    return config_eos


@app.put("/v1/config")
def fastapi_config_put(
    settings: Annotated[SettingsEOS, Query(description="settings")],
) -> ConfigEOS:
    """Write the provided settings into the current settings.

    The existing settings are completely overwritten. Note that for any setting
    value that is None, the configuration will fall back to values from other sources such as
    environment variables, the EOS configuration file, or default values.

    Args:
        settings (SettingsEOS): The settings to write into the current settings.

    Returns:
        configuration (ConfigEOS): The current configuration after the write.
    """
    try:
        config_eos.merge_settings(settings, force=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error on update of configuration: {e}")
    return config_eos


@app.get("/v1/measurement/keys")
def fastapi_measurement_keys_get() -> list[str]:
    """Get a list of available measurement keys."""
    return sorted(measurement_eos.record_keys)


@app.get("/v1/measurement/load-mr/series/by-name")
def fastapi_measurement_load_mr_series_by_name_get(
    name: Annotated[str, Query(description="Load name.")],
) -> PydanticDateTimeSeries:
    """Get the meter reading of given load name as series."""
    key = measurement_eos.name_to_key(name=name, topic="measurement_load")
    if key is None:
        raise HTTPException(
            status_code=404, detail=f"Measurement load with name '{name}' is not available."
        )
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    pdseries = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.put("/v1/measurement/load-mr/value/by-name")
def fastapi_measurement_load_mr_value_by_name_put(
    datetime: Annotated[str, Query(description="Datetime.")],
    name: Annotated[str, Query(description="Load name.")],
    value: Union[float | str],
) -> PydanticDateTimeSeries:
    """Merge the meter reading of given load name and value into EOS measurements at given datetime."""
    key = measurement_eos.name_to_key(name=name, topic="measurement_load")
    if key is None:
        raise HTTPException(
            status_code=404, detail=f"Measurement load with name '{name}' is not available."
        )
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    measurement_eos.update_value(datetime, key, value)
    pdseries = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.put("/v1/measurement/load-mr/series/by-name")
def fastapi_measurement_load_mr_series_by_name_put(
    name: Annotated[str, Query(description="Load name.")], series: PydanticDateTimeSeries
) -> PydanticDateTimeSeries:
    """Merge the meter readings series of given load name into EOS measurements at given datetime."""
    key = measurement_eos.name_to_key(name=name, topic="measurement_load")
    if key is None:
        raise HTTPException(
            status_code=404, detail=f"Measurement load with name '{name}' is not available."
        )
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    pdseries = series.to_series()  # make pandas series from PydanticDateTimeSeries
    measurement_eos.key_from_series(key=key, series=pdseries)
    pdseries = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.get("/v1/measurement/series")
def fastapi_measurement_series_get(
    key: Annotated[str, Query(description="Prediction key.")],
) -> PydanticDateTimeSeries:
    """Get the measurements of given key as series."""
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    pdseries = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.put("/v1/measurement/value")
def fastapi_measurement_value_put(
    datetime: Annotated[str, Query(description="Datetime.")],
    key: Annotated[str, Query(description="Prediction key.")],
    value: Union[float | str],
) -> PydanticDateTimeSeries:
    """Merge the measurement of given key and value into EOS measurements at given datetime."""
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    measurement_eos.update_value(datetime, key, value)
    pdseries = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.put("/v1/measurement/series")
def fastapi_measurement_series_put(
    key: Annotated[str, Query(description="Prediction key.")], series: PydanticDateTimeSeries
) -> PydanticDateTimeSeries:
    """Merge measurement given as series into given key."""
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
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
    key: Annotated[str, Query(description="Prediction key.")],
    start_datetime: Annotated[
        Optional[str],
        Query(description="Starting datetime (inclusive)."),
    ] = None,
    end_datetime: Annotated[
        Optional[str],
        Query(description="Ending datetime (exclusive)."),
    ] = None,
) -> PydanticDateTimeSeries:
    """Get prediction for given key within given date range as series.

    Args:
        key (str): Prediction key
        start_datetime (Optional[str]): Starting datetime (inclusive).
            Defaults to start datetime of latest prediction.
        end_datetime (Optional[str]: Ending datetime (exclusive).
            Defaults to end datetime of latest prediction.
    """
    if key not in prediction_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
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
    key: Annotated[str, Query(description="Prediction key.")],
    start_datetime: Annotated[
        Optional[str],
        Query(description="Starting datetime (inclusive)."),
    ] = None,
    end_datetime: Annotated[
        Optional[str],
        Query(description="Ending datetime (exclusive)."),
    ] = None,
    interval: Annotated[
        Optional[str],
        Query(description="Time duration for each interval."),
    ] = None,
) -> List[Any]:
    """Get prediction for given key within given date range as value list.

    Args:
        key (str): Prediction key
        start_datetime (Optional[str]): Starting datetime (inclusive).
            Defaults to start datetime of latest prediction.
        end_datetime (Optional[str]: Ending datetime (exclusive).
            Defaults to end datetime of latest prediction.
        interval (Optional[str]): Time duration for each interval.
            Defaults to 1 hour.
    """
    if key not in prediction_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
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


@app.post("/v1/prediction/update")
def fastapi_prediction_update(force_update: bool = False, force_enable: bool = False) -> Response:
    """Update predictions for all providers.

    Args:
        force_update: Update data even if it is already cached.
            Defaults to False.
        force_enable: Update data even if provider is disabled.
            Defaults to False.
    """
    try:
        prediction_eos.update_data(force_update=force_update, force_enable=force_enable)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error on update of provider: {e}")
    return Response()


@app.post("/v1/prediction/update/{provider_id}")
def fastapi_prediction_update_provider(
    provider_id: str, force_update: Optional[bool] = False, force_enable: Optional[bool] = False
) -> Response:
    """Update predictions for given provider ID.

    Args:
        provider_id: ID of provider to update.
        force_update: Update data even if it is already cached.
            Defaults to False.
        force_enable: Update data even if provider is disabled.
            Defaults to False.
    """
    try:
        provider = prediction_eos.provider_by_id(provider_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found.")
    try:
        provider.update_data(force_update=force_update, force_enable=force_enable)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error on update of provider: {e}")
    return Response()


@app.get("/strompreis")
def fastapi_strompreis() -> list[float]:
    """Deprecated: Electricity Market Price Prediction per Wh (€/Wh).

    Electricity prices start at 00.00.00 today and are provided for 48 hours.
    If no prices are available the missing ones at the start of the series are
    filled with the first available price.

    Note:
        Electricity price charges are added.

    Note:
        Set ElecPriceAkkudoktor as elecprice_provider, then update data with
        '/v1/prediction/update'
        and then request data with
        '/v1/prediction/list?key=elecprice_marketprice_wh' or
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
    start_datetime = to_datetime().start_of("day")
    end_datetime = start_datetime.add(days=2)
    try:
        elecprice = prediction_eos.key_to_array(
            key="elecprice_marketprice_wh",
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        ).tolist()
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not get the electricity price forecast: {e}. Did you configure the electricity price forecast provider?",
        )

    return elecprice


class GesamtlastRequest(PydanticBaseModel):
    year_energy: float
    measured_data: List[Dict[str, Any]]
    hours: int


@app.post("/gesamtlast")
def fastapi_gesamtlast(request: GesamtlastRequest) -> list[float]:
    """Deprecated: Total Load Prediction with adjustment.

    Endpoint to handle total load prediction adjusted by latest measured data.

    Total load prediction starts at 00.00.00 today and is provided for 48 hours.
    If no prediction values are available the missing ones at the start of the series are
    filled with the first available prediction value.

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

    # Get the forcast starting at start of day
    start_datetime = to_datetime().start_of("day")
    end_datetime = start_datetime.add(days=2)
    try:
        prediction_list = prediction_eos.key_to_array(
            key="load_mean_adjusted",
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        ).tolist()
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not get the total load forecast: {e}. Did you configure the load forecast provider?",
        )

    return prediction_list


@app.get("/gesamtlast_simple")
def fastapi_gesamtlast_simple(year_energy: float) -> list[float]:
    """Deprecated: Total Load Prediction.

    Endpoint to handle total load prediction.

    Total load prediction starts at 00.00.00 today and is provided for 48 hours.
    If no prediction values are available the missing ones at the start of the series are
    filled with the first available prediction value.

    Args:
        year_energy (float): Yearly energy consumption in Wh.

    Note:
        Set LoadAkkudoktor as load_provider, then update data with
        '/v1/prediction/update'
        and then request data with
        '/v1/prediction/list?key=load_mean' instead.
    """
    settings = SettingsEOS(
        load_provider="LoadAkkudoktor",
        loadakkudoktor_year_energy=year_energy / 1000,  # Convert to kWh
    )
    config_eos.merge_settings(settings=settings)
    ems_eos.set_start_datetime()  # Set energy management start datetime to current hour.

    # Create load forecast
    prediction_eos.update_data(force_update=True)

    # Get the forcast starting at start of day
    start_datetime = to_datetime().start_of("day")
    end_datetime = start_datetime.add(days=2)
    try:
        prediction_list = prediction_eos.key_to_array(
            key="load_mean",
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        ).tolist()
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not get the total load forecast: {e}. Did you configure the load forecast provider?",
        )

    return prediction_list


class ForecastResponse(PydanticBaseModel):
    temperature: list[Optional[float]]
    pvpower: list[float]


@app.get("/pvforecast")
def fastapi_pvforecast() -> ForecastResponse:
    """Deprecated: PV Forecast Prediction.

    Endpoint to handle PV forecast prediction.

    PVForecast starts at 00.00.00 today and is provided for 48 hours.
    If no forecast values are available the missing ones at the start of the series are
    filled with the first available forecast value.

    Note:
        Set PVForecastAkkudoktor as pvforecast_provider, then update data with
        '/v1/prediction/update'
        and then request data with
        '/v1/prediction/list?key=pvforecast_ac_power' and
        '/v1/prediction/list?key=pvforecastakkudoktor_temp_air' instead.
    """
    settings = SettingsEOS(
        elecprice_provider="PVForecastAkkudoktor",
    )
    config_eos.merge_settings(settings=settings)

    ems_eos.set_start_datetime()  # Set energy management start datetime to current hour.

    # Create PV forecast
    prediction_eos.update_data(force_update=True)

    # Get the forcast starting at start of day
    start_datetime = to_datetime().start_of("day")
    end_datetime = start_datetime.add(days=2)
    try:
        ac_power = prediction_eos.key_to_array(
            key="pvforecast_ac_power",
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        ).tolist()
        temp_air = prediction_eos.key_to_array(
            key="pvforecastakkudoktor_temp_air",
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        ).tolist()
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not get the PV forecast: {e}. Did you configure the PV forecast provider?",
        )

    # Return both forecasts as a JSON response
    return ForecastResponse(temperature=temp_air, pvpower=ac_power)


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


@app.delete("/{path:path}", include_in_schema=False)
async def proxy_delete(request: Request, path: str) -> Response:
    return await proxy(request, path)


@app.get("/{path:path}", include_in_schema=False)
async def proxy_get(request: Request, path: str) -> Response:
    return await proxy(request, path)


@app.post("/{path:path}", include_in_schema=False)
async def proxy_post(request: Request, path: str) -> Response:
    return await proxy(request, path)


@app.put("/{path:path}", include_in_schema=False)
async def proxy_put(request: Request, path: str) -> Response:
    return await proxy(request, path)


async def proxy(request: Request, path: str) -> Union[Response | RedirectResponse | HTMLResponse]:
    if config_eos.server_eosdash_host and config_eos.server_eosdash_port:
        # Proxy to fasthtml server
        url = f"http://{config_eos.server_eosdash_host}:{config_eos.server_eosdash_port}/{path}"
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
EOSdash server not reachable: '{url}'
Did you start the EOSdash server
or set 'server_eos_startup_eosdash'?
If there is no application server intended please
set 'server_eosdash_host' or 'server_eosdash_port' to None.
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


def start_eos() -> None:
    """Start EOS server."""
    try:
        uvicorn.run(
            app,
            host=str(config_eos.server_eos_host),
            port=config_eos.server_eos_port,
            log_level="debug",
            access_log=True,
        )
    except Exception as e:
        logger.error(
            f"Could not bind to host {config_eos.server_eos_host}:{config_eos.server_eos_port}. Error: {e}"
        )
        sys.exit(1)


if __name__ == "__main__":
    start_eos()
