#!/usr/bin/env python3

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncGenerator, Dict, List, Optional, Union

import psutil
import uvicorn
from fastapi import Body, FastAPI
from fastapi import Path as FastapiPath
from fastapi import Query, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)

from akkudoktoreos.config.config import ConfigEOS, SettingsEOS, get_config
from akkudoktoreos.core.cache import CacheFileStore
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
from akkudoktoreos.prediction.elecprice import ElecPriceCommonSettings
from akkudoktoreos.prediction.load import LoadCommonSettings
from akkudoktoreos.prediction.loadakkudoktor import LoadAkkudoktorCommonSettings
from akkudoktoreos.prediction.prediction import PredictionCommonSettings, get_prediction
from akkudoktoreos.prediction.pvforecast import PVForecastCommonSettings
from akkudoktoreos.server.rest.error import create_error_page
from akkudoktoreos.server.rest.tasks import repeat_every
from akkudoktoreos.server.server import get_default_host, wait_for_port_free
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

logger = get_logger(__name__)
config_eos = get_config()
measurement_eos = get_measurement()
prediction_eos = get_prediction()
ems_eos = get_ems()

# Command line arguments
args = None


# ----------------------
# EOSdash server startup
# ----------------------


def start_eosdash(
    host: str,
    port: int,
    eos_host: str,
    eos_port: int,
    log_level: str,
    access_log: bool,
    reload: bool,
    eos_dir: str,
    eos_config_dir: str,
) -> subprocess.Popen:
    """Start the EOSdash server as a subprocess.

    This function starts the EOSdash server by launching it as a subprocess. It checks if the server
    is already running on the specified port and either returns the existing process or starts a new one.

    Args:
        host (str): The hostname for the EOSdash server.
        port (int): The port for the EOSdash server.
        eos_host (str): The hostname for the EOS server.
        eos_port (int): The port for the EOS server.
        log_level (str): The logging level for the EOSdash server.
        access_log (bool): Flag to enable or disable access logging.
        reload (bool): Flag to enable or disable auto-reloading.
        eos_dir (str): Path to the EOS data directory.
        eos_config_dir (str): Path to the EOS configuration directory.

    Returns:
        subprocess.Popen: The process of the EOSdash server.

    Raises:
        RuntimeError: If the EOSdash server fails to start.
    """
    eosdash_path = Path(__file__).parent.resolve().joinpath("eosdash.py")

    # Do a one time check for port free to generate warnings if not so
    wait_for_port_free(port, timeout=0, waiting_app_name="EOSdash")

    cmd = [
        sys.executable,
        "-m",
        "akkudoktoreos.server.eosdash",
        "--host",
        str(host),
        "--port",
        str(port),
        "--eos-host",
        str(eos_host),
        "--eos-port",
        str(eos_port),
        "--log_level",
        log_level,
        "--access_log",
        str(access_log),
        "--reload",
        str(reload),
    ]
    # Set environment before any subprocess run, to keep custom config dir
    env = os.environ.copy()
    env["EOS_DIR"] = eos_dir
    env["EOS_CONFIG_DIR"] = eos_config_dir

    try:
        server_process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except subprocess.CalledProcessError as ex:
        error_msg = f"Could not start EOSdash: {ex}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    return server_process


# ----------------------
# EOS REST Server
# ----------------------


def cache_clear(clear_all: Optional[bool] = None) -> None:
    """Cleanup expired cache files."""
    if clear_all:
        CacheFileStore().clear(clear_all=True)
    else:
        CacheFileStore().clear(before_datetime=to_datetime())


def cache_load() -> dict:
    """Load cache from cachefilestore.json."""
    return CacheFileStore().load_store()


def cache_save() -> dict:
    """Save cache to cachefilestore.json."""
    return CacheFileStore().save_store()


@repeat_every(seconds=float(config_eos.cache.cleanup_interval))
def cache_cleanup_task() -> None:
    """Repeating task to clear cache from expired cache files."""
    cache_clear()


@repeat_every(
    seconds=10,
    wait_first=config_eos.ems.startup_delay,
)
def energy_management_task() -> None:
    """Repeating task for energy management."""
    ems_eos.manage_energy()


async def server_shutdown_task() -> None:
    """One-shot task for shutting down the EOS server.

    This coroutine performs the following actions:
    1. Ensures the cache is saved by calling the cache_save function.
    2. Waits for 5 seconds to allow the EOS server to complete any ongoing tasks.
    3. Gracefully shuts down the current process by sending the appropriate signal.

    If running on Windows, the CTRL_C_EVENT signal is sent to terminate the process.
    On other operating systems, the SIGTERM signal is used.

    Finally, logs a message indicating that the EOS server has been terminated.
    """
    # Assure cache is saved
    cache_save()

    # Give EOS time to finish some work
    await asyncio.sleep(5)

    # Gracefully shut down this process.
    pid = psutil.Process().pid
    if os.name == "nt":
        os.kill(pid, signal.CTRL_C_EVENT)  # type: ignore[attr-defined,unused-ignore]
    else:
        os.kill(pid, signal.SIGTERM)  # type: ignore[attr-defined,unused-ignore]

    logger.info(f"🚀 EOS terminated, PID {pid}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan manager for the app."""
    # On startup
    if config_eos.server.startup_eosdash:
        try:
            if args is None:
                # No command line arguments
                host = config_eos.server.eosdash_host
                port = config_eos.server.eosdash_port
                eos_host = config_eos.server.host
                eos_port = config_eos.server.port
                log_level = "info"
                access_log = False
                reload = False
            else:
                host = args.host
                port = (
                    config_eos.server.eosdash_port
                    if config_eos.server.eosdash_port
                    else (args.port + 1)
                )
                eos_host = args.host
                eos_port = args.port
                log_level = args.log_level
                access_log = args.access_log
                reload = args.reload

            host = host if host else get_default_host()
            port = port if port else 8504
            eos_host = eos_host if eos_host else get_default_host()
            eos_port = eos_port if eos_port else 8503

            eos_dir = str(config_eos.general.data_folder_path)
            eos_config_dir = str(config_eos.general.config_folder_path)

            eosdash_process = start_eosdash(
                host=host,
                port=port,
                eos_host=eos_host,
                eos_port=eos_port,
                log_level=log_level,
                access_log=access_log,
                reload=reload,
                eos_dir=eos_dir,
                eos_config_dir=eos_config_dir,
            )
        except Exception as e:
            logger.error(f"Failed to start EOSdash server. Error: {e}")
            sys.exit(1)
    cache_load()
    if config_eos.cache.cleanup_interval is None:
        logger.warning("Cache file cleanup disabled. Set cache.cleanup_interval.")
    else:
        await cache_cleanup_task()
    await energy_management_task()

    # Handover to application
    yield

    # On shutdown
    cache_save()


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


class PdfResponse(FileResponse):
    media_type = "application/pdf"


@app.post("/v1/admin/cache/clear", tags=["admin"])
def fastapi_admin_cache_clear_post(clear_all: Optional[bool] = None) -> dict:
    """Clear the cache from expired data.

    Deletes expired cache files.

    Args:
        clear_all (Optional[bool]): Delete all cached files. Default is False.

    Returns:
        data (dict): The management data after cleanup.
    """
    try:
        cache_clear(clear_all=clear_all)
        data = CacheFileStore().current_store()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error on cache clear: {e}")
    return data


@app.post("/v1/admin/cache/save", tags=["admin"])
def fastapi_admin_cache_save_post() -> dict:
    """Save the current cache management data.

    Returns:
        data (dict): The management data that was saved.
    """
    try:
        data = cache_save()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error on cache save: {e}")
    return data


@app.post("/v1/admin/cache/load", tags=["admin"])
def fastapi_admin_cache_load_post() -> dict:
    """Load cache management data.

    Returns:
        data (dict): The management data that was loaded.
    """
    try:
        data = cache_save()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error on cache load: {e}")
    return data


@app.get("/v1/admin/cache", tags=["admin"])
def fastapi_admin_cache_get() -> dict:
    """Current cache management data.

    Returns:
        data (dict): The management data.
    """
    try:
        data = CacheFileStore().current_store()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error on cache data retrieval: {e}")
    return data


@app.post("/v1/admin/server/restart", tags=["admin"])
async def fastapi_admin_server_restart_post() -> dict:
    """Restart the server.

    Restart EOS properly by starting a new instance before exiting the old one.
    """
    logger.info("🔄 Restarting EOS...")

    # Start a new EOS (Uvicorn) process
    # Force a new process group to make the new process easily distinguishable from the current one
    # Set environment before any subprocess run, to keep custom config dir
    env = os.environ.copy()
    env["EOS_DIR"] = str(config_eos.general.data_folder_path)
    env["EOS_CONFIG_DIR"] = str(config_eos.general.config_folder_path)

    new_process = subprocess.Popen(
        [
            sys.executable,
        ]
        + sys.argv,
        env=env,
        start_new_session=True,
    )
    logger.info(f"🚀 EOS restarted, PID {new_process.pid}")

    # Gracefully shut down this process.
    asyncio.create_task(server_shutdown_task())

    # Will be executed because shutdown is delegated to async coroutine
    return {
        "message": "Restarting EOS...",
        "pid": new_process.pid,
    }


@app.post("/v1/admin/server/shutdown", tags=["admin"])
async def fastapi_admin_server_shutdown_post() -> dict:
    """Shutdown the server."""
    logger.info("🔄 Stopping EOS...")

    # Gracefully shut down this process.
    asyncio.create_task(server_shutdown_task())

    # Will be executed because shutdown is delegated to async coroutine
    return {
        "message": "Stopping EOS...",
        "pid": psutil.Process().pid,
    }


@app.get("/v1/health")
def fastapi_health_get():  # type: ignore
    """Health check endpoint to verify that the EOS server is alive."""
    return JSONResponse(
        {
            "status": "alive",
            "pid": psutil.Process().pid,
        }
    )


@app.post("/v1/config/reset", tags=["config"])
def fastapi_config_reset_post() -> ConfigEOS:
    """Reset the configuration to the EOS configuration file.

    Returns:
        configuration (ConfigEOS): The current configuration after update.
    """
    try:
        config_eos.reset_settings()
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Cannot reset configuration: {e}",
        )
    return config_eos


@app.put("/v1/config/file", tags=["config"])
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


@app.get("/v1/config", tags=["config"])
def fastapi_config_get() -> ConfigEOS:
    """Get the current configuration.

    Returns:
        configuration (ConfigEOS): The current configuration.
    """
    return config_eos


@app.put("/v1/config", tags=["config"])
def fastapi_config_put(settings: SettingsEOS) -> ConfigEOS:
    """Update the current config with the provided settings.

    Note that for any setting value that is None or unset, the configuration will fall back to
    values from other sources such as environment variables, the EOS configuration file, or default
    values.

    Args:
        settings (SettingsEOS): The settings to write into the current settings.

    Returns:
        configuration (ConfigEOS): The current configuration after the write.
    """
    try:
        config_eos.merge_settings(settings)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error on update of configuration: {e}")
    return config_eos


@app.put("/v1/config/{path:path}", tags=["config"])
def fastapi_config_put_key(
    path: str = FastapiPath(
        ..., description="The nested path to the configuration key (e.g., general/latitude)."
    ),
    value: Any = Body(..., description="The value to assign to the specified configuration path."),
) -> ConfigEOS:
    """Update a nested key or index in the config model.

    Args:
        path (str): The nested path to the key (e.g., "general/latitude" or "optimize/nested_list/0").
        value (Any): The new value to assign to the key or index at path.

    Returns:
        configuration (ConfigEOS): The current configuration after the update.
    """
    try:
        config_eos.set_config_value(path, value)
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return config_eos


@app.get("/v1/config/{path:path}", tags=["config"])
def fastapi_config_get_key(
    path: str = FastapiPath(
        ..., description="The nested path to the configuration key (e.g., general/latitude)."
    ),
) -> Response:
    """Get the value of a nested key or index in the config model.

    Args:
        path (str): The nested path to the key (e.g., "general/latitude" or "optimize/nested_list/0").

    Returns:
        value (Any): The value of the selected nested key.
    """
    try:
        return config_eos.get_config_value(path)
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/v1/measurement/keys", tags=["measurement"])
def fastapi_measurement_keys_get() -> list[str]:
    """Get a list of available measurement keys."""
    return sorted(measurement_eos.record_keys)


@app.get("/v1/measurement/load-mr/series/by-name", tags=["measurement"])
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


@app.put("/v1/measurement/load-mr/value/by-name", tags=["measurement"])
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


@app.put("/v1/measurement/load-mr/series/by-name", tags=["measurement"])
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


@app.get("/v1/measurement/series", tags=["measurement"])
def fastapi_measurement_series_get(
    key: Annotated[str, Query(description="Prediction key.")],
) -> PydanticDateTimeSeries:
    """Get the measurements of given key as series."""
    if key not in measurement_eos.record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    pdseries = measurement_eos.key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.put("/v1/measurement/value", tags=["measurement"])
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


@app.put("/v1/measurement/series", tags=["measurement"])
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


@app.put("/v1/measurement/dataframe", tags=["measurement"])
def fastapi_measurement_dataframe_put(data: PydanticDateTimeDataFrame) -> None:
    """Merge the measurement data given as dataframe into EOS measurements."""
    dataframe = data.to_dataframe()
    measurement_eos.import_from_dataframe(dataframe)


@app.put("/v1/measurement/data", tags=["measurement"])
def fastapi_measurement_data_put(data: PydanticDateTimeData) -> None:
    """Merge the measurement data given as datetime data into EOS measurements."""
    datetimedata = data.to_dict()
    measurement_eos.import_from_dict(datetimedata)


@app.get("/v1/prediction/providers", tags=["prediction"])
def fastapi_prediction_providers_get(enabled: Optional[bool] = None) -> list[str]:
    """Get a list of available prediction providers.

    Args:
        enabled (bool): Return enabled/disabled providers. If unset, return all providers.
    """
    if enabled is not None:
        enabled_status = [enabled]
    else:
        enabled_status = [True, False]
    return sorted(
        [
            provider.provider_id()
            for provider in prediction_eos.providers
            if provider.enabled() in enabled_status
        ]
    )


@app.get("/v1/prediction/keys", tags=["prediction"])
def fastapi_prediction_keys_get() -> list[str]:
    """Get a list of available prediction keys."""
    return sorted(prediction_eos.record_keys)


@app.get("/v1/prediction/series", tags=["prediction"])
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


@app.get("/v1/prediction/dataframe", tags=["prediction"])
def fastapi_prediction_dataframe_get(
    keys: Annotated[list[str], Query(description="Prediction keys.")],
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
        Query(description="Time duration for each interval. Defaults to 1 hour."),
    ] = None,
) -> PydanticDateTimeDataFrame:
    """Get prediction for given key within given date range as series.

    Args:
        key (str): Prediction key
        start_datetime (Optional[str]): Starting datetime (inclusive).
            Defaults to start datetime of latest prediction.
        end_datetime (Optional[str]: Ending datetime (exclusive).

    Defaults to end datetime of latest prediction.
    """
    for key in keys:
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
    df = prediction_eos.keys_to_dataframe(
        keys=keys, start_datetime=start_datetime, end_datetime=end_datetime, interval=interval
    )
    return PydanticDateTimeDataFrame.from_dataframe(df, tz=config_eos.general.timezone)


@app.get("/v1/prediction/list", tags=["prediction"])
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
        Query(description="Time duration for each interval. Defaults to 1 hour."),
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


@app.put("/v1/prediction/import/{provider_id}", tags=["prediction"])
def fastapi_prediction_import_provider(
    provider_id: str = FastapiPath(..., description="Provider ID."),
    data: Optional[Union[PydanticDateTimeDataFrame, PydanticDateTimeData, dict]] = None,
    force_enable: Optional[bool] = None,
) -> Response:
    """Import prediction for given provider ID.

    Args:
        provider_id: ID of provider to update.
        data: Prediction data.
        force_enable: Update data even if provider is disabled.
            Defaults to False.
    """
    try:
        provider = prediction_eos.provider_by_id(provider_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found.")
    if not provider.enabled() and not force_enable:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not enabled.")
    try:
        provider.import_from_json(json_str=json.dumps(data))
        provider.update_datetime = to_datetime(in_timezone=config_eos.general.timezone)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Error on import for provider '{provider_id}': {e}"
        )
    return Response()


@app.post("/v1/prediction/update", tags=["prediction"])
def fastapi_prediction_update(
    force_update: Optional[bool] = False, force_enable: Optional[bool] = False
) -> Response:
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
        trace = "".join(traceback.TracebackException.from_exception(e).format())
        raise HTTPException(
            status_code=400,
            detail=f"Error on prediction update: {e}{trace}",
        )
    return Response()


@app.post("/v1/prediction/update/{provider_id}", tags=["prediction"])
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
        raise HTTPException(
            status_code=400, detail=f"Error on update of provider '{provider_id}': {e}"
        )
    return Response()


@app.get("/strompreis", tags=["prediction"])
def fastapi_strompreis() -> list[float]:
    """Deprecated: Electricity Market Price Prediction per Wh (€/Wh).

    Electricity prices start at 00.00.00 today and are provided for 48 hours.
    If no prices are available the missing ones at the start of the series are
    filled with the first available price.

    Note:
        Electricity price charges are added.

    Note:
        Set ElecPriceAkkudoktor as provider, then update data with
        '/v1/prediction/update'
        and then request data with
        '/v1/prediction/list?key=elecprice_marketprice_wh' or
        '/v1/prediction/list?key=elecprice_marketprice_kwh' instead.
    """
    settings = SettingsEOS(
        elecprice=ElecPriceCommonSettings(
            provider="ElecPriceAkkudoktor",
        )
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


@app.post("/gesamtlast", tags=["prediction"])
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
        prediction=PredictionCommonSettings(
            hours=request.hours,
        ),
        load=LoadCommonSettings(
            provider="LoadAkkudoktor",
            provider_settings=LoadAkkudoktorCommonSettings(
                loadakkudoktor_year_energy=request.year_energy,
            ),
        ),
    )
    config_eos.merge_settings(settings=settings)
    ems_eos.set_start_datetime()  # Set energy management start datetime to current hour.

    # Insert measured data into EOS measurement
    # Convert from energy per interval to dummy energy meter readings
    measurement_key = "load0_mr"
    measurement_eos.key_delete_by_datetime(key=measurement_key)  # delete all load0_mr measurements
    energy = {}
    try:
        for data_dict in request.measured_data:
            dt_str = to_datetime(data_dict["time"], as_string=True)
            value = float(data_dict["Last"])
            energy[dt_str] = value
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Invalid measured data: {e}.",
        )
    energy_mr_dates = []
    energy_mr_values = []
    energy_mr = 0.0
    for i, key in enumerate(sorted(energy)):
        energy_mr += energy[key]
        dt = to_datetime(key)
        if i == 0:
            # first element, add start value before
            dt_before = dt - to_duration("1 hour")
            energy_mr_dates.append(dt_before)
            energy_mr_values.append(0.0)
        energy_mr_dates.append(dt)
        energy_mr_values.append(energy_mr)
    measurement_eos.key_from_lists(measurement_key, energy_mr_dates, energy_mr_values)

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


@app.get("/gesamtlast_simple", tags=["prediction"])
def fastapi_gesamtlast_simple(year_energy: float) -> list[float]:
    """Deprecated: Total Load Prediction.

    Endpoint to handle total load prediction.

    Total load prediction starts at 00.00.00 today and is provided for 48 hours.
    If no prediction values are available the missing ones at the start of the series are
    filled with the first available prediction value.

    Args:
        year_energy (float): Yearly energy consumption in Wh.

    Note:
        Set LoadAkkudoktor as provider, then update data with
        '/v1/prediction/update'
        and then request data with
        '/v1/prediction/list?key=load_mean' instead.
    """
    settings = SettingsEOS(
        load=LoadCommonSettings(
            provider="LoadAkkudoktor",
            provider_settings=LoadAkkudoktorCommonSettings(
                loadakkudoktor_year_energy=year_energy / 1000,  # Convert to kWh
            ),
        )
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


@app.get("/pvforecast", tags=["prediction"])
def fastapi_pvforecast() -> ForecastResponse:
    """Deprecated: PV Forecast Prediction.

    Endpoint to handle PV forecast prediction.

    PVForecast starts at 00.00.00 today and is provided for 48 hours.
    If no forecast values are available the missing ones at the start of the series are
    filled with the first available forecast value.

    Note:
        Set PVForecastAkkudoktor as provider, then update data with
        '/v1/prediction/update'
        and then request data with
        '/v1/prediction/list?key=pvforecast_ac_power' and
        '/v1/prediction/list?key=pvforecastakkudoktor_temp_air' instead.
    """
    settings = SettingsEOS(pvforecast=PVForecastCommonSettings(provider="PVForecastAkkudoktor"))
    config_eos.merge_settings(settings=settings)

    ems_eos.set_start_datetime()  # Set energy management start datetime to current hour.

    # Create PV forecast
    try:
        prediction_eos.update_data(force_update=True)
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not get the PV forecast: {e}",
        )

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


@app.post("/optimize", tags=["optimize"])
def fastapi_optimize(
    parameters: OptimizationParameters,
    start_hour: Annotated[
        Optional[int], Query(description="Defaults to current hour of the day.")
    ] = None,
    ngen: Optional[int] = None,
) -> OptimizeResponse:
    if start_hour is None:
        start_hour = to_datetime().hour
    extra_args: dict[str, Any] = dict()
    if ngen is not None:
        extra_args["ngen"] = ngen

    # TODO: Remove when config and prediction update is done by EMS.
    config_eos.update()
    prediction_eos.update_data()

    # Perform optimization simulation
    opt_class = optimization_problem(verbose=bool(config_eos.server.verbose))
    result = opt_class.optimierung_ems(parameters=parameters, start_hour=start_hour, **extra_args)
    # print(result)
    return result


@app.get("/visualization_results.pdf", response_class=PdfResponse, tags=["optimize"])
def get_pdf() -> PdfResponse:
    # Endpoint to serve the generated PDF with visualization results
    output_path = config_eos.general.data_output_path
    if output_path is None or not output_path.is_dir():
        raise HTTPException(status_code=404, detail=f"Output path does not exist: {output_path}.")
    file_path = output_path / "visualization_results.pdf"
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="No visualization result available.")
    return PdfResponse(file_path)


@app.get("/site-map", include_in_schema=False)
def site_map() -> RedirectResponse:
    return RedirectResponse(url="/docs")


# Keep the redirect last to handle all requests that are not taken by the Rest API.


@app.delete("/{path:path}", include_in_schema=False)
async def redirect_delete(request: Request, path: str) -> Response:
    return redirect(request, path)


@app.get("/{path:path}", include_in_schema=False)
async def redirect_get(request: Request, path: str) -> Response:
    return redirect(request, path)


@app.post("/{path:path}", include_in_schema=False)
async def redirect_post(request: Request, path: str) -> Response:
    return redirect(request, path)


@app.put("/{path:path}", include_in_schema=False)
async def redirect_put(request: Request, path: str) -> Response:
    return redirect(request, path)


def redirect(request: Request, path: str) -> Union[HTMLResponse, RedirectResponse]:
    # Path is not for EOSdash
    if not (path.startswith("eosdash") or path == ""):
        host = config_eos.server.eosdash_host
        if host is None:
            host = config_eos.server.host
        host = str(host)
        port = config_eos.server.eosdash_port
        if port is None:
            port = 8504
        # Make hostname Windows friendly
        if host == "0.0.0.0" and os.name == "nt":
            host = "localhost"
        url = f"http://{host}:{port}/"
        error_page = create_error_page(
            status_code="404",
            error_title="Page Not Found",
            error_message=f"""<pre>
URL is unknown: '{request.url}'
Did you want to connect to <a href="{url}" class="back-button">EOSdash</a>?
</pre>
""",
            error_details="Unknown URL",
        )
        return HTMLResponse(content=error_page, status_code=404)

    # Make hostname Windows friendly
    host = str(config_eos.server.eosdash_host)
    if host == "0.0.0.0" and os.name == "nt":
        host = "localhost"
    if host and config_eos.server.eosdash_port:
        # Redirect to EOSdash server
        url = f"http://{host}:{config_eos.server.eosdash_port}/{path}"
        return RedirectResponse(url=url, status_code=303)

    # Redirect the root URL to the site map
    return RedirectResponse(url="/docs", status_code=303)


def run_eos(host: str, port: int, log_level: str, access_log: bool, reload: bool) -> None:
    """Run the EOS server with the specified configurations.

    This function starts the EOS server using the Uvicorn ASGI server. It accepts
    arguments for the host, port, log level, access log, and reload options. The
    log level is converted to lowercase to ensure compatibility with Uvicorn's
    expected log level format. If an error occurs while attempting to bind the
    server to the specified host and port, an error message is logged and the
    application exits.

    Parameters:
    host (str): The hostname to bind the server to.
    port (int): The port number to bind the server to.
    log_level (str): The log level for the server. Options include "critical", "error",
                     "warning", "info", "debug", and "trace".
    access_log (bool): Whether to enable or disable the access log. Set to True to enable.
    reload (bool): Whether to enable or disable auto-reload. Set to True for development.

    Returns:
    None
    """
    # Make hostname Windows friendly
    if host == "0.0.0.0" and os.name == "nt":
        host = "localhost"

    # Wait for EOS port to be free - e.g. in case of restart
    wait_for_port_free(port, timeout=120, waiting_app_name="EOS")

    try:
        uvicorn.run(
            "akkudoktoreos.server.eos:app",
            host=host,
            port=port,
            log_level=log_level.lower(),  # Convert log_level to lowercase
            access_log=access_log,
            reload=reload,
        )
    except Exception as e:
        logger.error(f"Could not bind to host {host}:{port}. Error: {e}")
        raise e


def main() -> None:
    """Parse command-line arguments and start the EOS server with the specified options.

    This function sets up the argument parser to accept command-line arguments for
    host, port, log_level, access_log, and reload. It uses default values from the
    config_eos module if arguments are not provided. After parsing the arguments,
    it starts the EOS server with the specified configurations.

    Command-line Arguments:
    --host (str): Host for the EOS server (default: value from config).
    --port (int): Port for the EOS server (default: value from config).
    --log_level (str): Log level for the server. Options: "critical", "error", "warning", "info", "debug", "trace" (default: "info").
    --access_log (bool): Enable or disable access log. Options: True or False (default: False).
    --reload (bool): Enable or disable auto-reload. Useful for development. Options: True or False (default: False).
    """
    parser = argparse.ArgumentParser(description="Start EOS server.")

    # Host and port arguments with defaults from config_eos
    parser.add_argument(
        "--host",
        type=str,
        default=str(config_eos.server.host),
        help="Host for the EOS server (default: value from config)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config_eos.server.port,
        help="Port for the EOS server (default: value from config)",
    )

    # Optional arguments for log_level, access_log, and reload
    parser.add_argument(
        "--log_level",
        type=str,
        default="info",
        help='Log level for the server. Options: "critical", "error", "warning", "info", "debug", "trace" (default: "info")',
    )
    parser.add_argument(
        "--access_log",
        type=bool,
        default=False,
        help="Enable or disable access log. Options: True or False (default: True)",
    )
    parser.add_argument(
        "--reload",
        type=bool,
        default=False,
        help="Enable or disable auto-reload. Useful for development. Options: True or False (default: False)",
    )

    args = parser.parse_args()

    host = args.host if args.host else get_default_host()
    port = args.port if args.port else 8503

    try:
        run_eos(host, port, args.log_level, args.access_log, args.reload)
    except:
        sys.exit(1)


if __name__ == "__main__":
    main()
