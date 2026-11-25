"""Control adapter."""

import asyncio
import math
import re
from concurrent.futures import Future
from types import MappingProxyType
from typing import Annotated, Any, ClassVar, Optional, Union, get_args

from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.adapter.adapterabc import AdapterProvider

# Importing the settings classes imports the controller modules, which binds each controller
# class to its settings class (ControllerBase.__init_subclass__).
from akkudoktoreos.adapter.controllers.byd.bydbatterybox import BydBatteryBoxSettings
from akkudoktoreos.adapter.controllers.controlabc import (
    ControlDiagnosticEntry,
    ControlDiagnosticLog,
    ControllerBase,
    ControllerBaseSettings,
    ControlSharedDataStore,
)
from akkudoktoreos.adapter.controllers.sma.smaenergymeter import SmaEnergyMeterSettings
from akkudoktoreos.adapter.controllers.sma.smastpe import SmaStpeSettings
from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.ems import EnergyManagementStage
from akkudoktoreos.measurement.measurement import EMR_ROLES
from akkudoktoreos.utils.datetimeutil import to_datetime

# Pydantic picks the settings class by the `controller_type` literal of each settings class.
ControllerSettings = Annotated[
    Union[SmaEnergyMeterSettings, SmaStpeSettings, BydBatteryBoxSettings],
    Field(discriminator="controller_type"),
]

# Fail at import, not at first start, if a settings class in the union has no controller bound.
for _settings_class in get_args(get_args(ControllerSettings)[0]):
    _settings_class.controller_class()


_SHARED_DATA_KEY = re.compile(r"^[^:\s]+:[^:\s]+$")


def _check_shared_data_key(data_key: str) -> None:
    """Raise ValueError unless ``data_key`` has the form ``<device_id>:<key>``."""
    if not _SHARED_DATA_KEY.match(data_key):
        raise ValueError(f"Shared data key {data_key!r} is not of the form '<device_id>:<key>'.")


class ControlAdapterCommonSettings(SettingsBaseModel):
    """Common settings for the device control adapter."""

    controllers: dict[str, ControllerSettings] = Field(
        default_factory=dict,
        json_schema_extra={
            "description": "Device controller settings, keyed by device_id.",
            "examples": [
                {
                    "meter1": {
                        "controller_type": "sma:energy-meter",
                        "serial": 1900123456,
                    }
                }
            ],
        },
    )

    load_emr_data_keys: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Shared data keys `<device_id>:<key>` of the load energy meter readings [kWh] "
                "the controllers provide. Each is stored as measurement "
                "`<device_id>_<key>` and counts as a load energy meter reading."
            ),
            "examples": [["meter2:load_kwh"]],
        },
    )
    grid_export_emr_data_keys: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Shared data keys `<device_id>:<key>` of the energy meter readings of energy "
                "export to grid [kWh] the controllers provide. Each is stored as measurement "
                "`<device_id>_<key>` and counts as a grid export energy meter reading."
            ),
            "examples": [["meter1:grid_export_kwh"]],
        },
    )
    grid_import_emr_data_keys: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Shared data keys `<device_id>:<key>` of the energy meter readings of energy "
                "import from grid [kWh] the controllers provide. Each is stored as measurement "
                "`<device_id>_<key>` and counts as a grid import energy meter reading."
            ),
            "examples": [["meter1:grid_import_kwh"]],
        },
    )
    pv_production_emr_data_keys: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Shared data keys `<device_id>:<key>` of the PV production energy meter "
                "readings [kWh] the controllers provide. Each is stored as measurement "
                "`<device_id>_<key>` and counts as a PV production energy meter reading."
            ),
            "examples": [["meter3:pv_production_kwh"]],
        },
    )
    device_measurement_data_keys: Optional[dict[str, str]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Measurements of devices fed by the controllers: maps the measurement key of a "
                "device (see `devices.measurement_keys`, e.g. the SoC of a battery) to the "
                "shared data key `<device_id>:<key>` of the reading."
            ),
            "examples": [{"battery1-soc-factor": "inverter1:bat_soc_factor"}],
        },
    )

    @field_validator(
        "load_emr_data_keys",
        "grid_export_emr_data_keys",
        "grid_import_emr_data_keys",
        "pv_production_emr_data_keys",
    )
    @classmethod
    def _check_emr_data_keys(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        """Only check the format: a key of a removed controller must not invalidate the config."""
        for data_key in value or []:
            _check_shared_data_key(data_key)
        return value

    @field_validator("device_measurement_data_keys")
    @classmethod
    def _check_device_measurement_data_keys(
        cls, value: Optional[dict[str, str]]
    ) -> Optional[dict[str, str]]:
        """Only check the format of the shared data keys (see `_check_emr_data_keys`)."""
        for data_key in (value or {}).values():
            _check_shared_data_key(data_key)
        return value

    @staticmethod
    def measurement_key(data_key: str) -> str:
        """Return the measurement key of an energy meter reading of a controller.

        Args:
            data_key: Shared data key ``<device_id>:<key>``, e.g. ``meter1:grid_import_kwh``.

        Returns:
            str: ``<device_id>_<key>``, e.g. ``meter1_grid_import_kwh``.
        """
        return data_key.replace(":", "_")

    def emr_measurement_keys(self, role: str) -> list[str]:
        """Return the measurement keys of the energy meter readings of a role.

        Args:
            role: ``"load"``, ``"grid_export"``, ``"grid_import"`` or ``"pv_production"``.

        Returns:
            list[str]: The measurement keys of ``<role>_emr_data_keys``, in their order.
        """
        data_keys: Optional[list[str]] = getattr(self, f"{role}_emr_data_keys")
        return [self.measurement_key(data_key) for data_key in data_keys or []]

    def measurement_data_keys(self) -> dict[str, str]:
        """Return the shared data key of every measurement the controllers feed.

        Returns:
            dict[str, str]: Measurement key -> shared data key ``<device_id>:<key>``, for the
            energy meter readings of all roles and the device measurements.
        """
        mapping: dict[str, str] = {}
        for role in EMR_ROLES:
            for data_key in getattr(self, f"{role}_emr_data_keys") or []:
                mapping[self.measurement_key(data_key)] = data_key
        mapping.update(self.device_measurement_data_keys or {})
        return mapping


class ControlAdapter(AdapterProvider):
    """Device control adapter.

    Orchestrates and manages device controllers: creates and starts them according to the
    configuration, stops them when they are removed or reconfigured, lets them write their
    measurements before the optimization and hands the energy management plan to them.

    The device control adapter is a singleton as every adapter, so the class level state
    below is the state of the one instance.
    """

    controllers: dict[str, ControllerBase] = {}
    diagnostic_logs: dict[str, ControlDiagnosticLog] = {}
    # Error of the last failed start per configured device ("<type>: <message>"); kept here
    # because the diagnostic log may be off. Cleared by a successful start or removal.
    start_errors: dict[str, str] = {}
    shared_data: ControlSharedDataStore = ControlSharedDataStore()
    # Event loop the reconcile after a configuration change runs on, and the configuration
    # whose changes are tracked (see track_config_changes()).
    _reconcile_loop: ClassVar[Optional[asyncio.AbstractEventLoop]] = None
    _tracked_config_id: ClassVar[Optional[int]] = None

    def provider_id(self) -> str:
        """Return the unique identifier for the adapter provider."""
        return "Control"

    def controller_by_id(self, device_id: str) -> ControllerBase:
        """Retrieves a controller by its unique device identifier.

        This method searches through the list of all available controllers and
        returns the first controller whose `device_id` matches the given
        `device_id`. If no matching provider is found, the method raises
        an exception.

        Args:
            device_id (str): The unique device identifier of the desired controller.

        Returns:
            ControllerBase: The controller matching the given `device_id`.

        Raises:
            ValueError if provider id is unknown.

        Example:
            controller = adapter.controller_by_id("battery1")
        """
        device_ids = list(self.controllers.keys())
        if device_id not in device_ids:
            error_msg = f"Unknown device id: '{device_id}' of '{device_ids}'."
            logger.error(error_msg)
            raise ValueError(error_msg)
        return self.controllers[device_id]

    async def _update_data(self) -> None:
        """Custom adapter data update logic.

        Data update may be requested at different stages of energy management. The stage can be
        detected by self.ems.stage().

        The set of running controllers is reconciled with the configuration at every stage, so
        devices (e.g. an energy meter) are already running when they are needed.
        At DATA_ACQUISITION the controllers write their measurements into the measurement
        store, at CONTROL_DISPATCH the plan is handed to them.
        """
        await self._reconcile()

        stage = self.ems.stage()
        if stage == EnergyManagementStage.DATA_ACQUISITION:
            await self._acquire_measurements()
        elif stage == EnergyManagementStage.CONTROL_DISPATCH:
            self._dispatch_plan()

    async def _acquire_measurements(self) -> None:
        """Write the readings the controllers feed into the measurement store.

        Called at ``EnergyManagementStage.DATA_ACQUISITION``, before the optimization. The
        readings mapped in ``adapter.control`` (``*_emr_data_keys``,
        ``device_measurement_data_keys``) are read from one coherent image of the shared data
        store and written with one ``measurement.update_value(date, values)`` call.

        A reading is skipped, and the others are still written, if its controller is not
        configured, its value is missing (device not connected yet), older than the
        ``measurement_max_age_s`` of its controller (device lost), or not a finite number.
        Writing stale data would put a wrong value into the measurement history with the
        current date.
        """
        settings = self.config.adapter.control
        sources = settings.measurement_data_keys()
        if not sources:
            return
        date = to_datetime()  # one date for all devices
        entries = self.shared_data.get_many(set(sources.values()))

        values: dict[str, float] = {}
        for measurement_key, data_key in sources.items():
            device_id = data_key.split(":", 1)[0]
            controller_settings = settings.controllers.get(device_id)
            if controller_settings is None:
                logger.warning(
                    "Device control: measurement '{}' refers to '{}' of no configured controller",
                    measurement_key,
                    data_key,
                )
                continue
            entry = entries.get(data_key)
            if entry is None:
                logger.debug("Device control: no data for '{}' yet", data_key)
                continue
            value, age = entry
            max_age = controller_settings.measurement_max_age_s
            if age > max_age:
                logger.warning(
                    "Device control: '{}' is {:.0f} s old (max {:.0f} s), not written",
                    data_key,
                    age,
                    max_age,
                )
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                logger.error(
                    "Device control: '{}' for measurement '{}' is not a finite number: {!r}",
                    data_key,
                    measurement_key,
                    value,
                )
                continue
            values[measurement_key] = float(value)

        # A key the store does not take (e.g. of a removed battery) must not lose the others.
        writable = set(self.measurement.record_keys_writable)
        for measurement_key in [key for key in values if key not in writable]:
            logger.warning(
                "Device control: '{}' is no measurement key (any more), '{}' not written",
                measurement_key,
                sources[measurement_key],
            )
            del values[measurement_key]

        if values:
            try:
                await self.measurement.update_value(date, values)
            except Exception:
                logger.exception("Device control: writing the measurements failed")

    def _dispatch_plan(self) -> None:
        """Hand the energy management plan to every controller."""
        plan = self.ems.plan()
        for device_id, controller in self.controllers.items():
            try:
                controller.update(plan)
            except Exception:
                logger.exception("Device controller {}: schedule update failed", device_id)

    def _diagnostic_log(
        self, device_id: str, settings: ControllerBaseSettings
    ) -> ControlDiagnosticLog:
        """Get the diagnostic log for a controller.

        Libe resizes the log if required by settings.
        """
        log = self.diagnostic_logs.get(device_id)
        if log is None:
            log = self.diagnostic_logs[device_id] = ControlDiagnosticLog(
                settings.diagnostic_log_capacity
            )
        elif log.capacity != settings.diagnostic_log_capacity:
            log.resize(settings.diagnostic_log_capacity)  # live, no restart
        return log

    def _record_lifecycle(
        self, log: ControlDiagnosticLog, device_id: str, event: str, **data: Any
    ) -> None:
        """Record an adapter-side lifecycle event (runs on the event loop)."""
        log.record(
            ControlDiagnosticEntry(
                date_time=to_datetime(),
                event=event,
                data=MappingProxyType({"device_id": device_id, **data}),
            )
        )

    async def _reconcile(self) -> None:
        """Stop controllers that were removed or reconfigured, start the missing ones."""
        configured = self.config.adapter.control.controllers

        # A controller is stale if its device is gone from the configuration or its
        # settings changed since it was started (compared by the values of the fields).
        stale = {
            device_id: controller
            for device_id, controller in self.controllers.items()
            if device_id not in configured or controller.settings_changed(configured[device_id])
        }
        for device_id in stale:
            del self.controllers[device_id]
        await self._stop_all(stale)
        for device_id in list(self.start_errors):
            if device_id not in configured:
                del self.start_errors[device_id]

        # The stopped controllers' data must not outlive them: consumers would keep reading the
        # last value. A thread that missed its stop timeout may still republish its keys;
        # consumers guard against that by checking the age of the data.
        for controller in stale.values():
            self.shared_data.remove_sync(controller.shared_data_keys())

        # Log handling after the stop: the safe-state entries written by the stopping thread
        # are already in the log, so the marker lands behind them.
        for device_id, controller in stale.items():
            log = self.diagnostic_logs.get(device_id)
            if log is None:
                continue
            if device_id in configured:
                self._record_lifecycle(
                    log, device_id, "controller_stopped", reason="settings_changed"
                )
            else:
                # Device removed from the configuration: nothing can read its log anymore.
                # Keep it instead (drop this pop) if you want post-mortem access.
                self.diagnostic_logs.pop(device_id, None)

        for device_id, settings in configured.items():
            # Also applies a changed diagnostic_log_capacity to controllers that keep running:
            # settings_changed() ignores that field, the log is resized live.
            log = self._diagnostic_log(device_id, settings)
            if device_id in self.controllers:
                # Changed live settings (e.g. read intervals) reach the running controller.
                self.controllers[device_id].apply_live_settings(settings)
                continue
            try:
                controller = type(settings).controller_class()(
                    device_id, self.shared_data, log, settings
                )
                await controller.start()
            except Exception as exc:
                logger.exception("Device controller {}: start failed", device_id)
                error = f"{type(exc).__name__}: {exc}"
                self.start_errors[device_id] = error
                self._record_lifecycle(log, device_id, "controller_start_failed", error=error)
                continue  # retried at the next update
            self.controllers[device_id] = controller  # register only after a successful start
            self.start_errors.pop(device_id, None)
            self._record_lifecycle(log, device_id, "controller_started")

    async def reconcile_now(self) -> None:
        """Reconcile the controllers with the configuration now, e.g. right after it changed.

        Holds ``_adapter_lock`` like the adapter update of an energy management run, so the two
        never start or stop controllers at the same time. Does nothing while the adapter is
        disabled, as the adapter update of an energy management run.
        """
        if not self.enabled():
            return
        async with self._adapter_lock:
            await self._reconcile()

    def track_config_changes(self, loop: asyncio.AbstractEventLoop) -> None:
        """Reconcile the controllers right after their configuration changed.

        Without this a changed setting (e.g. the serial of an energy meter) reaches the
        controller only at the next energy management run. ``set_nested_value`` calls the
        callback in the thread of the change, e.g. a worker thread of a REST endpoint, so the
        reconcile is handed to ``loop``. Tracks the whole adapter section, so that enabling this
        adapter also applies at once. Changes not made by ``set_nested_value`` (e.g. a reverted
        backup) are applied at the next energy management run.

        Registers the callback once per configuration; a later call (e.g. a restart of the
        server in the same process) only replaces the loop.

        Args:
            loop: The event loop the controllers run on.
        """
        ControlAdapter._reconcile_loop = loop
        config = self.config
        if ControlAdapter._tracked_config_id == id(config):
            return

        def on_change(model: Any, path: str, old_value: Any, new_value: Any) -> None:
            reconcile_loop = ControlAdapter._reconcile_loop
            if reconcile_loop is None or reconcile_loop.is_closed():
                return  # no server running: applied at the next energy management run
            # Never raise: the change is already made, an exception would fail its request.
            try:
                future = asyncio.run_coroutine_threadsafe(self.reconcile_now(), reconcile_loop)
            except Exception as exc:  # e.g. the loop closed meanwhile
                logger.warning("Device control: no reconcile after change of '{}': {}", path, exc)
                return
            future.add_done_callback(_log_reconcile_failure)

        config.track_nested_value("/adapter", on_change)
        ControlAdapter._tracked_config_id = id(config)

    @staticmethod
    async def _stop_all(controllers: dict[str, ControllerBase]) -> None:
        """Stop controllers concurrently; one failing or slow stop does not block the others."""
        results = await asyncio.gather(
            *(controller.stop() for controller in controllers.values()),
            return_exceptions=True,
        )
        for device_id, result in zip(controllers, results):
            if isinstance(result, BaseException):
                logger.opt(exception=result).error("Device controller {}: stop failed", device_id)

    async def clear(self) -> None:
        """Stop device control threads and delete device controllers."""
        controllers = dict(self.controllers)
        self.controllers.clear()
        self.start_errors.clear()
        await self._stop_all(controllers)
        self.shared_data.replace_sync({})


def _log_reconcile_failure(future: Future[None]) -> None:
    """Log a failed reconcile after a configuration change (it runs detached)."""
    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        logger.opt(exception=exc).error(
            "Device control: reconcile after configuration change failed"
        )


async def device_control_clear() -> None:
    """Cleanup device control adapter.

    Stop all device control threads and delete device controllers.

    Called on server shutdown.
    """
    await ControlAdapter().clear()
