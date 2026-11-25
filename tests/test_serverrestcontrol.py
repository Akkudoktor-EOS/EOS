"""Tests for the REST API of the device controllers (/v1/control)."""

import math
from types import MappingProxyType
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akkudoktoreos.adapter.control import ControlAdapter
from akkudoktoreos.adapter.controllers.controlabc import (
    ControlDiagnosticEntry,
    ControlDiagnosticLog,
    ControlStatus,
)
from akkudoktoreos.adapter.controllers.controldiagnostics import DiagnosticGraph
from akkudoktoreos.adapter.controllers.sma.smastpe import SmaCommand
from akkudoktoreos.devices.devicesabc import BatteryOperationMode
from akkudoktoreos.server.rest import control as rest
from akkudoktoreos.server.rest.error import register_problem_handlers
from akkudoktoreos.utils.datetimeutil import to_datetime

NOW = to_datetime("2026-11-12 12:00:00+00:00")

CONTROLLERS = {
    "meter1": {"controller_type": "sma:energy-meter", "serial": 1900123456},
    "inverter1": {
        "controller_type": "sma:sunny-tripower-smart-energy",
        "host": "192.168.178.6",
        "battery_id": "battery1",
        "grid_power_key": "meter1:grid_net_w",
        "diagnostic_log_capacity": 100,
    },
}


class FakeController:
    """Running controller: the REST API reads its status and whether its settings changed."""

    def __init__(self, status: ControlStatus, settings_pending: bool = False) -> None:
        self._status = status
        self.settings_pending = settings_pending

    @property
    def status(self) -> ControlStatus:
        return self._status

    def settings_changed(self, settings: Any) -> bool:
        return self.settings_pending


def entry(event: str, **data: Any) -> ControlDiagnosticEntry:
    return ControlDiagnosticEntry(date_time=NOW, event=event, data=MappingProxyType(data))


@pytest.fixture
def adapter(config_eos) -> Iterator[ControlAdapter]:
    """Control adapter with the controllers configured, its state restored afterwards."""
    config_eos.merge_settings_from_dict({"adapter": {"control": {"controllers": CONTROLLERS}}})
    adapter = ControlAdapter()
    saved = (adapter.controllers, adapter.diagnostic_logs, adapter.start_errors)
    adapter.controllers = {}
    adapter.diagnostic_logs = {}
    adapter.start_errors = {}
    yield adapter
    adapter.controllers, adapter.diagnostic_logs, adapter.start_errors = saved


@pytest.fixture
def client(adapter: ControlAdapter) -> Iterator[TestClient]:
    """Client of an app with only the control router."""
    app = FastAPI()
    register_problem_handlers(app)
    app.include_router(rest.router)
    with TestClient(app) as test_client:
        yield test_client


def run_inverter(adapter: ControlAdapter, settings_pending: bool = False, **status: Any) -> None:
    """Register inverter1 as running with the given status fields."""
    values: dict[str, Any] = {"date_time": NOW, "connected": True}
    values.update(status)
    adapter.controllers["inverter1"] = FakeController(  # type: ignore[assignment]
        ControlStatus(**values), settings_pending
    )


class TestList:
    def test_all_configured_controllers_are_listed_with_their_state(
        self, client: TestClient, adapter: ControlAdapter
    ) -> None:
        run_inverter(adapter)
        adapter.start_errors["meter1"] = "OSError: no network interface"

        response = client.get("/v1/control")

        assert response.status_code == 200
        by_id = {item["device_id"]: item for item in response.json()}
        assert list(by_id) == ["inverter1", "meter1"]  # sorted
        assert by_id["inverter1"]["state"] == "running"
        assert by_id["inverter1"]["connected"] is True
        assert by_id["inverter1"]["controller_type"] == "sma:sunny-tripower-smart-energy"
        assert by_id["meter1"]["state"] == "start_failed"
        assert by_id["meter1"]["error"] == "OSError: no network interface"
        assert by_id["meter1"]["connected"] is False

    def test_controller_that_is_not_running_is_stopped(self, client: TestClient) -> None:
        states = {item["device_id"]: item["state"] for item in client.get("/v1/control").json()}
        assert states == {"inverter1": "stopped", "meter1": "stopped"}

    def test_diagnostics_enabled_follows_the_log_capacity(
        self, client: TestClient, adapter: ControlAdapter
    ) -> None:
        by_id = {item["device_id"]: item for item in client.get("/v1/control").json()}
        assert by_id["inverter1"]["diagnostics_enabled"] is True
        assert by_id["meter1"]["diagnostics_enabled"] is False

        adapter.diagnostic_logs["meter1"] = ControlDiagnosticLog(capacity=10)
        by_id = {item["device_id"]: item for item in client.get("/v1/control").json()}
        assert by_id["meter1"]["diagnostics_enabled"] is True

    def test_no_controller_configured_gives_an_empty_list(
        self, client: TestClient, config_eos
    ) -> None:
        config_eos.adapter.control.controllers = {}
        assert client.get("/v1/control").json() == []


class TestStatus:
    def test_status_of_a_running_controller(
        self, client: TestClient, adapter: ControlAdapter
    ) -> None:
        command = SmaCommand(
            date_time=NOW,
            valid_until=NOW.add(hours=1),
            label="FORCED_CHARGE",
            mode=BatteryOperationMode.FORCED_CHARGE,
            operation_factor=0.6,
        )
        run_inverter(
            adapter,
            active_command=command,
            data=MappingProxyType({"bat_soc_percent": 55, "temp": math.nan}),
            error="last read failed",
        )

        response = client.get("/v1/control/inverter1/status")

        assert response.status_code == 200
        status = response.json()
        assert status["state"] == "running"
        assert status["error"] == "last read failed"
        assert status["data"] == {"bat_soc_percent": 55, "temp": None}
        active = status["active_command"]
        assert active["label"] == "FORCED_CHARGE"
        assert active["payload"] == {
            "mode": "FORCED_CHARGE",
            "operation_factor": 0.6,
            "soc_min_percent": None,
            "soc_max_percent": None,
        }
        assert to_datetime(active["valid_until"]) == NOW.add(hours=1)
        assert status["settings"]["host"] == "192.168.178.6"

    def test_settings_pending_while_the_configuration_differs(
        self, client: TestClient, adapter: ControlAdapter
    ) -> None:
        run_inverter(adapter)
        assert client.get("/v1/control/inverter1/status").json()["settings_pending"] is False

        run_inverter(adapter, settings_pending=True)
        by_id = {item["device_id"]: item for item in client.get("/v1/control").json()}
        assert by_id["inverter1"]["settings_pending"] is True
        assert by_id["meter1"]["settings_pending"] is False  # not running: nothing pending

    def test_no_command_is_null(self, client: TestClient, adapter: ControlAdapter) -> None:
        run_inverter(adapter)
        assert client.get("/v1/control/inverter1/status").json()["active_command"] is None

    def test_status_of_a_controller_that_failed_to_start(
        self, client: TestClient, adapter: ControlAdapter
    ) -> None:
        adapter.start_errors["meter1"] = "OSError: no network interface"
        status = client.get("/v1/control/meter1/status").json()
        assert status["state"] == "start_failed"
        assert status["error"] == "OSError: no network interface"
        assert status["data"] == {}
        assert status["settings"]["serial"] == 1900123456


class TestDiagnostics:
    def test_diagnostics_of_a_controller(self, client: TestClient, adapter: ControlAdapter) -> None:
        adapter.diagnostic_logs["inverter1"] = ControlDiagnosticLog(capacity=10)
        response = client.get("/v1/control/inverter1/diagnostics", params={"max_points": 50})
        assert response.status_code == 200
        assert response.json()["device_id"] == "inverter1"

    def test_diagnostics_serve_the_graphs_the_controller_declared(
        self, client: TestClient, adapter: ControlAdapter
    ) -> None:
        log = adapter.diagnostic_logs["inverter1"] = ControlDiagnosticLog(capacity=10)
        graph = DiagnosticGraph(
            title="Grid", unit="W", kind="numeric", series=["readings.grid_w"]
        )
        log.configure_series({"readings": ("grid_w", "pv_w")}, (graph,))
        log.record(entry("readings", grid_w=100.0, pv_w=200.0))
        view = client.get("/v1/control/inverter1/diagnostics").json()
        assert [g["title"] for g in view["graphs"]] == ["Grid"]
        assert set(view["series"]) == {"readings.grid_w", "readings.pv_w"}

    def test_diagnostics_before_the_first_start_are_empty(self, client: TestClient) -> None:
        view = client.get("/v1/control/inverter1/diagnostics").json()
        assert (view["last_seq"], view["graphs"], view["series"]) == (0, [], {})

    def test_the_former_path_is_gone(self, client: TestClient) -> None:
        assert client.get("/control/inverter1/diagnostics").status_code == 404


class TestEvents:
    def test_events_newest_first_and_limited(
        self, client: TestClient, adapter: ControlAdapter
    ) -> None:
        log = adapter.diagnostic_logs["inverter1"] = ControlDiagnosticLog(capacity=10)
        for index in range(5):
            log.record(entry("register_write", index=index))

        events = client.get("/v1/control/inverter1/events", params={"limit": 3}).json()

        assert [event["seq"] for event in events] == [5, 4, 3]
        assert events[0]["event"] == "register_write"
        assert events[0]["data"] == {"index": 4}

    def test_event_data_is_json_safe(self, client: TestClient, adapter: ControlAdapter) -> None:
        log = adapter.diagnostic_logs["inverter1"] = ControlDiagnosticLog(capacity=10)
        log.record(
            entry(
                "command_received",
                values=(1, 2),
                mode=BatteryOperationMode.IDLE,
                at=NOW,
                ratio=math.inf,
            )
        )

        (event,) = client.get("/v1/control/inverter1/events").json()

        assert event["data"]["values"] == [1, 2]
        assert event["data"]["mode"] == "IDLE"
        assert to_datetime(event["data"]["at"]) == NOW
        assert event["data"]["ratio"] is None

    def test_no_events_while_the_log_is_off(
        self, client: TestClient, adapter: ControlAdapter
    ) -> None:
        log = adapter.diagnostic_logs["meter1"] = ControlDiagnosticLog(capacity=0)
        log.record(entry("readings_updated"))
        assert client.get("/v1/control/meter1/events").json() == []


class TestUnknownDevice:
    @pytest.mark.parametrize("path", ["status", "diagnostics", "events"])
    def test_unknown_device_is_a_problem_404(self, client: TestClient, path: str) -> None:
        response = client.get(f"/v1/control/nope/{path}")
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["title"] == "Unknown device controller"
