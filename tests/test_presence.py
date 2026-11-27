"""Causal native presence forecasting; no household or controller is contacted."""

import functools
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from akkudoktoreos.measurement.quality import MeasurementSample, SampleQuality
from akkudoktoreos.prediction.presence import (
    PresenceCommonSettings,
    PresenceCorrection,
    PresenceDataRecord,
    PresenceHistory,
    PresenceZoneSettings,
    forecast_presence,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)  # a Monday


def sample(at, value, status="measured", key="motion"):
    return MeasurementSample(
        date_time=at, key=key, value=value, quality=SampleQuality(status=status)
    )


def predict(samples, **changes):
    options: dict[str, Any] = dict(
        measurement_key="motion",
        now=NOW,
        starts=[NOW],
        timezone_name="UTC",
        settings=PresenceCommonSettings(),
    )
    options.update(changes)
    return forecast_presence(samples, **options)


def minutes(days, value_at, now=NOW):
    """One sample per minute of the last ``days`` days up to ``now``; ``value_at(t)`` gives it."""
    first = now - timedelta(days=days)
    return [
        sample(first + timedelta(minutes=i), value_at(first + timedelta(minutes=i)))
        for i in range(days * 1440 + 1)
    ]


def office_hours(t):
    """Present from 08:00 to 22:00 (UTC), away otherwise."""
    return 1.0 if 8 <= t.hour < 22 else 0.0


@functools.lru_cache(maxsize=None)
def routine_until(now=NOW):
    return minutes(21, office_hours, now=now)


@pytest.fixture
def routine():
    return list(routine_until())


def test_default_provider_is_disabled_and_enabled_requires_binding():
    assert PresenceCommonSettings().provider is None
    with pytest.raises(ValidationError):
        PresenceCommonSettings(provider="PresenceHistory")


def test_cold_start_is_unknown_with_comfort_and_no_fabricated_calendar():
    estimate = predict([])[0]
    assert estimate.probability is None
    assert estimate.require_comfort
    assert estimate.source == "unknown"


def test_too_little_covered_time_stays_unknown():
    history = [
        sample(NOW - timedelta(days=1) + timedelta(hours=2, minutes=i), 0.0) for i in range(5)
    ]
    estimate = predict(history, starts=[NOW + timedelta(hours=2)])[0]
    assert estimate.probability is None
    assert estimate.support_days == 0
    assert estimate.require_comfort


def test_slots_without_neighbours_teach_no_transition():
    # Every second quarter hour is observed for three days: slots enough, but no transition.
    history = [
        sample(NOW - timedelta(days=3) + timedelta(minutes=30 * q + m), 1.0)
        for q in range(144)
        for m in range(15)
    ]
    estimate = predict(history, starts=[NOW + timedelta(hours=1)])[0]
    assert estimate.probability is None and estimate.require_comfort


def test_future_samples_and_other_sensor_keys_cannot_change_forecast():
    base = [sample(NOW, 1.0)]
    poison = base + [sample(NOW + timedelta(seconds=1), 0.0), sample(NOW, 0.0, key="other")]
    assert [row.model_dump() for row in predict(base)] == [
        row.model_dump() for row in predict(poison)
    ]


def test_present_is_corrected_immediately_without_historical_training():
    estimate = predict([sample(NOW, 1.0)])[0]
    assert estimate.probability == 1
    assert estimate.require_comfort
    assert estimate.source == "sensor_present"


def test_the_pattern_is_recognised_and_the_state_decays_to_it():
    # Away since 22:00 the evening before, now 03:00 the next day: the room should be warm by
    # 09:00 and may be cool at 04:00, and the later forecast does not remember the state.
    now = datetime(2026, 11, 10, 3, tzinfo=timezone.utc)
    starts = [now + timedelta(minutes=15 * i) for i in range(1, 82)]
    out = predict(list(routine_until(now)), now=now, starts=starts)
    by_hour = {(row.date_time.hour, row.date_time.minute): row for row in out}
    assert by_hour[(4, 0)].probability < 0.05
    assert by_hour[(12, 0)].probability > 0.9
    assert by_hour[(23, 0)].probability < 0.3
    assert all(row.support_days >= 20 and not row.history_is_provisional for row in out)
    assert out[-1].probability == pytest.approx(out[-1].historical_probability, abs=0.02)


def test_the_state_is_carried_forward_with_the_rates_of_the_chain():
    # Present now (with a recent detection), the chain only forgets slowly in a steady pattern.
    now = datetime(2026, 11, 10, 12, 5, tzinfo=timezone.utc)
    starts = [now + timedelta(minutes=15 * i) for i in range(1, 8)]
    out = predict(list(routine_until(now)), now=now, starts=starts)
    assert out[0].source == "sensor_present"
    assert out[0].probability >= out[-1].probability >= out[-1].historical_probability - 1e-9
    assert out[0].probability > 0.95


def test_fit_recovers_known_rates():
    # A chain with constant rates a = 0.10 (absent to present) and b = 0.20 per quarter hour.
    rng = np.random.default_rng(7)
    days = 40
    state, quarters = 0, []
    for _ in range(days * 96):
        draw = rng.random()
        state = int(draw < 0.10) if state == 0 else int(draw >= 0.20)
        quarters.append(state)
    first = NOW - timedelta(days=days)
    history = [
        sample(first + timedelta(minutes=15 * q + m), float(state))
        for q, state in enumerate(quarters)
        for m in range(15)
    ]
    out = predict(history, starts=[NOW + timedelta(hours=h) for h in range(1, 13)])
    # stationary share a / (a + b) = 1/3, from a state that has been forgotten after a few hours
    assert np.mean([row.historical_probability for row in out]) == pytest.approx(1 / 3, abs=0.07)


def test_a_flash_of_the_radar_is_held_as_presence():
    # One detection every 5 minutes while at home (a flickering radar), nothing otherwise.
    def flicker(t):
        return 1.0 if 8 <= t.hour < 22 and t.minute % 5 == 0 else 0.0

    history = minutes(14, flicker)
    start = [NOW + timedelta(hours=1)]  # Monday 13:00
    held = predict(history, starts=start)[0]
    raw = predict(history, starts=start, settings=PresenceCommonSettings(occupancy_hold_seconds=0))[
        0
    ]
    assert held.historical_probability > 0.8
    assert raw.historical_probability < 0.5


def test_hold_is_a_union_clipped_to_the_covered_time():
    # Detections every minute and a long hold neither count more than the slot nor survive a gap
    # in the samples: the slot after an outage is not covered and teaches nothing.
    history = minutes(14, lambda t: 1.0)
    gap_start = NOW - timedelta(hours=1)
    kept = [s for s in history if not gap_start <= s.date_time < NOW - timedelta(minutes=20)]
    settings = PresenceCommonSettings(occupancy_hold_seconds=3600)
    out = predict(kept, settings=settings, starts=[NOW + timedelta(minutes=15)])
    assert out[0].probability is not None and 0 <= out[0].probability <= 1


def test_a_robot_vacuum_in_the_ignore_window_is_no_presence():
    # Away all day and night except a nightly robot run 01:15-02:50 (UTC) that the radar sees.
    def robot(t):
        return 1.0 if (t.hour, t.minute) >= (1, 15) and (t.hour, t.minute) < (2, 50) else 0.0

    history = minutes(14, robot)
    now = datetime(2026, 11, 9, 20, tzinfo=timezone.utc)
    starts = [now + timedelta(hours=5, minutes=15 * i) for i in range(1, 3)]  # 01:15, 01:30
    seen = predict(history, now=now, starts=starts)
    ignored = predict(history, now=now, starts=starts, ignore_windows=[("01:00", "03:00")])
    assert min(row.historical_probability for row in seen) > 0.5
    assert max(row.historical_probability for row in ignored) < 0.05
    # also the live sensor state: a detection inside the window is not presence
    live = history + [sample(datetime(2026, 11, 9, 2, tzinfo=timezone.utc), 1.0)]
    at_two = datetime(2026, 11, 9, 2, tzinfo=timezone.utc)
    kwargs = dict(now=at_two, starts=[at_two], ignore_windows=[("01:00", "03:00")])
    assert predict(live, **kwargs)[0].source != "sensor_present"
    assert predict(live, now=at_two, starts=[at_two])[0].source == "sensor_present"


def test_ignore_windows_are_validated_clock_times():
    assert PresenceZoneSettings(measurement_key="m", ignore_windows=[("22:00", "02:00")])
    with pytest.raises(ValidationError):
        PresenceZoneSettings(measurement_key="m", ignore_windows=[("late", "02:00")])


@pytest.mark.parametrize(
    "status,value", [("invalid", 0.0), ("unavailable", None), ("estimated", 0.0)]
)
def test_nonmeasured_sensor_data_is_not_absence(routine, status, value):
    estimate = predict(routine + [sample(NOW, value, status)])[0]
    assert estimate.require_comfort
    assert estimate.source == "unknown"


def test_an_unhealthy_sensor_starts_the_chain_at_the_profile(routine):
    # The last sample is stale: the near future follows the profile, never a state, and keeps comfort.
    now = NOW + timedelta(hours=3)
    out = predict(routine, now=now, starts=[now + timedelta(minutes=15 * i) for i in (1, 2)])
    assert [row.source for row in out] == ["unknown", "unknown"]
    assert all(row.require_comfort for row in out)
    assert all(row.probability == row.historical_probability for row in out)


def test_absence_requires_continuous_valid_delay_and_history_support():
    now = NOW.replace(hour=23)  # the routine has been away for an hour by now
    history = list(routine_until(now))
    estimate = predict(history, now=now, starts=[now])[0]
    assert estimate.source == "sensor_absent"
    assert not estimate.require_comfort
    gap = [
        s
        for s in history
        if not now - timedelta(minutes=25) <= s.date_time <= now - timedelta(minutes=20)
    ]
    assert predict(gap, now=now, starts=[now])[0].source == "unknown"  # the chain is broken
    assert predict(history[-40:], now=now, starts=[now])[0].require_comfort  # no history yet


def test_too_little_history_never_lowers_comfort_unless_opted_in():
    history = minutes(3, lambda t: 0.0)
    start = [NOW + timedelta(hours=2)]
    closed = predict(history, starts=start)[0]
    assert closed.history_is_provisional and closed.support_days == 3 and closed.require_comfort
    opted = PresenceCommonSettings(
        allow_provisional_setback=True, absence_probability_threshold=0.4
    )
    assert not predict(history, starts=start, settings=opted)[0].require_comfort


def test_invalid_replacement_of_same_timestamp_wins():
    estimate = predict([sample(NOW, 1.0), sample(NOW, None, "invalid")])[0]
    assert estimate.source == "unknown"
    assert estimate.probability is None


def test_manual_away_expires_and_observed_presence_can_correct_it():
    correction = PresenceCorrection(
        zone_id="area1", kind="away", issued_at=NOW, valid_until=NOW + timedelta(hours=1)
    )
    starts = [NOW + timedelta(minutes=minute) for minute in (0, 45, 60)]
    result = predict([], starts=starts, correction=correction)
    assert [row.require_comfort for row in result] == [False, False, True]
    assert predict([sample(NOW, 1.0)], correction=correction)[0].require_comfort


def test_manual_comfort_overrides_supported_absence(routine):
    correction = PresenceCorrection(
        zone_id="area1", kind="comfort", issued_at=NOW, valid_until=NOW + timedelta(hours=1)
    )
    assert predict(routine, correction=correction)[0].source == "manual_comfort"


def test_naive_times_and_nonprospective_intervals_rejected():
    with pytest.raises(ValueError, match="aware"):
        predict([], now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="prospective"):
        predict([], starts=[NOW - timedelta(seconds=1)])


@pytest.mark.asyncio
async def test_native_provider_roundtrip_uses_measurement_quality_and_prediction_keys(
    config_eos, monkeypatch
):
    from akkudoktoreos.core.coreabc import get_measurement, get_prediction
    from akkudoktoreos.prediction import presence as module

    config_eos.merge_settings_from_dict(
        {
            "presence": {
                "provider": "PresenceHistory",
                "zones": {"area1": {"measurement_key": "motion"}},
            },
            "measurement": {"channels": {"motion": {"quantity": "presence", "unit": "1"}}},
            "prediction": {"hours": 1},
        }
    )

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)
    provider = next(row for row in get_prediction().providers if isinstance(row, PresenceHistory))
    monkeypatch.setattr(type(provider), "ems_start_datetime", NOW)
    monkeypatch.setattr(provider, "_corrections", {})
    await get_measurement().import_samples([sample(NOW, 1.0)])
    await provider._update_data()
    values = await provider.key_to_lists(
        "presence_probability_area1",
        start_datetime=to_datetime(NOW),
        end_datetime=to_datetime(NOW + timedelta(hours=1)),
    )
    assert values[1][0] == 1.0
    assert "presence_comfort_area1" in PresenceDataRecord.record_keys()
    assert provider.enabled()


@pytest.mark.parametrize("kind", ["away", "comfort"])
def test_native_eos_datetimes_preserve_timezone_at_correction_return_boundary(kind):
    correction = PresenceCorrection(
        zone_id="area1", kind=kind, issued_at=NOW, valid_until=NOW + timedelta(minutes=30)
    )
    starts = [to_datetime(NOW + timedelta(minutes=m)) for m in (0, 15, 30)]
    result = predict([], now=to_datetime(NOW), starts=starts, correction=correction)
    assert [r.source for r in result] == (
        ["manual_away", "manual_away", "unknown"]
        if kind == "away"
        else ["manual_comfort", "manual_comfort", "unknown"]
    )
