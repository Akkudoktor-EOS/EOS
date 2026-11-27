"""Reported absences ("away from ... until ..."): presence provider, bands and request plumbing."""

import math
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from akkudoktoreos.measurement.quality import MeasurementSample, SampleQuality
from akkudoktoreos.prediction.presence import (
    PresenceAbsence,
    PresenceCommonSettings,
    PresenceEstimate,
    forecast_presence,
)
from akkudoktoreos.thermal.thermalcomfort import (
    DEFAULT_ABSENCE_MINIMUM_C,
    comfort_bands_from_presence,
    reported_absence_band,
)
from akkudoktoreos.thermal.thermalmodels import ThermalComfortBand

TZN = "Europe/Berlin"
TZ = ZoneInfo(TZN)
KEY = "motion"
MONDAY = datetime(2026, 7, 6, tzinfo=TZ)
LIVE: dict[str, Any] = dict(absence_delay_seconds=900, allow_provisional_setback=True, absence_probability_threshold=0.4)


def sample(ts, value):
    return MeasurementSample(
        date_time=datetime.fromtimestamp(ts, timezone.utc), key=KEY, value=value,
        quality=SampleQuality(status="measured"),
    )


def history(days, absent, start=MONDAY):
    out = []
    for d in range(days):
        day = start + timedelta(days=d)
        for m in range(1440):
            out.append(sample((day + timedelta(minutes=m)).timestamp(), 0.0 if absent(d, day, m) else 1.0))
    return out


def office(d, day, m):
    return day.weekday() < 5 and 450 <= m < 1020


def run(samples, now, hours=48, absences=None, **cfg):
    now_utc = now.astimezone(timezone.utc)
    first = math.ceil(now_utc.timestamp() / 900) * 900
    starts = [datetime.fromtimestamp(first + 900 * i, timezone.utc) for i in range(hours * 4)]
    return forecast_presence(
        samples, measurement_key=KEY, now=now_utc, starts=starts, timezone_name=TZN,
        settings=PresenceCommonSettings(**cfg), absences=absences,
    )


def until(data, now):
    limit = now.astimezone(timezone.utc)
    return [s for s in data if s.date_time <= limit]


NOW = MONDAY + timedelta(days=28, hours=9, minutes=5)  # Monday 09:05 after four weeks
DATA = until(history(29, office), NOW)


def at(estimates, when):
    return next(e for e in estimates if e.date_time == when.astimezone(timezone.utc))


# ---- 5. nothing changes without an entry --------------------------------------------------


def test_settings_default_to_no_absences():
    s = PresenceCommonSettings()
    assert s.absences == [] and s.absence_return_hold_minutes == 120


# ---- 1. input: time zones, open end, validation ---------------------------------------------
def test_naive_times_use_the_installation_zone_and_offsets_are_exact():
    a = PresenceAbsence(start=datetime(2026, 11, 14, 13, 55), end=datetime(2026, 11, 15, 14, 0))
    start, end = a.resolve(TZN)
    assert start == datetime(2026, 11, 14, 13, 55, tzinfo=TZ).timestamp()
    assert end == datetime(2026, 11, 15, 14, 0, tzinfo=TZ).timestamp()
    b = PresenceAbsence.model_validate({"start": "2026-11-14T12:55:00Z", "end": "2026-11-15T13:00:00+00:00"})
    assert b.resolve("Asia/Tokyo")[0] == start  # an offset ignores the installation zone
    assert PresenceAbsence(start=datetime(2026, 11, 14, 13, 55)).resolve(TZN)[1] is None  # open end


def test_invalid_absences_are_rejected():
    with pytest.raises(ValidationError):
        PresenceAbsence(start=datetime(2026, 11, 15), end=datetime(2026, 11, 14))
    with pytest.raises(ValidationError):
        PresenceAbsence(start=datetime(2026, 11, 14, tzinfo=timezone.utc), end=datetime(2026, 11, 15))
    with pytest.raises(ValidationError):
        PresenceCommonSettings(absence_return_hold_minutes=-1)


def test_absences_load_from_the_configuration_dictionary():
    s = PresenceCommonSettings.model_validate(
        {"absences": [{"start": "2026-11-14T13:55:00", "end": "2026-11-15T14:00:00", "label": "Reise"}]}
    )
    assert s.absences[0].label == "Reise"
    again = PresenceCommonSettings.model_validate(s.model_dump())
    assert [a.model_dump() for a in again.absences] == [a.model_dump() for a in s.absences]


# ---- 2. priority over the learned pattern, return comfort -------------------------------------
def test_reported_absence_beats_the_learned_pattern_and_the_return_gets_comfort():
    end = NOW + timedelta(hours=24)
    absence = [PresenceAbsence(start=NOW - timedelta(hours=1), end=end)]
    plain = run(DATA, NOW, **LIVE)
    est = run(DATA, NOW, absences=absence, **LIVE)
    # Monday 17:30 would be learned as present ...
    evening = NOW.replace(hour=18, minute=0)
    assert at(plain, evening).require_comfort and not at(plain, evening).source == "reported_absence"
    # ... but the report wins: reduction until the return
    assert at(est, evening).source == "reported_absence" and not at(est, evening).require_comfort
    assert at(est, evening).probability == 0.0
    # the slot with the return and the two hours after it need comfort
    ret = end.replace(minute=0, second=0, microsecond=0)
    for k in range(0, 8):
        slot = at(est, ret + timedelta(minutes=15 * k))
        assert slot.require_comfort and slot.source == "reported_return"
    after = at(est, ret + timedelta(hours=2, minutes=30))
    assert after.source != "reported_return"
    # the slot just before the return stays an absence slot
    assert at(est, ret - timedelta(minutes=15)).source == "reported_absence"


def test_return_slot_straddling_a_quarter_hour_is_comfortable():
    end = NOW + timedelta(hours=10, minutes=7)  # not on the grid
    est = run(DATA, NOW, absences=[PresenceAbsence(start=NOW - timedelta(hours=1), end=end)], **LIVE)
    straddle = (NOW + timedelta(hours=10, minutes=0)).replace(second=0, microsecond=0)
    straddle = straddle.replace(minute=(straddle.minute // 15) * 15)
    assert at(est, straddle).source in ("reported_return", "reported_absence")
    first_after = [e for e in est if e.source == "reported_return"][0]
    assert first_after.date_time <= end.astimezone(timezone.utc) < first_after.date_time + timedelta(minutes=15)


def test_open_ended_absence_covers_the_whole_horizon_without_a_return():
    est = run(DATA, NOW, absences=[PresenceAbsence(start=NOW - timedelta(hours=1))], hours=72, **LIVE)
    assert all(e.source == "reported_absence" for e in est[2:]) and not any(e.require_comfort for e in est[2:])
    assert not any(e.source == "reported_return" for e in est)


def test_a_future_absence_only_covers_its_own_period():
    start = (NOW + timedelta(hours=6)).replace(minute=0)
    est = run(DATA, NOW, absences=[PresenceAbsence(start=start, end=start + timedelta(hours=5))], **LIVE)
    kinds = [e.source for e in est]
    assert "reported_absence" in kinds and "reported_return" in kinds
    assert kinds[0] != "reported_absence"
    assert at(est, start + timedelta(minutes=15)).source == "reported_absence"
    assert at(est, start - timedelta(minutes=15)).source != "reported_absence"


def test_two_back_to_back_absences_do_not_force_comfort_in_between():
    a = PresenceAbsence(start=NOW - timedelta(hours=1), end=NOW + timedelta(hours=5))
    b = PresenceAbsence(start=NOW + timedelta(hours=5), end=NOW + timedelta(hours=9))
    est = run(DATA, NOW, absences=[a, b], **LIVE)
    joint = (NOW + timedelta(hours=5)).replace(minute=0, second=0, microsecond=0)
    assert at(est, joint + timedelta(minutes=15)).source == "reported_absence"


def test_someone_at_home_right_now_is_not_overruled_by_a_report():
    present = DATA + [sample(NOW.timestamp(), 1.0)]  # replaces the 09:05 sample (last wins)
    est = run(present, NOW, absences=[PresenceAbsence(start=NOW - timedelta(hours=1), end=NOW + timedelta(hours=24))], **LIVE)
    assert est[0].source == "sensor_present" and est[0].require_comfort
    assert est[20].source == "reported_absence"


def test_zone_filter_and_manual_comfort_priority():
    a = PresenceAbsence(start=NOW - timedelta(hours=1), end=NOW + timedelta(hours=24), zone_ids=["other"])
    assert a.zone_ids == ["other"]
    from akkudoktoreos.prediction.presence import PresenceCorrection

    absence = [PresenceAbsence(start=NOW - timedelta(hours=1), end=NOW + timedelta(hours=24))]
    corr = PresenceCorrection(zone_id="z", kind="comfort", issued_at=NOW - timedelta(minutes=1),
                              valid_until=NOW + timedelta(hours=3))
    now_utc = NOW.astimezone(timezone.utc)
    starts = [now_utc + timedelta(minutes=15 * i) for i in range(8)]
    est = forecast_presence(DATA, measurement_key=KEY, now=now_utc, starts=starts, timezone_name=TZN,
                            settings=PresenceCommonSettings(**LIVE), correction=corr, absences=absence)
    assert all(e.source == "manual_comfort" and e.require_comfort for e in est[:6])


# ---- 4. learning: a reported absence must not distort the weekly pattern ----------------------
def test_reported_absence_days_do_not_enter_the_learned_pattern():
    # Week 3 the household is away all day (including evenings), reported as an absence.
    def away_week(d, day, m):
        return office(d, day, m) or (14 <= d < 19)

    polluted = until(history(29, away_week), NOW)
    window_start = (MONDAY + timedelta(days=14)).astimezone(timezone.utc)
    window_end = (MONDAY + timedelta(days=19)).astimezone(timezone.utc)
    clean = [s for s in polluted if not (window_start <= s.date_time < window_end)]
    report = [PresenceAbsence(start=MONDAY + timedelta(days=14), end=MONDAY + timedelta(days=19))]
    a = run(clean, NOW, **LIVE)
    ignoring = run(polluted, NOW, absences=report, **LIVE)
    naive = run(polluted, NOW, **LIVE)
    assert [e.model_dump() for e in a] == [e.model_dump() for e in ignoring]
    assert [e.model_dump() for e in a] != [e.model_dump() for e in naive]  # the report mattered


# ---- 3. minimum temperature -------------------------------------------------------------------
OCC = ThermalComfortBand(min_temperature_c=20.7, target_temperature_c=21.0, max_temperature_c=24.0)
UNOCC = ThermalComfortBand(min_temperature_c=16.0, target_temperature_c=16.3, max_temperature_c=21.5)


def test_absence_band_minimum_order_and_default():
    assert reported_absence_band(OCC, UNOCC, None) == UNOCC
    low = reported_absence_band(OCC, UNOCC, 12.0)
    assert low.min_temperature_c == 12.0 and low.max_temperature_c == 21.5
    none = reported_absence_band(OCC, None, None)
    assert none.min_temperature_c == DEFAULT_ABSENCE_MINIMUM_C == 14.0
    assert none.min_temperature_c < none.target_temperature_c <= none.max_temperature_c
    assert reported_absence_band(OCC, None, 30.0).min_temperature_c == OCC.min_temperature_c  # never above comfort


def _estimates(kinds):
    t0 = datetime(2026, 11, 14, 14, tzinfo=timezone.utc)
    out = []
    for i, kind in enumerate(kinds):
        comfort = kind in ("comfort", "reported_return")
        out.append(PresenceEstimate(
            date_time=t0 + timedelta(minutes=15 * i), probability=1.0 if comfort else 0.0,
            probability_upper=1.0 if comfort else 0.0, require_comfort=comfort,
            source="history" if kind == "comfort" else kind))
    return out


def test_bands_use_the_absence_minimum_and_warm_up_before_the_return_even_without_zone_band():
    est = _estimates(["reported_absence"] * 8 + ["reported_return"] * 2)
    reported = reported_absence_band(OCC, None, None)
    bands = comfort_bands_from_presence(est, OCC, None, preheat_seconds=3600, reported_absence=reported)
    assert bands[0] == reported  # far from the return: protection minimum only
    assert bands[5].min_temperature_c == reported.min_temperature_c and bands[5].target_temperature_c == OCC.target_temperature_c
    assert bands[8] == OCC and bands[9] == OCC
    # learned reductions keep the occupied band when the zone has no absence band
    learned = comfort_bands_from_presence(_estimates(["history"] * 2), OCC, None, reported_absence=reported)
    assert learned == (OCC, OCC)


def test_default_arguments_keep_the_old_band_conversion():
    est = _estimates(["reported_absence"] * 3 + ["comfort"] * 2)
    old = comfort_bands_from_presence(est, OCC, UNOCC, preheat_seconds=900)
    same = comfort_bands_from_presence(est, OCC, UNOCC, preheat_seconds=900, reported_absence=None)
    assert old == same
    assert comfort_bands_from_presence(est, OCC, None) == tuple(OCC for _ in est)
