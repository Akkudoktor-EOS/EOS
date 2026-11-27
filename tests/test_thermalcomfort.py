"""Presence-driven comfort timing and warm-up opportunity, without device control."""

from datetime import datetime, timedelta, timezone

import pytest

from akkudoktoreos.prediction.presence import PresenceEstimate
from akkudoktoreos.thermal.thermalcomfort import comfort_bands_from_presence
from akkudoktoreos.thermal.thermalmodels import ThermalComfortBand


def test_runtime_morning_windows_preserve_deadlines_and_uncovered_presence():
    from akkudoktoreos.thermal.thermalcomfort import apply_comfort_windows
    from akkudoktoreos.thermal.thermalmodels import TemperatureInterval

    start = datetime(2026, 11, 9, 5, 45, tzinfo=timezone.utc)
    starts = [start + timedelta(minutes=15 * i) for i in range(4)]
    occupied = ThermalComfortBand(
        min_temperature_c=20.7, target_temperature_c=21, max_temperature_c=21.5
    )
    windows = (
        TemperatureInterval(
            start_datetime=start,
            end_datetime=starts[1],
            min_temperature_c=18,
            target_temperature_c=18,
            max_temperature_c=21.5,
        ),
        TemperatureInterval(
            start_datetime=starts[1],
            end_datetime=starts[3],
            min_temperature_c=18.5,
            target_temperature_c=19,
            max_temperature_c=21.5,
        ),
    )
    result = apply_comfort_windows(starts, (occupied,) * 4, windows)
    assert [band.min_temperature_c for band in result] == [18, 18.5, 18.5, 20.7]
    assert result[-1] == occupied
    with pytest.raises(ValueError, match="non-overlapping"):
        apply_comfort_windows(starts, (occupied,) * 4, (windows[1], windows[0]))


def test_presence_sets_runtime_bands_and_preheat_keeps_physical_warmup_possible():
    start = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    estimates = [
        PresenceEstimate(
            date_time=start + timedelta(minutes=i * 15),
            probability=1.0 if i == 3 else 0.0,
            require_comfort=i == 3,
            source="history",
        )
        for i in range(4)
    ]
    occupied = ThermalComfortBand(
        min_temperature_c=20, target_temperature_c=20.5, max_temperature_c=21
    )
    away = ThermalComfortBand(min_temperature_c=16, target_temperature_c=17, max_temperature_c=19)
    bands = comfort_bands_from_presence(estimates, occupied, away, preheat_seconds=1800)
    assert [band.target_temperature_c for band in bands] == [17, 20.5, 20.5, 20.5]
    assert [band.min_temperature_c for band in bands] == [16, 16, 16, 20]


def test_unknown_presence_or_no_absence_preferences_preserves_comfort():
    start = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    estimate = PresenceEstimate(date_time=start, source="unknown")
    occupied = ThermalComfortBand(
        min_temperature_c=20, target_temperature_c=20.5, max_temperature_c=21
    )
    assert comfort_bands_from_presence([estimate], occupied)[0].min_temperature_c == 20
    with pytest.raises(ValueError):
        comfort_bands_from_presence([estimate], occupied, preheat_seconds=-1)


def requirements(values):
    start = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    return [
        PresenceEstimate(
            date_time=start + timedelta(minutes=i * 15),
            probability=float(required),
            require_comfort=required,
            source="history",
        )
        for i, required in enumerate(values)
    ]


def preferences():
    return (
        ThermalComfortBand(min_temperature_c=20, target_temperature_c=20.5, max_temperature_c=21),
        ThermalComfortBand(min_temperature_c=16, target_temperature_c=17, max_temperature_c=19),
    )


def test_departure_preserves_hard_absence_limit_unless_warm_coasting_is_explicit():
    occupied, away = preferences()
    estimates = requirements([True, False, False])
    strict = comfort_bands_from_presence(estimates, occupied, away)
    coasting = comfort_bands_from_presence(estimates, occupied, away, allow_warm_coasting=True)
    assert strict == (occupied, away, away)
    assert coasting[0] == occupied
    assert all(b.min_temperature_c == away.min_temperature_c for b in coasting[1:])
    assert all(b.target_temperature_c == away.target_temperature_c for b in coasting[1:])
    assert all(b.max_temperature_c == occupied.max_temperature_c for b in coasting[1:])
    assert strict[0].min_temperature_c > strict[1].max_temperature_c
    assert coasting[0].min_temperature_c <= coasting[1].max_temperature_c


def test_initial_absence_and_later_return_open_preheat_without_lowering_occupied_comfort():
    occupied, away = preferences()
    estimates = requirements([False, False, False, True, False])
    result = comfort_bands_from_presence(
        estimates, occupied, away, preheat_seconds=1800, allow_warm_coasting=True
    )
    assert [b.target_temperature_c for b in result] == [17, 20.5, 20.5, 20.5, 17]
    assert [b.min_temperature_c for b in result] == [16, 16, 16, 20, 16]
    assert result[3] == occupied
    assert all(b.max_temperature_c == 21 for b in result)


def test_unknown_presence_stays_occupied_even_with_warm_coasting():
    occupied, away = preferences()
    estimates = requirements([False, False])
    estimates[1] = estimates[1].model_copy(update={"source": "unknown"})
    result = comfort_bands_from_presence(
        estimates, occupied, away, preheat_seconds=900, allow_warm_coasting=True
    )
    assert result[0].target_temperature_c == occupied.target_temperature_c
    assert result[0].min_temperature_c == away.min_temperature_c
    assert result[1] == occupied


def test_preheat_never_weakens_a_configured_absence_minimum_to_repair_bad_preferences():
    occupied, _ = preferences()
    incompatible = ThermalComfortBand(
        min_temperature_c=22, target_temperature_c=22.5, max_temperature_c=23
    )
    with pytest.raises(ValueError):
        comfort_bands_from_presence(
            requirements([False, True]), occupied, incompatible, preheat_seconds=900
        )


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_warm_coasting_is_not_implicitly_enabled_by_coercion(value):
    occupied, away = preferences()
    with pytest.raises(ValueError, match="explicit boolean"):
        comfort_bands_from_presence(
            requirements([False]), occupied, away, allow_warm_coasting=value
        )
