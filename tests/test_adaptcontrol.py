"""Tests for the device control adapter (``control.py``).

Scope: only the adapter module — ``ControlAdapter``,
``ControlAdapterCommonSettings``, the import-time settings<->controller
binding invariant, ``_reconcile``, ``_diagnostic_log``, ``_acquire_measurements``,
``_dispatch_plan``, ``_update_data``, ``clear``, ``provider_id`` and
``device_control_clear``.

Everything in ``controlabc.py`` (shared store, settings base, command and
status dataclasses, thread bases, controller base, diagnostic log) is tested
in ``test_adaptercontrolabc.py`` and is intentionally not re-tested here.

Note: ``ControlAdapter`` is a pydantic model (``AdapterProvider`` →
``SettingsBaseModel``) with ``validate_assignment`` on its fields. Tests must
therefore:

* only ever assign *real* ``ControllerBase`` instances to
  ``adapter.controllers``;
* patch ``config`` / ``ems`` on the class via ``PropertyMock``;
* patch adapter methods on the class (``patch.object(type(adapter), ...)``),
  because assigning bound methods to the instance is silently ignored.
"""

import asyncio
import json
import math
import threading
from dataclasses import dataclass
from typing import Any, ClassVar, Iterator, cast, get_args
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from pydantic import ValidationError

from akkudoktoreos.adapter.control import (
    ControlAdapter,
    ControlAdapterCommonSettings,
    ControllerSettings,
    device_control_clear,
)
from akkudoktoreos.adapter.controllers.controlabc import (
    ControlDiagnosticEntry,
    ControlDiagnosticLog,
    ControllerBase,
    ControllerBaseSettings,
    ControlSharedDataStore,
    MonitorThreadBase,
)
from akkudoktoreos.adapter.controllers.sma.smaenergymeter import (
    SmaEnergyMeter,
    SmaEnergyMeterSettings,
)
from akkudoktoreos.core.ems import EnergyManagementStage
from akkudoktoreos.utils.datetimeutil import to_datetime

# ---------------------------------------------------------------------------
# Local controller / settings / thread pair used by the adapter tests
# ---------------------------------------------------------------------------


class _AdapterTestThread(MonitorThreadBase):
    """Minimal thread that publishes exactly the keys its controller declares."""

    def __init__(
        self,
        device_id: str,
        shared_data: ControlSharedDataStore,
        diagnostic_log: ControlDiagnosticLog,
        *,
        keys: tuple[str, ...],
    ) -> None:
        # poll_interval must be > 0: with 0.0, MonitorThreadBase.run() spins
        # on Event.wait(0.0) and floods the test event loop with status
        # notifications, which makes the test appear to hang at teardown.
        super().__init__(device_id, shared_data, diagnostic_log, poll_interval=0.05)
        self._keys = keys

    def _connect(self) -> None: ...
    def _disconnect(self) -> None: ...
    def _read_status(self) -> dict[str, Any]:
        return {}

    def published_keys(self) -> tuple[str, ...]:
        return self._keys


class AdapterTestSettings(ControllerBaseSettings):
    # Not part of the production union; only used to drive the adapter in tests.
    controller_type: str = "test:adapter"
    poll: float = 1.0


class AdapterTestController(ControllerBase):
    Settings: ClassVar[type[AdapterTestSettings]] = AdapterTestSettings
    PROVIDED_DATA_KEYS: ClassVar[tuple[str, ...]] = ("grid_w",)

    starts = 0
    stops = 0

    def _create_control_thread(self) -> MonitorThreadBase:
        return _AdapterTestThread(
            self.device_id, self.shared_data, self.diagnostic_log, keys=self.shared_data_keys()
        )

    async def start(self) -> None:
        type(self).starts += 1
        await super().start()

    async def stop(self) -> None:
        type(self).stops += 1
        await super().stop()


class LiveTestSettings(ControllerBaseSettings):
    # Not part of the production union; a controller with one live field besides the log.
    controller_type: str = "test:live"
    poll: float = 1.0
    host: str = "a"


class LiveTestController(AdapterTestController):
    Settings: ClassVar[type[LiveTestSettings]] = LiveTestSettings  # type: ignore[assignment]
    _LIVE_FIELDS: ClassVar[set[str]] = ControllerBase._LIVE_FIELDS | {"poll"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.live_changes: list[float] = []

    def _on_live_settings_changed(self) -> None:
        self.live_changes.append(cast(LiveTestSettings, self.settings).poll)


def _make_controller(device_id: str = "m1") -> AdapterTestController:
    """Build a real, not-yet-started controller for ``device_id``.

    The controller signature is ``(device_id, settings, shared_data, diagnostic_log)``
    with the log as a keyword-only argument (see ``ControllerBase.__init__``).
    """
    c = AdapterTestController(
        device_id,
        ControlSharedDataStore(),
        ControlDiagnosticLog(capacity=100),
        AdapterTestSettings(),
    )
    return c


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class Harness:
    adapter: ControlAdapter
    config: MagicMock
    ems: MagicMock


@pytest.fixture
def h() -> Iterator[Harness]:
    """Fresh adapter with ``config`` and ``ems`` patched on the class.

    ``controllers``, ``diagnostic_logs`` and ``shared_data`` are pydantic
    fields of the adapter, so they are reset to empty values (never to mocks).
    ``config`` / ``ems`` are provided by ``ConfigMixin`` / ``EmsMixin`` on the
    class, so they are patched with ``PropertyMock``; instance assignment
    would be ignored.
    """
    adapter = ControlAdapter()
    adapter.controllers = {}
    adapter.diagnostic_logs = {}
    adapter.start_errors = {}
    adapter.shared_data = ControlSharedDataStore()

    config = MagicMock(name="config")
    config.adapter.control.controllers = {}
    ems = MagicMock(name="ems")
    ems.stage.return_value = EnergyManagementStage.OPTIMIZATION

    with patch.object(
        type(adapter), "config", new_callable=PropertyMock, return_value=config
    ), patch.object(
        type(adapter), "ems", new_callable=PropertyMock, return_value=ems
    ):
        AdapterTestController.starts = 0
        AdapterTestController.stops = 0
        yield Harness(adapter=adapter, config=config, ems=ems)


# ---------------------------------------------------------------------------
# Import-time binding invariant
# ---------------------------------------------------------------------------


class TestImportTimeBinding:
    def test_every_settings_class_in_union_has_a_bound_controller(self):
        # control.py runs this loop at import time. Re-assert it here so a
        # new controller that forgets ``Settings: ClassVar[...]`` fails a named
        # test rather than an obscure import error.
        union = get_args(get_args(ControllerSettings)[0])
        assert union, "ControllerSettings union is empty"
        for settings_cls in union:
            controller_cls = settings_cls.controller_class()
            assert issubclass(controller_cls, ControllerBase)

    def test_unbound_settings_class_raises(self):
        class Unbound(ControllerBaseSettings):
            pass

        with pytest.raises(RuntimeError, match="No controller class bound"):
            Unbound.controller_class()

    def test_energy_meter_settings_bind_to_energy_meter_controller(self):
        assert SmaEnergyMeterSettings.controller_class() is SmaEnergyMeter


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestControlAdapterCommonSettings:
    def test_discriminates_by_controller_type(self):
        settings = ControlAdapterCommonSettings.model_validate(
            {"controllers": {"m1": {"controller_type": "sma:energy-meter", "serial": 123}}}
        )
        assert isinstance(settings.controllers["m1"], SmaEnergyMeterSettings)

    def test_shared_data_keys_of_all_controllers(self):
        settings = ControlAdapterCommonSettings.model_validate(
            {
                "controllers": {
                    "m2": {"controller_type": "sma:energy-meter", "serial": 2},
                    "m1": {"controller_type": "sma:energy-meter", "serial": 1},
                }
            }
        )
        suffixes = SmaEnergyMeterSettings(controller_type="sma:energy-meter", serial=1).shared_data_keys
        assert settings.shared_data_keys == sorted(
            f"{device_id}:{suffix}" for device_id in ("m1", "m2") for suffix in suffixes
        )
        assert "m1:grid_net_w" in settings.shared_data_keys

    def test_controller_type_survives_saving_the_config(self, config_eos):
        # The config file is saved without default values: the type must not be one.
        controllers = {
            "meter1": {"controller_type": "sma:energy-meter", "serial": 123},
            "inverter1": {
                "controller_type": "sma:sunny-tripower-smart-energy",
                "host": "192.168.1.6",
                "battery_id": "battery1",
                "grid_power_key": "meter1:grid_net_w",
            },
        }
        config_eos.set_nested_value("adapter/control/controllers", controllers)
        saved = json.loads(config_eos.to_config_json())["adapter"]["control"]["controllers"]
        assert saved["meter1"]["controller_type"] == "sma:energy-meter"
        assert saved["inverter1"]["controller_type"] == "sma:sunny-tripower-smart-energy"
        reloaded = ControlAdapterCommonSettings.model_validate({"controllers": saved})
        assert isinstance(reloaded.controllers["meter1"], SmaEnergyMeterSettings)

    def test_controller_type_is_required(self):
        with pytest.raises(ValidationError, match="controller_type"):
            SmaEnergyMeterSettings(serial=1)  # type: ignore[call-arg]

    def test_shared_data_keys_without_controllers(self):
        assert ControlAdapterCommonSettings().shared_data_keys == []

    def test_rejects_unknown_controller_type(self):
        with pytest.raises(ValidationError):
            ControlAdapterCommonSettings.model_validate(
                {"controllers": {"m1": {"controller_type": "does:not-exist"}}}
            )

    def test_maps_controller_readings_to_measurement_keys(self):
        settings = ControlAdapterCommonSettings.model_validate(
            {
                "grid_import_emr_data_keys": ["meter1:grid_import_kwh"],
                "grid_export_emr_data_keys": ["meter1:grid_export_kwh"],
                "device_measurement_data_keys": {"battery1-soc-factor": "inverter1:bat_soc_factor"},
            }
        )
        # The measurement key of a role reading is derived from its shared data key.
        assert settings.emr_measurement_keys("grid_import") == ["meter1_grid_import_kwh"]
        assert settings.emr_measurement_keys("load") == []
        assert settings.measurement_data_keys() == {
            "meter1_grid_import_kwh": "meter1:grid_import_kwh",
            "meter1_grid_export_kwh": "meter1:grid_export_kwh",
            "battery1-soc-factor": "inverter1:bat_soc_factor",
        }

    def test_rejects_a_malformed_shared_data_key(self):
        with pytest.raises(ValidationError, match="<device_id>:<key>"):
            ControlAdapterCommonSettings.model_validate({"load_emr_data_keys": ["load_kwh"]})
        with pytest.raises(ValidationError, match="<device_id>:<key>"):
            ControlAdapterCommonSettings.model_validate(
                {"device_measurement_data_keys": {"battery1-soc-factor": "soc"}}
            )

    def test_accepts_a_key_of_a_controller_that_is_not_configured(self):
        # A removed controller must not make the configuration invalid.
        settings = ControlAdapterCommonSettings.model_validate(
            {"grid_import_emr_data_keys": ["gone:grid_import_kwh"]}
        )
        assert settings.grid_import_emr_data_keys == ["gone:grid_import_kwh"]


def test_provider_id(h: Harness):
    assert h.adapter.provider_id() == "Control"


# ---------------------------------------------------------------------------
# _diagnostic_log
# ---------------------------------------------------------------------------


class TestDiagnosticLog:
    def test_creates_log_with_configured_capacity(self, h: Harness):
        settings = AdapterTestSettings(diagnostic_log_capacity=42)
        log = h.adapter._diagnostic_log("m1", settings)

        assert log.capacity == 42
        assert h.adapter.diagnostic_logs["m1"] is log

    def test_returns_existing_log_without_resize(self, h: Harness):
        settings = AdapterTestSettings(diagnostic_log_capacity=42)
        first = h.adapter._diagnostic_log("m1", settings)
        second = h.adapter._diagnostic_log("m1", settings)

        assert first is second

    def test_resizes_existing_log_live(self, h: Harness):
        h.adapter._diagnostic_log("m1", AdapterTestSettings(diagnostic_log_capacity=10))
        resized = h.adapter._diagnostic_log(
            "m1", AdapterTestSettings(diagnostic_log_capacity=500)
        )

        assert resized.capacity == 500
        assert h.adapter.diagnostic_logs["m1"] is resized


# ---------------------------------------------------------------------------
# _reconcile
# ---------------------------------------------------------------------------


class TestReconcile:
    @pytest.mark.asyncio
    async def test_starts_configured_controller_and_registers_after_start(self, h: Harness):
        h.config.adapter.control.controllers = {"m1": AdapterTestSettings()}

        await h.adapter._reconcile()

        assert "m1" in h.adapter.controllers
        assert isinstance(h.adapter.controllers["m1"], AdapterTestController)
        assert h.adapter.controllers["m1"]._thread is not None
        assert AdapterTestController.starts == 1

    @pytest.mark.asyncio
    async def test_keeps_unchanged_controller(self, h: Harness):
        h.config.adapter.control.controllers = {"m1": AdapterTestSettings()}
        await h.adapter._reconcile()
        first = h.adapter.controllers["m1"]

        await h.adapter._reconcile()

        assert h.adapter.controllers["m1"] is first
        assert AdapterTestController.starts == 1
        assert AdapterTestController.stops == 0

    @pytest.mark.asyncio
    async def test_replaces_controller_whose_settings_changed(self, h: Harness):
        h.config.adapter.control.controllers = {"m1": AdapterTestSettings(poll=1.0)}
        await h.adapter._reconcile()
        first = h.adapter.controllers["m1"]

        h.config.adapter.control.controllers = {"m1": AdapterTestSettings(poll=2.0)}
        await h.adapter._reconcile()

        assert h.adapter.controllers["m1"] is not first
        assert AdapterTestController.starts == 2
        assert AdapterTestController.stops == 1

    @pytest.mark.asyncio
    async def test_removes_stale_controller_and_its_shared_keys(self, h: Harness):
        h.config.adapter.control.controllers = {"m1": AdapterTestSettings()}
        await h.adapter._reconcile()
        h.adapter.shared_data.publish_sync({"m1:grid_w": 42})

        h.config.adapter.control.controllers = {}
        await h.adapter._reconcile()

        assert "m1" not in h.adapter.controllers
        assert AdapterTestController.stops == 1
        # The stale controller's keys must not outlive it.
        assert h.adapter.shared_data.snapshot() == {}

    @pytest.mark.asyncio
    async def test_failed_start_is_not_registered_and_retried(self, h: Harness):
        class FailingStart(AdapterTestController):
            def _create_control_thread(self):
                raise RuntimeError("boom")

        h.config.adapter.control.controllers = {"m1": AdapterTestSettings()}
        with patch.object(AdapterTestSettings, "controller_class", return_value=FailingStart):
            await h.adapter._reconcile()
        assert "m1" not in h.adapter.controllers

        # Next reconcile uses the real controller and succeeds.
        await h.adapter._reconcile()
        assert "m1" in h.adapter.controllers

    @pytest.mark.asyncio
    async def test_one_failing_stop_does_not_block_others(self, h: Harness):
        h.config.adapter.control.controllers = {
            "m1": AdapterTestSettings(),
            "m2": AdapterTestSettings(poll=2.0),
        }
        await h.adapter._reconcile()

        async def bad_stop(self):
            raise RuntimeError("stop failed")

        h.adapter.controllers["m1"].stop = bad_stop.__get__(  # type: ignore[method-assign]
            h.adapter.controllers["m1"]
        )

        h.config.adapter.control.controllers = {}
        await h.adapter._reconcile()

        assert h.adapter.controllers == {}
        assert AdapterTestController.stops == 1  # only m2's real stop ran


# ---------------------------------------------------------------------------
# _reconcile: diagnostic log lifecycle
# ---------------------------------------------------------------------------


class TestReconcileDiagnosticLog:
    """The adapter owns the per-device diagnostic log; it must survive reconfiguration
    and be dropped only when the device itself is removed.
    """

    @pytest.mark.asyncio
    async def test_log_is_created_on_first_start(self, h: Harness):
        h.config.adapter.control.controllers = {
            "m1": AdapterTestSettings(diagnostic_log_capacity=100)
        }

        await h.adapter._reconcile()

        assert "m1" in h.adapter.diagnostic_logs
        assert h.adapter.diagnostic_logs["m1"].capacity == 100

    @pytest.mark.asyncio
    async def test_log_survives_settings_change(self, h: Harness):
        h.config.adapter.control.controllers = {
            "m1": AdapterTestSettings(diagnostic_log_capacity=100)
        }
        await h.adapter._reconcile()
        first_log = h.adapter.diagnostic_logs["m1"]

        # Change a non-live setting -> restart, log must stay.
        h.config.adapter.control.controllers = {
            "m1": AdapterTestSettings(diagnostic_log_capacity=100, poll=2.0)
        }
        await h.adapter._reconcile()

        assert h.adapter.diagnostic_logs["m1"] is first_log

    @pytest.mark.asyncio
    async def test_log_survives_capacity_change_without_restart(self, h: Harness):
        h.config.adapter.control.controllers = {
            "m1": AdapterTestSettings(diagnostic_log_capacity=10)
        }
        await h.adapter._reconcile()
        first = h.adapter.controllers["m1"]

        h.config.adapter.control.controllers = {
            "m1": AdapterTestSettings(diagnostic_log_capacity=200)
        }
        await h.adapter._reconcile()

        # The controller is kept (live field), the log is resized in place.
        assert h.adapter.controllers["m1"] is first
        assert h.adapter.diagnostic_logs["m1"].capacity == 200

    @pytest.mark.asyncio
    async def test_log_is_dropped_when_device_is_removed(self, h: Harness):
        h.config.adapter.control.controllers = {"m1": AdapterTestSettings()}
        await h.adapter._reconcile()
        assert "m1" in h.adapter.diagnostic_logs

        h.config.adapter.control.controllers = {}
        await h.adapter._reconcile()

        assert "m1" not in h.adapter.diagnostic_logs

    @pytest.mark.asyncio
    async def test_lifecycle_events_are_recorded(self, h: Harness):
        h.config.adapter.control.controllers = {
            "m1": AdapterTestSettings(diagnostic_log_capacity=100)
        }
        await h.adapter._reconcile()

        events = [e.event for e in h.adapter.diagnostic_logs["m1"].snapshot()]
        assert "controller_started" in events

    @pytest.mark.asyncio
    async def test_stopped_event_recorded_on_settings_change(self, h: Harness):
        h.config.adapter.control.controllers = {
            "m1": AdapterTestSettings(diagnostic_log_capacity=100, poll=1.0)
        }
        await h.adapter._reconcile()
        log = h.adapter.diagnostic_logs["m1"]
        log.drain()

        h.config.adapter.control.controllers = {
            "m1": AdapterTestSettings(diagnostic_log_capacity=100, poll=2.0)
        }
        await h.adapter._reconcile()

        events = [e.event for e in log.snapshot()]
        assert "controller_stopped" in events

    @pytest.mark.asyncio
    async def test_start_failure_is_recorded(self, h: Harness):
        class FailingStart(AdapterTestController):
            def _create_control_thread(self):
                raise RuntimeError("boom")

        h.config.adapter.control.controllers = {
            "m1": AdapterTestSettings(diagnostic_log_capacity=100)
        }
        with patch.object(AdapterTestSettings, "controller_class", return_value=FailingStart):
            await h.adapter._reconcile()

        events = [
            e.event for e in h.adapter.diagnostic_logs["m1"].snapshot()
        ]
        assert "controller_start_failed" in events


class TestReconcileLiveSettings:
    @pytest.mark.asyncio
    async def test_live_field_reaches_running_controller_without_restart(self, h: Harness):
        h.config.adapter.control.controllers = {"m1": LiveTestSettings()}
        await h.adapter._reconcile()
        first = cast(LiveTestController, h.adapter.controllers["m1"])

        h.config.adapter.control.controllers = {"m1": LiveTestSettings(poll=2.0)}
        await h.adapter._reconcile()

        assert h.adapter.controllers["m1"] is first
        assert first.live_changes == [2.0]
        assert cast(LiveTestSettings, first.settings).poll == 2.0
        await h.adapter._stop_all(dict(h.adapter.controllers))

    @pytest.mark.asyncio
    async def test_unchanged_live_fields_do_not_call_the_hook(self, h: Harness):
        h.config.adapter.control.controllers = {"m1": LiveTestSettings()}
        await h.adapter._reconcile()
        await h.adapter._reconcile()
        assert cast(LiveTestController, h.adapter.controllers["m1"]).live_changes == []
        await h.adapter._stop_all(dict(h.adapter.controllers))

    @pytest.mark.asyncio
    async def test_other_field_restarts(self, h: Harness):
        h.config.adapter.control.controllers = {"m1": LiveTestSettings()}
        await h.adapter._reconcile()
        first = h.adapter.controllers["m1"]

        h.config.adapter.control.controllers = {"m1": LiveTestSettings(poll=2.0, host="b")}
        await h.adapter._reconcile()

        second = cast(LiveTestController, h.adapter.controllers["m1"])
        assert second is not first
        assert second.live_changes == []
        await h.adapter._stop_all(dict(h.adapter.controllers))


class TestReconcileOnConfigChange:
    """A changed configuration is applied right away, not only at the next EMS run."""

    @pytest.fixture(autouse=True)
    def _tracking(self) -> Iterator[None]:
        saved = (ControlAdapter._reconcile_loop, ControlAdapter._tracked_config_id)
        ControlAdapter._reconcile_loop = None
        ControlAdapter._tracked_config_id = None
        yield
        ControlAdapter._reconcile_loop, ControlAdapter._tracked_config_id = saved

    @staticmethod
    def _on_change(h: Harness) -> Any:
        """The callback the adapter registered for configuration changes."""
        h.config.track_nested_value.assert_called_once()
        path, callback = h.config.track_nested_value.call_args.args
        assert path == "/adapter"
        return callback

    @pytest.mark.asyncio
    async def test_reconcile_now_does_nothing_while_disabled(self, h: Harness):
        h.config.adapter.provider = ["HomeAssistant"]
        h.config.adapter.control.controllers = {"m1": AdapterTestSettings()}

        await h.adapter.reconcile_now()

        assert h.adapter.controllers == {}

    @pytest.mark.asyncio
    async def test_reconcile_now_holds_the_adapter_lock(self, h: Harness):
        h.config.adapter.provider = ["Control"]
        h.config.adapter.control.controllers = {"m1": AdapterTestSettings()}

        async with h.adapter._adapter_lock:  # e.g. the adapter update of an EMS run
            task = asyncio.create_task(h.adapter.reconcile_now())
            for _ in range(5):  # let the task run up to the lock
                await asyncio.sleep(0)
            assert not task.done()
            assert h.adapter.controllers == {}  # waits for the lock
        await task

        assert "m1" in h.adapter.controllers

    @pytest.mark.asyncio
    async def test_change_in_a_worker_thread_restarts_the_controller(self, h: Harness):
        h.config.adapter.provider = ["Control"]
        h.config.adapter.control.controllers = {"m1": AdapterTestSettings(poll=1.0)}
        await h.adapter._reconcile()
        first = h.adapter.controllers["m1"]
        h.adapter.track_config_changes(asyncio.get_running_loop())
        on_change = self._on_change(h)

        h.config.adapter.control.controllers = {"m1": AdapterTestSettings(poll=2.0)}
        reconciled = asyncio.Event()
        reconcile_now = ControlAdapter.reconcile_now

        async def reconcile_and_signal(adapter: ControlAdapter) -> None:
            await reconcile_now(adapter)
            reconciled.set()

        with patch.object(ControlAdapter, "reconcile_now", reconcile_and_signal):
            # set_nested_value calls the callback in the thread of the change (REST worker
            # thread)
            worker = threading.Thread(
                target=on_change, args=(h.config, "adapter/control/controllers/m1/poll", 1.0, 2.0)
            )
            worker.start()
            worker.join()
            await asyncio.wait_for(reconciled.wait(), timeout=5)

        assert h.adapter.controllers["m1"] is not first
        settings = h.adapter.controllers["m1"].settings
        assert isinstance(settings, AdapterTestSettings)
        assert settings.poll == 2.0
        await h.adapter._stop_all(h.adapter.controllers)

    @pytest.mark.asyncio
    async def test_registers_once_and_follows_the_loop(self, h: Harness):
        h.adapter.track_config_changes(asyncio.get_running_loop())
        other_loop = asyncio.new_event_loop()
        try:
            h.adapter.track_config_changes(other_loop)
            assert ControlAdapter._reconcile_loop is other_loop
        finally:
            other_loop.close()
        self._on_change(h)  # registered once

    @pytest.mark.asyncio
    async def test_change_without_running_loop_does_not_raise(self, h: Harness):
        closed_loop = asyncio.new_event_loop()
        closed_loop.close()
        h.adapter.track_config_changes(closed_loop)

        # The change is made already: the callback must not fail its request.
        self._on_change(h)(h.config, "adapter/control/controllers", None, {})

    @pytest.mark.asyncio
    async def test_set_nested_value_of_the_config_triggers_the_reconcile(self, config_eos):
        adapter = ControlAdapter()
        config_eos.set_nested_value(
            "adapter/control/controllers",
            {"meter1": {"controller_type": "sma:energy-meter", "serial": 1}},
        )
        reconciled = asyncio.Event()
        with patch.object(
            ControlAdapter,
            "reconcile_now",
            new_callable=AsyncMock,
            side_effect=lambda *args: reconciled.set(),
        ) as reconcile:
            adapter.track_config_changes(asyncio.get_running_loop())
            config_eos.set_nested_value("adapter/control/controllers/meter1/serial", 2)
            await asyncio.wait_for(reconciled.wait(), timeout=5)
        assert reconcile.await_count == 1


class TestReconcileStartErrors:
    """The adapter keeps the start error itself: the diagnostic log may be off."""

    @staticmethod
    def failing(message: str = "boom") -> type[AdapterTestController]:
        class FailingStart(AdapterTestController):
            def _create_control_thread(self):
                raise RuntimeError(message)

        return FailingStart

    @pytest.mark.asyncio
    async def test_start_error_is_kept_with_the_diagnostic_log_off(self, h: Harness):
        h.config.adapter.control.controllers = {"m1": AdapterTestSettings()}
        with patch.object(AdapterTestSettings, "controller_class", return_value=self.failing()):
            await h.adapter._reconcile()

        assert h.adapter.start_errors == {"m1": "RuntimeError: boom"}
        assert "m1" not in h.adapter.controllers

    @pytest.mark.asyncio
    async def test_successful_start_clears_the_start_error(self, h: Harness):
        h.config.adapter.control.controllers = {"m1": AdapterTestSettings()}
        with patch.object(AdapterTestSettings, "controller_class", return_value=self.failing()):
            await h.adapter._reconcile()

        await h.adapter._reconcile()  # retried with the working controller

        assert h.adapter.start_errors == {}
        assert "m1" in h.adapter.controllers

    @pytest.mark.asyncio
    async def test_removed_device_drops_its_start_error(self, h: Harness):
        h.config.adapter.control.controllers = {"m1": AdapterTestSettings()}
        with patch.object(AdapterTestSettings, "controller_class", return_value=self.failing()):
            await h.adapter._reconcile()

        h.config.adapter.control.controllers = {}
        await h.adapter._reconcile()

        assert h.adapter.start_errors == {}

    @pytest.mark.asyncio
    async def test_clear_drops_the_start_errors(self, h: Harness):
        h.adapter.start_errors = {"m1": "RuntimeError: boom"}
        await h.adapter.clear()
        assert h.adapter.start_errors == {}


# ---------------------------------------------------------------------------
# Stage dispatch
# ---------------------------------------------------------------------------


class TestUpdateData:
    @pytest.mark.asyncio
    async def test_reconcile_runs_at_every_stage(self, h: Harness):
        h.ems.stage.return_value = EnergyManagementStage.OPTIMIZATION

        with patch.object(type(h.adapter), "_reconcile", AsyncMock()) as reconcile, \
             patch.object(type(h.adapter), "_acquire_measurements", AsyncMock()) as acquire, \
             patch.object(type(h.adapter), "_dispatch_plan", MagicMock()) as dispatch:
            await h.adapter._update_data()

        reconcile.assert_awaited_once()
        acquire.assert_not_awaited()
        dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_data_acquisition_acquires_measurements(self, h: Harness):
        h.ems.stage.return_value = EnergyManagementStage.DATA_ACQUISITION

        with patch.object(type(h.adapter), "_reconcile", AsyncMock()) as reconcile, \
             patch.object(type(h.adapter), "_acquire_measurements", AsyncMock()) as acquire, \
             patch.object(type(h.adapter), "_dispatch_plan", MagicMock()) as dispatch:
            await h.adapter._update_data()

        reconcile.assert_awaited_once()
        acquire.assert_awaited_once()
        dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_control_dispatch_dispatches_plan(self, h: Harness):
        h.ems.stage.return_value = EnergyManagementStage.CONTROL_DISPATCH

        with patch.object(type(h.adapter), "_reconcile", AsyncMock()) as reconcile, \
             patch.object(type(h.adapter), "_acquire_measurements", AsyncMock()) as acquire, \
             patch.object(type(h.adapter), "_dispatch_plan", MagicMock()) as dispatch:
            await h.adapter._update_data()

        reconcile.assert_awaited_once()
        acquire.assert_not_awaited()
        dispatch.assert_called_once()


# ---------------------------------------------------------------------------
# _acquire_measurements / _dispatch_plan
# ---------------------------------------------------------------------------


class TestAcquireMeasurements:
    """The adapter writes the readings mapped in adapter.control into the measurement store."""

    @pytest.fixture
    def measurement(self, h: Harness) -> Iterator[MagicMock]:
        measurement = MagicMock(name="measurement")
        measurement.update_value = AsyncMock()
        measurement.record_keys_writable = [
            "meter1_grid_import_kwh",
            "meter1_grid_export_kwh",
            "gone_grid_import_kwh",
            "battery1-soc-factor",
        ]
        with patch.object(
            type(h.adapter), "measurement", new_callable=PropertyMock, return_value=measurement
        ):
            yield measurement

    @staticmethod
    def _configure(h: Harness, **mapping: Any) -> None:
        h.config.adapter.control = ControlAdapterCommonSettings.model_validate(
            {
                "controllers": {
                    "meter1": {
                        "controller_type": "sma:energy-meter",
                        "serial": 1,
                        "measurement_max_age_s": 60,
                    },
                    "inverter1": {
                        "controller_type": "sma:sunny-tripower-smart-energy",
                        "host": "h",
                        "battery_id": "battery1",
                        "grid_power_key": "meter1:grid_net_w",
                    },
                },
                **mapping,
            }
        )

    @pytest.mark.asyncio
    async def test_writes_role_and_device_readings_at_the_current_time(
        self, h: Harness, measurement: MagicMock
    ):
        self._configure(
            h,
            grid_import_emr_data_keys=["meter1:grid_import_kwh"],
            device_measurement_data_keys={"battery1-soc-factor": "inverter1:bat_soc_factor"},
        )
        h.adapter.shared_data.publish_sync(
            {"meter1:grid_import_kwh": 1234.5, "inverter1:bat_soc_factor": 0.55}
        )

        before = to_datetime()
        await h.adapter._acquire_measurements()
        after = to_datetime()

        measurement.update_value.assert_awaited_once()
        assert measurement.update_value.await_args is not None
        date, values = measurement.update_value.await_args.args
        assert before <= date <= after
        assert values == {"meter1_grid_import_kwh": 1234.5, "battery1-soc-factor": 0.55}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_value", [True, "12", math.nan, math.inf, -math.inf])
    async def test_skips_invalid_values(self, h: Harness, measurement: MagicMock, bad_value: Any):
        self._configure(h, grid_import_emr_data_keys=["meter1:grid_import_kwh"])
        h.adapter.shared_data.publish_sync({"meter1:grid_import_kwh": bad_value})

        await h.adapter._acquire_measurements()

        measurement.update_value.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_missing_and_keeps_the_others(self, h: Harness, measurement: MagicMock):
        self._configure(
            h, grid_import_emr_data_keys=["meter1:grid_import_kwh", "meter1:grid_export_kwh"]
        )
        h.adapter.shared_data.publish_sync({"meter1:grid_export_kwh": 10.0})

        await h.adapter._acquire_measurements()

        assert measurement.update_value.await_args is not None
        assert measurement.update_value.await_args.args[1] == {"meter1_grid_export_kwh": 10.0}

    @pytest.mark.asyncio
    async def test_skips_stale_values(self, h: Harness, measurement: MagicMock):
        self._configure(h, grid_import_emr_data_keys=["meter1:grid_import_kwh"])
        h.adapter.shared_data.publish_sync({"meter1:grid_import_kwh": 1.0})
        original = h.adapter.shared_data.get_many
        with patch.object(
            h.adapter.shared_data,
            "get_many",
            lambda keys: {k: (v, 3600.0) for k, (v, _) in original(keys).items()},
        ):
            await h.adapter._acquire_measurements()

        measurement.update_value.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_a_key_of_a_controller_that_is_not_configured(
        self, h: Harness, measurement: MagicMock
    ):
        self._configure(h, grid_import_emr_data_keys=["gone:grid_import_kwh"])
        h.adapter.shared_data.publish_sync({"gone:grid_import_kwh": 1.0})

        await h.adapter._acquire_measurements()

        measurement.update_value.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nothing_mapped_writes_nothing(self, h: Harness, measurement: MagicMock):
        self._configure(h)

        await h.adapter._acquire_measurements()

        measurement.update_value.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_key_the_store_does_not_take_does_not_lose_the_others(
        self, h: Harness, measurement: MagicMock
    ):
        # battery2 was removed: its SoC key is no measurement key any more.
        self._configure(
            h,
            grid_import_emr_data_keys=["meter1:grid_import_kwh"],
            device_measurement_data_keys={"battery2-soc-factor": "inverter1:bat_soc_factor"},
        )
        h.adapter.shared_data.publish_sync(
            {"meter1:grid_import_kwh": 7.0, "inverter1:bat_soc_factor": 0.5}
        )

        await h.adapter._acquire_measurements()

        assert measurement.update_value.await_args is not None
        assert measurement.update_value.await_args.args[1] == {"meter1_grid_import_kwh": 7.0}

    @pytest.mark.asyncio
    async def test_a_failed_write_is_logged_not_raised(self, h: Harness, measurement: MagicMock):
        self._configure(h, grid_import_emr_data_keys=["meter1:grid_import_kwh"])
        h.adapter.shared_data.publish_sync({"meter1:grid_import_kwh": 1.0})
        measurement.update_value.side_effect = RuntimeError("store down")

        await h.adapter._acquire_measurements()  # does not raise


class TestDispatchPlan:
    def test_passes_plan_to_each_controller(self, h: Harness):
        plan = object()
        h.ems.plan.return_value = plan
        c1, c2 = _make_controller("m1"), _make_controller("m2")
        h.adapter.controllers = {"m1": c1, "m2": c2}

        with patch.object(c1, "update", MagicMock()) as u1, \
             patch.object(c2, "update", MagicMock()) as u2:
            h.adapter._dispatch_plan()

        u1.assert_called_once_with(plan)
        u2.assert_called_once_with(plan)

    def test_one_failure_does_not_block_others(self, h: Harness):
        h.ems.plan.return_value = object()
        bad = _make_controller("bad")
        good = _make_controller("good")
        h.adapter.controllers = {"bad": bad, "good": good}

        with patch.object(bad, "update", MagicMock(side_effect=RuntimeError("boom"))), \
             patch.object(good, "update", MagicMock()) as u_good:
            h.adapter._dispatch_plan()

        u_good.assert_called_once()


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    @pytest.mark.asyncio
    async def test_stops_all_controllers_and_empties_shared_data(self, h: Harness):
        h.config.adapter.control.controllers = {
            "m1": AdapterTestSettings(),
            "m2": AdapterTestSettings(poll=2.0),
        }
        await h.adapter._reconcile()
        h.adapter.shared_data.publish_sync({"m1:grid_w": 1, "m2:grid_w": 2})

        await h.adapter.clear()

        assert h.adapter.controllers == {}
        assert h.adapter.shared_data.snapshot() == {}
        assert AdapterTestController.stops == 2

    @pytest.mark.asyncio
    async def test_clear_keeps_diagnostic_logs(self, h: Harness):
        # ``clear`` intentionally leaves ``diagnostic_logs`` alone so that
        # post-mortem access to a stopped controller's log is possible until
        # the device is removed from the configuration (see _reconcile).
        h.config.adapter.control.controllers = {"m1": AdapterTestSettings()}
        await h.adapter._reconcile()

        await h.adapter.clear()

        assert "m1" in h.adapter.diagnostic_logs

    @pytest.mark.asyncio
    async def test_clear_is_idempotent(self, h: Harness):
        await h.adapter.clear()
        await h.adapter.clear()
        assert h.adapter.controllers == {}
        assert h.adapter.shared_data.snapshot() == {}


# ---------------------------------------------------------------------------
# device_control_clear
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_device_control_clear_uses_module_singleton():
    # ``device_control_clear`` constructs ControlAdapter() and clears it.
    # That only affects our adapter if AdapterProvider returns the same instance
    # for every construction; otherwise the call clears a fresh, empty instance
    # and the fixture state is untouched.
    a1 = ControlAdapter()
    a2 = ControlAdapter()
    if a1 is not a2:
        pytest.skip("ControlAdapter is not a singleton; device_control_clear cannot be tested this way")

    saved_controllers = a1.controllers
    saved_logs = a1.diagnostic_logs
    saved_shared = a1.shared_data
    try:
        a1.controllers = {}
        a1.diagnostic_logs = {}
        a1.shared_data = ControlSharedDataStore()
        a1.shared_data.publish_sync({"x": 1})

        await device_control_clear()

        assert a1.shared_data.snapshot() == {}
        assert a1.controllers == {}
    finally:
        a1.controllers = saved_controllers
        a1.diagnostic_logs = saved_logs
        a1.shared_data = saved_shared
