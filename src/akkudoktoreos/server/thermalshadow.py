"""Standalone EOS thermal shadow app without EMS, adapters or background jobs.

Run on the EOS host with uvicorn and --factory. The optional configuration file
contains a thermal section only; the operational EOS configuration is not read.
"""

import json
import os
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from akkudoktoreos.server.rest.thermal import router
from akkudoktoreos.thermal.thermalsettings import ThermalCommonSettings
from akkudoktoreos.thermal.thermaltracking import (
    ThermalTrackingPolicy,
    ThermalTrackingSettings,
)

if TYPE_CHECKING:
    from akkudoktoreos.server.rest.thermaldata import ThermalRuntimeContextProtocol


def _include_tracking_pilot(
    application: FastAPI, settings: ThermalTrackingSettings | None = None
) -> None:
    """Inject an independent bounded policy; the full optimizer stays shadow-only."""
    from akkudoktoreos.server.rest.thermaltracking import router as tracking_router

    configured = settings if settings is not None else ThermalTrackingSettings()
    application.state.thermal_tracking_settings = ThermalTrackingSettings.model_validate(
        configured.model_dump(mode="python")
    )
    application.state.thermal_tracking_policy = ThermalTrackingPolicy()
    application.state.thermal_tracking_lock = Lock()
    application.include_router(tracking_router)


def create_thermal_shadow_app(settings: ThermalCommonSettings | None = None) -> FastAPI:
    """Create an isolated app; importing this module does not start a server."""
    application = FastAPI(title="EOS Thermal Shadow", version="1")
    configured = settings if settings is not None else ThermalCommonSettings()
    application.state.thermal_settings = ThermalCommonSettings.model_validate(
        configured.model_dump(mode="python")
    )
    application.state.thermal_solve_lock = Lock()
    application.include_router(router)
    _include_tracking_pilot(
        application, configured.tracking if configured.mode == "shadow" else None
    )

    @application.get("/health")
    def health() -> dict[str, Any]:
        """Identify a non-actuating shadow process before clients send any input."""
        return {
            "service": "eos-thermal-shadow",
            "status": "alive",
            "mode": application.state.thermal_settings.mode,
            "shadow_only": True,
            "execution_allowed": False,
        }

    return application


def create_app() -> FastAPI:
    """Uvicorn factory; read only the explicitly selected shadow configuration."""
    path = os.environ.get("EOS_THERMAL_SHADOW_CONFIG")
    settings = ThermalCommonSettings()
    if path:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        settings = ThermalCommonSettings.model_validate(raw.get("thermal", {}))
    return create_thermal_shadow_app(settings)


def create_native_thermal_shadow_app(
    context: "ThermalRuntimeContextProtocol",
    tracking_settings: ThermalTrackingSettings | None = None,
    absence_store: Any = None,
) -> FastAPI:
    """Create the narrow native-data app around an explicitly supplied context.

    This constructor is also the isolated ASGI test seam. It does not initialize
    ConfigEOS, measurements, predictions, EMS or an adapter.
    """
    from akkudoktoreos.server.rest.thermaldata import router as native_router

    application = FastAPI(title="EOS Native Thermal Shadow", version="1")
    application.state.thermal_runtime_context = context
    application.state.absence_store = absence_store
    application.include_router(native_router)
    _include_tracking_pilot(application, tracking_settings)

    @application.get("/health")
    def health() -> dict[str, Any]:
        """Identify this isolated process without performing an input update."""
        return {
            "service": "eos-native-thermal-shadow",
            "status": "alive",
            "mode": "shadow",
            "shadow_only": True,
            "execution_allowed": False,
        }

    return application


def create_native_app() -> FastAPI:
    """Initialize native EOS data only after requiring explicit isolated folders.

    No main-server lifespan, EMS, adapter, scheduler or notify callback is used.
    The caller must provide a reviewed private configuration with thermal shadow
    mode and disabled database background maintenance. Context ingestion saves
    data explicitly; the operational EOS installation is never reconfigured.
    """
    config_value = os.environ.get("EOS_CONFIG_DIR")
    data_value = os.environ.get("EOS_DATA_DIR")
    if not config_value or not data_value:
        raise ValueError("Native thermal shadow requires explicit EOS_CONFIG_DIR and EOS_DATA_DIR")
    config_path = Path(config_value)
    data_path = Path(data_value)
    if not config_path.is_absolute() or not data_path.is_absolute():
        raise ValueError("Native thermal shadow directories must be absolute")
    if config_path.resolve() == data_path.resolve():
        raise ValueError("Native thermal shadow config and data directories must be separate")
    if not (config_path / "EOS.config.json").is_file():
        raise ValueError(
            "A reviewed private EOS.config.json is required before native initialization"
        )

    # Import locally, after the filesystem isolation checks. Never call
    # singletons_init(), get_ems(), get_adapter() or the main-server app.
    from akkudoktoreos.core.coreabc import (
        get_config,
        get_database,
        get_measurement,
        get_prediction,
    )
    from akkudoktoreos.thermal.thermalconfigrequest import ThermalRuntimeContext

    config = get_config(init=True)
    loaded_config_path = config.general.config_folder_path
    loaded_data_path = config.general.data_folder_path
    if loaded_config_path is None or loaded_config_path.resolve() != config_path.resolve():
        raise ValueError(
            "Native thermal configuration did not load from the explicit isolated folder"
        )
    if loaded_data_path is None or loaded_data_path.resolve() != data_path.resolve():
        raise ValueError("Native thermal data folder differs from the explicit isolated folder")
    storage_root = data_path.resolve()
    for label, path in (
        ("cache", config.cache.path()),
        ("output", config.general.data_output_path),
        ("database", loaded_data_path / "db"),
    ):
        if path is not None and not path.resolve().is_relative_to(storage_root):
            raise ValueError(
                f"Native thermal {label} storage must remain inside isolated data folder"
            )
    if config.thermal.mode != "shadow":
        raise ValueError("Native thermal process requires thermal.mode=shadow")
    if (
        config.database.autosave_interval_sec is not None
        or config.database.compaction_interval_sec is not None
    ):
        raise ValueError(
            "Native thermal process requires explicit saves, without background maintenance"
        )
    get_database(init=True)
    context = ThermalRuntimeContext(
        config=config,
        measurement=get_measurement(init=True),
        prediction=get_prediction(init=True),
    )
    from akkudoktoreos.thermal.thermalabsences import AbsenceStore

    # Reported absences set at run time live next to the native data and survive a restart.
    store = AbsenceStore(
        loaded_data_path / "presence_absences.json",
        config.general.timezone or "UTC",
        history_days=config.presence.history_days,
    )
    context.absence_store = store
    return create_native_thermal_shadow_app(
        context, tracking_settings=getattr(config.thermal, "tracking", None), absence_store=store
    )
