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
from loguru import logger

from akkudoktoreos.config.config import ConfigEOS, SettingsEOS
from akkudoktoreos.core.cache import CacheFileStore, cache_clear, cache_load, cache_save
from akkudoktoreos.core.coreabc import (
    get_config,
    get_ems,
    get_measurement,
    get_prediction,
    get_resource_registry,
    singletons_init,
)
from akkudoktoreos.core.emplan import EnergyManagementPlan, ResourceStatus
from akkudoktoreos.core.ems import ems_manage_energy
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.core.logging import logging_track_config, read_file_log
from akkudoktoreos.core.pydantic import (
    PydanticBaseModel,
    PydanticDateTimeData,
    PydanticDateTimeDataFrame,
    PydanticDateTimeSeries,
)
from akkudoktoreos.core.version import __version__
from akkudoktoreos.devices.devices import ResourceKey
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.prediction.elecprice import ElecPriceCommonSettings
from akkudoktoreos.prediction.load import LoadCommonSettings
from akkudoktoreos.prediction.loadakkudoktor import LoadAkkudoktorCommonSettings
from akkudoktoreos.prediction.pvforecast import PVForecastCommonSettings
from akkudoktoreos.server.rest.cli import cli_apply_args_to_config, cli_parse_args
from akkudoktoreos.server.rest.error import create_error_page
from akkudoktoreos.server.rest.starteosdash import supervise_eosdash
from akkudoktoreos.server.retentionmanager import RetentionManager
from akkudoktoreos.server.server import (
    drop_root_privileges,
    fix_data_directories_permissions,
    get_host_ip,
    wait_for_port_free,
)
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

# ----------------------
# EOS REST Server
# ----------------------


def save_eos_state() -> None:
    """Save EOS state."""
    get_resource_registry().save()
    get_prediction().save()
    get_measurement().save()
    cache_save()  # keep last


def load_eos_state() -> None:
    """Load EOS state."""
    cache_load()  # keep first
    get_measurement().load()
    get_prediction().load()
    get_resource_registry().load()


def terminate_eos() -> None:
    """Gracefully shut down the EOS server process."""
    pid = psutil.Process().pid
    if os.name == "nt":
        os.kill(pid, signal.CTRL_C_EVENT)  # type: ignore[attr-defined,unused-ignore]
    else:
        os.kill(pid, signal.SIGTERM)  # type: ignore[attr-defined,unused-ignore]

    logger.info(f"🚀 EOS terminated, PID {pid}")


def save_eos_database() -> None:
    """Save EOS database."""
    get_prediction().save()
    get_measurement().save()


def compact_eos_database() -> None:
    """Compact EOS database."""
    get_prediction().db_compact()
    get_measurement().db_compact()
    get_prediction().db_vacuum()
    get_measurement().db_vacuum()


async def server_shutdown_task() -> None:
    """One-shot task for shutting down the EOS server.

    This coroutine performs the following actions:
    1. Ensures the EOS state is saved by calling the save_eos_state function.
    2. Waits for 5 seconds to allow the EOS server to complete any ongoing tasks.
    3. Gracefully shuts down the current process by sending the appropriate signal.

    If running on Windows, the CTRL_C_EVENT signal is sent to terminate the process.
    On other operating systems, the SIGTERM signal is used.

    Finally, logs a message indicating that the EOS server has been terminated.
    """
    save_eos_state()

    # Give EOS time to finish some work
    await asyncio.sleep(5)

    terminate_eos()

    sys.exit(0)


def config_eos_ready() -> bool:
    """Check whether the EOS configuration system is ready.

    This function can be used as an activation condition for the
    RetentionManager to delay the start of its tick loop until the
    configuration system is available.

    Returns:
        bool: ``True`` if the configuration system is ready (``get_config``
            is not ``None``), ``False`` otherwise.
    """
    return get_config() is not None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan manager for the app."""
    # On startup
    load_eos_state()

    # Prepare the Manager and all task that are handled by the manager
    manager = RetentionManager(
        config_getter=get_config().get_nested_value,
        shutdown_timeout=10,
    )
    manager.register(
        name="supervise_eosdash",
        func=supervise_eosdash,
        interval_attr="server/eosdash_supervise_interval_sec",
        fallback_interval=5.0,
    )
    manager.register("cache_clear", cache_clear, interval_attr="cache/cleanup_interval")
    manager.register(
        "save_eos_database", save_eos_database, interval_attr="database/autosave_interval_sec"
    )
    manager.register(
        "compact_eos_database", save_eos_database, interval_attr="database/compaction_interval_sec"
    )
    manager.register("manage_energy", ems_manage_energy, interval_attr="ems/interval")

    # Start the manager an by this all EOS repeated tasks
    retention_manager_task = asyncio.create_task(manager.run(activation_condition=config_eos_ready))

    # Handover to application
    yield

    # waits for any in-flight job to finish cleanly
    retention_manager_task.cancel()
    await asyncio.gather(retention_manager_task, return_exceptions=True)

    # On shutdown
    save_eos_state()


app = FastAPI(
    title="Akkudoktor-EOS",
    description="This project provides a comprehensive solution for simulating and optimizing an energy system based on renewable energy sources. With a focus on photovoltaic (PV) systems, battery storage (batteries), load management (consumer requirements), heat pumps, electric vehicles, and consideration of electricity price data, this system enables forecasting and optimization of energy flow and costs over a specified period.",
    summary="Comprehensive solution for simulating and optimizing an energy system based on renewable energy sources",
    version=f"v{__version__}",
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
    lifespan=lifespan,
)


class PdfResponse(FileResponse):
    media_type = "application/pdf"


@app.post("/v1/admin/cache/clear", tags=["admin"])
def fastapi_admin_cache_clear_post() -> dict:
    """Clear the cache.

    Deletes all cache files.

    Returns:
        data (dict): The management data after cleanup.
    """
    try:
        cache_clear(clear_all=True)
        data = CacheFileStore().current_store()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error on cache clear: {e}")
    return data


@app.post("/v1/admin/cache/clear-expired", tags=["admin"])
def fastapi_admin_cache_clear_expired_post() -> dict:
    """Clear the cache from expired data.

    Deletes expired cache files.

    Returns:
        data (dict): The management data after cleanup.
    """
    try:
        cache_clear(clear_all=False)
        data = CacheFileStore().current_store()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error on cache clear expired: {e}")
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


@app.get("/v1/admin/database/stats", tags=["admin"])
def fastapi_admin_database_stats_get() -> dict:
    """Get statistics from database.

    Returns:
        data (dict): The database statistics
    """
    data = {}
    try:
        # Get the stats
        data[get_measurement().db_namespace()] = get_measurement().db_get_stats()
        data[get_prediction().__class__.__name__] = get_prediction().db_get_stats()
    except Exception as e:
        trace = "".join(traceback.TracebackException.from_exception(e).format())
        raise HTTPException(
            status_code=400, detail=f"Error on database statistic retrieval: {e}\n{trace}"
        )
    return data


@app.post("/v1/admin/database/vacuum", tags=["admin"])
def fastapi_admin_database_vacuum_post() -> dict:
    """Remove old records from database.

    Returns:
        data (dict): The database stats after removal of old records.
    """
    data = {}
    try:
        get_measurement().db_vacuum()
        get_prediction().db_vacuum()
        # Get the stats
        data[get_measurement().db_namespace()] = get_measurement().db_get_stats()
        data[get_prediction().__class__.__name__] = get_prediction().db_get_stats()
    except Exception as e:
        trace = "".join(traceback.TracebackException.from_exception(e).format())
        raise HTTPException(status_code=400, detail=f"Error on database vacuum: {e}\n{trace}")
    return data


@app.post("/v1/admin/server/restart", tags=["admin"])
async def fastapi_admin_server_restart_post() -> dict:
    """Restart the server.

    Restart EOS properly by starting a new instance before exiting the old one.
    """
    save_eos_state()

    # Start a new EOS (Uvicorn) process
    logger.info("🔄 Restarting EOS...")

    # Force a new process group to make the new process easily distinguishable from the current one
    # Set environment before any subprocess run, to keep custom config dir
    env = os.environ.copy()
    env["EOS_DIR"] = str(get_config().general.data_folder_path)
    env["EOS_CONFIG_DIR"] = str(get_config().general.config_folder_path)

    if os.name == "nt":
        # Windows
        DETACHED_PROCESS = 0x00000008
        # getattr avoids mypy warning on Linux
        CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

        new_process = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
            ]
            + sys.argv,
            env=env,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            # stdin, stdout, stderr are inherited by default
        )
    else:
        # Unix/Linux/macOS
        new_process = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
            ]
            + sys.argv,
            env=env,
            start_new_session=True,
            close_fds=True,
            # stdin, stdout, stderr are inherited by default
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


@app.get("/v1/health", tags=["health"])
def fastapi_health_get():  # type: ignore
    """Health check endpoint to verify that the EOS server is alive."""
    return JSONResponse(
        {
            "status": "alive",
            "pid": psutil.Process().pid,
            "version": __version__,
            "energy-management": {
                "start_datetime": to_datetime(get_ems().start_datetime, as_string=True),
                "last_run_datetime": to_datetime(get_ems().last_run_datetime, as_string=True),
            },
        }
    )


@app.post("/v1/config/reset", tags=["config"])
def fastapi_config_reset_post() -> ConfigEOS:
    """Reset the configuration to the EOS configuration file.

    Returns:
        configuration (ConfigEOS): The current configuration after update.
    """
    try:
        get_config().reset_settings()
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Cannot reset configuration: {e}",
        )
    return get_config()


@app.get("/v1/config/backup", tags=["config"])
def fastapi_config_backup_get() -> dict[str, dict[str, Any]]:
    """Get the EOS configuration backup identifiers and backup metadata.

    Returns:
        dict[str, dict[str, Any]]: Mapping of backup identifiers to metadata.
    """
    try:
        result = get_config().list_backups()
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not list configuration backups: {e}",
        )
    return result


@app.put("/v1/config/revert", tags=["config"])
def fastapi_config_revert_put(
    backup_id: str = Query(..., description="EOS configuration backup ID."),
) -> ConfigEOS:
    """Revert the configuration to a EOS configuration backup.

    Returns:
        configuration (ConfigEOS): The current configuration after revert.
    """
    try:
        get_config().revert_settings(backup_id)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error on reverting of configuration: {e}",
        )
    return get_config()


@app.put("/v1/config/file", tags=["config"])
def fastapi_config_file_put() -> ConfigEOS:
    """Save the current configuration to the EOS configuration file.

    Returns:
        configuration (ConfigEOS): The current configuration that was saved.
    """
    try:
        get_config().to_config_file()
    except:
        raise HTTPException(
            status_code=404,
            detail=f"Cannot save configuration to file '{get_config().config_file_path}'.",
        )
    return get_config()


@app.get("/v1/config", tags=["config"])
def fastapi_config_get() -> ConfigEOS:
    """Get the current configuration.

    Returns:
        configuration (ConfigEOS): The current configuration.
    """
    return get_config()


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
        get_config().merge_settings(settings)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error on update of configuration: {e}")
    return get_config()


@app.put("/v1/config/{path:path}", tags=["config"])
def fastapi_config_put_key(
    path: str = FastapiPath(
        ..., description="The nested path to the configuration key (e.g., general/latitude)."
    ),
    value: Optional[Any] = Body(
        None, description="The value to assign to the specified configuration path (can be None)."
    ),
) -> ConfigEOS:
    """Update a nested key or index in the config model.

    Args:
        path (str): The nested path to the key (e.g., "general/latitude" or "optimize/nested_list/0").
        value (Any): The new value to assign to the key or index at path.

    Returns:
        configuration (ConfigEOS): The current configuration after the update.
    """
    try:
        get_config().set_nested_value(path, value)
    except Exception as e:
        trace = "".join(traceback.TracebackException.from_exception(e).format())
        raise HTTPException(
            status_code=400,
            detail=f"Error on update of configuration '{path}','{value}': {e}\n{trace}",
        )

    return get_config()


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
        return get_config().get_nested_value(path)
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/v1/logging/log", tags=["logging"])
async def fastapi_logging_get_log(
    limit: int = Query(100, description="Maximum number of log entries to return."),
    level: Optional[str] = Query(None, description="Filter by log level (e.g., INFO, ERROR)."),
    contains: Optional[str] = Query(None, description="Filter logs containing this substring."),
    regex: Optional[str] = Query(None, description="Filter logs by matching regex in message."),
    from_time: Optional[str] = Query(
        None, description="Start time (ISO format) for filtering logs."
    ),
    to_time: Optional[str] = Query(None, description="End time (ISO format) for filtering logs."),
    tail: bool = Query(False, description="If True, returns the most recent lines (tail mode)."),
) -> JSONResponse:
    """Get structured log entries from the EOS log file.

    Filters and returns log entries based on the specified query parameters. The log
    file is expected to contain newline-delimited JSON entries.

    Args:
        limit (int): Maximum number of entries to return.
        level (Optional[str]): Filter logs by severity level (e.g., DEBUG, INFO).
        contains (Optional[str]): Return only logs that include this string in the message.
        regex (Optional[str]): Return logs that match this regular expression in the message.
        from_time (Optional[str]): ISO 8601 timestamp to filter logs not older than this.
        to_time (Optional[str]): ISO 8601 timestamp to filter logs not newer than this.
        tail (bool): If True, fetch the most recent log entries (like `tail`).

    Returns:
        JSONResponse: A JSON list of log entries.
    """
    log_path = get_config().logging.file_path
    try:
        logs = read_file_log(
            log_path=log_path,
            limit=limit,
            level=level,
            contains=contains,
            regex=regex,
            from_time=from_time,
            to_time=to_time,
            tail=tail,
        )
        return JSONResponse(content=logs)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/v1/resource/status", tags=["resource"])
def fastapi_devices_status_get(
    resource_id: Annotated[str, Query(description="Resource ID.")],
    actuator_id: Annotated[Optional[str], Query(description="Actuator ID.")] = None,
) -> ResourceStatus:
    """Get the latest status of a resource/ device.

    Return:
        latest_status: The latest status of a resource/ device.
    """
    key = ResourceKey(resource_id=resource_id, actuator_id=actuator_id)
    if not get_resource_registry().status_exists(key):
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    status_latest = get_resource_registry().status_latest(key)
    if status_latest is None:
        raise HTTPException(status_code=404, detail=f"Key '{key}' does not have a status.")
    return status_latest


@app.put("/v1/resource/status", tags=["resource"])
def fastapi_devices_status_put(
    resource_id: Annotated[str, Query(description="Resource ID.")],
    status: Annotated[ResourceStatus, Body(description="Resource Status.")],
    actuator_id: Annotated[Optional[str], Query(description="Actuator ID.")] = None,
) -> ResourceStatus:
    """Update the status of a resource/ device.

    Return:
        latest_status: The latest status of a resource/ device.
    """
    key = ResourceKey(resource_id=resource_id, actuator_id=actuator_id)
    try:
        get_resource_registry().update_status(key, status)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error on resource status update key='{key}', status='{status}': {e}",
        )
    status_latest = get_resource_registry().status_latest(key)
    if status_latest is None:
        raise HTTPException(status_code=404, detail=f"Key '{key}' does not have a status.")
    return status_latest


@app.get("/v1/measurement/keys", tags=["measurement"])
def fastapi_measurement_keys_get() -> list[str]:
    """Get a list of available measurement keys."""
    return sorted(get_measurement().record_keys)


@app.get("/v1/measurement/series", tags=["measurement"])
def fastapi_measurement_series_get(
    key: Annotated[str, Query(description="Measurement key.")],
) -> PydanticDateTimeSeries:
    """Get the measurements of given key as series."""
    if key not in get_measurement().record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    pdseries = get_measurement().key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.put("/v1/measurement/value", tags=["measurement"])
def fastapi_measurement_value_put(
    datetime: Annotated[str, Query(description="Datetime.")],
    key: Annotated[str, Query(description="Measurement key.")],
    value: Union[float | str],
) -> PydanticDateTimeSeries:
    """Merge the measurement of given key and value into EOS measurements at given datetime."""
    if key not in get_measurement().record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    if isinstance(value, str):
        # Try to convert to float
        try:
            value = float(value)
        except:
            logger.debug(
                f'/v1/measurement/value key: {key} value: "{value}" - string value not convertable to float'
            )
    get_measurement().update_value(datetime, key, value)
    pdseries = get_measurement().key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.put("/v1/measurement/series", tags=["measurement"])
def fastapi_measurement_series_put(
    key: Annotated[str, Query(description="Measurement key.")], series: PydanticDateTimeSeries
) -> PydanticDateTimeSeries:
    """Merge measurement given as series into given key."""
    if key not in get_measurement().record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    pdseries = series.to_series()  # make pandas series from PydanticDateTimeSeries
    get_measurement().key_from_series(key=key, series=pdseries)
    pdseries = get_measurement().key_to_series(key=key)
    return PydanticDateTimeSeries.from_series(pdseries)


@app.put("/v1/measurement/dataframe", tags=["measurement"])
def fastapi_measurement_dataframe_put(data: PydanticDateTimeDataFrame) -> None:
    """Merge the measurement data given as dataframe into EOS measurements."""
    dataframe = data.to_dataframe()
    get_measurement().import_from_dataframe(dataframe)


@app.put("/v1/measurement/data", tags=["measurement"])
def fastapi_measurement_data_put(data: PydanticDateTimeData) -> None:
    """Merge the measurement data given as datetime data into EOS measurements."""
    datetimedata = data.to_dict()
    get_measurement().import_from_dict(datetimedata)


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
            for provider in get_prediction().providers
            if provider.enabled() in enabled_status
        ]
    )


@app.get("/v1/prediction/keys", tags=["prediction"])
def fastapi_prediction_keys_get() -> list[str]:
    """Get a list of available prediction keys."""
    return sorted(get_prediction().record_keys)


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
    if key not in get_prediction().record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    if start_datetime is None:
        start_datetime = get_prediction().ems_start_datetime
    else:
        start_datetime = to_datetime(start_datetime)
    if end_datetime is None:
        end_datetime = get_prediction().end_datetime
    else:
        end_datetime = to_datetime(end_datetime)
    pdseries = get_prediction().key_to_series(
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
        if key not in get_prediction().record_keys:
            raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    if start_datetime is None:
        start_datetime = get_prediction().ems_start_datetime
    else:
        start_datetime = to_datetime(start_datetime)
    if end_datetime is None:
        end_datetime = get_prediction().end_datetime
    else:
        end_datetime = to_datetime(end_datetime)
    df = get_prediction().keys_to_dataframe(
        keys=keys, start_datetime=start_datetime, end_datetime=end_datetime, interval=interval
    )
    return PydanticDateTimeDataFrame.from_dataframe(df, tz=get_config().general.timezone)


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
    if key not in get_prediction().record_keys:
        raise HTTPException(status_code=404, detail=f"Key '{key}' is not available.")
    if start_datetime is None:
        start_datetime = get_prediction().ems_start_datetime
    else:
        start_datetime = to_datetime(start_datetime)
    if end_datetime is None:
        end_datetime = get_prediction().end_datetime
    else:
        end_datetime = to_datetime(end_datetime)
    if interval is None:
        interval = to_duration("1 hour")
    else:
        interval = to_duration(interval)
    prediction_list = (
        get_prediction()
        .key_to_array(
            key=key,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            interval=interval,
        )
        .tolist()
    )
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
        provider = get_prediction().provider_by_id(provider_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found.")
    if not provider.enabled() and not force_enable:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not enabled.")
    try:
        provider.import_from_json(json_str=json.dumps(data))
        provider.update_datetime = to_datetime(in_timezone=get_config().general.timezone)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Error on import for provider '{provider_id}': {e}"
        )
    return Response()


@app.post("/v1/prediction/update", tags=["prediction"])
async def fastapi_prediction_update(
    force_update: Optional[bool] = False, force_enable: Optional[bool] = False
) -> Response:
    """Update predictions for all providers.

    Args:
        force_update: Update data even if it is already cached.
            Defaults to False.
        force_enable: Update data even if provider is disabled.
            Defaults to False.
    """
    # Ensure there is only one optimization/ energy management run at a time
    try:
        await get_ems().run(
            mode=EnergyManagementMode.PREDICTION,
            force_update=force_update,
            force_enable=force_enable,
        )
    except Exception as e:
        trace = "".join(traceback.TracebackException.from_exception(e).format())
        raise HTTPException(
            status_code=400,
            detail=f"Error on prediction update: {e}\n{trace}",
        )

    return Response()


@app.post("/v1/prediction/update/{provider_id}", tags=["prediction"])
async def fastapi_prediction_update_provider(
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
        provider = get_prediction().provider_by_id(provider_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found.")

    # Ensure there is only one optimization/ energy management run at a time
    try:
        await get_ems().run(
            mode=EnergyManagementMode.PREDICTION,
            force_update=force_update,
            force_enable=force_enable,
        )
    except Exception as e:
        trace = "".join(traceback.TracebackException.from_exception(e).format())
        raise HTTPException(
            status_code=400,
            detail=f"Error on prediction update: {e}\n{trace}",
        )

    return Response()


@app.get("/v1/energy-management/optimization/solution", tags=["energy-management"])
def fastapi_energy_management_optimization_solution_get() -> OptimizationSolution:
    """Get the latest solution of the optimization."""
    solution = get_ems().optimization_solution()
    if solution is None:
        raise HTTPException(
            status_code=404,
            detail="Can not get the optimization solution.\nDid you configure automatic optimization?",
        )
    return solution


@app.get("/v1/energy-management/plan", tags=["energy-management"])
def fastapi_energy_management_plan_get() -> EnergyManagementPlan:
    """Get the latest energy management plan."""
    plan = get_ems().plan()
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail="Can not get the energy management plan.\nDid you configure automatic optimization?",
        )
    return plan


@app.get("/strompreis", tags=["prediction"])
async def fastapi_strompreis() -> list[float]:
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
    get_config().merge_settings(settings=settings)

    # Ensure there is only one optimization/ energy management run at a time
    try:
        await get_ems().run(
            mode=EnergyManagementMode.PREDICTION,
            force_update=True,
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not update predictions: {e}",
        )
    # Get the current date and the end date based on prediction hours
    # Fetch prices for the specified date range
    start_datetime = to_datetime().start_of("day")
    end_datetime = start_datetime.add(days=2)
    try:
        elecprice = (
            get_prediction()
            .key_to_array(
                key="elecprice_marketprice_wh",
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
            .tolist()
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not get the electricity price forecast: {e}.\nDid you configure the electricity price forecast provider?",
        )

    return elecprice


class GesamtlastRequest(PydanticBaseModel):
    year_energy: float
    measured_data: List[Dict[str, Any]]
    hours: int


@app.post("/gesamtlast", tags=["prediction"])
async def fastapi_gesamtlast(request: GesamtlastRequest) -> list[float]:
    """Deprecated: Total Load Prediction with adjustment.

    Endpoint to handle total load prediction adjusted by latest measured data.

    Total load prediction starts at 00.00.00 today and is provided for 48 hours.
    If no prediction values are available the missing ones at the start of the series are
    filled with the first available prediction value.

    Note:
        Use '/v1/prediction/list?key=loadforecast_power_w' instead.
        Load energy meter readings to be added to EOS measurement by:
        '/v1/measurement/value' or
        '/v1/measurement/series' or
        '/v1/measurement/dataframe' or
        '/v1/measurement/data'
    """
    settings = {
        "prediction": {
            "hours": request.hours,
        },
        "load": {
            "provider": "LoadAkkudoktorAdjusted",
            "loadakkudoktor": {
                "loadakkudoktor_year_energy_kwh": request.year_energy,
            },
        },
        "measurement": {
            "load_emr_keys": ["gesamtlast_emr"],
        },
    }
    get_config().merge_settings_from_dict(settings)

    # Insert measured data into EOS measurement
    # Convert from energy per interval to dummy energy meter readings
    measurement_key = "gesamtlast_emr"
    get_measurement().key_delete_by_datetime(
        key=measurement_key
    )  # delete all gesamtlast_emr measurements
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
    get_measurement().key_from_lists(measurement_key, energy_mr_dates, energy_mr_values)

    # Ensure there is only one optimization/ energy management run at a time
    try:
        await get_ems().run(
            mode=EnergyManagementMode.PREDICTION,
            force_update=True,
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not update predictions: {e}",
        )

    # Get the forcast starting at start of day
    start_datetime = to_datetime().start_of("day")
    end_datetime = start_datetime.add(days=2)
    try:
        prediction_list = (
            get_prediction()
            .key_to_array(
                key="loadforecast_power_w",
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
            .tolist()
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not get the total load forecast: {e}.\nDid you configure the load forecast provider?",
        )

    return prediction_list


@app.get("/gesamtlast_simple", tags=["prediction"])
async def fastapi_gesamtlast_simple(year_energy: float) -> list[float]:
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
        '/v1/prediction/list?key=loadforecast_power_w' instead.
    """
    settings = SettingsEOS(
        load=LoadCommonSettings(
            provider="LoadAkkudoktor",
            loadakkudoktor=LoadAkkudoktorCommonSettings(
                loadakkudoktor_year_energy_kwh=year_energy / 1000,  # Convert to kWh
            ),
        )
    )
    get_config().merge_settings(settings=settings)

    # Ensure there is only one optimization/ energy management run at a time
    try:
        await get_ems().run(
            mode=EnergyManagementMode.PREDICTION,
            force_update=True,
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not update predictions: {e}",
        )

    # Get the forcast starting at start of day
    start_datetime = to_datetime().start_of("day")
    end_datetime = start_datetime.add(days=2)
    try:
        prediction_list = (
            get_prediction()
            .key_to_array(
                key="loadforecast_power_w",
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
            .tolist()
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not get the total load forecast: {e}.\nDid you configure the load forecast provider?",
        )

    return prediction_list


class ForecastResponse(PydanticBaseModel):
    temperature: list[Optional[float]]
    pvpower: list[float]


@app.get("/pvforecast", tags=["prediction"])
async def fastapi_pvforecast() -> ForecastResponse:
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
    get_config().merge_settings(settings=settings)

    # Ensure there is only one optimization/ energy management run at a time
    try:
        await get_ems().run(
            mode=EnergyManagementMode.PREDICTION,
            force_update=True,
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not update predictions: {e}",
        )

    # Get the forcast starting at start of day
    start_datetime = to_datetime().start_of("day")
    end_datetime = start_datetime.add(days=2)
    try:
        ac_power = (
            get_prediction()
            .key_to_array(
                key="pvforecast_ac_power",
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
            .tolist()
        )
        temp_air = (
            get_prediction()
            .key_to_array(
                key="pvforecastakkudoktor_temp_air",
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
            .tolist()
        )
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"Can not get the PV forecast: {e}. Did you configure the PV forecast provider?",
        )

    # Return both forecasts as a JSON response
    return ForecastResponse(temperature=temp_air, pvpower=ac_power)


@app.post("/optimize", tags=["optimize"])
async def fastapi_optimize(
    parameters: GeneticOptimizationParameters,
    start_hour: Annotated[
        Optional[int], Query(description="Defaults to current hour of the day.")
    ] = None,
    ngen: Annotated[
        Optional[int], Query(description="Number of indivuals to generate for genetic algorithm.")
    ] = None,
) -> GeneticSolution:
    """Deprecated: Optimize.

    Endpoint to handle optimization.

    Note:
        Use automatic optimization instead.
    """
    if start_hour is None:
        start_datetime = None
    else:
        start_datetime = to_datetime().set(hour=start_hour)

    # Ensure there is only one optimization/ energy management run at a time
    try:
        await get_ems().run(
            start_datetime=start_datetime,
            mode=EnergyManagementMode.OPTIMIZATION,
            genetic_parameters=parameters,
            genetic_individuals=ngen,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Optimize error: {e}.")

    solution = get_ems().genetic_solution()
    if solution is None:
        raise HTTPException(status_code=400, detail="Optimize error: no solution stored by run.")

    return solution


@app.get("/visualization_results.pdf", response_class=PdfResponse, tags=["optimize"])
def get_pdf() -> PdfResponse:
    # Endpoint to serve the generated PDF with visualization results
    output_path = get_config().general.data_output_path
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
        host = get_config().server.eosdash_host
        if host is None:
            host = get_config().server.host
        host = str(host)
        port = get_config().server.eosdash_port
        if port is None:
            port = 8504
        if host == "0.0.0.0":  # noqa: S104
            # Use IP of EOS host
            host = get_host_ip()
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

    host = str(get_config().server.eosdash_host)
    if host == "0.0.0.0":  # noqa: S104
        # Use IP of EOS host
        host = get_host_ip()
    if host and get_config().server.eosdash_port:
        # Redirect to EOSdash server
        url = f"http://{host}:{get_config().server.eosdash_port}/{path}"
        return RedirectResponse(url=url, status_code=303)

    # Redirect the root URL to the site map
    return RedirectResponse(url="/docs", status_code=303)


def run_eos() -> None:
    """Run the EOS server with the specified configurations.

    Starts the EOS server using the Uvicorn ASGI server. Logs an error and exits if
    binding to the host and port fails.

    Returns:
        None
    """
    # get_config(init=True) creates the configuration
    # this should not be done before nor later
    config_eos = get_config(init=True)

    # set logging to what is in config
    logger.remove()
    logging_track_config(config_eos, "logging", None, None)

    # make logger track logging changes in config
    config_eos.track_nested_value("/logging", logging_track_config)

    # Set config to actual environment variable & config file content
    config_eos.reset_settings()

    # add arguments to config
    args: argparse.Namespace
    args_unknown: list[str]
    args, args_unknown = cli_parse_args()
    cli_apply_args_to_config(args)

    # prepare runtime arguments
    if args:
        run_as_user = args.run_as_user
        # Setup EOS reload for development
        if args.reload is None:
            reload = False
        else:
            logger.debug(f"reload set by argument to {args.reload}")
            reload = args.reload
    else:
        run_as_user = None

    # Switch data directories ownership to user
    fix_data_directories_permissions(run_as_user=run_as_user)

    # Switch privileges to run_as_user
    drop_root_privileges(run_as_user=run_as_user)

    # Init the other singletons (besides config_eos)
    singletons_init()

    # Wait for EOS port to be free - e.g. in case of restart
    port = config_eos.server.port
    if port is None:
        port = 8503
    wait_for_port_free(port, timeout=120, waiting_app_name="EOS")

    # Normalize log_level to uvicorn log level
    VALID_UVICORN_LEVELS = {"critical", "error", "warning", "info", "debug", "trace"}
    uv_log_level: Optional[str] = config_eos.logging.console_level
    if uv_log_level is None:
        uv_log_level = "critical"  # effectively disables logging
    else:
        uv_log_level = uv_log_level.lower()
    if uv_log_level == "none":
        uv_log_level = "critical"  # effectively disables logging
    elif uv_log_level not in VALID_UVICORN_LEVELS:
        uv_log_level = "info"  # fallback

    try:
        # Let uvicorn run the fastAPI app
        uvicorn.run(
            "akkudoktoreos.server.eos:app",
            host=str(config_eos.server.host),
            port=port,
            log_level=uv_log_level,
            access_log=True,  # Fix server access logging to True
            reload=reload,
            proxy_headers=True,
            forwarded_allow_ips="*",
        )
    except Exception as e:
        logger.exception("Failed to start uvicorn server.")
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
    --log_level (str): Log level for the server console. Options: "critical", "error", "warning", "info", "debug", "trace" (default: "info").
    --reload (bool): Enable or disable auto-reload. Useful for development. Options: True or False (default: False).
    """
    try:
        run_eos()
    except Exception as ex:
        error_msg = f"Failed to run EOS: {ex}"
        logger.error(error_msg)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
