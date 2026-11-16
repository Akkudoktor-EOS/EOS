"""Room start-value smoothing and rate-limited cold recovery (both opt-in, default unchanged)."""

from datetime import timedelta
from typing import Any

import pytest
from conftest import LEARNING_NOW as NOW
from conftest import fleet_request_data as request_data
from conftest import learning_model as model
from conftest import learning_observer as _observer
from conftest import learning_sample as sample
from pydantic import ValidationError

from akkudoktoreos.measurement.quality import MeasurementSample
from akkudoktoreos.thermal.thermalmodels import ThermalRC2Parameters
from akkudoktoreos.thermal.thermaloptimization import (
    ThermalRecoverySettings,
    ThermalShadowRequest,
    optimize_thermal_shadow,
)
from akkudoktoreos.thermal.thermalsettings import ThermalZoneSettings


def observe(**updates: Any):
    """Five-minute sensor reporting needs a 400 s hold; inputs are measured each minute."""
    base = {
        "outdoor": [sample("outdoor", NOW - timedelta(minutes=k), 5.0) for k in range(240, -1, -1)],
        "power": [sample("power", NOW - timedelta(minutes=k), 1500.0) for k in range(240, -1, -1)],
        "max_age_seconds": 400,
    }
    return _observer(**{**base, **updates})


def stepped_room(values: dict[int, float], default: float = 20.0) -> list[MeasurementSample]:
    """Room samples each minute for 4 h; ``values`` maps minutes-before-now to a value."""
    return [
        sample("room", NOW - timedelta(minutes=240 - k), values.get(240 - k, default))
        for k in range(241)
    ]


def toggling_room() -> list[MeasurementSample]:
    """Sensor reports every five minutes, alternating 20.4 / 21.0 (burst-wise heater)."""
    rows = []
    for k in range(0, 241, 5):
        rows.append(sample("room", NOW - timedelta(minutes=240 - k), 20.4 if (k // 5) % 2 else 21.0))
    return rows


class TestRoomStartValueSmoothing:
    def test_default_is_unchanged_and_adds_no_diagnostics(self):
        room = toggling_room()
        a = observe(room=room)
        b = observe(room=room, room_observation_window_seconds=None)
        assert a[0] == b[0] and a[1] == b[1]
        assert not any(key.startswith("room_state_") for key in a[1])

    def test_mean_replaces_last_raw_value(self):
        room = toggling_room()
        raw = observe(room=room)[0].room_temperature_c
        state, notes = observe(room=room, room_observation_window_seconds=900)
        assert notes["room_state_smoothing_used"] is True
        assert notes["room_state_window_seconds"] == 900
        assert notes["room_state_window_values"] >= 3
        assert notes["room_state_raw_c"] == pytest.approx(room[-1].value)
        assert notes["room_state_smoothed_c"] == pytest.approx(20.7, abs=0.15)
        # The raw start follows the last toggle; the smoothed one is near the mean.
        assert abs(state.room_temperature_c - 20.7) < abs(raw - 20.7)

    def test_constant_room_is_unchanged_by_smoothing(self):
        raw = observe()[0]
        smooth, notes = observe(room_observation_window_seconds=1200)
        assert smooth.room_temperature_c == pytest.approx(raw.room_temperature_c, abs=1e-9)
        assert smooth.mass_temperature_c == pytest.approx(raw.mass_temperature_c, abs=1e-6)
        assert notes["room_state_smoothed_c"] == pytest.approx(20.0)

    def test_fewer_than_three_values_falls_back_and_says_so(self):
        # Only two measured samples inside a 600 s window.
        room = [
            sample("room", NOW - timedelta(minutes=240 - k), 20.0) for k in range(0, 241, 5)
        ]
        raw, _ = observe(room=room)
        state, notes = observe(room=room, room_observation_window_seconds=420)
        assert notes["room_state_smoothing_used"] is False
        assert "fewer than three" in notes["room_state_smoothing_fallback"]
        assert state == raw
        assert notes["room_state_window_values"] == 2

    def test_freshness_gate_stays_on_the_raw_value(self):
        room = [row for row in toggling_room() if row.date_time <= NOW - timedelta(minutes=10)]
        with pytest.raises(ValueError, match="stale"):
            observe(room=room, room_observation_window_seconds=900)

    def test_window_validation(self):
        with pytest.raises(ValueError, match="positive"):
            observe(room_observation_window_seconds=0)
        with pytest.raises(ValidationError):
            ThermalZoneSettings(zone_id="z", state_room_observation_window_seconds=60)
        assert ThermalZoneSettings(zone_id="z").state_room_observation_window_seconds is None


def cold_request(rate: float | None, *, room: float = 19.0, n: int = 16, **extra: Any):
    data = request_data(n)
    data["state"].update(room_temperature_c=room, mass_temperature_c=room)
    data["max_electric_power_w"] = 6000.0
    data["recovery"] = {"max_duration_seconds": 14400, **extra}
    if rate is not None:
        data["recovery"]["max_heating_rate_kelvin_per_hour"] = rate
    data["smoothing_seconds"] = None
    return ThermalShadowRequest.model_validate(data)


class TestRateLimitedRecovery:
    def test_default_is_bit_identical_and_serializes_unchanged(self):
        a, b = cold_request(None), cold_request(None)
        assert a.recovery is not None and a.recovery.max_heating_rate_kelvin_per_hour is None
        assert "max_heating_rate" not in a.model_dump_json()
        ra, rb = optimize_thermal_shadow(a), optimize_thermal_shadow(b)
        assert ra.recovery is not None and ra.recovery.completed
        assert ra.recovery.heat_power_w == rb.recovery.heat_power_w
        assert "heating_rate" not in ra.recovery.model_dump_json()
        assert "recovery_heating_rate_kelvin_per_hour" not in ra.checks

    def test_rate_limits_first_slots_and_keeps_plan_valid(self):
        free = optimize_thermal_shadow(cold_request(None))
        slow = optimize_thermal_shadow(cold_request(0.8))
        assert slow.status in ("optimal", "feasible"), slow.reason
        assert slow.recovery is not None and slow.recovery.completed
        assert slow.recovery.heating_rate_raised is False
        assert slow.recovery.heating_rate_kelvin_per_hour == pytest.approx(0.8)
        assert slow.recovery.heat_power_w[0] < free.recovery.heat_power_w[0]
        rooms = slow.recovery.room_temperature_c
        for k in range(1, len(rooms)):
            # Endpoint never above the ramp (except a forced minimum output, not used here).
            assert rooms[k] <= min(20.5, 19.0 + 0.8 * 0.25 * k) + 1e-6
        assert slow.checks["recovery_active"] is True
        assert slow.checks["recovery_heating_rate_raised"] is False
        assert slow.checks["max_minute_comfort_violation_c"] <= 1e-5
        # The target handed to the controller follows the ramp, not the final target;
        # an interval cannot carry a target below its own (unwaived) minimum of 20.0.
        assert slow.plan is not None
        assert slow.plan.intervals[0].target_temperature_c == pytest.approx(
            max(20.0, rooms[1]), abs=1e-6
        )
        assert free.plan.intervals[0].target_temperature_c == pytest.approx(20.5)

    def test_too_slow_rate_is_raised_but_plan_stays_solvable(self):
        # 0.01 K/h cannot reach the 20.0 minimum within one hour.
        result = optimize_thermal_shadow(cold_request(0.01, max_duration_seconds=3600))
        assert result.status in ("optimal", "feasible"), result.reason
        assert result.recovery is not None and result.recovery.completed
        assert result.recovery.heating_rate_raised is True
        assert result.recovery.requested_heating_rate_kelvin_per_hour == pytest.approx(0.01)
        assert result.recovery.heating_rate_kelvin_per_hour > 0.01
        assert result.checks["recovery_heating_rate_raised"] is True

    def test_unreachable_even_unrestricted_matches_old_behavior(self):
        old = optimize_thermal_shadow(
            _weak(None)
        )
        new = optimize_thermal_shadow(_weak(0.3))
        assert old.status == new.status == "infeasible"
        assert old.recovery.heat_power_w == new.recovery.heat_power_w

    def test_setback_after_the_recovery_does_not_end_a_slow_rise_early(self):
        # Slots 0-1 need 20.0, later slots only 16.0: reaching a setback slot is no reason to stop
        # the rise below the waived minimum (that would make a later 20.0 slot unreachable).
        data = request_data(8)
        data["state"].update(room_temperature_c=18.5, mass_temperature_c=18.5)
        data["max_electric_power_w"] = 6000.0
        low = {"min_temperature_c": 16.0, "target_temperature_c": 17.0, "max_temperature_c": 21.0}
        data["comfort_bands"] = data["comfort_bands"][:2] + [low] * 5 + [data["comfort_bands"][0]]
        data["recovery"] = {"max_duration_seconds": 5400, "max_heating_rate_kelvin_per_hour": 0.4}
        data["smoothing_seconds"] = None
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.status in ("optimal", "feasible"), result.reason
        assert result.recovery is not None and result.recovery.completed
        assert result.recovery.room_temperature_c[-1] >= 20.0 - 1e-6
        assert result.recovery.heating_rate_raised is True

    def test_no_recovery_needed_ignores_rate(self):
        base = optimize_thermal_shadow(cold_request(None, room=20.6))
        rated = optimize_thermal_shadow(cold_request(0.5, room=20.6))
        assert base.recovery is None and rated.recovery is None
        assert base.candidate_heat_power_w == rated.candidate_heat_power_w

    def test_rate_bounds(self):
        with pytest.raises(ValidationError):
            ThermalRecoverySettings(max_heating_rate_kelvin_per_hour=0)
        with pytest.raises(ValidationError):
            ThermalRecoverySettings(max_heating_rate_kelvin_per_hour=21)

    def test_zone_setting_round_trip(self):
        zone = ThermalZoneSettings(
            zone_id="z",
            state_room_observation_window_seconds=900,
            recovery_max_heating_rate_kelvin_per_hour=0.5,
        )
        again = ThermalZoneSettings.model_validate(zone.model_dump())
        assert again.state_room_observation_window_seconds == 900
        assert again.recovery_max_heating_rate_kelvin_per_hour == 0.5
        assert model()  # keep the shared fixture import honest
        assert ThermalRC2Parameters is not None


def _weak(rate: float | None):
    data = request_data(8)
    data["state"].update(room_temperature_c=18.0, mass_temperature_c=18.0)
    data["max_electric_power_w"] = 300.0
    data["recovery"] = {"max_duration_seconds": 900}
    if rate is not None:
        data["recovery"]["max_heating_rate_kelvin_per_hour"] = rate
    return ThermalShadowRequest.model_validate(data)
