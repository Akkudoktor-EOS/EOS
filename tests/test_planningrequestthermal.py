"""The planning request builds a thermal device for a heat source with a zone.

A configuration with a zone (calibrated building model, comfort band, presence area), a heat
pump source and measured room, outdoor and power histories gives a thermal device with plausible
comfort bands (lowered while the presence forecast says nobody is home), an initial state from
the state estimate, the operating state before slot 0 and the settings of the source. Missing
inputs leave the heat pump out of the planning with a warning that names them; without a thermal
configuration there is no thermal device.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd
import pytest
import test_searchscenarios as sc
from test_requestfixtures import (
    BATTERY,
    resolve_task,
)

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.measurement.quality import MeasurementSample
from akkudoktoreos.optimization.kernel import Plan, simulate
from akkudoktoreos.optimization.request import PlanningRequest
from akkudoktoreos.optimization.request.devices import DeviceResolver
from akkudoktoreos.optimization.request.thermal import ThermalResolver
from akkudoktoreos.prediction.presence import PresenceEstimate
from akkudoktoreos.utils.datetimeutil import to_datetime

NOW = to_datetime("2026-09-16T10:00:00+02:00", in_timezone="Europe/Berlin")
HISTORY_MINUTES = 14 * 60


class FakeThermalMeasurement:
    """Raw histories of the thermal channels; unknown keys are an error."""

    def __init__(self, series: dict[str, Any]) -> None:
        self.series = series

    async def get_samples(
        self, key: str, start: datetime, end: datetime, **_: Any
    ) -> list[MeasurementSample]:
        """Return the stored samples of a channel within the range."""
        if key not in self.series:
            raise ValueError(f"No measurement channel definition for {key!r}.")
        return [
            MeasurementSample(date_time=stamp, key=key, value=value)
            for stamp, value in self.series[key]
            if start <= stamp < end
        ]


class FakePresence:
    """A presence provider: nobody is home from 12:00 to 17:00 local time."""

    received: list[Any] = []

    async def forecast_zone(
        self, zone: str, *, now: datetime, starts: list[datetime], **kwargs: Any
    ) -> list[PresenceEstimate]:
        """Return one estimate per start; remember the absences of the request."""
        FakePresence.received.append(list(kwargs.get("absences", [])))
        if any(start < now for start in starts):  # like the real provider
            raise ValueError("Presence forecast requires prospective intervals")
        estimates = []
        for start in starts:
            local = start.astimezone(NOW.timezone)
            away = 12 <= local.hour < 17
            estimates.append(
                PresenceEstimate(
                    date_time=start,
                    probability=0.02 if away else 0.95,
                    require_comfort=not away,
                    source="history",
                )
            )
        return estimates


class FakePrediction:
    """Weather forecast with a constant outdoor temperature; no humidity."""

    def __init__(self, outdoor_c: float = 4.0, present: bool = True) -> None:
        self.outdoor_c = outdoor_c
        self.present = present

    async def update_data(self) -> None:
        """Nothing to update."""

    async def key_to_raw_series(self, key: str, dropna: bool = False) -> pd.Series:
        """Return the hourly series of a key from local midnight for three days."""
        if key != "weather_temp_air" or not self.present:
            raise KeyError(key)
        origin = NOW.start_of("day")
        index = [origin + timedelta(hours=h) for h in range(72)]
        return pd.Series([self.outdoor_c] * 72, index=pd.DatetimeIndex(index))

    def provider_by_id(self, provider_id: str) -> FakePresence:
        """Return the presence provider."""
        return FakePresence()


def history(
    value: float, *, minutes: int = HISTORY_MINUTES, newest: Optional[float] = None
) -> list[tuple[datetime, float]]:
    """A measured value every minute up to now; the last ``minutes`` of it, with a new value."""
    rows: list[tuple[datetime, float]] = []
    for index in range(minutes + 1):
        stamp = NOW - timedelta(minutes=minutes - index)
        rows.append((stamp, value if newest is None or index < minutes else newest))
    return rows


def power_history(running_minutes: int) -> list[tuple[datetime, float]]:
    """Heat source power: off before, running at 1200 W during the last minutes."""
    rows: list[tuple[datetime, float]] = []
    for index in range(HISTORY_MINUTES + 1):
        stamp = NOW - timedelta(minutes=HISTORY_MINUTES - index)
        rows.append((stamp, 1200.0 if index > HISTORY_MINUTES - running_minutes else 5.0))
    return rows


def heat_history(running_minutes: int) -> list[tuple[datetime, float]]:
    """Delivered heat: none before, 3.2 kW during the last minutes."""
    rows: list[tuple[datetime, float]] = []
    for index in range(HISTORY_MINUTES + 1):
        stamp = NOW - timedelta(minutes=HISTORY_MINUTES - index)
        rows.append((stamp, 3200.0 if index > HISTORY_MINUTES - running_minutes else 0.0))
    return rows


THERMAL: dict[str, Any] = {
    "thermal": {
        "measurement_max_age_seconds": 300,
        "zones": {
            "living": {
                "enabled": True,
                "room_temperature_measurement_key": "living-temp",
                "outdoor_temperature_measurement_key": "outdoor-temp",
                "model_reference": "calibrated",
                "model_parameters": sc.HEAT_BUILDING.model_dump(mode="json"),
                "comfort": {
                    "min_temperature_c": 20.0,
                    "target_temperature_c": 21.0,
                    "max_temperature_c": 23.0,
                },
                "unoccupied_comfort": {
                    "min_temperature_c": 17.0,
                    "target_temperature_c": 17.5,
                    "max_temperature_c": 23.0,
                },
                "presence_area_id": "living",
            }
        },
    },
    "presence": {"provider": "PresenceHistory", "zones": {"living": {"measurement_key": "away"}}},
    "measurement": {"channels": {"away": {"quantity": "presence", "unit": "1"}}},
}
SOURCE: dict[str, Any] = {
    "kind": "heat_pump",
    "thermal_power_measurement_key": "hp-heat",
    "enabled": True,
    "max_electric_power_w": 3000,
    "min_electric_power_w": 400,
    "zone_ids": ["living"],
    "electric_power_measurement_key": "hp-power",
    "heat_pump": {
        "custom_profile": sc.HEAT_PROFILE.model_dump(mode="json"),
        "delivery_temperature_c": 20.0,
        "electrical": sc.HEAT_ELECTRICAL.model_dump(mode="json"),
    },
}
DEVICES: dict[str, Any] = {
    "batteries": {"bat1": BATTERY},
    "inverters": {"inv1": {"battery_id": "bat1", "profile_adjustment": {"nominal_power_w": 8000}}},
    "heat_sources": {"hp1": SOURCE},
}


@pytest.fixture(name="thermal_world")
def thermal_world_fixture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace measurement and prediction of the thermal request; adjust ``series`` per test."""
    series = {
        "living-temp": history(20.8),
        "outdoor-temp": history(4.0),
        "hp-power": power_history(100),
        "hp-heat": heat_history(100),
    }
    measurement = FakeThermalMeasurement(series)
    prediction = FakePrediction()
    monkeypatch.setattr(ThermalResolver, "measurement", measurement)
    monkeypatch.setattr(ThermalResolver, "prediction", prediction)
    # The electrical devices: no measurements other than the state of charge request.
    monkeypatch.setattr(DeviceResolver, "measurement", _EmptyMeasurement())
    monkeypatch.setattr(PlanningRequest, "prediction", prediction)
    return {"series": series, "prediction": prediction}


class _EmptyMeasurement:
    async def key_to_lists(self, **_: Any) -> tuple[list[Any], list[Any]]:
        raise KeyError("no measurement")


def with_zone(**changes: Any) -> dict[str, Any]:
    """The thermal configuration with changes to the zone."""
    zone = {**THERMAL["thermal"]["zones"]["living"], **changes}
    return {**THERMAL, "thermal": {**THERMAL["thermal"], "zones": {"living": zone}}}


@pytest.mark.asyncio
async def test_a_heat_pump_with_a_zone_becomes_a_thermal_device(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
    assert len(task.thermal) == 1
    info = task.thermal[0]
    unit = info.params
    assert (info.source_id, info.zone_id) == ("hp1", "living")
    assert task.problem.thermal == (unit,)
    assert unit.slots == task.problem.slots == 24
    # The initial state: the room as measured (within the projection of the heat), mass near it.
    room, mass = (float(v) for v in unit.initial_temperature_c)
    assert 20.5 <= room <= 21.3 and 20.0 <= mass <= 21.5
    # The weather forecast; the heat pump ran before now (the run share before slot 0 is 1).
    np.testing.assert_allclose(unit.outdoor_c, 4.0)
    assert unit.initial_running is True
    # Comfort bands: occupied 20/21/23, away 17/17.5/23 from 12:00 to 17:00, hourly slots.
    bands = info.bands
    assert len(bands) == 24
    for slot, band in enumerate(bands):
        hour = (NOW.hour + slot) % 24
        away = 12 <= hour < 17
        assert band.min_temperature_c == (17.0 if away else 20.0), (slot, hour)
        np.testing.assert_allclose(
            unit.comfort_min_c[slot], 20.0
        )  # the occupied minimum, weighted by presence
        np.testing.assert_allclose(unit.comfort_max_c[slot], band.max_temperature_c)
    # The protection minimum never exceeds the lowest band and defaults to 14 degC.
    assert unit.protection_min_c is not None
    assert float(unit.protection_min_c.max()) <= 14.0 + 1e-9
    # The prices of the source reach the objective.
    assert task.objective.thermal.comfort_eur_per_kh == 100.0
    assert task.objective.thermal.protection_eur_per_kh == 1000.0
    assert simulate(task.problem, Plan.idle(task.problem)).thermal is not None


@pytest.mark.asyncio
async def test_the_presence_probability_weighs_the_shortfall_below_the_occupied_minimum(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
    info = task.thermal[0]
    unit = info.params
    assert unit.tiered
    # the occupied minimum always applies; the executor still gets the band of the threshold
    np.testing.assert_allclose(unit.comfort_min_c, 20.0)
    assert {band.min_temperature_c for band in info.bands} == {17.0, 20.0}
    comfort_price = task.objective.thermal.comfort_eur_per_kh
    for slot in range(unit.slots):
        away = 12 <= (NOW.hour + slot) % 24 < 17
        probability = 0.02 if away else 0.95
        np.testing.assert_allclose(
            unit.under_weight[slot],
            probability * np.array([0.5, 2.0, comfort_price]) / comfort_price,
        )


@pytest.mark.asyncio
async def test_a_run_inside_a_quarter_hour_plans_the_heat_pump_with_the_presence_forecast(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    # The real run moment is not rounded: the presence forecast takes no quarter hour that has
    # begun, so the quarter of the run takes the estimate of the first one ahead.
    task = await resolve_task(
        config_eos, DEVICES, {"bat1": 50}, extra=THERMAL, start="2026-09-16T10:03:30+02:00"
    )
    assert len(task.thermal) == 1 and not task.warnings
    bands = task.thermal[0].bands
    assert len(bands) == 24
    for slot, band in enumerate(bands):
        away = 12 <= (NOW.hour + slot) % 24 < 17
        assert band.min_temperature_c == (17.0 if away else 20.0), slot


@pytest.mark.asyncio
async def test_the_source_settings_reach_the_device(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    source = {
        **SOURCE,
        "comfort_penalty_eur_per_kh": 40.0,
        "protection_penalty_eur_per_kh": 400.0,
    }
    devices = {**DEVICES, "heat_sources": {"hp1": source}}
    thermal_world["series"]["hp-power"] = power_history(0)  # off for the whole history
    thermal_world["series"]["hp-heat"] = heat_history(0)
    task = await resolve_task(config_eos, devices, {"bat1": 50}, extra=THERMAL)
    unit = task.thermal[0].params
    assert unit.initial_running is False
    assert task.objective.thermal.comfort_eur_per_kh == 40.0
    assert task.objective.thermal.protection_eur_per_kh == 400.0


@pytest.mark.asyncio
async def test_without_presence_the_zone_keeps_its_comfort_band(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    extra = with_zone(presence_area_id=None)
    extra["presence"] = {}
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=extra)
    assert {(b.min_temperature_c, b.max_temperature_c) for b in task.thermal[0].bands} == {
        (20.0, 23.0)
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "age_minutes", "occupied"),
    [
        (22.0, 5, (21.0, 22.0, 23.0)),  # the user raised the target: the band moves with it
        (19.5, 5, (18.5, 19.5, 23.0)),
        (21.0, 5, (20.0, 21.0, 23.0)),
        (30.0, 5, (20.0, 21.0, 23.0)),  # implausible: the configured band stays
        (22.0, 24 * 60, (20.0, 21.0, 23.0)),  # stale: the configured band stays
    ],
)
async def test_the_room_target_of_the_user_moves_the_occupied_band(
    config_eos: ConfigEOS,
    thermal_world: dict[str, Any],
    value: float,
    age_minutes: int,
    occupied: tuple[float, float, float],
) -> None:
    thermal_world["series"]["target"] = [(NOW - timedelta(minutes=age_minutes), value)]
    extra = with_zone(presence_area_id=None, target_measurement_key="target")
    extra["presence"] = {}
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=extra)
    band = task.thermal[0].bands[0]
    assert (band.min_temperature_c, band.target_temperature_c, band.max_temperature_c) == occupied


@pytest.mark.asyncio
async def test_a_missing_room_target_channel_never_stops_the_planning(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    extra = with_zone(presence_area_id=None, target_measurement_key="no-such-channel")
    extra["presence"] = {}
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=extra)
    assert len(task.thermal) == 1
    assert {(b.min_temperature_c, b.max_temperature_c) for b in task.thermal[0].bands} == {
        (20.0, 23.0)
    }


@pytest.mark.asyncio
async def test_without_a_thermal_configuration_there_is_no_thermal_device(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    devices = {k: DEVICES[k] for k in ("batteries", "inverters")}
    task = await resolve_task(config_eos, devices, {"bat1": 50})
    assert task.thermal == () and task.problem.thermal == ()
    # A configured but disabled source is not planned either.
    disabled = {**DEVICES, "heat_sources": {"hp1": {**SOURCE, "enabled": False}}}
    task = await resolve_task(config_eos, disabled, {"bat1": 50}, extra=THERMAL)
    assert task.thermal == ()


def no_model(world: dict[str, Any]) -> dict[str, Any]:
    return with_zone(model_parameters=None)


def no_outdoor_forecast(world: dict[str, Any]) -> dict[str, Any]:
    world["prediction"].present = False
    return THERMAL


def no_room_history(world: dict[str, Any]) -> dict[str, Any]:
    world["series"]["living-temp"] = []
    return THERMAL


def stale_power(world: dict[str, Any]) -> dict[str, Any]:
    world["series"]["hp-power"] = [(NOW - timedelta(hours=1), 5.0)] + power_history(0)[:1]
    return THERMAL


def no_power_channel(world: dict[str, Any]) -> dict[str, Any]:
    world["series"].pop("hp-power")
    return THERMAL


def short_history(world: dict[str, Any]) -> dict[str, Any]:
    world["series"]["living-temp"] = history(20.8, minutes=120)
    return THERMAL


MISSING_INPUTS: dict[str, tuple[Any, str]] = {
    "no_model": (no_model, "needs calibrated building"),
    "no_outdoor_forecast": (no_outdoor_forecast, "lacks the outdoor temperature"),
    "no_room_history": (no_room_history, "room temperature of living"),
    "stale_power": (stale_power, "electric power of hp1"),
    "no_power_channel": (no_power_channel, "electric power of heat source hp1"),
    "short_history": (short_history, "cannot be estimated"),
}


def assert_not_planned(task: Any, message: str, device: str = "hp1") -> None:
    """The task has no thermal device and one warning that names the device and the cause."""
    assert task.thermal == () and task.problem.thermal == ()
    assert len(task.battery_ids) == 1  # the electrical devices are still planned
    assert len(task.warnings) == 1
    warning = task.warnings[0]
    assert warning.code == "thermal_not_planned"
    assert warning.devices == [device]
    assert re.search(message, warning.message), warning.message


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(MISSING_INPUTS))
async def test_missing_inputs_leave_the_heat_pump_out_with_a_warning_that_names_them(
    name: str, config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    change, message = MISSING_INPUTS[name]
    extra = change(thermal_world)
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=extra)
    assert_not_planned(task, message)


@pytest.mark.asyncio
async def test_the_heat_is_inferred_from_the_power_where_no_heat_is_measured(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    plain = {**SOURCE, "thermal_power_measurement_key": None}
    # With a modelled start energy a power snapshot cannot be turned into heat: the heat pump
    # is left out with a warning that names the cause.
    devices = {**DEVICES, "heat_sources": {"hp1": plain}}
    assert_not_planned(
        await resolve_task(config_eos, devices, {"bat1": 50}, extra=THERMAL),
        "bind a delivered heat measurement",
    )
    # Without it the heat follows from the coefficient of performance.
    electrical = {
        "standby_power_w": 5.0,
        "running_auxiliary_power_w": 20.0,
        "startup_energy_wh": 0.0,
    }
    plain["heat_pump"] = {**SOURCE["heat_pump"], "electrical": electrical}
    task = await resolve_task(config_eos, devices, {"bat1": 50}, extra=THERMAL)
    assert len(task.thermal) == 1


@pytest.mark.asyncio
async def test_unsupported_sources_are_left_out_with_a_message(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    room_heater = {
        "kind": "electric_room",
        "enabled": True,
        "max_electric_power_w": 2000,
        "zone_ids": ["living"],
    }
    devices = {**DEVICES, "heat_sources": {"hp1": room_heater}}
    assert_not_planned(
        await resolve_task(config_eos, devices, {"bat1": 50}, extra=THERMAL),
        "only heat pump sources",
    )
    no_delivery = {
        **SOURCE,
        "heat_pump": {
            "custom_profile": sc.HEAT_PROFILE.model_dump(mode="json"),
            "electrical": sc.HEAT_ELECTRICAL.model_dump(mode="json"),
        },
    }
    devices = {**DEVICES, "heat_sources": {"hp1": no_delivery}}
    assert_not_planned(
        await resolve_task(config_eos, devices, {"bat1": 50}, extra=THERMAL),
        "delivery rating temperature",
    )
    assert_not_planned(
        await resolve_task(
            config_eos,
            DEVICES,
            {"bat1": 50},
            extra={
                **THERMAL,
                "thermal": {
                    **THERMAL["thermal"],
                    "zones": {
                        "living": {
                            **THERMAL["thermal"]["zones"]["living"],
                            "enabled": False,
                        }
                    },
                },
            },
        ),
        "not configured or not enabled",
    )


_ = (datetime, timezone)


@pytest.mark.asyncio
async def test_blocks_and_reported_absences_of_the_request_reach_the_thermal_device(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    from akkudoktoreos.optimization.request import BlockedInterval
    from akkudoktoreos.prediction.presence import PresenceAbsence

    block = BlockedInterval(start=NOW + timedelta(hours=3), end=NOW + timedelta(hours=5))
    absence = PresenceAbsence(start=NOW + timedelta(hours=1), end=NOW + timedelta(hours=9))
    FakePresence.received = []
    config_eos.merge_settings_from_dict(THERMAL)
    task = await resolve_task(
        config_eos,
        DEVICES,
        {"bat1": 50},
        extra=THERMAL,
        request_extra={"heat_source_blocks": {"hp1": [block]}, "absences": [absence]},
    )
    blocked = task.thermal[0].params.blocked
    assert blocked is not None
    # Hourly slots from 10:00: 13:00-15:00 are slots 3 and 4.
    assert [slot for slot in range(24) if blocked[slot]] == [3, 4]
    assert FakePresence.received == [[absence]]
    with pytest.raises(ValueError, match="unknown or disabled heat sources"):
        await resolve_task(
            config_eos,
            DEVICES,
            {"bat1": 50},
            extra=THERMAL,
            request_extra={"heat_source_blocks": {"ghost": [block]}},
        )


@pytest.mark.asyncio
async def test_the_planned_heat_never_draws_more_than_the_source_limit(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    from akkudoktoreos.devices.models.thermal import base_electric_w
    from akkudoktoreos.optimization.planner import run_planning

    limit_w = 900.0
    # A cold house: the heat pump has to run at full power for hours.
    thermal_world["series"]["living-temp"] = history(17.0)
    free = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
    free_heat = run_planning(free).result.thermal.heat_w[0]  # type: ignore[union-attr]
    free_unit = free.thermal[0].params
    assert float(free_unit.operating_electric_w.max()) + 25.0 > limit_w  # the limit binds
    source = {**SOURCE, "max_electric_power_w": limit_w, "min_electric_power_w": 300}
    devices = {**DEVICES, "heat_sources": {"hp1": source}}
    task = await resolve_task(config_eos, devices, {"bat1": 50}, extra=THERMAL)
    unit = task.thermal[0].params
    outcome = run_planning(task)
    assert outcome.solution.warnings == []
    heat = outcome.result.thermal.heat_w[0]  # type: ignore[union-attr]
    assert float(heat.max()) > 0.0  # it still heats, with less than without the limit
    assert float(heat.max()) < float(free_heat.max())
    assert unit.fixed_power_w is not None
    for slot, value in enumerate(heat):
        if value <= 0.0:
            continue
        electric = (
            base_electric_w(
                unit.operating_heat_w,
                unit.operating_electric_w,
                unit.operating_count,
                slot,
                float(value),
            )
            + float(unit.fixed_power_w[slot])
            + unit.running_auxiliary_power_w
        )
        assert 0.0 <= electric <= limit_w + 1e-6, (slot, electric)


def defrost_in_the_last_minutes(minutes: int, heat_w: float) -> list[tuple[datetime, float]]:
    """Delivered heat: 3.2 kW, but ``heat_w`` (heat taken from the room) in the last minutes."""
    rows = heat_history(100)
    return [
        (stamp, heat_w if NOW - stamp < timedelta(minutes=minutes) and stamp < NOW else value)
        for stamp, value in rows
    ]


@pytest.mark.asyncio
async def test_a_defrost_that_takes_heat_from_the_room_does_not_stop_the_heat_plan(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    """A defrost (negative delivered heat) since the newest room value is heat taken from the room.

    The room sensor reports every few minutes, so the state is projected over the heat measured
    since.
    """
    series = thermal_world["series"]
    # The newest room value is four minutes old; the heat pump defrosted in these minutes.
    series["living-temp"] = [row for row in history(20.8) if row[0] <= NOW - timedelta(minutes=4)]
    rooms = {}
    for heat_w in (3200.0, -220.0):
        series["hp-heat"] = defrost_in_the_last_minutes(4, heat_w)
        task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
        assert [w.message for w in task.warnings] == []
        rooms[heat_w] = float(task.thermal[0].params.initial_temperature_c[0])
    # The heat taken from the room is not thrown away: the room is estimated colder.
    assert rooms[-220.0] < rooms[3200.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [MemoryError("Allocation failed"), RecursionError()])
async def test_a_failure_of_the_process_is_not_reported_as_a_missing_input(
    error: BaseException,
    config_eos: ConfigEOS,
    thermal_world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(self: ThermalResolver, source: Any) -> Any:
        raise error

    monkeypatch.setattr(ThermalResolver, "_device", fail)
    with pytest.raises(type(error)):
        await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("no heat"), KeyError("weather"), TypeError("odd")])
async def test_an_error_of_the_input_still_leaves_the_heat_pump_out_with_a_warning(
    error: Exception,
    config_eos: ConfigEOS,
    thermal_world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(self: ThermalResolver, source: Any) -> Any:
        raise error

    monkeypatch.setattr(ThermalResolver, "_device", fail)
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
    assert_not_planned(task, str(error) or type(error).__name__)


@pytest.mark.asyncio
async def test_a_tight_limit_keeps_every_slot_within_it_with_the_start_energy_counted(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    from akkudoktoreos.optimization.planner import run_planning

    # The lowest operating point runs within 380 W (360 W with standby and auxiliaries), the
    # energy of a full start (30 Wh in the hour of a slot = 30 W) does not fit on top of it:
    # a start is a ramp of the run share, so the heat pump runs, but never above the limit.
    source = {**SOURCE, "max_electric_power_w": 380.0, "min_electric_power_w": 300}
    devices = {**DEVICES, "heat_sources": {"hp1": source}}
    task = await resolve_task(config_eos, devices, {"bat1": 50}, extra=THERMAL)
    assert len(task.thermal) == 1
    assert not task.warnings
    outcome = run_planning(task)
    thermal = outcome.result.thermal
    assert thermal is not None
    power = thermal.electric_wh[0] / task.thermal[0].params.slot_hours
    assert float(power.max()) <= 380.0 + 1e-6

    roomy = {**SOURCE, "max_electric_power_w": 3000.0}
    task = await resolve_task(
        config_eos, {**DEVICES, "heat_sources": {"hp1": roomy}}, {"bat1": 50}, extra=THERMAL
    )
    assert not task.warnings


@pytest.mark.asyncio
async def test_a_negative_heat_beyond_the_size_of_the_heat_pump_is_a_sensor_error(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    """A defrost takes at most the heat pump's own size from the room; more is rejected."""
    from akkudoktoreos.optimization.request.thermal import THERMAL_NOT_PLANNED, max_heat_w

    size_w = max_heat_w(sc.HEAT_PROFILE)
    assert size_w == 7000.0  # the largest heat of the performance map
    series = thermal_world["series"]
    series["living-temp"] = [row for row in history(20.8) if row[0] <= NOW - timedelta(minutes=4)]
    # Inside the size: the defrost counts and the heat pump is planned.
    series["hp-heat"] = defrost_in_the_last_minutes(4, -size_w)
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
    assert [w.message for w in task.warnings] == []
    assert len(task.thermal) == 1
    # Beyond it: the same rejection as a non-finite value, reported, not planned.
    series["hp-heat"] = defrost_in_the_last_minutes(4, -size_w - 1.0)
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
    assert task.thermal == ()
    assert [w.code for w in task.warnings] == [THERMAL_NOT_PLANNED]
    assert "below the plausible minimum" in task.warnings[0].message
    # The latest value alone is checked as well (no room value newer than the heat).
    series["living-temp"] = history(20.8)
    series["hp-heat"] = [
        (stamp, -size_w - 1.0 if stamp == NOW else value) for stamp, value in heat_history(100)
    ]
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
    assert [w.code for w in task.warnings] == [THERMAL_NOT_PLANNED]
    assert "below the plausible minimum" in task.warnings[0].message


@pytest.mark.asyncio
async def test_a_failed_heat_plan_is_reported_in_the_solution(
    config_eos: ConfigEOS, thermal_world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the solver fails the other devices are planned and the solution carries a warning."""
    from akkudoktoreos.optimization.planner import run_planning
    from akkudoktoreos.optimization.search import heatplan

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("solver failed")

    monkeypatch.setattr(heatplan, "linprog", explode)
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
    outcome = run_planning(task)
    codes = [(w.code, w.devices) for w in outcome.solution.warnings]
    assert ("thermal_heat_plan_failed", ["hp1"]) in codes


@pytest.mark.asyncio
async def test_a_heat_plan_that_violates_the_allowed_values_never_cancels_the_run(
    config_eos: ConfigEOS, thermal_world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A violating heat plan is replaced by a valid one and reported; the run goes on."""
    from dataclasses import replace

    from akkudoktoreos.optimization.planner import run_planning
    from akkudoktoreos.optimization.search import DeviceSearch, check_plan

    original = DeviceSearch.solve

    def spoil(self: DeviceSearch, *args: Any, **kwargs: Any) -> Any:
        found = original(self, *args, **kwargs)
        plan = found.plan.copy()
        plan.thermal_heat_w[0][3] = -500.0  # heat below zero: a violation
        return replace(
            found,
            plan=plan,
            violations=check_plan(self.problem, plan, self.space, self.settings),
        )

    monkeypatch.setattr(DeviceSearch, "solve", spoil)
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
    outcome = run_planning(task)
    heat = outcome.result.thermal.heat_w[0]  # type: ignore[union-attr]
    assert np.all(np.asarray(heat) >= 0.0)
    held = [w for w in outcome.solution.warnings if w.code == "thermal_heat_plan_failed"]
    assert held and held[0].devices == ["hp1"] and "replaced" in held[0].message


@pytest.mark.asyncio
async def test_a_heat_source_that_is_not_planned_does_not_cancel_a_run_that_names_it(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    """A stale measurement leaves ``hp1`` out; an event for it must not fail the battery run.

    The id of a configured heat source without a thermal device is dropped from
    ``changed_devices`` (its warning stays); an id that no device and no warning names is still
    an error.
    """
    from akkudoktoreos.optimization.planner import run_planning

    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=stale_power(thermal_world))
    assert task.thermal == ()
    outcome = run_planning(task, changed_devices=["hp1"])
    assert outcome.solution is not None
    codes = [(w.code, w.devices) for w in outcome.solution.warnings]
    assert ("thermal_not_planned", ["hp1"]) in codes
    outcome = run_planning(task, changed_devices=["hp1", "bat1"])  # the planned ones are kept
    assert outcome.solution is not None
    with pytest.raises(ValueError, match="Unknown devices"):
        run_planning(task, changed_devices=["hp1", "no-such-device"])


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["search_result", "heat_program"])
@pytest.mark.parametrize("value", ["above_table", "nan"])
async def test_a_broken_heat_proposal_never_cancels_the_run(
    value: str,
    entry: str,
    config_eos: ConfigEOS,
    thermal_world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Heat above the table or NaN is a violation to repair, never an exception.

    The broken value comes once as the result of the search (the check of the end of the run
    must report it, not simulate it) and once from the heat program (no candidate). The run ends, the heat plan is finite and within
    the table, and the solution carries a warning for the heat pump.
    """
    from dataclasses import replace

    from akkudoktoreos.optimization.planner import run_planning
    from akkudoktoreos.optimization.search import DeviceSearch, check_plan, heatplan

    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
    unit = task.problem.thermal[0]
    top_w = float(unit.heat_limit_w[3])
    broken = top_w + 1.0 if value == "above_table" else float("nan")

    if entry == "search_result":
        original = DeviceSearch.solve

        def spoil(self: DeviceSearch, *args: Any, **kwargs: Any) -> Any:
            found = original(self, *args, **kwargs)
            plan = found.plan.copy()
            plan.thermal_heat_w[0][3] = broken
            return replace(
                found,
                plan=plan,
                violations=check_plan(self.problem, plan, self.space, self.settings),
            )

        monkeypatch.setattr(DeviceSearch, "solve", spoil)
    else:
        solve = heatplan.plan_heat

        def poisoned(*args: Any, **kwargs: Any) -> Any:
            found = solve(*args, **kwargs)
            if found is not None:
                found[0][3] = broken
            return found

        monkeypatch.setattr(heatplan, "plan_heat", poisoned)

    outcome = run_planning(task)
    heat = np.asarray(outcome.result.thermal.heat_w[0])  # type: ignore[union-attr]
    assert np.all(np.isfinite(heat))
    assert np.all(heat >= 0.0) and np.all(heat <= unit.heat_limit_w + 1e-6)
    warned = [w for w in outcome.solution.warnings if w.code == "thermal_heat_plan_failed"]
    assert warned and all(w.devices == ["hp1"] for w in warned)


@pytest.mark.asyncio
async def test_a_block_that_lets_the_room_fall_below_the_protection_minimum_is_reported(
    config_eos: ConfigEOS, thermal_world: dict[str, Any]
) -> None:
    """A heating block is hard; when it freezes the room the solution says where and when.

    The block lasts the whole horizon at -15 degrees outside. The heat stays 0, and the solution
    carries a ``thermal_protection_violated`` warning for the heat pump that names the zone and
    the first slot with the room below the protection minimum.
    """
    from akkudoktoreos.optimization.planner import run_planning
    from akkudoktoreos.optimization.request import BlockedInterval

    thermal_world["prediction"].outdoor_c = -15.0
    block = BlockedInterval(start=NOW - timedelta(hours=1), end=NOW + timedelta(hours=40))
    task = await resolve_task(
        config_eos,
        DEVICES,
        {"bat1": 50},
        extra=THERMAL,
        request_extra={"heat_source_blocks": {"hp1": [block]}},
    )
    outcome = run_planning(task)
    heat = np.asarray(outcome.result.thermal.heat_w[0])  # type: ignore[union-attr]
    assert np.all(heat == 0.0)  # the block holds
    protection = float(task.problem.thermal[0].protection_min_c[0])  # type: ignore[index]
    rooms = np.asarray(outcome.result.thermal.temperature_c[0])[:, 0]  # type: ignore[union-attr]
    first = next(i for i in range(1, len(rooms)) if rooms[i] < protection)
    warned = [w for w in outcome.solution.warnings if w.code == "thermal_protection_violated"]
    assert len(warned) == 1 and warned[0].devices == ["hp1"]
    assert "living" in warned[0].message and f"slot {first}" in warned[0].message


# The band of the plant: occupied 20.7/21.0/24.0, absent 16.0/16.3/21.5.
INSTALLATION_ZONE: dict[str, Any] = {
    "comfort": {"min_temperature_c": 20.7, "target_temperature_c": 21.0, "max_temperature_c": 24.0},
    "unoccupied_comfort": {
        "min_temperature_c": 16.0,
        "target_temperature_c": 16.3,
        "max_temperature_c": 21.5,
    },
    "absence_minimum_temperature_c": 16.0,
}


def _band_tuple(band: Any) -> tuple[float, float, float]:
    return (
        round(band.min_temperature_c, 6),
        round(band.target_temperature_c, 6),
        round(band.max_temperature_c, 6),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "age_minutes", "occupied"),
    [
        (21.0, 5, (20.7, 21.0, 24.0)),  # the controller stands at the configured target: unchanged
        (21.5, 5, (21.2, 21.5, 24.0)),
        (23.0, 5, (22.7, 23.0, 24.0)),
        (24.0, 5, (23.7, 24.0, 24.0)),  # upper limit of the plausible range
        (16.0, 5, (16.0, 16.0, 24.0)),  # lower limit: the minimum stays at the absence floor
        (24.1, 5, (20.7, 21.0, 24.0)),  # above 24: the configured band stays
        (15.9, 5, (20.7, 21.0, 24.0)),  # below the absence minimum: the configured band stays
        (21.5, 239, (21.2, 21.5, 24.0)),  # a hold value stays valid for just under 4 h ...
        (21.5, 241, (20.7, 21.0, 24.0)),  # ... and not longer: the configured band stays
    ],
)
async def test_the_installation_band_follows_a_fresh_room_target_only(
    config_eos: ConfigEOS,
    thermal_world: dict[str, Any],
    value: float,
    age_minutes: int,
    occupied: tuple[float, float, float],
) -> None:
    thermal_world["series"]["room_target"] = [(NOW - timedelta(minutes=age_minutes), value)]
    extra = with_zone(
        presence_area_id=None, target_measurement_key="room_target", **INSTALLATION_ZONE
    )
    extra["presence"] = {}
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=extra)
    assert {_band_tuple(band) for band in task.thermal[0].bands} == {occupied}


@pytest.mark.asyncio
@pytest.mark.parametrize("series", [None, []])
async def test_a_room_target_channel_without_a_value_keeps_the_installation_band(
    config_eos: ConfigEOS, thermal_world: dict[str, Any], series: Optional[list[Any]]
) -> None:
    # No input at all (no sample yet, or the channel is not even delivered): the plan is the same
    # as without the setting.
    if series is not None:
        thermal_world["series"]["room_target"] = series
    reference = with_zone(presence_area_id=None, **INSTALLATION_ZONE)
    reference["presence"] = {}
    expected = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=reference)
    extra = with_zone(
        presence_area_id=None, target_measurement_key="room_target", **INSTALLATION_ZONE
    )
    extra["presence"] = {}
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=extra)
    assert task.thermal[0].bands == expected.thermal[0].bands
    assert {_band_tuple(band) for band in task.thermal[0].bands} == {(20.7, 21.0, 24.0)}


def _real_presence(config_eos: ConfigEOS, thermal_world: dict[str, Any], monkeypatch: Any) -> Any:
    """The real presence provider (absence store of this test) behind the thermal request."""
    from akkudoktoreos.core.coreabc import get_prediction
    from akkudoktoreos.prediction.presence import PresenceHistory

    config_eos.merge_settings_from_dict(THERMAL)
    provider = next(row for row in get_prediction().providers if isinstance(row, PresenceHistory))
    provider._absence_store = None  # a store in the data folder of this test
    monkeypatch.setattr(thermal_world["prediction"], "provider_by_id", lambda _id: provider)
    return provider


@pytest.mark.asyncio
async def test_a_reported_absence_in_the_horizon_lowers_exactly_its_slots_to_the_absence_band(
    config_eos: ConfigEOS, thermal_world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from akkudoktoreos.thermal.thermalabsences import AbsenceInput

    provider = _real_presence(config_eos, thermal_world, monkeypatch)
    # Hourly slots from 10:00: away from 13:00 to 18:00 are the slots 3 to 7.
    provider.absence_store().add(
        AbsenceInput(start=NOW + timedelta(hours=3), end=NOW + timedelta(hours=8)), now=NOW
    )
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
    bands = task.thermal[0].bands
    lowered = [slot for slot, band in enumerate(bands) if band.min_temperature_c == 17.0]
    assert lowered == [3, 4, 5, 6, 7]
    assert {band.min_temperature_c for band in bands} == {17.0, 20.0}


@pytest.mark.asyncio
async def test_without_a_reported_absence_no_slot_is_lowered_by_the_store(
    config_eos: ConfigEOS, thermal_world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_presence(config_eos, thermal_world, monkeypatch)
    task = await resolve_task(config_eos, DEVICES, {"bat1": 50}, extra=THERMAL)
    assert {band.min_temperature_c for band in task.thermal[0].bands} == {20.0}
