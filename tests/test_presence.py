"""Causal native presence forecasting; no household or controller is contacted."""

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from akkudoktoreos.measurement.quality import MeasurementSample, SampleQuality
from akkudoktoreos.prediction.presence import (
    PresenceCommonSettings,
    PresenceCorrection,
    PresenceDataRecord,
    PresenceHistory,
    _wilson_upper,
    forecast_presence,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


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


def absence_history():
    # Each of forty independent dates has twelve explicitly measured minutes.
    return [
        sample(NOW - timedelta(days=day) + timedelta(minutes=minute), 0.0)
        for day in range(1, 41)
        for minute in range(15)
    ]


def test_default_provider_is_disabled_and_enabled_requires_binding():
    assert PresenceCommonSettings().provider is None
    with pytest.raises(ValidationError):
        PresenceCommonSettings(provider="PresenceHistory")


def test_cold_start_is_unknown_with_comfort_and_no_fabricated_calendar():
    estimate = predict([])[0]
    assert estimate.probability is None
    assert estimate.require_comfort
    assert estimate.source == "unknown"


@pytest.mark.parametrize("occupied", [0.0, 1.0])
def test_one_day_publishes_provisional_pattern_without_authorizing_absence(occupied):
    # Yesterday is Sunday, today Monday: even this fallback is visible, labelled
    # as cross-day-type evidence and never allowed to cut comfort on its own.
    history = [
        sample(NOW - timedelta(days=1) + timedelta(hours=2, minutes=i), occupied) for i in range(15)
    ]
    estimate = predict(history, starts=[NOW + timedelta(hours=2)])[0]
    assert estimate.probability == occupied
    assert estimate.historical_probability == occupied
    assert estimate.history_is_provisional
    assert estimate.support_days == 1
    assert estimate.support_population == "all_days"
    assert estimate.require_comfort
    assert estimate.probability_upper is not None


def test_one_day_with_missing_bucket_coverage_stays_unknown():
    history = [
        sample(NOW - timedelta(days=1) + timedelta(hours=2, minutes=i), 0.0) for i in range(5)
    ]
    estimate = predict(history, starts=[NOW + timedelta(hours=2)])[0]
    assert estimate.probability is None
    assert estimate.support_days == 0
    assert estimate.require_comfort


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


@pytest.mark.parametrize(
    "status,value", [("invalid", 0.0), ("unavailable", None), ("estimated", 0.0)]
)
def test_nonmeasured_sensor_data_is_not_absence(status, value):
    estimate = predict(absence_history() + [sample(NOW, value, status)])[0]
    assert estimate.require_comfort
    assert estimate.source == "unknown"


def test_absence_requires_continuous_valid_delay_and_history_support():
    recent = [sample(NOW - timedelta(minutes=minute), 0.0) for minute in range(31)]
    assert predict(recent)[0].require_comfort  # No historical support yet.
    delayed = predict(absence_history() + recent)[0]
    assert delayed.source == "sensor_absent"
    assert not delayed.require_comfort
    brief = predict(absence_history() + recent[:10])[0]
    assert brief.require_comfort
    assert brief.source == "unknown"


def test_sensor_outage_breaks_absence_delay_and_does_not_teach_absence():
    recent = [sample(NOW - timedelta(minutes=minute), 0.0) for minute in range(31)]
    broken = [row for row in recent if not 10 <= (NOW - row.date_time).total_seconds() / 60 <= 15]
    assert predict(absence_history() + broken)[0].require_comfort
    assert predict([sample(NOW - timedelta(days=1), 0.0)])[0].support_days == 0


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


def test_manual_comfort_overrides_supported_absence():
    correction = PresenceCorrection(
        zone_id="area1", kind="comfort", issued_at=NOW, valid_until=NOW + timedelta(hours=1)
    )
    assert predict(absence_history(), correction=correction)[0].source == "manual_comfort"


def test_naive_times_and_nonprospective_intervals_rejected():
    with pytest.raises(ValueError, match="aware"):
        predict([], now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="prospective"):
        predict([], starts=[NOW - timedelta(seconds=1)])


def test_dst_fold_has_one_independent_vote_per_local_date():
    # Both occurrences of 02:00 on a DST fold remain a single historical date.
    fold = datetime(2026, 10, 25, tzinfo=timezone.utc)
    samples = [sample(fold + timedelta(minutes=minute), 0.0) for minute in range(15)]
    samples += [sample(fold + timedelta(hours=1, minutes=minute), 0.0) for minute in range(15)]
    estimates = predict(
        samples,
        starts=[datetime(2026, 11, 15, 1, tzinfo=timezone.utc)],
        timezone_name="Europe/Berlin",
    )
    assert estimates[0].support_days == 1
    assert estimates[0].require_comfort


def test_sparse_120_second_samples_fail_80_percent_coverage_with_90_second_ttl():
    samples = [
        sample(NOW - timedelta(days=day) + timedelta(seconds=second), 0.0)
        for day in range(1, 41)
        for second in range(0, 900, 120)
    ]
    estimate = predict(samples, starts=[NOW + timedelta(days=7)])[0]
    assert estimate.support_days == 0
    assert estimate.require_comfort


def test_two_incomplete_dst_occurrences_do_not_add_up_to_a_complete_day():
    fold = datetime(2026, 10, 25, tzinfo=timezone.utc)
    samples = [
        sample(fold + timedelta(hours=hour, minutes=minute), 0.0)
        for hour in (0, 1)
        for minute in range(6)
    ]
    estimate = predict(
        samples,
        starts=[datetime(2026, 11, 15, 1, tzinfo=timezone.utc)],
        timezone_name="Europe/Berlin",
    )[0]
    assert estimate.support_days == 0


def test_dst_fold_future_is_compared_as_absolute_time():
    zone = ZoneInfo("Europe/Berlin")
    now = datetime(2026, 10, 25, 2, 45, tzinfo=zone, fold=0)
    start = datetime(2026, 10, 25, 2, 0, tzinfo=zone, fold=1)
    assert start.timestamp() - now.timestamp() == 900
    assert predict([], now=now, starts=[start], timezone_name="Europe/Berlin")[0].require_comfort


def bucket_history(days, *, minutes=15, occupied_weekdays=(), one_occupied_day=None):
    """Provide actual minute samples, not repeated synthetic daily pseudo-votes."""
    return [
        sample(
            NOW - timedelta(days=day) + timedelta(hours=2, minutes=minute),
            float(
                (NOW - timedelta(days=day)).weekday() in occupied_weekdays
                or day == one_occupied_day
            ),
        )
        for day in range(1, days + 1)
        for minute in range(minutes)
    ]


@pytest.mark.parametrize("probability", [0.0, 1.0])
@pytest.mark.parametrize(
    "effective_n", [1.0, 1.2870662460567823, 2.7, 6.3286, 15.3664, 38.2754, 89.0]
)
def test_wilson_endpoints_remain_inside_the_mathematical_probability_range(
    probability, effective_n
):
    upper = _wilson_upper(probability, effective_n)
    assert 0.0 <= upper <= 1.0
    expected = 1.0 if probability == 1.0 else 1.96**2 / (effective_n + 1.96**2)
    assert upper == pytest.approx(expected, abs=1e-15)


def test_all_positive_irregular_history_produces_a_valid_finite_estimate():
    history = [
        sample(NOW - timedelta(days=day) + timedelta(hours=2, minutes=minute), 1.0)
        for day in (1, 3, 59, 83)
        for minute in range(15)
    ]
    estimate = predict(
        history,
        starts=[NOW + timedelta(hours=2)],
        settings=PresenceCommonSettings(history_days=84, minimum_support_days=2),
    )[0]
    assert estimate.probability == 1.0
    assert estimate.probability_upper == pytest.approx(1.0)
    assert estimate.require_comfort


@pytest.mark.parametrize(
    "days,allow_absence",
    [(14, 0), (28, 28), (42, 28), (56, 28), (70, 28), (84, 28)],
)
def test_more_absence_history_does_not_switch_to_an_unusable_weekday_population(
    days, allow_absence
):
    # The first four slots remain conservative because the live sensor is unknown.
    starts = [NOW + timedelta(minutes=15 * index) for index in range(32)]
    history = [
        sample(NOW - timedelta(days=day) + timedelta(minutes=minute), 0.0)
        for day in range(1, days + 1)
        for minute in range(480)
    ]
    estimates = predict(history, starts=starts, settings=PresenceCommonSettings(history_days=days))
    assert sum(not row.require_comfort for row in estimates) == allow_absence
    assert sum(row.source == "unknown" for row in estimates) == 4
    estimate = estimates[8]
    assert estimate.support_population == "workday"
    assert estimate.support_days == days * 5 // 7
    assert estimate.weekday_support_days == days // 7
    assert estimate.weekday_effective_support_days < 8.7
    assert estimate.weekday_probability_upper is not None
    assert estimate.weekday_probability_upper > 0.2
    assert not estimate.weekday_presence_guard
    assert PresenceCommonSettings().absence_probability_threshold == 0.2


@pytest.mark.parametrize("days", [48, 49, 50, 55, 56, 57, 69, 70, 83, 84])
def test_raw_seventh_weekday_does_not_degrade_supported_absence(days):
    estimate = predict(
        bucket_history(days),
        starts=[NOW + timedelta(hours=2)],
        settings=PresenceCommonSettings(history_days=days),
    )[0]
    assert not estimate.require_comfort
    assert estimate.support_population == "workday"
    assert estimate.probability_upper is not None and estimate.probability_upper < 0.2


def test_default_56_day_window_ignores_more_than_a_year_of_old_samples():
    starts = [NOW + timedelta(hours=2)]
    recent = predict(bucket_history(56), starts=starts)[0]
    long = predict(bucket_history(400), starts=starts)[0]
    assert long.model_dump() == recent.model_dump()
    assert long.support_population == "workday" and long.support_days == 40
    assert long.weekday_support_days == 8
    assert long.effective_support_days > 15.3664
    assert not long.require_comfort


def test_exact_weekday_is_selected_only_when_its_effective_support_can_decide_absence():
    # This explicit nondefault setting exercises the generic selection boundary;
    # the default threshold stays 0.2 and is not relaxed to repair the provider.
    settings = PresenceCommonSettings(
        history_days=84, recency_half_life_days=1000, absence_probability_threshold=0.3
    )
    estimate = predict(bucket_history(84), starts=[NOW + timedelta(hours=2)], settings=settings)[0]
    assert estimate.support_population == "weekday"
    assert estimate.support_days == estimate.weekday_support_days == 12
    assert estimate.effective_support_days == estimate.weekday_effective_support_days
    assert estimate.probability_upper is not None and estimate.probability_upper < 0.3
    assert not estimate.require_comfort


@pytest.mark.parametrize("days", [28, 56, 84])
def test_known_wednesday_occupancy_cannot_be_diluted_by_absent_other_weekdays(days):
    wednesday = NOW + timedelta(days=2, hours=2)
    estimate = predict(
        bucket_history(days, occupied_weekdays=(2,)),
        starts=[wednesday],
        settings=PresenceCommonSettings(history_days=days),
    )[0]
    assert estimate.support_population == "workday"
    assert estimate.support_days == days * 5 // 7  # No second set of weekday pseudo-votes.
    assert estimate.weekday_probability == 1.0
    assert estimate.weekday_presence_guard
    assert estimate.probability_upper == pytest.approx(1.0)
    assert estimate.require_comfort
    assert estimate.source == "history"


def test_one_known_occupied_weekday_guards_absence_before_seven_weekday_examples():
    wednesday = NOW + timedelta(days=2, hours=2)
    # Keep only one Wednesday, which was occupied, while retaining all other days.
    history = [
        row
        for row in bucket_history(56, one_occupied_day=5)
        if row.date_time.weekday() != 2 or row.date_time.date() == (NOW - timedelta(days=5)).date()
    ]
    estimate = predict(history, starts=[wednesday])[0]
    assert estimate.weekday_support_days == 1
    assert estimate.support_days == 33
    assert estimate.weekday_probability == 1.0
    # The point estimate is explicitly the pool's estimate, not an asserted
    # weekday prediction. Separate diagnostics explain its guarded upper bound.
    assert estimate.historical_probability is not None
    assert estimate.historical_probability < 0.2
    assert estimate.weekday_presence_guard
    assert estimate.require_comfort
    assert estimate.probability_upper == pytest.approx(1.0)


def test_one_rare_occupied_weekday_still_guards_with_many_absent_weekdays():
    estimate = predict(
        bucket_history(84, one_occupied_day=5),
        starts=[NOW + timedelta(days=2, hours=2)],
        settings=PresenceCommonSettings(history_days=84),
    )[0]
    assert estimate.weekday_support_days == 12
    assert estimate.weekday_presence_guard
    assert estimate.historical_probability is not None and estimate.historical_probability < 0.2
    assert estimate.probability_upper == estimate.weekday_probability_upper
    assert estimate.require_comfort


def test_partial_weekday_coverage_is_neither_an_absence_vote_nor_a_known_occupancy_vote():
    history = [row for row in bucket_history(56) if row.date_time.weekday() != 2]
    history += [
        row
        for row in bucket_history(56, minutes=5, occupied_weekdays=(2,))
        if row.date_time.weekday() == 2
    ]
    estimate = predict(history, starts=[NOW + timedelta(days=2, hours=2)])[0]
    assert estimate.support_days == 32
    assert estimate.weekday_support_days == 0
    assert not estimate.weekday_presence_guard
    assert estimate.weekday_probability is None


def test_weekday_guard_preserves_live_presence_and_manual_comfort_priorities():
    history = bucket_history(56, occupied_weekdays=(0,))
    correction = PresenceCorrection(
        zone_id="area1", kind="away", issued_at=NOW, valid_until=NOW + timedelta(hours=3)
    )
    estimate = predict(history + [sample(NOW, 1.0)], correction=correction)[0]
    assert estimate.require_comfort and estimate.source == "sensor_present"
    correction = correction.model_copy(update={"kind": "comfort"})
    estimate = predict(history, starts=[NOW + timedelta(hours=2)], correction=correction)[0]
    assert estimate.require_comfort and estimate.source == "manual_comfort"


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


@pytest.mark.parametrize("occupied", [0.0,1.0])
def test_economic_provisional_policy_is_opt_in(occupied):
    history=[sample(NOW-timedelta(days=1)+timedelta(hours=2,minutes=i),occupied) for i in range(15)]
    estimate=predict(history,starts=[NOW+timedelta(hours=2)],settings=PresenceCommonSettings(allow_provisional_setback=True,absence_probability_threshold=.4))[0]
    assert estimate.history_is_provisional
    assert estimate.require_comfort == bool(occupied)
    assert predict([],settings=PresenceCommonSettings(allow_provisional_setback=True))[0].require_comfort
