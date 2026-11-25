"""REST API of the device controllers.

Read-only status of the device controllers of the control adapter, for clients such as
EOSdash::

    GET /v1/control                           configured controllers with their state
    GET /v1/control/{device_id}/status        status, active command, readings and settings
    GET /v1/control/{device_id}/diagnostics   diagnostic series and suggested graphs
    GET /v1/control/{device_id}/events        last raw diagnostic events, newest first

Every configured controller is listed, also one that failed to start (state
``start_failed`` with the start error) or is not running (state ``stopped``). The
diagnostic series and events are only collected while the diagnostic log of the controller
is enabled (``diagnostic_log_capacity`` > 0, see ``diagnostics_enabled``).
"""

import dataclasses
import datetime
import enum
import math
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter
from fastapi import Path as FastapiPath
from fastapi import Query
from pydantic import BaseModel, Field

from akkudoktoreos.adapter.control import ControlAdapter
from akkudoktoreos.adapter.controllers.controlabc import (
    ControlCommand,
    ControlDiagnosticLog,
    ControllerBaseSettings,
    ControlStatus,
)
from akkudoktoreos.adapter.controllers.controldiagnostics import DiagnosticView
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.server.rest.error import EOSProblem
from akkudoktoreos.utils.datetimeutil import DateTime

router = APIRouter(prefix="/v1/control", tags=["control"])

DeviceIdPath = Annotated[
    str, FastapiPath(description="Device id of the controller, e.g. 'inverter1'.")
]

ControllerState = Literal["running", "start_failed", "stopped"]

# Fields of the ControlCommand base class; the other fields of a command are its payload.
_COMMAND_BASE_FIELDS = frozenset(field.name for field in dataclasses.fields(ControlCommand))


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
class ControllerSummary(PydanticBaseModel):
    """State of one configured device controller."""

    device_id: str = Field(description="Device id of the controller.")
    controller_type: str = Field(description="Controller type, e.g. 'sma:energy-meter'.")
    state: ControllerState = Field(
        description=(
            "'running': started; 'start_failed': the last start failed (see 'error'); "
            "'stopped': not running, e.g. until the next start after a reconfiguration."
        )
    )
    connected: bool = Field(default=False, description="Whether the device is connected.")
    error: Optional[str] = Field(
        default=None, description="Start error, or the last error of the running controller."
    )
    status_time: Optional[DateTime] = Field(
        default=None, description="Time of the last status of the running controller."
    )
    diagnostics_enabled: bool = Field(
        description="Whether diagnostic series and events are collected (log capacity > 0)."
    )
    settings_pending: bool = Field(
        default=False,
        description=(
            "Whether the configured settings differ from the ones the running controller uses. "
            "While the Control adapter is enabled, they are applied when the controller is "
            "restarted: right after a change of a setting by PUT /v1/config/{path} (as the "
            "Config tab does), otherwise (e.g. after the whole configuration was replaced or "
            "reverted) at the next energy management run."
        ),
    )


class ControlCommandView(PydanticBaseModel):
    """Command a controller executes."""

    label: str = Field(description="Label of the command, e.g. the operation mode.")
    date_time: DateTime = Field(description="Start of the command.")
    valid_until: Optional[DateTime] = Field(
        default=None, description="End of the command; None: until the next command."
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Controller specific fields of the command, e.g. 'mode' and 'operation_factor'."
        ),
    )


class ControllerStatusView(ControllerSummary):
    """Status of one device controller."""

    active_command: Optional[ControlCommandView] = Field(
        default=None, description="Command in effect; None: no command (safe state)."
    )
    data: dict[str, Any] = Field(
        default_factory=dict, description="Readings the controller published last."
    )
    settings: dict[str, Any] = Field(
        default_factory=dict, description="Settings of the controller."
    )


class DiagnosticEvent(PydanticBaseModel):
    """One raw event of the diagnostic log of a controller."""

    seq: int = Field(description="Sequence number in the diagnostic log.")
    date_time: DateTime = Field(description="Time of the event.")
    event: str = Field(description="Event name, e.g. 'register_write'.")
    data: dict[str, Any] = Field(default_factory=dict, description="Event data.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def json_safe(value: Any) -> Any:
    """Convert a value of a status, command or event to JSON compatible data.

    Enums give their value, date and time values ISO strings, tuples and sets lists,
    dataclasses and models dicts, and non-finite floats None. Other values give ``str()``.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, enum.Enum):
        return json_safe(value.value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: json_safe(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [json_safe(item) for item in value]
        return items if isinstance(value, (list, tuple)) else sorted(items, key=str)
    return str(value)


def command_view(command: ControlCommand) -> ControlCommandView:
    """View of a command: base fields and the controller specific payload."""
    return ControlCommandView(
        label=command.label,
        date_time=command.date_time,
        valid_until=command.valid_until,
        payload={
            field.name: json_safe(getattr(command, field.name))
            for field in dataclasses.fields(command)
            if field.name not in _COMMAND_BASE_FIELDS
        },
    )


def _adapter() -> ControlAdapter:
    return ControlAdapter()


def _configured(adapter: ControlAdapter) -> dict[str, ControllerBaseSettings]:
    return dict(adapter.config.adapter.control.controllers)


def _settings_of(adapter: ControlAdapter, device_id: str) -> ControllerBaseSettings:
    """Settings of a configured controller, or raise 404."""
    settings = _configured(adapter).get(device_id)
    if settings is None:
        raise EOSProblem(
            status=404,
            title="Unknown device controller",
            detail=f"No device controller '{device_id}' is configured.",
        )
    return settings


def _controller_type(settings: ControllerBaseSettings) -> str:
    """Controller type of the settings (declared by every concrete settings class)."""
    return str(getattr(settings, "controller_type", type(settings).__name__))


def _log_of(adapter: ControlAdapter, device_id: str) -> Optional[ControlDiagnosticLog]:
    return adapter.diagnostic_logs.get(device_id)


def _state(
    adapter: ControlAdapter, device_id: str, settings: ControllerBaseSettings
) -> tuple[ControllerSummary, Optional[ControlStatus]]:
    """Summary of a configured controller and the status of the running one."""
    log = _log_of(adapter, device_id)
    diagnostics_enabled = log.enabled if log is not None else settings.diagnostic_log_capacity > 0
    controller = adapter.controllers.get(device_id)
    status: Optional[ControlStatus] = None
    if controller is not None:
        try:
            status = controller.status
        except RuntimeError:  # not started (any more)
            status = None
    if status is not None:
        summary = ControllerSummary(
            device_id=device_id,
            controller_type=_controller_type(settings),
            state="running",
            connected=status.connected,
            error=status.error,
            status_time=status.date_time,
            diagnostics_enabled=diagnostics_enabled,
            settings_pending=controller is not None and controller.settings_changed(settings),
        )
    else:
        start_error = adapter.start_errors.get(device_id)
        summary = ControllerSummary(
            device_id=device_id,
            controller_type=_controller_type(settings),
            state="start_failed" if start_error is not None else "stopped",
            error=start_error,
            diagnostics_enabled=diagnostics_enabled,
        )
    return summary, status


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("", response_model=list[ControllerSummary])
async def get_controllers() -> list[ControllerSummary]:
    """Return the configured device controllers with their state, sorted by device id."""
    adapter = _adapter()
    configured = _configured(adapter)
    return [
        _state(adapter, device_id, configured[device_id])[0] for device_id in sorted(configured)
    ]


@router.get("/{device_id}/status", response_model=ControllerStatusView)
async def get_controller_status(device_id: DeviceIdPath) -> ControllerStatusView:
    """Return the status of a device controller: state, active command, readings, settings.

    Raises:
        EOSProblem: 404 if no controller with this device id is configured.
    """
    adapter = _adapter()
    settings = _settings_of(adapter, device_id)
    summary, status = _state(adapter, device_id, settings)
    return ControllerStatusView(
        **summary.model_dump(),
        active_command=None
        if status is None or status.active_command is None
        else command_view(status.active_command),
        data={} if status is None else json_safe(status.data),
        settings=settings.model_dump(mode="json"),
    )


@router.get("/{device_id}/diagnostics", response_model=DiagnosticView)
async def get_controller_diagnostics(
    device_id: DeviceIdPath,
    since_seq: Annotated[
        int | None,
        Query(ge=0, description="Return diagnostic data after this sequence number."),
    ] = None,
    max_points: Annotated[
        int,
        Query(ge=1, le=5000, description="Maximum number of diagnostic points to return."),
    ] = 500,
) -> DiagnosticView:
    """Return the diagnostic series and suggested graphs of a device controller.

    The series are collected while the diagnostic log of the controller is enabled and
    survive controller restarts. Pass the ``last_seq`` of the previous view as ``since_seq``
    to get new points only.

    Raises:
        EOSProblem: 404 if no controller with this device id is configured.
    """
    adapter = _adapter()
    _settings_of(adapter, device_id)
    log = _log_of(adapter, device_id)
    if log is None:  # not reconciled yet
        return DiagnosticView(device_id=device_id, last_seq=0, graphs=[], series={})
    view = log.series_view(since_seq or 0, max_points)
    return view.model_copy(update={"device_id": device_id})


@router.get("/{device_id}/events", response_model=list[DiagnosticEvent])
async def get_controller_events(
    device_id: DeviceIdPath,
    limit: Annotated[
        int, Query(ge=1, le=1000, description="Maximum number of events to return.")
    ] = 100,
) -> list[DiagnosticEvent]:
    """Return the last raw events of the diagnostic log of a device controller, newest first.

    Empty while the diagnostic log is off.

    Raises:
        EOSProblem: 404 if no controller with this device id is configured.
    """
    adapter = _adapter()
    _settings_of(adapter, device_id)
    log = _log_of(adapter, device_id)
    entries = () if log is None else log.snapshot()
    return [
        DiagnosticEvent(
            seq=entry.seq,
            date_time=entry.date_time,
            event=entry.event,
            data=json_safe(entry.data),
        )
        for entry in reversed(entries[-limit:])
    ]
