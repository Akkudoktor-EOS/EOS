"""Tests for the SMA Energy Meter device controller (``smaenergymeter.py``).

Scope: ``SmaEnergyMeterSettings``, ``SmaEnergyMeterThread`` and ``SmaEnergyMeter``.

The Speedwire socket layer (``smaspeedwire.SpeedwireListener``) is replaced by
fakes; its own decoding logic is tested separately in the smaspeedwire test
module. These tests therefore need no network access and never open a socket.
"""

import threading
import time
from typing import Any, cast
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from akkudoktoreos.adapter.controllers.controlabc import (
    ControlDiagnosticLog,
    ControlSharedDataStore,
)
from akkudoktoreos.adapter.controllers.sma import smaenergymeter as em
from akkudoktoreos.adapter.controllers.sma.smaspeedwire import (
    Measurement,
    MeterTelegram,
    ObisCode,
    SpeedwireListener,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

# ---------------------------------------------------------------------------
# Fakes and helpers
# ---------------------------------------------------------------------------


class FakeListener:
    """Stand-in for ``smaspeedwire.SpeedwireListener``.

    ``receive()`` pops from a scripted list of return values (``None`` or a
    ``MeterTelegram``), so the thread's cycle logic can be driven step by step.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.open_calls = 0
        self.close_calls = 0
        self.script: list[MeterTelegram | None] = []
        self.receive_calls = 0
        # Set when the script is used up: the telegrams before were handled.
        self.drained = threading.Event()
        self.args = args
        self.kwargs = kwargs

    def open(self) -> None:
        self.open_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def receive(self) -> MeterTelegram | None:
        self.receive_calls += 1
        if not self.script:
            self.drained.set()
            return None
        return self.script.pop(0)


def _measurement(name: str, value: float) -> Measurement:
    return Measurement(
        obis=ObisCode(0, 0, 0, 0),
        raw_value=int(value),
        name=name,
        value=value,
        unit="",
    )


def _telegram(
    *,
    serial: int = 1900123456,
    import_kwh: float = 100.0,
    export_kwh: float = 50.0,
    import_w: float = 1000.0,
    export_w: float = 400.0,
) -> MeterTelegram:
    """Build a telegram carrying exactly the four readings the thread requires."""
    return MeterTelegram(
        susy_id=0x0080,
        serial_number=serial,
        ticker_ms=0,
        measurements=(
            _measurement(em._IMPORT_ENERGY, import_kwh),
            _measurement(em._EXPORT_ENERGY, export_kwh),
            _measurement(em._IMPORT_POWER, import_w),
            _measurement(em._EXPORT_POWER, export_w),
        ),
        received_at=to_datetime(in_timezone="UTC"),
    )


def fake(thread: em.SmaEnergyMeterThread) -> FakeListener:
    """The FakeListener installed in a thread."""
    return cast(FakeListener, thread._listener)


def _make_thread(
    *,
    device_id: str = "meter1",
    serial: int = 1900123456,
    rejoin_after: float = 60.0,
) -> em.SmaEnergyMeterThread:
    """Build a thread with a FakeListener installed in place of the real one."""
    thread = em.SmaEnergyMeterThread(
        device_id,
        ControlSharedDataStore(),
        ControlDiagnosticLog(),
        serial=serial,
        rejoin_after=rejoin_after,
    )
    thread._listener = cast(SpeedwireListener, FakeListener())
    return thread


# ---------------------------------------------------------------------------
# Constants and settings
# ---------------------------------------------------------------------------


class TestGridKeyConstants:
    def test_grid_keys_are_unique_and_unit_tagged(self):
        assert len(set(em.GRID_KEYS)) == len(em.GRID_KEYS)
        for key in em.GRID_KEYS:
            assert key.endswith(("_w", "_kwh")), key


class TestSmaEnergyMeterSettings:
    def test_defaults(self):
        s = em.SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1)
        assert s.controller_type == "sma:energy-meter"
        assert s.serial == 1
        assert s.interface_ip == "0.0.0.0"
        assert s.rejoin_after == 60.0

    def test_rejoin_after_must_be_positive(self):
        with pytest.raises(ValidationError):
            em.SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1, rejoin_after=0)
        with pytest.raises(ValidationError):
            em.SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1, rejoin_after=-1)

    def test_shared_data_keys_are_read_only(self):
        # ``shared_data_keys`` is a computed field (instance-level property), not a
        # model field and not settable. Reading it on an instance is the public API;
        # reading it on the class returns the property descriptor, which is why this
        # asserts on an instance.
        s = em.SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1)
        assert s.shared_data_keys == em.GRID_KEYS

    def test_shared_data_keys_are_not_a_model_field(self):
        # Read-only in the pydantic sense: computed, not a field, so it can neither
        # be set at construction nor show up as an input field in the JSON schema.
        assert "shared_data_keys" not in em.SmaEnergyMeterSettings.model_fields
        s = em.SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1, shared_data_keys=("x",))
        assert s.shared_data_keys == em.GRID_KEYS  # silently ignored, value unchanged


# ---------------------------------------------------------------------------
# Thread — construction and public surface
# ---------------------------------------------------------------------------


class TestSmaEnergyMeterThreadConstruction:
    def test_published_keys_are_prefixed(self):
        thread = _make_thread(device_id="meter7")
        assert thread.published_keys() == tuple("meter7:" + k for k in em.GRID_KEYS)

    def test_poll_interval_is_zero_so_receive_paces_the_loop(self):
        thread = _make_thread()
        assert thread.poll_interval == 0.0

    def test_listener_is_constructed_with_interface_and_timeout(self):
        with patch.object(em, "SpeedwireListener") as listener_cls:
            em.SmaEnergyMeterThread(
                "meter1",
                ControlSharedDataStore(),
                ControlDiagnosticLog(),
                serial=1,
                interface_ip="192.168.1.10",
            )
        listener_cls.assert_called_once()
        args, kwargs = listener_cls.call_args
        assert args[0] == "192.168.1.10"
        assert kwargs["timeout"] == em._RECEIVE_TIMEOUT_S


# ---------------------------------------------------------------------------
# Thread — connect / disconnect / on_connected
# ---------------------------------------------------------------------------


class TestSmaEnergyMeterThreadHooks:
    def test_connect_opens_listener(self):
        thread = _make_thread()
        thread._connect()
        assert fake(thread).open_calls == 1

    def test_disconnect_closes_listener(self):
        thread = _make_thread()
        thread._disconnect()
        assert fake(thread).close_calls == 1

    def test_disconnect_is_idempotent(self):
        # SpeedwireListener.close() is idempotent; the hook must not add its own state.
        thread = _make_thread()
        thread._disconnect()
        thread._disconnect()
        assert fake(thread).close_calls == 2

    def test_on_connected_resets_last_telegram_time(self):
        thread = _make_thread()
        thread._last_telegram = 0.0
        thread._on_connected()
        assert thread._last_telegram > 0.0


# ---------------------------------------------------------------------------
# Thread — _to_readings
# ---------------------------------------------------------------------------


class TestToReadings:
    def test_complete_telegram_maps_all_readings(self):
        thread = _make_thread()
        readings = thread._to_readings(
            _telegram(import_kwh=100.0, export_kwh=50.0, import_w=1000.0, export_w=400.0)
        )
        assert readings == {
            em.GRID_IMPORT_KWH: 100.0,
            em.GRID_EXPORT_KWH: 50.0,
            em.GRID_IMPORT_W: 1000.0,
            em.GRID_EXPORT_W: 400.0,
            em.GRID_NET_W: 600.0,
        }

    def test_net_w_is_import_minus_export(self):
        thread = _make_thread()
        readings = thread._to_readings(_telegram(import_w=100.0, export_w=250.0))
        assert readings is not None
        assert readings[em.GRID_NET_W] == -150.0

    def test_foreign_serial_is_ignored(self):
        thread = _make_thread(serial=42)
        assert thread._to_readings(_telegram(serial=43)) is None

    def test_foreign_serial_is_logged_once(self):
        thread = _make_thread(serial=42)
        with patch.object(em.logger, "warning") as warn:
            thread._to_readings(_telegram(serial=43))
            thread._to_readings(_telegram(serial=43))
        warn.assert_called_once()

    @pytest.mark.parametrize(
        "missing",
        [em._IMPORT_ENERGY, em._EXPORT_ENERGY, em._IMPORT_POWER, em._EXPORT_POWER],
    )
    def test_missing_required_value_is_rejected(self, missing):
        thread = _make_thread()
        telegram = _telegram()
        telegram = MeterTelegram(
            susy_id=telegram.susy_id,
            serial_number=telegram.serial_number,
            ticker_ms=telegram.ticker_ms,
            measurements=tuple(m for m in telegram.measurements if m.name != missing),
        )
        assert thread._to_readings(telegram) is None

    def test_missing_required_value_is_logged_once(self):
        thread = _make_thread()
        incomplete = MeterTelegram(
            susy_id=0,
            serial_number=1900123456,
            ticker_ms=0,
            measurements=(),
        )
        with patch.object(em.logger, "warning") as warn:
            thread._to_readings(incomplete)
            thread._to_readings(incomplete)
        warn.assert_called_once()


# ---------------------------------------------------------------------------
# Thread — _read_status
# ---------------------------------------------------------------------------


class TestReadStatus:
    def test_publishes_all_readings_in_one_batch(self):
        store = ControlSharedDataStore()
        log = ControlDiagnosticLog()
        thread = em.SmaEnergyMeterThread("meter1", store, log, serial=1900123456)
        thread._listener = cast(SpeedwireListener, FakeListener())
        fake(thread).script = [_telegram(import_w=1000.0, export_w=400.0)]

        thread._read_status()

        snapshot = store.snapshot()
        assert snapshot == {
            "meter1:grid_import_kwh": 100.0,
            "meter1:grid_export_kwh": 50.0,
            "meter1:grid_import_w": 1000.0,
            "meter1:grid_export_w": 400.0,
            "meter1:grid_net_w": 600.0,
        }

    def test_all_values_share_one_timestamp(self):
        # ``get_many`` reads one coherent image with a single ``now``, so all ages
        # of one publish batch are exactly equal. (Do not loop ``get_many`` once
        # per key — each call captures its own ``now`` and the ages would differ.)
        store = ControlSharedDataStore()
        log = ControlDiagnosticLog()
        thread = em.SmaEnergyMeterThread("meter1", store, log, serial=1900123456)
        thread._listener = cast(SpeedwireListener, FakeListener())
        fake(thread).script = [_telegram()]

        thread._read_status()

        entries = store.get_many(f"meter1:{k}" for k in em.GRID_KEYS)
        ages = [entries[f"meter1:{k}"][1] for k in em.GRID_KEYS]
        assert len(ages) == len(em.GRID_KEYS)
        assert max(ages) == min(ages)

    def test_no_telegram_publishes_nothing(self):
        store = ControlSharedDataStore()
        log = ControlDiagnosticLog()
        thread = em.SmaEnergyMeterThread("meter1", store, log, serial=1900123456)
        thread._listener = cast(SpeedwireListener, FakeListener())  # empty script -> receive() returns None

        thread._read_status()

        assert store.snapshot() == {}

    def test_status_data_is_empty(self):
        # Readings go to the shared store, not through the status callback.
        thread = _make_thread()
        fake(thread).script = [_telegram()]
        data = thread._read_status()
        assert data == {}

    def test_foreign_telegram_publishes_nothing(self):
        store = ControlSharedDataStore()
        log = ControlDiagnosticLog()
        thread = em.SmaEnergyMeterThread("meter1", store, log, serial=1900123456)
        thread._listener = cast(SpeedwireListener, FakeListener())
        fake(thread).script = [_telegram(serial=999)]

        thread._read_status()

        assert store.snapshot() == {}

    def test_last_telegram_time_advances_on_decoded_telegram(self):
        thread = _make_thread()
        thread._last_telegram = 0.0
        fake(thread).script = [_telegram()]
        thread._read_status()
        assert thread._last_telegram > 0.0

    def test_rejoin_after_silence_closes_listener(self):
        thread = _make_thread(rejoin_after=0.0)
        thread._last_telegram = time.monotonic() - 1.0
        with patch.object(em.logger, "warning") as warn:
            thread._read_status()
        assert fake(thread).close_calls == 1
        warn.assert_called_once()

    def test_rejoin_resets_last_telegram_timer(self):
        thread = _make_thread(rejoin_after=0.0)
        thread._last_telegram = time.monotonic() - 1.0
        thread._read_status()
        assert time.monotonic() - thread._last_telegram < 0.1

    def test_socket_error_propagates(self):
        thread = _make_thread()

        def boom():
            raise OSError("socket gone")

        fake(thread).receive = boom  # type: ignore[method-assign]
        with pytest.raises(OSError, match="socket gone"):
            thread._read_status()


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class TestSmaEnergyMeterController:
    def test_readings_become_diagnostic_series(self):
        log = ControlDiagnosticLog(capacity=10)
        controller = em.SmaEnergyMeter(
            "meter1",
            ControlSharedDataStore(),
            log,
            em.SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1900123456),
        )
        thread = controller._create_control_thread()
        assert isinstance(thread, em.SmaEnergyMeterThread)
        thread._listener = cast(SpeedwireListener, FakeListener())
        fake(thread).script = [_telegram(import_w=1000.0, export_w=400.0)]

        thread._read_status()

        view = controller.diagnostic_view()
        assert list(view.get_series("readings_updated.grid_net_w").values) == [600.0]
        assert [g.title for g in view.graphs] == ["Grid power", "Grid energy"]

    def test_graphs_use_the_collected_series(self):
        for graph in em.SmaEnergyMeter.DIAGNOSTIC_GRAPHS:
            for key in graph.series:
                event, field = key.split(".")
                fields = em.SmaEnergyMeter.DIAGNOSTIC_SERIES[event]
                assert fields is not None and field in fields, key

    def test_settings_class_is_bound(self):
        assert em.SmaEnergyMeter.Settings is em.SmaEnergyMeterSettings
        assert em.SmaEnergyMeterSettings.controller_class() is em.SmaEnergyMeter

    def test_provided_data_keys_match_grid_keys(self):
        assert em.SmaEnergyMeter.PROVIDED_DATA_KEYS == em.GRID_KEYS

    def test_settings_declare_the_keys_the_controller_provides(self):
        # The settings mirror the controller's PROVIDED_DATA_KEYS; the tuple is
        # only reachable from an instance (computed field, not a class attribute).
        assert (
            em.SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1).shared_data_keys
            == em.SmaEnergyMeter.PROVIDED_DATA_KEYS
        )
        assert em.SmaEnergyMeter.PROVIDED_DATA_KEYS == em.GRID_KEYS

    def test_shared_data_keys_are_prefixed(self):
        controller = em.SmaEnergyMeter(
            "meter9",
            ControlSharedDataStore(),
            ControlDiagnosticLog(),
            em.SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1)
        )
        assert controller.shared_data_keys() == tuple("meter9:" + k for k in em.GRID_KEYS)

    def test_settings_property_is_narrowed(self):
        controller = em.SmaEnergyMeter(
            "meter1",
            ControlSharedDataStore(),
            ControlDiagnosticLog(),
            em.SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1900123456),
        )
        s = controller.settings
        assert isinstance(s, em.SmaEnergyMeterSettings)
        assert s.serial == 1900123456

    def test_create_control_thread_uses_settings(self):
        controller = em.SmaEnergyMeter(
            "meter1",
            ControlSharedDataStore(),
            ControlDiagnosticLog(),
            em.SmaEnergyMeterSettings(
                controller_type="sma:energy-meter",
                serial=42,
                interface_ip="192.168.1.10",
                rejoin_after=5.0,
            ),
        )
        with patch.object(em, "SpeedwireListener"):
            thread = controller._create_control_thread()

        assert isinstance(thread, em.SmaEnergyMeterThread)
        assert thread.device_id == "meter1"
        assert thread._serial == 42
        assert thread._rejoin_after == 5.0
        assert thread.published_keys() == controller.shared_data_keys()

    @pytest.mark.asyncio
    async def test_start_runs_thread_and_receives_telegrams(self):
        store = ControlSharedDataStore()

        with patch.object(em, "SpeedwireListener") as listener_cls:
            # The thread's __init__ calls ``SpeedwireListener(...)``; return a
            # scripted FakeListener from that call so the thread drives it.
            listeners: list[FakeListener] = []

            def make_listener(*a: Any, **kw: Any) -> FakeListener:
                listeners.append(_scripted(FakeListener(*a, **kw)))
                return listeners[-1]

            listener_cls.side_effect = make_listener
            controller = em.SmaEnergyMeter(
                "meter1",
                store,
                ControlDiagnosticLog(),
                em.SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1900123456),
            )
            await controller.start()
            try:
                assert listeners[0].drained.wait(timeout=5)
                assert store.get("meter1:grid_net_w") == 600.0
            finally:
                await controller.stop()

    @pytest.mark.asyncio
    async def test_start_rejects_key_mismatch(self):
        class WrongKeys(em.SmaEnergyMeter):
            # Inherit Settings = SmaEnergyMeterSettings from SmaEnergyMeter; only
            # override the declared keys, so published_keys() and shared_data_keys()
            # disagree and start() must raise before the thread is started.
            PROVIDED_DATA_KEYS = ("grid_w",)

        controller = WrongKeys(
            "meter1",
            ControlSharedDataStore(),
            ControlDiagnosticLog(),
            em.SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1),
        )
        with patch.object(em, "SpeedwireListener", FakeListener):
            with pytest.raises(RuntimeError, match="publishes"):
                await controller.start()
        assert controller._thread is None

    def test_receive_only_controller_has_no_schedule(self):
        controller = em.SmaEnergyMeter(
            "meter1",
            ControlSharedDataStore(),
            ControlDiagnosticLog(),
            em.SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1),
        )
        with pytest.raises(NotImplementedError):
            controller._emplan_to_commands(None)


# ---------------------------------------------------------------------------
# start() integration helper
# ---------------------------------------------------------------------------


def _scripted(listener: FakeListener) -> FakeListener:
    # One telegram, then silence: enough for start() to publish once and stop.
    listener.script = [_telegram()]
    return listener
