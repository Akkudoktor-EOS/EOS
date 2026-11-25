"""Tests for the transport-independent device-control base classes.

These tests intentionally use small fake controllers and deterministic clocks. They
exercise public behavior and the important scheduling/lifecycle edge cases without
opening real device connections.
"""

import asyncio
import threading
import time
from collections.abc import Mapping
from typing import Any, ClassVar, NamedTuple

import pytest
from pydantic import ValidationError

from akkudoktoreos.adapter.controllers.controlabc import (
    ControlCommand,
    ControlDiagnosticEntry,
    ControlDiagnosticLog,
    ControllerBase,
    ControllerBaseSettings,
    ControllerThreadBase,
    ControlSharedDataStore,
    ControlStatus,
    MonitorThreadBase,
)
from akkudoktoreos.adapter.controllers.controldiagnostics import DiagnosticGraph
from akkudoktoreos.utils.datetimeutil import to_datetime

# ---------------------------------------------------------------------------
# Shared data store
# ---------------------------------------------------------------------------


class TestControlSharedDataStore:
    def test_empty_store(self):
        store = ControlSharedDataStore()

        assert store.snapshot() == {}
        assert store.get("missing") is None
        assert store.get("missing", 42) == 42
        assert store.get_with_age("missing") is None
        assert store.get_many(["missing"]) == {}

    def test_publish_merges_and_reads_values(self):
        store = ControlSharedDataStore()
        store.publish_sync({"meter:grid_w": 123, "meter:soc": 55})

        assert store.snapshot() == {"meter:grid_w": 123, "meter:soc": 55}
        assert store.get("meter:grid_w") == 123
        assert store.get("meter:soc") == 55

        store.publish_sync({"meter:grid_w": 456, "inverter:power_w": -20})
        assert store.snapshot() == {
            "meter:grid_w": 456,
            "meter:soc": 55,
            "inverter:power_w": -20,
        }

    def test_replace_discards_previous_values(self):
        store = ControlSharedDataStore()
        store.publish_sync({"a": 1, "b": 2})
        store.replace_sync({"c": 3})

        assert store.snapshot() == {"c": 3}
        assert store.get("a") is None

    def test_remove_ignores_unknown_keys(self):
        store = ControlSharedDataStore()
        store.publish_sync({"a": 1, "b": 2})
        store.remove_sync(["a", "not-present"])

        assert store.snapshot() == {"b": 2}

    def test_readers_receive_age(self):
        store = ControlSharedDataStore()
        store.publish_sync({"a": 17})
        result = store.get_with_age("a")

        assert result is not None
        value, age = result
        assert value == 17
        assert age >= 0
        assert store.get_many(["a", "missing"])["a"][0] == 17
        assert store.get_many(["a"])["a"][1] >= 0

    @pytest.mark.asyncio
    async def test_async_methods_match_sync_methods(self):
        store = ControlSharedDataStore()
        await store.publish({"a": 1, "b": 2})
        assert store.snapshot() == {"a": 1, "b": 2}

        await store.replace({"b": 3})
        assert store.snapshot() == {"b": 3}

    def test_snapshot_is_a_copy(self):
        store = ControlSharedDataStore()
        store.publish_sync({"a": 1})
        snapshot = store.snapshot()
        snapshot["a"] = 2

        assert store.get("a") == 1


# ---------------------------------------------------------------------------
# Diagnostic log
# ---------------------------------------------------------------------------


class TestControlDiagnosticLog:
    def test_zero_capacity_is_disabled(self):
        log = ControlDiagnosticLog()
        assert log.capacity == 0
        assert log.enabled is False

        # recording is a no-op
        log.record(ControlDiagnosticEntry(to_datetime(in_timezone="UTC"), "evt", {}))
        assert log.snapshot() == ()

    def test_records_and_drains_in_order(self):
        log = ControlDiagnosticLog(capacity=10)
        assert log.enabled is True
        for i in range(3):
            log.record(
                ControlDiagnosticEntry(to_datetime(in_timezone="UTC"), f"e{i}", {"i": i})
            )

        snap = log.snapshot()
        assert [e.event for e in snap] == ["e0", "e1", "e2"]
        # snapshot does not clear
        assert log.snapshot() == snap

        drained = log.drain()
        assert drained == snap
        assert log.snapshot() == ()

    def test_capacity_drops_oldest(self):
        log = ControlDiagnosticLog(capacity=3)
        for i in range(5):
            log.record(ControlDiagnosticEntry(to_datetime(in_timezone="UTC"), f"e{i}", {}))
        assert [e.event for e in log.snapshot()] == ["e2", "e3", "e4"]

    def test_resize_shrinks_keeping_newest(self):
        log = ControlDiagnosticLog(capacity=5)
        for i in range(5):
            log.record(ControlDiagnosticEntry(to_datetime(in_timezone="UTC"), f"e{i}", {}))
        log.resize(2)
        assert log.capacity == 2
        assert [e.event for e in log.snapshot()] == ["e3", "e4"]

    def test_negative_capacity_is_rejected(self):
        with pytest.raises(ValueError, match="capacity"):
            ControlDiagnosticLog(capacity=-1)


# ---------------------------------------------------------------------------
# Settings and data objects
# ---------------------------------------------------------------------------


class TestControllerBaseSettings:
    def test_unbound_settings_have_no_shared_keys(self):
        assert ControllerBaseSettings().shared_data_keys == ()

    def test_measurement_max_age_must_be_positive(self):
        with pytest.raises(ValidationError):
            ControllerBaseSettings(measurement_max_age_s=0)
        with pytest.raises(ValidationError):
            ControllerBaseSettings(measurement_max_age_s=-1)

    def test_binding_is_idempotent_but_cannot_be_reassigned(self):
        class LocalSettings(ControllerBaseSettings):
            pass

        class First:
            pass

        class Second:
            pass

        LocalSettings.bind_controller_class(First)
        LocalSettings.bind_controller_class(First)
        with pytest.raises(RuntimeError, match="already bound"):
            LocalSettings.bind_controller_class(Second)

    def test_controller_class_fails_when_unbound(self):
        class UnboundSettings(ControllerBaseSettings):
            pass

        with pytest.raises(RuntimeError, match="No controller class bound"):
            UnboundSettings.controller_class()


class TestControlDataObjects:
    def test_command_defaults(self):
        now = to_datetime("2026-01-01T00:00:00+00:00")
        command = ControlCommand(date_time=now)

        assert command.date_time == now
        assert command.valid_until is None
        assert command.refresh_interval is None
        assert command.label == ""

    def test_status_defaults_to_empty_readings(self):
        status = ControlStatus(date_time=to_datetime(in_timezone="UTC"), connected=False)

        assert status.active_command is None
        assert dict(status.data) == {}
        assert status.error is None

    def test_status_readings_are_exposed_as_mapping(self):
        status = ControlStatus(
            date_time=to_datetime(in_timezone="UTC"),
            connected=True,
            data={"grid_w": 10},
        )
        assert isinstance(status.data, Mapping)
        assert status.data["grid_w"] == 10


# ---------------------------------------------------------------------------
# Thread fakes
# ---------------------------------------------------------------------------


class FakeMonitorThread(MonitorThreadBase):
    def __init__(
        self,
        device_id: str,
        shared_data: ControlSharedDataStore,
        diagnostic_log: ControlDiagnosticLog,
        *,
        fail_connect: bool = False,
        fail_read: bool = False,
        **kwargs: Any,
    ):
        super().__init__(device_id, shared_data, diagnostic_log, **kwargs)
        self.fail_connect = fail_connect
        self.fail_read = fail_read
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.read_calls = 0
        self.shutdown_calls = 0
        # Set by the thread, so tests wait for an event instead of polling with sleeps.
        self.has_read = threading.Event()
        self.has_reconnected = threading.Event()

    def _connect(self) -> None:
        self.connect_calls += 1
        if self.connect_calls >= 2:
            self.has_reconnected.set()
        if self.fail_connect:
            raise ConnectionError("connect failed")

    def _disconnect(self) -> None:
        self.disconnect_calls += 1

    def _read_status(self) -> dict[str, Any]:
        self.read_calls += 1
        self.has_read.set()
        if self.fail_read:
            raise OSError("read failed")
        return {"reading": self.read_calls}

    def _on_shutdown(self) -> None:
        self.shutdown_calls += 1


class FakeCommandThread(ControllerThreadBase[ControlCommand]):
    def __init__(
        self,
        device_id: str,
        shared_data: ControlSharedDataStore,
        diagnostic_log: ControlDiagnosticLog,
        **kwargs: Any
    ):
        super().__init__(device_id, shared_data, diagnostic_log, **kwargs)
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.read_calls = 0
        self.applied: list[ControlCommand] = []
        self.safe_state_calls = 0
        self.fail_apply = False

    def _connect(self) -> None:
        self.connect_calls += 1

    def _disconnect(self) -> None:
        self.disconnect_calls += 1

    def _read_status(self) -> dict[str, Any]:
        self.read_calls += 1
        return {"reading": self.read_calls}

    def _apply_command(self, command: ControlCommand) -> None:
        if self.fail_apply:
            raise OSError("apply failed")
        self.applied.append(command)

    def _enter_safe_state(self) -> None:
        self.safe_state_calls += 1


def _log(capacity: int = 100) -> ControlDiagnosticLog:
    """Convenience: a log with diagnostics enabled."""
    return ControlDiagnosticLog(capacity=capacity)


class TestMonitorThreadBase:
    def test_constructor_validation(self):
        with pytest.raises(ValueError, match="poll_interval"):
            FakeMonitorThread("meter", ControlSharedDataStore(), _log(), poll_interval=-1)
        with pytest.raises(ValueError, match="reconnect_backoff"):
            FakeMonitorThread("meter", ControlSharedDataStore(), _log(), reconnect_backoff=())

    def test_initial_status_and_thread_properties(self):
        thread = FakeMonitorThread("meter", ControlSharedDataStore(), _log(), poll_interval=0)

        status = thread.get_status()
        assert thread.name == "control-meter"
        assert thread.daemon
        assert status.connected is False
        assert status.error is None
        assert dict(status.data) == {}
        assert thread.published_keys() == ()

    def test_publish_updates_status_and_keeps_old_data_on_error(self):
        thread = FakeMonitorThread("meter", ControlSharedDataStore(), _log())
        thread._connected = True
        thread._publish(data={"grid_w": 12})

        first = thread.get_status()
        assert first.connected is True
        assert first.data["grid_w"] == 12

        thread._publish(error="temporary failure")
        second = thread.get_status()
        assert second.error == "temporary failure"
        assert second.data["grid_w"] == 12

    def test_publish_notifies_only_when_state_changes(self):
        thread = FakeMonitorThread("meter", ControlSharedDataStore(), _log())
        statuses: list[ControlStatus] = []

        def notify(status: ControlStatus) -> None:
            statuses.append(status)

        thread._notify = notify  # type: ignore[method-assign]

        thread._publish(data={"grid_w": 12})
        thread._publish(data={"grid_w": 12})
        thread._publish(data={"grid_w": 13})

        assert len(statuses) == 2
        assert statuses[-1].data["grid_w"] == 13

    def test_disconnect_quietly_clears_connected_flag_even_if_disconnect_fails(self):
        class BrokenDisconnect(FakeMonitorThread):
            def _disconnect(self) -> None:
                raise RuntimeError("disconnect failed")

        thread = BrokenDisconnect("meter", ControlSharedDataStore(), _log())
        thread._connected = True
        thread._disconnect_quietly()
        assert thread._connected is False

    def test_bind_loop_and_stop_before_start(self):
        thread = FakeMonitorThread("meter", ControlSharedDataStore(), _log())
        loop = asyncio.new_event_loop()
        try:
            calls: list[ControlStatus] = []
            thread.bind_loop(loop, calls.append)
            thread.stop(timeout=0.01)
            assert thread._stop_event.is_set()
            assert thread._wakeup.is_set()
        finally:
            loop.close()

    def test_thread_connects_polls_and_stops_cleanly(self):
        thread = FakeMonitorThread("meter", ControlSharedDataStore(), _log(), poll_interval=0.01)
        thread.start()
        assert thread.has_read.wait(timeout=5)

        thread.stop(timeout=1)
        assert not thread.is_alive()
        assert thread.connect_calls == 1
        assert thread.read_calls >= 1
        assert thread.disconnect_calls == 1
        assert thread.shutdown_calls == 1
        assert thread.get_status().connected is False

    def test_failed_read_disconnects_and_retries(self):
        thread = FakeMonitorThread(
            "meter",
            ControlSharedDataStore(),
            _log(),
            poll_interval=0.01,
            reconnect_backoff=(0.01,),
            fail_read=True,
        )
        thread.start()

        try:
            # The error is published before the back-off, so it is there at the reconnect.
            assert thread.has_reconnected.wait(timeout=5), "Thread did not reconnect"

            # Check the error before stopping, because shutdown publishes
            # a final status with error=None.
            assert thread.connect_calls >= 2, "Thread did not reconnect"
            assert thread.get_status().error == "read failed"
            assert thread.disconnect_calls >= 1
        finally:
            thread.stop(timeout=1)

    def test_record_diagnostic_is_noop_when_disabled(self):
        thread = FakeMonitorThread("meter", ControlSharedDataStore(), ControlDiagnosticLog())
        thread._record_diagnostic("evt", value=1)
        assert thread.diagnostic_log.snapshot() == ()

    def test_record_diagnostic_appends_structured_entry(self):
        log = _log()
        thread = FakeMonitorThread("meter", ControlSharedDataStore(), log)
        thread._record_diagnostic("pid", target=1.0, actual=0.5)

        entries = log.snapshot()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.event == "pid"
        assert dict(entry.data) == {"target": 1.0, "actual": 0.5}

    def test_record_diagnostic_survives_clock_failure(self):
        # The clock is called once during construction (to seed ``_status``).
        # Install the failing clock afterwards so only the diagnostic path sees it.
        thread = FakeMonitorThread("meter", ControlSharedDataStore(), _log())

        def broken_clock() -> Any:
            raise RuntimeError("clock broken")

        thread._clock = broken_clock

        # must not raise
        thread._record_diagnostic("evt", value=1)
        assert thread.diagnostic_log.snapshot() == ()


class TestControllerThreadBase:
    def test_refresh_interval_validation(self):
        with pytest.raises(ValueError, match="refresh_interval"):
            FakeCommandThread("inverter", ControlSharedDataStore(), _log(), refresh_interval=0)

    def test_set_schedule_sorts_commands(self):
        thread = FakeCommandThread("inverter", ControlSharedDataStore(), _log())
        now = to_datetime("2026-01-01T00:00:00+00:00")
        later = ControlCommand(date_time=now.add(minutes=2), label="later")
        earlier = ControlCommand(date_time=now, label="earlier")

        thread.set_schedule([later, earlier])

        assert thread._schedule == (earlier, later)
        assert thread._wakeup.is_set()

    def test_select_current_and_upcoming_command(self):
        thread = FakeCommandThread("inverter", ControlSharedDataStore(), _log())
        now = to_datetime("2026-01-01T00:00:00+00:00")
        first = ControlCommand(date_time=now.subtract(minutes=1), label="first")
        second = ControlCommand(date_time=now.add(minutes=1), label="second")
        thread.set_schedule([second, first])

        selected, upcoming = thread._select(now)
        assert selected == first
        assert upcoming == second.date_time

    def test_expired_command_is_not_selected(self):
        thread = FakeCommandThread("inverter", ControlSharedDataStore(), _log())
        now = to_datetime("2026-01-01T00:00:00+00:00")
        command = ControlCommand(
            date_time=now.subtract(minutes=2),
            valid_until=now.subtract(seconds=1),
        )
        thread.set_schedule([command])

        selected, upcoming = thread._select(now)
        assert selected is None
        assert upcoming is None

    def test_wait_time_accounts_for_upcoming_command(self):
        thread = FakeCommandThread("inverter", ControlSharedDataStore(), _log(), poll_interval=30)
        now = to_datetime("2026-01-01T00:00:00+00:00")
        assert thread._wait_time(now, now.add(seconds=3)) == pytest.approx(3)
        assert thread._wait_time(now, now.subtract(seconds=1)) >= 0.05

    def test_cycle_applies_new_command_only_once_without_refresh(self):
        now = to_datetime("2026-01-01T00:00:00+00:00")
        thread = FakeCommandThread(
            "inverter",
            ControlSharedDataStore(),
            _log(),
            poll_interval=5,
            clock=lambda: now,
        )
        command = ControlCommand(date_time=now, label="setpoint")
        thread.set_schedule([command])

        thread._connected = True
        thread._cycle()
        thread._cycle()

        assert thread.applied == [command]
        assert thread.safe_state_calls == 0

    def test_cycle_enters_safe_state_once_without_command(self):
        now = to_datetime("2026-01-01T00:00:00+00:00")
        thread = FakeCommandThread("inverter", ControlSharedDataStore(), _log(), clock=lambda: now)
        thread._connected = True

        thread._cycle()
        thread._cycle()

        assert thread.safe_state_calls == 1
        assert thread.get_status().active_command is None

    def test_command_specific_refresh_interval_overrides_thread_default(self):
        now = to_datetime("2026-01-01T00:00:00+00:00")
        thread = FakeCommandThread(
            "inverter", ControlSharedDataStore(), _log(), refresh_interval=100, clock=lambda: now
        )
        command = ControlCommand(date_time=now, refresh_interval=2)
        assert thread._refresh_interval_of(command) == 2

    def test_shutdown_enters_safe_state(self):
        thread = FakeCommandThread("inverter", ControlSharedDataStore(), _log())
        thread._on_shutdown()
        assert thread.safe_state_calls == 1


# ---------------------------------------------------------------------------
# EOS-side controller
# ---------------------------------------------------------------------------


class FakeControllerSettings(ControllerBaseSettings):
    pass


class FakeController(ControllerBase):
    Settings: ClassVar[type[FakeControllerSettings]] = FakeControllerSettings
    PROVIDED_DATA_KEYS: ClassVar[tuple[str, ...]] = ("grid_w",)

    def _create_control_thread(self) -> MonitorThreadBase:
        thread = FakeMonitorThread(
            "unused",
            self.shared_data,
            self.diagnostic_log,
            poll_interval=0.01,
        )
        # ControllerBase.start() checks this declaration against the controller.
        thread.published_keys = lambda: self.shared_data_keys()  # type: ignore[method-assign]
        return thread


class FakeCommandController(FakeController):
    def _create_control_thread(self) -> MonitorThreadBase:
        thread = FakeCommandThread(
            "unused", self.shared_data, self.diagnostic_log, poll_interval=0.01
        )
        thread.published_keys = lambda: self.shared_data_keys()  # type: ignore[method-assign]
        return thread

    def _emplan_to_commands(self, plan):
        return [] if plan is None else list(plan)

class TestControllerBase:
    def test_controller_requires_correct_settings_type(self):
        class OtherSettings(ControllerBaseSettings):
            pass

        with pytest.raises(TypeError, match="expects FakeControllerSettings"):
            FakeController(
                "meter",
                ControlSharedDataStore(),
                ControlDiagnosticLog(),
                OtherSettings(),
            )

    def test_shared_data_keys_are_prefixed(self):
        controller = FakeController(
            "meter-1",
            ControlSharedDataStore(),
            ControlDiagnosticLog(),
            FakeControllerSettings(),
        )
        assert controller.shared_data_keys() == ("meter-1:grid_w",)

    def test_emplan_to_commands_default_raises(self):
        controller = FakeController(
            "meter-1",
            ControlSharedDataStore(),
            ControlDiagnosticLog(),
            FakeControllerSettings(),
        )
        with pytest.raises(NotImplementedError, match="must implement"):
            controller._emplan_to_commands(None)

    def test_settings_is_a_deep_copy_snapshot(self):
        settings = FakeControllerSettings()
        controller = FakeController(
            "meter-1",
            ControlSharedDataStore(),
            ControlDiagnosticLog(),
            settings,
        )
        settings.measurement_max_age_s = 5.0
        assert controller.settings.measurement_max_age_s == 60.0

    def test_settings_changed_ignores_live_fields(self):
        controller = FakeController(
            "meter-1",
            ControlSharedDataStore(),
            ControlDiagnosticLog(),
            FakeControllerSettings(diagnostic_log_capacity=0),
        )
        # live field change is ignored
        assert (
            controller.settings_changed(
                FakeControllerSettings(diagnostic_log_capacity=500)
            )
            is False
        )
        # non-live field change is detected
        assert (
            controller.settings_changed(
                FakeControllerSettings(measurement_max_age_s=5.0)
            )
            is True
        )

    @pytest.mark.asyncio
    async def test_start_status_update_and_stop(self):
        controller = FakeController(
            "meter-1",
            ControlSharedDataStore(),
            ControlDiagnosticLog(),
            FakeControllerSettings(),
        )
        await controller.start()
        try:
            assert controller.status is not None
            controller.update(None)  # receive-only controller: no-op
            assert controller.status.connected in (False, True)
        finally:
            await controller.stop()

        with pytest.raises(RuntimeError, match="not started"):
            _ = controller.status

    @pytest.mark.asyncio
    async def test_start_rejects_thread_published_key_mismatch(self):
        class MismatchController(FakeController):
            PROVIDED_DATA_KEYS: ClassVar[tuple[str, ...]] = ("expected",)

            def _create_control_thread(self) -> MonitorThreadBase:
                return FakeMonitorThread(
                    "mismatch",
                    self.shared_data,
                    self.diagnostic_log,
                    poll_interval=0.01
                )

        controller = MismatchController(
            "meter-1",
            ControlSharedDataStore(),
            ControlDiagnosticLog(),
            FakeControllerSettings(),
        )
        with pytest.raises(RuntimeError, match="publishes"):
            await controller.start()
        assert controller._thread is None

    @pytest.mark.asyncio
    async def test_update_requires_started_controller(self):
        controller = FakeController(
            "meter-1",
            ControlSharedDataStore(),
            ControlDiagnosticLog(),
            FakeControllerSettings(),
        )
        with pytest.raises(RuntimeError, match="not started"):
            controller.update(None)

    @pytest.mark.asyncio
    async def test_diagnostic_log_survives_thread_restart(self):
        log = _log()
        controller = FakeController(
            "meter-1",
            ControlSharedDataStore(),
            log,
            FakeControllerSettings(),
        )
        await controller.start()
        controller.diagnostic_log.record(
            ControlDiagnosticEntry(to_datetime(in_timezone="UTC"), "before", {})
        )
        await controller.stop()

        assert [e.event for e in controller.get_diagnostic_log()] == ["before"]
        # and drain
        assert [e.event for e in controller.drain_diagnostic_log()] == ["before"]
        assert controller.drain_diagnostic_log() == ()


#-----------------------------------------------------------------
# Diagnostic log and series of the device controller base classes.
#-----------------------------------------------------------------

class _DiagThread(MonitorThreadBase):
    """Thread that only records diagnostic events."""

    def _connect(self) -> None: ...

    def _disconnect(self) -> None: ...

    def _read_status(self) -> dict[str, Any]:
        return {}

    def record_cycle(self, i: int) -> None:
        """Three events like a control cycle. The last one is not tracked as series."""
        self._record_diagnostic(
            "readings_updated", ac_power_w=i * 10, soc_percent=50 + i, read_ms=8.0
        )
        self._record_diagnostic("setpoint_calculated", setpoint_w=-i, inputs_valid=i < 3)
        self._record_diagnostic("modbus_read", address=30775, count=2, ms=3.0)


class _DiagSettings(ControllerBaseSettings):
    pass


class _DiagController(ControllerBase):
    Settings: ClassVar[type[_DiagSettings]] = _DiagSettings
    DIAGNOSTIC_SERIES: ClassVar = {
        "readings_updated": None,
        "setpoint_calculated": ("setpoint_w", "inputs_valid"),
    }

    def _create_control_thread(self) -> MonitorThreadBase:
        return _DiagThread(self.device_id, self.shared_data, self.diagnostic_log)


class _Diag(NamedTuple):
    log: ControlDiagnosticLog
    controller: _DiagController
    thread: _DiagThread


class TestControlDiagnostics:
    """Sequence numbers of the diagnostic log and the device independent diagnostic view."""

    @staticmethod
    def _make(capacity: int = 50) -> _Diag:
        log = ControlDiagnosticLog(capacity)
        shared = ControlSharedDataStore()
        controller = _DiagController("inverter1", shared, log, _DiagSettings())
        return _Diag(log, controller, _DiagThread("inverter1", shared, log))

    @pytest.fixture
    def diag(self) -> _Diag:
        return self._make()

    def test_seq_is_assigned_and_monotonic(self, diag: _Diag) -> None:
        for i in range(1, 4):
            diag.thread.record_cycle(i)
        entries = diag.log.snapshot()
        assert [e.seq for e in entries] == list(range(1, 10))
        assert diag.log.last_seq == 9
        assert isinstance(entries[0], ControlDiagnosticEntry)

    def test_diagnostic_view_of_the_controller(self, diag: _Diag) -> None:
        for i in range(1, 6):
            diag.thread.record_cycle(i)
        view = diag.controller.diagnostic_view()
        assert view.device_id == "inverter1"
        assert view.last_seq == 15
        assert set(view.series) == {
            "readings_updated.ac_power_w",
            "readings_updated.soc_percent",
            "readings_updated.read_ms",
            "setpoint_calculated.setpoint_w",
            "setpoint_calculated.inputs_valid",
        }
        titles = {graph.title for graph in view.graphs}
        assert {"Power (W)", "Percent (%)", "Duration (ms)", "Modes and states"} <= titles
        assert view.get_series("readings_updated.ac_power_w").iloc[-1] == 50.0

    def test_the_log_serves_the_declared_graphs(self) -> None:
        graph = DiagnosticGraph(
            title="AC power", unit="W", kind="numeric", series=["readings_updated.ac_power_w"]
        )

        class _GraphController(_DiagController):
            DIAGNOSTIC_GRAPHS: ClassVar = (graph,)

        log = ControlDiagnosticLog(50)
        shared = ControlSharedDataStore()
        _GraphController("inverter1", shared, log, _DiagSettings())
        _DiagThread("inverter1", shared, log).record_cycle(1)
        # Readers without the controller (REST API) get the declared graphs, too.
        assert log.series_view().graphs == [graph]

    def test_series_outlive_drain_and_event_buffer(self) -> None:
        diag = self._make(capacity=5)  # the event buffer keeps the last 5 events only
        for i in range(1, 21):
            diag.thread.record_cycle(i)
        assert len(diag.log.snapshot()) == 5
        assert len(diag.controller.diagnostic_view().get_series("readings_updated.ac_power_w")) == 20
        diag.log.drain()
        assert len(diag.controller.diagnostic_view().get_series("readings_updated.ac_power_w")) == 20

    def test_incremental_polling_and_reset(self, diag: _Diag) -> None:
        for i in range(1, 4):
            diag.thread.record_cycle(i)
        first = diag.controller.diagnostic_view()
        for i in range(4, 6):
            diag.thread.record_cycle(i)
        newer = diag.controller.diagnostic_view(since_seq=first.last_seq)
        assert list(newer.get_series("readings_updated.ac_power_w").values) == [40.0, 50.0]
        assert not newer.reset
        # a client that is ahead of the log (restart of EOS) is told to start over
        assert diag.controller.diagnostic_view(since_seq=10_000).reset

    def test_disabled_log_collects_nothing(self) -> None:
        diag = self._make(capacity=0)
        diag.thread.record_cycle(1)
        assert diag.log.last_seq == 0
        assert not diag.controller.diagnostic_view().series

    def test_new_controller_instance_keeps_series(self, diag: _Diag) -> None:
        diag.thread.record_cycle(1)
        # the adapter replaces a controller whose settings changed, the log is kept
        replacement = _DiagController(
            "inverter1", ControlSharedDataStore(), diag.log, _DiagSettings()
        )
        diag.thread.record_cycle(2)
        series = replacement.diagnostic_view().get_series("readings_updated.ac_power_w")
        assert list(series.values) == [10.0, 20.0]


    def test_writer_thread_and_reader(self) -> None:
        """A writer thread and an incremental reader: every point arrives once, in order.

        The writer records bursts of cycles of different sizes, the reader takes a view after
        each burst. Events hand over between the two threads, there are no sleeps. That no
        point of a view lies beyond its ``last_seq`` is checked by the series store tests.
        """
        bursts = (1, 7, 2, 30, 5, 13, 42)
        cycles = sum(bursts)
        diag = self._make(capacity=20)
        go = threading.Event()
        written = threading.Event()

        def writer() -> None:
            cycle = 0
            for burst in bursts:
                go.wait(timeout=5.0)
                go.clear()
                for _ in range(burst):
                    cycle += 1
                    diag.thread.record_cycle(cycle)
                written.set()

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        since = 0
        seen: list[float] = []
        try:
            for _ in bursts:
                go.set()
                assert written.wait(timeout=5.0)
                written.clear()
                view = diag.controller.diagnostic_view(since_seq=since)
                seen.extend(view.get_series("readings_updated.ac_power_w").values.tolist())
                since = view.last_seq
        finally:
            thread.join(timeout=5.0)
        assert not thread.is_alive()
        # no point lost and none twice (ac_power_w = i * 10)
        assert seen == [10.0 * i for i in range(1, cycles + 1)]
