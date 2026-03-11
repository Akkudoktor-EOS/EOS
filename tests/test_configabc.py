"""Tests for configabc.TimeWindow and TimeWindowSequence.

Timezone contract under test:

* ``start_time`` is always **naive** (no ``tzinfo``).
* ``date`` is inherently timezone-free (a calendar date).
* ``date_time`` / ``reference_date`` passed to ``contains()``,
  ``earliest_start_time()``, and ``latest_start_time()`` may be
  timezone-aware or naive.
* When a timezone-aware datetime is supplied, ``start_time`` is
  interpreted as wall-clock time **in that timezone** — no tz
  conversion is applied to ``start_time`` itself.
* Constructing a ``TimeWindow`` with an aware ``start_time`` raises
  ``ValidationError``.
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pendulum
import pytest
from pydantic import ValidationError

from akkudoktoreos.config.configabc import TimeWindow
from akkudoktoreos.config.configabc import TimeWindow as _TW_check
from akkudoktoreos.config.configabc import (  # noqa — ensure Time is importable
    TimeWindowSequence,
    ValueTimeWindow,
    ValueTimeWindowSequence,
)
from akkudoktoreos.utils.datetimeutil import Time

# ===========================================================================
# Helpers
# ===========================================================================

def naive_dt(year, month, day, hour=0, minute=0, second=0):
    """Return a truly naive DateTime (no timezone)."""
    return pendulum.instance(
        datetime.datetime(year, month, day, hour, minute, second)
    ).naive()


def aware_dt(year, month, day, hour=0, minute=0, second=0, tz="Europe/Berlin"):
    """Return a timezone-aware pendulum DateTime."""
    return pendulum.datetime(year, month, day, hour, minute, second, tz=tz)


def make_window(start_h, duration_h, **kwargs):
    """Build a TimeWindow with a naive start_time at ``start_h:00``."""
    return TimeWindow(
        start_time=f"{start_h:02d}:00:00",
        duration=f"{duration_h} hours",
        **kwargs,
    )


# ===========================================================================
# Construction / validation
# ===========================================================================

class TestTimeWindowConstruction:
    def test_naive_start_time_accepted(self):
        w = make_window(8, 2)
        assert w.start_time.tzinfo is None

    def test_aware_start_time_stripped_to_naive(self):
        """An aware start_time is silently stripped to naive (to_time may add a tz)."""
        w = TimeWindow(
            start_time=Time(8, 0, 0, tzinfo=pendulum.timezone("Europe/Berlin")),
            duration="2 hours",
        )
        assert w.start_time.tzinfo is None
        assert w.start_time.hour == 8

    def test_duration_string_parsed(self):
        w = make_window(8, 3)
        assert w.duration.total_seconds() == 3 * 3600

    def test_day_of_week_integer_valid(self):
        w = make_window(8, 2, day_of_week=0)
        assert w.day_of_week == 0

    def test_day_of_week_integer_out_of_range(self):
        with pytest.raises(ValidationError):
            make_window(8, 2, day_of_week=7)

    def test_day_of_week_english_string(self):
        w = make_window(8, 2, day_of_week="Monday")
        assert w.day_of_week == 0

    def test_day_of_week_english_string_case_insensitive(self):
        w = make_window(8, 2, day_of_week="friday")
        assert w.day_of_week == 4

    def test_day_of_week_invalid_string(self):
        with pytest.raises(ValidationError, match="Invalid weekday"):
            make_window(8, 2, day_of_week="notaday")

    def test_day_of_week_localized_german(self):
        w = make_window(8, 2, day_of_week="Montag", locale="de")
        assert w.day_of_week == 0


# ===========================================================================
# _window_start_end
# ===========================================================================

class TestWindowStartEnd:
    def test_naive_reference_date(self):
        w = make_window(8, 2)
        ref = naive_dt(2024, 6, 15, 10, 0, 0)
        start, end = w._window_start_end(ref)
        assert start.hour == 8
        assert start.minute == 0
        assert end.hour == 10
        assert end.minute == 0
        assert start.timezone is None

    def test_aware_reference_date_berlin(self):
        w = make_window(8, 2)
        ref = aware_dt(2024, 6, 15, 10, 0, 0, tz="Europe/Berlin")
        start, end = w._window_start_end(ref)
        assert start.hour == 8
        assert end.hour == 10
        assert str(start.timezone) == "Europe/Berlin"

    def test_aware_reference_date_utc(self):
        w = make_window(6, 4)
        ref = aware_dt(2024, 1, 10, 9, 0, 0, tz="UTC")
        start, end = w._window_start_end(ref)
        assert start.hour == 6
        assert end.hour == 10
        assert str(start.timezone) == "UTC"

    def test_aware_reference_date_eastern(self):
        w = make_window(20, 4)
        ref = aware_dt(2024, 6, 15, 21, 0, 0, tz="US/Eastern")
        start, end = w._window_start_end(ref)
        assert start.hour == 20
        assert end.hour == 0   # midnight next day
        assert str(start.timezone) == "US/Eastern"


# ===========================================================================
# contains() — naive datetime
# ===========================================================================

class TestContainsNaive:
    def setup_method(self, method):
        self.w = make_window(8, 2)

    def test_inside_window(self):
        assert self.w.contains(naive_dt(2024, 6, 15, 9, 0, 0))

    def test_at_start(self):
        assert self.w.contains(naive_dt(2024, 6, 15, 8, 0, 0))

    def test_at_end_exclusive(self):
        assert not self.w.contains(naive_dt(2024, 6, 15, 10, 0, 0))

    def test_before_window(self):
        assert not self.w.contains(naive_dt(2024, 6, 15, 7, 59, 59))

    def test_after_window(self):
        assert not self.w.contains(naive_dt(2024, 6, 15, 10, 0, 1))

    def test_with_fitting_duration(self):
        dt = naive_dt(2024, 6, 15, 8, 0, 0)
        assert self.w.contains(dt, duration=pendulum.duration(hours=2))

    def test_with_duration_too_long(self):
        dt = naive_dt(2024, 6, 15, 8, 0, 0)
        assert not self.w.contains(dt, duration=pendulum.duration(hours=3))

    def test_with_duration_starting_late(self):
        dt = naive_dt(2024, 6, 15, 9, 30, 0)
        assert not self.w.contains(dt, duration=pendulum.duration(hours=1))

    def test_with_duration_exactly_fitting_late(self):
        dt = naive_dt(2024, 6, 15, 9, 0, 0)
        assert self.w.contains(dt, duration=pendulum.duration(hours=1))


# ===========================================================================
# contains() — aware datetime
# ===========================================================================

class TestContainsAware:
    def setup_method(self, method):
        self.w = make_window(8, 2)

    def test_inside_window_berlin(self):
        dt = aware_dt(2024, 6, 15, 9, 0, 0, tz="Europe/Berlin")
        assert self.w.contains(dt)

    def test_before_window_berlin(self):
        dt = aware_dt(2024, 6, 15, 7, 30, 0, tz="Europe/Berlin")
        assert not self.w.contains(dt)

    def test_after_window_berlin(self):
        dt = aware_dt(2024, 6, 15, 10, 30, 0, tz="Europe/Berlin")
        assert not self.w.contains(dt)

    def test_start_time_is_local_not_utc(self):
        # 06:00 UTC is before the 08:00 UTC window → outside
        dt_utc = aware_dt(2024, 6, 15, 6, 0, 0, tz="UTC")
        assert not self.w.contains(dt_utc)

        # 08:30 UTC is inside the 08:00–10:00 UTC window
        dt_utc_inside = aware_dt(2024, 6, 15, 8, 30, 0, tz="UTC")
        assert self.w.contains(dt_utc_inside)

    def test_crossing_midnight(self):
        w = make_window(23, 2)
        dt_inside = aware_dt(2024, 6, 15, 23, 30, 0, tz="Europe/Berlin")
        dt_outside = aware_dt(2024, 6, 15, 22, 59, 0, tz="Europe/Berlin")
        assert w.contains(dt_inside)
        assert not w.contains(dt_outside)

    def test_same_wall_clock_different_tz(self):
        """Naive start_time means 12:00 wall clock in *whatever* tz is passed."""
        w = make_window(12, 2)
        dt_berlin = aware_dt(2024, 6, 15, 13, 0, 0, tz="Europe/Berlin")
        dt_ny = aware_dt(2024, 6, 15, 13, 0, 0, tz="US/Eastern")
        assert w.contains(dt_berlin)
        assert w.contains(dt_ny)


# ===========================================================================
# contains() — day_of_week and date constraints
# ===========================================================================

class TestContainsConstraints:
    def test_day_of_week_match_naive(self):
        # 2024-06-17 is a Monday (day_of_week == 0)
        w = make_window(8, 4, day_of_week=0)
        assert w.contains(naive_dt(2024, 6, 17, 9, 0, 0))

    def test_day_of_week_no_match_naive(self):
        w = make_window(8, 4, day_of_week=0)
        # 2024-06-18 is a Tuesday
        assert not w.contains(naive_dt(2024, 6, 18, 9, 0, 0))

    def test_day_of_week_match_aware(self):
        w = make_window(8, 4, day_of_week=0)
        dt = aware_dt(2024, 6, 17, 9, 0, 0, tz="Europe/Berlin")
        assert w.contains(dt)

    def test_date_constraint_match(self):
        w = make_window(8, 4, date=pendulum.date(2024, 6, 17))
        assert w.contains(naive_dt(2024, 6, 17, 9, 0, 0))

    def test_date_constraint_no_match(self):
        w = make_window(8, 4, date=pendulum.date(2024, 6, 17))
        assert not w.contains(naive_dt(2024, 6, 18, 9, 0, 0))

    def test_date_constraint_aware_datetime(self):
        w = make_window(8, 4, date=pendulum.date(2024, 6, 17))
        dt = aware_dt(2024, 6, 17, 9, 0, 0, tz="US/Eastern")
        assert w.contains(dt)

    def test_date_and_day_of_week_both_must_hold(self):
        # 2024-06-18 is Tuesday; day_of_week=0 (Monday) → False even on matching date
        w = make_window(8, 4, date=pendulum.date(2024, 6, 18), day_of_week=0)
        assert not w.contains(naive_dt(2024, 6, 18, 9, 0, 0))


# ===========================================================================
# earliest_start_time / latest_start_time
# ===========================================================================

class TestStartTimes:
    def setup_method(self, method):
        self.w = make_window(8, 4)  # 08:00–12:00

    def test_earliest_naive(self):
        ref = naive_dt(2024, 6, 15)
        result = self.w.earliest_start_time(pendulum.duration(hours=2), reference_date=ref)
        assert result is not None
        assert result.hour == 8

    def test_latest_naive(self):
        ref = naive_dt(2024, 6, 15)
        result = self.w.latest_start_time(pendulum.duration(hours=2), reference_date=ref)
        assert result is not None
        assert result.hour == 10  # 12:00 - 2h = 10:00

    def test_earliest_aware_berlin(self):
        ref = aware_dt(2024, 6, 15, tz="Europe/Berlin")
        result = self.w.earliest_start_time(pendulum.duration(hours=1), reference_date=ref)
        assert result is not None
        assert result.hour == 8
        assert str(result.timezone) == "Europe/Berlin"

    def test_latest_aware_berlin(self):
        ref = aware_dt(2024, 6, 15, tz="Europe/Berlin")
        result = self.w.latest_start_time(pendulum.duration(hours=1), reference_date=ref)
        assert result is not None
        assert result.hour == 11
        assert str(result.timezone) == "Europe/Berlin"

    def test_duration_too_long_returns_none(self):
        ref = naive_dt(2024, 6, 15)
        result = self.w.earliest_start_time(pendulum.duration(hours=5), reference_date=ref)
        assert result is None

    def test_wrong_day_of_week_returns_none(self):
        w = make_window(8, 4, day_of_week=0)  # Monday only
        ref = naive_dt(2024, 6, 18)  # Tuesday
        assert w.earliest_start_time(pendulum.duration(hours=1), reference_date=ref) is None

    def test_wrong_date_returns_none(self):
        w = make_window(8, 4, date=pendulum.date(2024, 6, 17))
        ref = naive_dt(2024, 6, 18)
        assert w.earliest_start_time(pendulum.duration(hours=1), reference_date=ref) is None

    def test_earliest_aware_utc(self):
        ref = aware_dt(2024, 6, 15, tz="UTC")
        result = self.w.earliest_start_time(pendulum.duration(hours=2), reference_date=ref)
        assert result is not None
        assert result.hour == 8
        assert str(result.timezone) == "UTC"

    def test_latest_equals_window_end_minus_duration(self):
        ref = naive_dt(2024, 6, 15)
        result = self.w.latest_start_time(pendulum.duration(hours=4), reference_date=ref)
        assert result is not None
        assert result.hour == 8  # exactly at window start when duration == window size

    def test_latest_duration_leaves_no_room(self):
        # duration > window → None
        ref = naive_dt(2024, 6, 15)
        result = self.w.latest_start_time(pendulum.duration(hours=5), reference_date=ref)
        assert result is None


# ===========================================================================
# can_fit_duration / available_duration
# ===========================================================================

class TestFitAndAvailable:
    def setup_method(self, method):
        self.w = make_window(8, 3)  # 08:00–11:00

    def test_can_fit_exact(self):
        assert self.w.can_fit_duration(pendulum.duration(hours=3), naive_dt(2024, 6, 15))

    def test_can_fit_shorter(self):
        assert self.w.can_fit_duration(pendulum.duration(hours=1), naive_dt(2024, 6, 15))

    def test_cannot_fit_longer(self):
        assert not self.w.can_fit_duration(pendulum.duration(hours=4), naive_dt(2024, 6, 15))

    def test_available_duration_no_constraint(self):
        result = self.w.available_duration(naive_dt(2024, 6, 15))
        assert result == pendulum.duration(hours=3)

    def test_available_duration_wrong_date(self):
        w = make_window(8, 3, date=pendulum.date(2024, 6, 17))
        result = w.available_duration(naive_dt(2024, 6, 15))
        assert result is None


# ===========================================================================
# TimeWindowSequence
# ===========================================================================

class TestTimeWindowSequence:
    def setup_method(self, method):
        self.seq = TimeWindowSequence(
            windows=[
                make_window(8, 2),   # 08:00–10:00
                make_window(14, 3),  # 14:00–17:00
            ]
        )

    def test_contains_first_window(self):
        assert self.seq.contains(naive_dt(2024, 6, 15, 9, 0, 0))

    def test_contains_second_window(self):
        assert self.seq.contains(naive_dt(2024, 6, 15, 15, 0, 0))

    def test_contains_gap_between_windows(self):
        assert not self.seq.contains(naive_dt(2024, 6, 15, 12, 0, 0))

    def test_contains_with_duration_fits_second(self):
        dt = naive_dt(2024, 6, 15, 14, 0, 0)
        assert self.seq.contains(dt, duration=pendulum.duration(hours=2))

    def test_contains_aware(self):
        dt = aware_dt(2024, 6, 15, 9, 0, 0, tz="Europe/Berlin")
        assert self.seq.contains(dt)

    def test_earliest_start_time(self):
        ref = naive_dt(2024, 6, 15)
        result = self.seq.earliest_start_time(pendulum.duration(hours=1), ref)
        assert result is not None
        assert result.hour == 8

    def test_latest_start_time(self):
        ref = naive_dt(2024, 6, 15)
        result = self.seq.latest_start_time(pendulum.duration(hours=1), ref)
        assert result is not None
        assert result.hour == 16  # 17:00 - 1h

    def test_available_duration_sum(self):
        ref = naive_dt(2024, 6, 15)
        result = self.seq.available_duration(ref)
        assert result == pendulum.duration(hours=5)

    def test_empty_sequence_contains_false(self):
        seq = TimeWindowSequence()
        assert not seq.contains(naive_dt(2024, 6, 15, 9, 0, 0))

    def test_empty_sequence_earliest_none(self):
        seq = TimeWindowSequence()
        assert seq.earliest_start_time(pendulum.duration(hours=1), naive_dt(2024, 6, 15)) is None

    def test_empty_sequence_available_none(self):
        seq = TimeWindowSequence()
        assert seq.available_duration(naive_dt(2024, 6, 15)) is None

    def test_get_applicable_windows(self):
        ref = naive_dt(2024, 6, 15)
        applicable = self.seq.get_applicable_windows(ref)
        assert len(applicable) == 2

    def test_find_windows_for_duration_fits_both(self):
        ref = naive_dt(2024, 6, 15)
        fits = self.seq.find_windows_for_duration(pendulum.duration(hours=1), ref)
        assert len(fits) == 2

    def test_find_windows_for_duration_fits_only_second(self):
        ref = naive_dt(2024, 6, 15)
        fits = self.seq.find_windows_for_duration(pendulum.duration(hours=3), ref)
        assert len(fits) == 1
        assert fits[0].start_time.hour == 14

    def test_sort_windows_by_start_time(self):
        seq = TimeWindowSequence(
            windows=[make_window(14, 1), make_window(8, 1)]
        )
        ref = naive_dt(2024, 6, 15)
        seq.sort_windows_by_start_time(ref)
        assert seq.windows[0].start_time.hour == 8
        assert seq.windows[1].start_time.hour == 14

    def test_add_and_remove_window(self):
        seq = TimeWindowSequence()
        w = make_window(10, 1)
        seq.add_window(w)
        assert len(seq) == 1
        removed = seq.remove_window(0)
        assert removed == w
        assert len(seq) == 0

    def test_remove_from_empty_raises(self):
        seq = TimeWindowSequence()
        with pytest.raises(IndexError):
            seq.remove_window(0)

    def test_get_all_possible_start_times(self):
        ref = naive_dt(2024, 6, 15)
        result = self.seq.get_all_possible_start_times(pendulum.duration(hours=1), ref)
        assert len(result) == 2
        earliest_hours = sorted(e.hour for e, _, _ in result)
        assert earliest_hours == [8, 14]

    def test_iter_and_len_and_getitem(self):
        assert len(self.seq) == 2
        windows = list(self.seq)
        assert len(windows) == 2
        assert self.seq[0].start_time.hour == 8


# ===========================================================================
# ValueTimeWindow / ValueTimeWindowSequence
# ===========================================================================

class TestValueTimeWindow:
    def test_value_stored(self):
        w = ValueTimeWindow(start_time="08:00:00", duration="2 hours", value=0.288)
        assert w.value == pytest.approx(0.288)

    def test_value_default_none(self):
        w = ValueTimeWindow(start_time="08:00:00", duration="2 hours")
        assert w.value is None

    def test_inherits_aware_start_time_stripped(self):
        """ValueTimeWindow inherits the strip-to-naive behaviour from TimeWindow."""
        w = ValueTimeWindow(
            start_time=Time(8, 0, 0, tzinfo=pendulum.timezone("UTC")),
            duration="2 hours",
            value=0.1,
        )
        assert w.start_time.tzinfo is None
        assert w.start_time.hour == 8


class TestValueTimeWindowSequence:
    def setup_method(self, method):
        self.seq = ValueTimeWindowSequence(
            windows=[
                ValueTimeWindow(start_time="08:00:00", duration="4 hours", value=0.25),
                ValueTimeWindow(start_time="18:00:00", duration="4 hours", value=0.35),
            ]
        )

    def test_get_value_morning(self):
        dt = naive_dt(2024, 6, 15, 9, 0, 0)
        assert self.seq.get_value_for_datetime(dt) == pytest.approx(0.25)

    def test_get_value_evening(self):
        dt = naive_dt(2024, 6, 15, 19, 0, 0)
        assert self.seq.get_value_for_datetime(dt) == pytest.approx(0.35)

    def test_get_value_outside_all_windows(self):
        dt = naive_dt(2024, 6, 15, 13, 0, 0)
        assert self.seq.get_value_for_datetime(dt) == pytest.approx(0.0)

    def test_get_value_none_value_returns_zero(self):
        seq = ValueTimeWindowSequence(
            windows=[ValueTimeWindow(start_time="08:00:00", duration="4 hours", value=None)]
        )
        assert seq.get_value_for_datetime(naive_dt(2024, 6, 15, 9, 0, 0)) == pytest.approx(0.0)

    def test_get_value_aware_datetime(self):
        dt = aware_dt(2024, 6, 15, 9, 0, 0, tz="Europe/Berlin")
        assert self.seq.get_value_for_datetime(dt) == pytest.approx(0.25)


# ===========================================================================
# TimeWindowSequence.to_array
# ===========================================================================

class TestTimeWindowSequenceToArray:
    """Tests for TimeWindowSequence.to_array.

    Window layout used throughout:
        win1: 08:00–10:00  (2 h)
        win2: 14:00–17:00  (3 h)

    Grid step = 1 hour unless stated otherwise.
    """

    def setup_method(self, method):
        self.seq = TimeWindowSequence(
            windows=[
                make_window(8, 2),   # 08:00–10:00
                make_window(14, 3),  # 14:00–17:00
            ]
        )

    # ------------------------------------------------------------------
    # basic correctness
    # ------------------------------------------------------------------

    def test_basic_1h_steps_naive(self):
        start = naive_dt(2024, 6, 15, 0)
        end   = naive_dt(2024, 6, 15, 0).add(hours=24)
        arr = self.seq.to_array(start, end, pendulum.duration(hours=1))
        assert arr.shape == (24,)
        # Window 1: hours 8, 9
        assert arr[8] == pytest.approx(1.0)
        assert arr[9] == pytest.approx(1.0)
        assert arr[10] == pytest.approx(0.0)
        # Window 2: hours 14, 15, 16
        assert arr[14] == pytest.approx(1.0)
        assert arr[15] == pytest.approx(1.0)
        assert arr[16] == pytest.approx(1.0)
        assert arr[17] == pytest.approx(0.0)
        # Gap between windows
        assert arr[12] == pytest.approx(0.0)

    def test_basic_1h_steps_aware_berlin(self):
        start = aware_dt(2024, 6, 15, 0, tz="Europe/Berlin")
        end   = aware_dt(2024, 6, 16, 0, tz="Europe/Berlin")
        arr = self.seq.to_array(start, end, pendulum.duration(hours=1))
        assert arr.shape == (24,)
        assert arr[8] == pytest.approx(1.0)
        assert arr[9] == pytest.approx(1.0)
        assert arr[10] == pytest.approx(0.0)
        assert arr[14] == pytest.approx(1.0)
        assert arr[16] == pytest.approx(1.0)
        assert arr[17] == pytest.approx(0.0)

    def test_outside_all_windows_all_zeros(self):
        # Only 2 hours at midnight — no overlap with any window
        start = naive_dt(2024, 6, 15, 0)
        end   = naive_dt(2024, 6, 15, 2)
        arr = self.seq.to_array(start, end, pendulum.duration(hours=1))
        assert np.all(arr == 0.0)

    def test_inside_one_window_all_ones(self):
        # Entirely inside window 1 (08:00–10:00)
        start = naive_dt(2024, 6, 15, 8)
        end   = naive_dt(2024, 6, 15, 10)
        arr = self.seq.to_array(start, end, pendulum.duration(hours=1))
        assert np.all(arr == 1.0)

    def test_dtype_is_float64(self):
        start = naive_dt(2024, 6, 15, 0)
        end   = naive_dt(2024, 6, 15, 4)
        arr = self.seq.to_array(start, end, pendulum.duration(hours=1))
        assert arr.dtype == np.float64

    def test_end_is_exclusive(self):
        # end == window start → 0 steps inside
        start = naive_dt(2024, 6, 15, 6)
        end   = naive_dt(2024, 6, 15, 8)   # exclusive — 08:00 itself not emitted
        arr = self.seq.to_array(start, end, pendulum.duration(hours=1))
        assert arr.shape == (2,)
        assert np.all(arr == 0.0)

    # ------------------------------------------------------------------
    # sub-hour steps
    # ------------------------------------------------------------------

    def test_30min_steps(self):
        start = naive_dt(2024, 6, 15, 8)
        end   = naive_dt(2024, 6, 15, 10)
        arr = self.seq.to_array(start, end, pendulum.duration(minutes=30))
        # Steps: 08:00, 08:30 → both inside [08:00, 10:00)
        assert arr.shape == (4,)
        assert np.all(arr == 1.0)

    def test_15min_steps_boundary(self):
        # Steps at 09:45, 10:00, 10:15; only 09:45 inside window
        start = naive_dt(2024, 6, 15, 9, 45)
        end   = naive_dt(2024, 6, 15, 10, 30)
        arr = self.seq.to_array(start, end, pendulum.duration(minutes=15))
        # align_to_interval=True floors to interval boundary
        # interval=15 min; 09:45 is already on a 15-min boundary
        assert arr[0] == pytest.approx(1.0)   # 09:45 inside win1
        assert arr[1] == pytest.approx(0.0)   # 10:00 outside (exclusive end)

    # ------------------------------------------------------------------
    # align_to_interval
    # ------------------------------------------------------------------

    def test_align_to_interval_false_preserves_start(self):
        # Start at 08:10 — not on a whole-hour boundary
        start = naive_dt(2024, 6, 15, 8, 10)
        end   = naive_dt(2024, 6, 15, 10, 10)
        arr = self.seq.to_array(
            start, end, pendulum.duration(hours=1), align_to_interval=False
        )
        # Steps: 08:10 (inside win1), 09:10 (inside win1), 10:10 (outside)
        assert arr.shape == (2,)
        assert arr[0] == pytest.approx(1.0)
        assert arr[1] == pytest.approx(1.0)

    def test_align_to_interval_true_floors_start(self):
        # Start at 08:10; floored to 08:00 with 1h interval
        start = naive_dt(2024, 6, 15, 8, 10)
        end   = naive_dt(2024, 6, 15, 10, 10)
        arr = self.seq.to_array(
            start, end, pendulum.duration(hours=1), align_to_interval=True
        )
        # After flooring: steps 08:00, 09:00, 10:00 → 3 steps
        assert arr.shape == (3,)
        assert arr[0] == pytest.approx(1.0)   # 08:00
        assert arr[1] == pytest.approx(1.0)   # 09:00
        assert arr[2] == pytest.approx(0.0)   # 10:00

    # ------------------------------------------------------------------
    # boundary validation
    # ------------------------------------------------------------------

    def test_unsupported_boundary_raises(self):
        start = naive_dt(2024, 6, 15, 0)
        end   = naive_dt(2024, 6, 15, 4)
        with pytest.raises(ValueError, match="boundary"):
            self.seq.to_array(start, end, pendulum.duration(hours=1), boundary="strict")

    # ------------------------------------------------------------------
    # dropna (no effect for binary windows — accepted for compat)
    # ------------------------------------------------------------------

    def test_dropna_true_no_effect(self):
        start = naive_dt(2024, 6, 15, 0)
        end   = naive_dt(2024, 6, 15, 4)
        arr_t = self.seq.to_array(start, end, pendulum.duration(hours=1), dropna=True)
        arr_f = self.seq.to_array(start, end, pendulum.duration(hours=1), dropna=False)
        np.testing.assert_array_equal(arr_t, arr_f)

    # ------------------------------------------------------------------
    # empty sequence
    # ------------------------------------------------------------------

    def test_empty_sequence_all_zeros(self):
        seq = TimeWindowSequence()
        start = naive_dt(2024, 6, 15, 0)
        end   = naive_dt(2024, 6, 15, 4)
        arr = seq.to_array(start, end, pendulum.duration(hours=1))
        assert arr.shape == (4,)
        assert np.all(arr == 0.0)

    # ------------------------------------------------------------------
    # day_of_week and date constraints propagate
    # ------------------------------------------------------------------

    def test_day_of_week_constraint_respected(self):
        # Monday-only window; 2024-06-17 is Monday, 2024-06-18 is Tuesday
        seq = TimeWindowSequence(windows=[make_window(8, 2, day_of_week=0)])
        monday_start = naive_dt(2024, 6, 17, 7)
        tuesday_start = naive_dt(2024, 6, 18, 7)
        end_offset = pendulum.duration(hours=4)

        arr_mon = seq.to_array(monday_start, monday_start.add(hours=4),
                               pendulum.duration(hours=1))
        arr_tue = seq.to_array(tuesday_start, tuesday_start.add(hours=4),
                               pendulum.duration(hours=1))

        assert arr_mon[1] == pytest.approx(1.0)   # 08:00 Monday — inside
        assert np.all(arr_tue == 0.0)              # Tuesday — all outside


# ===========================================================================
# ValueTimeWindowSequence.to_array
# ===========================================================================

class TestValueTimeWindowSequenceToArray:
    """Tests for ValueTimeWindowSequence.to_array.

    Window layout:
        win1: 08:00–12:00  value=0.25
        win2: 18:00–22:00  value=0.35
    """

    def setup_method(self, method):
        self.seq = ValueTimeWindowSequence(
            windows=[
                ValueTimeWindow(start_time="08:00:00", duration="4 hours", value=0.25),
                ValueTimeWindow(start_time="18:00:00", duration="4 hours", value=0.35),
            ]
        )

    # ------------------------------------------------------------------
    # basic correctness
    # ------------------------------------------------------------------

    def test_basic_1h_steps_values(self):
        start = naive_dt(2024, 6, 15, 0)
        end   = naive_dt(2024, 6, 16, 0)
        arr = self.seq.to_array(start, end, pendulum.duration(hours=1))
        assert arr.shape == (24,)
        # win1: hours 8–11
        assert arr[8]  == pytest.approx(0.25)
        assert arr[11] == pytest.approx(0.25)
        assert arr[12] == pytest.approx(0.0)
        # win2: hours 18–21
        assert arr[18] == pytest.approx(0.35)
        assert arr[21] == pytest.approx(0.35)
        assert arr[22] == pytest.approx(0.0)
        # Gap
        assert arr[0]  == pytest.approx(0.0)
        assert arr[14] == pytest.approx(0.0)

    def test_dtype_is_float64(self):
        start = naive_dt(2024, 6, 15, 0)
        end   = naive_dt(2024, 6, 15, 4)
        arr = self.seq.to_array(start, end, pendulum.duration(hours=1))
        assert arr.dtype == np.float64

    def test_zero_outside_all_windows(self):
        start = naive_dt(2024, 6, 15, 12)
        end   = naive_dt(2024, 6, 15, 18)
        arr = self.seq.to_array(start, end, pendulum.duration(hours=1))
        assert np.all(arr == 0.0)

    def test_aware_datetime_berlin(self):
        start = aware_dt(2024, 6, 15, 0, tz="Europe/Berlin")
        end   = aware_dt(2024, 6, 16, 0, tz="Europe/Berlin")
        arr = self.seq.to_array(start, end, pendulum.duration(hours=1))
        assert arr.shape == (24,)
        assert arr[8]  == pytest.approx(0.25)
        assert arr[18] == pytest.approx(0.35)
        assert arr[7]  == pytest.approx(0.0)

    # ------------------------------------------------------------------
    # dropna semantics
    # ------------------------------------------------------------------

    def test_dropna_false_none_value_emits_nan(self):
        seq = ValueTimeWindowSequence(
            windows=[
                ValueTimeWindow(start_time="08:00:00", duration="2 hours", value=None),
                ValueTimeWindow(start_time="12:00:00", duration="2 hours", value=0.5),
            ]
        )
        start = naive_dt(2024, 6, 15, 8)
        end   = naive_dt(2024, 6, 15, 15)
        arr = seq.to_array(start, end, pendulum.duration(hours=1), dropna=False)
        # Steps: 08, 09 (nan), 10 (0), 11 (0), 12 (0.5), 13 (0.5), 14 (0)
        assert arr.shape == (7,)
        assert np.isnan(arr[0])
        assert np.isnan(arr[1])
        assert arr[2] == pytest.approx(0.0)
        assert arr[4] == pytest.approx(0.5)

    def test_dropna_true_none_value_step_omitted(self):
        seq = ValueTimeWindowSequence(
            windows=[
                ValueTimeWindow(start_time="08:00:00", duration="2 hours", value=None),
                ValueTimeWindow(start_time="12:00:00", duration="2 hours", value=0.5),
            ]
        )
        start = naive_dt(2024, 6, 15, 8)
        end   = naive_dt(2024, 6, 15, 15)
        arr = seq.to_array(start, end, pendulum.duration(hours=1), dropna=True)
        # 08 and 09 dropped (None value), remaining 5 steps: 10,11,12,13,14
        assert arr.shape == (5,)
        assert arr[0] == pytest.approx(0.0)  # 10:00
        assert arr[1] == pytest.approx(0.0)  # 11:00
        assert arr[2] == pytest.approx(0.5)  # 12:00
        assert arr[3] == pytest.approx(0.5)  # 13:00
        assert arr[4] == pytest.approx(0.0)  # 14:00

    def test_dropna_no_none_values_same_result(self):
        start = naive_dt(2024, 6, 15, 0)
        end   = naive_dt(2024, 6, 15, 6)
        arr_t = self.seq.to_array(start, end, pendulum.duration(hours=1), dropna=True)
        arr_f = self.seq.to_array(start, end, pendulum.duration(hours=1), dropna=False)
        np.testing.assert_array_equal(arr_t, arr_f)

    # ------------------------------------------------------------------
    # align_to_interval and boundary
    # ------------------------------------------------------------------

    def test_align_to_interval_false(self):
        # Start at 08:30 — between steps
        start = naive_dt(2024, 6, 15, 8, 30)
        end   = naive_dt(2024, 6, 15, 12, 30)
        arr = self.seq.to_array(
            start, end, pendulum.duration(hours=1), align_to_interval=False
        )
        # Steps: 08:30, 09:30, 10:30, 11:30 → all inside win1 [08:00–12:00)
        assert arr.shape == (4,)
        assert np.all(arr == pytest.approx(0.25))

    def test_unsupported_boundary_raises(self):
        start = naive_dt(2024, 6, 15, 0)
        end   = naive_dt(2024, 6, 15, 4)
        with pytest.raises(ValueError, match="boundary"):
            self.seq.to_array(start, end, pendulum.duration(hours=1), boundary="inner")

    # ------------------------------------------------------------------
    # empty sequence
    # ------------------------------------------------------------------

    def test_empty_sequence_all_zeros(self):
        seq = ValueTimeWindowSequence()
        start = naive_dt(2024, 6, 15, 0)
        end   = naive_dt(2024, 6, 15, 4)
        arr = seq.to_array(start, end, pendulum.duration(hours=1))
        assert arr.shape == (4,)
        assert np.all(arr == 0.0)

    # ------------------------------------------------------------------
    # overlapping windows — first match wins
    # ------------------------------------------------------------------

    def test_overlapping_windows_first_wins(self):
        seq = ValueTimeWindowSequence(
            windows=[
                ValueTimeWindow(start_time="08:00:00", duration="4 hours", value=0.10),
                ValueTimeWindow(start_time="09:00:00", duration="4 hours", value=0.99),
            ]
        )
        start = naive_dt(2024, 6, 15, 9)
        end   = naive_dt(2024, 6, 15, 11)
        arr = seq.to_array(start, end, pendulum.duration(hours=1))
        # 09:00 and 10:00 are in both windows; first (0.10) must win
        assert arr[0] == pytest.approx(0.10)
        assert arr[1] == pytest.approx(0.10)


# ===========================================================================
# align_to_interval — timezone-invariance
#
# These tests reproduce the bug that existed before the wall-clock floor fix.
# The old epoch-arithmetic implementation gave wrong results when the machine's
# local timezone was non-UTC:
#   - For naive datetimes, pendulum.instance() attached the *local* timezone,
#     so subtracting a UTC epoch shifted the floored start.
#   - For aware datetimes, subtracting a UTC epoch converted to UTC first,
#     then epoch.add() returned a UTC datetime instead of preserving the
#     original timezone.
#
# The `set_other_timezone` fixture (from conftest.py) temporarily changes
# pendulum's local timezone via pendulum.set_local_timezone() and restores it
# after the test. Calling it with no argument picks a non-UTC default
# ("Atlantic/Canary" or "Asia/Singapore"); calling it with "UTC" sets UTC.
#
# Each scenario runs two tests:
#   _utc   — local tz = UTC  (passes even with the old code)
#   _nonUTC — local tz = non-UTC  (would have FAILED with the old code)
# ===========================================================================


class TestAlignToIntervalTimezoneInvariance:
    """Verify align_to_interval produces identical results regardless of
    the machine's local timezone.

    Tests are paired: ``_utc`` sets local tz to UTC, ``_non_utc`` sets it to
    a non-UTC zone via ``set_other_timezone()``.  The pair must produce the
    same array — any divergence indicates a timezone-dependent bug.
    """

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tws_naive():
        """TimeWindowSequence with one window 08:00–10:00."""
        return TimeWindowSequence(windows=[make_window(8, 2)])

    @staticmethod
    def _tws_naive_start():
        return naive_dt(2024, 6, 15, 8, 10)

    @staticmethod
    def _tws_naive_end():
        return naive_dt(2024, 6, 15, 10, 10)

    # ------------------------------------------------------------------
    # TimeWindowSequence — naive datetime, 1-hour steps
    # floor 08:10 → 08:00; expect steps 08:00(1), 09:00(1), 10:00(0)
    # ------------------------------------------------------------------

    def test_tws_naive_floor_utc(self, set_other_timezone):
        set_other_timezone("UTC")
        arr = self._tws_naive().to_array(
            self._tws_naive_start(), self._tws_naive_end(),
            pendulum.duration(hours=1), align_to_interval=True,
        )
        assert arr.shape == (3,)
        assert arr[0] == pytest.approx(1.0)
        assert arr[1] == pytest.approx(1.0)
        assert arr[2] == pytest.approx(0.0)

    def test_tws_naive_floor_non_utc(self, set_other_timezone):
        set_other_timezone()
        arr = self._tws_naive().to_array(
            self._tws_naive_start(), self._tws_naive_end(),
            pendulum.duration(hours=1), align_to_interval=True,
        )
        assert arr.shape == (3,)
        assert arr[0] == pytest.approx(1.0)
        assert arr[1] == pytest.approx(1.0)
        assert arr[2] == pytest.approx(0.0)

    # ------------------------------------------------------------------
    # TimeWindowSequence — naive datetime, 30-min steps
    # floor 08:10 → 08:00; expect steps 08:00(1), 08:30(1), 09:00(1), 09:30(1), 10:00(0)
    # ------------------------------------------------------------------

    def test_tws_naive_30min_floor_utc(self, set_other_timezone):
        set_other_timezone("UTC")
        arr = self._tws_naive().to_array(
            self._tws_naive_start(), self._tws_naive_end(),
            pendulum.duration(minutes=30), align_to_interval=True,
        )
        assert arr.shape == (5,)
        assert np.all(arr[:4] == pytest.approx(1.0))
        assert arr[4] == pytest.approx(0.0)

    def test_tws_naive_30min_floor_non_utc(self, set_other_timezone):
        set_other_timezone()
        arr = self._tws_naive().to_array(
            self._tws_naive_start(), self._tws_naive_end(),
            pendulum.duration(minutes=30), align_to_interval=True,
        )
        assert arr.shape == (5,)
        assert np.all(arr[:4] == pytest.approx(1.0))
        assert arr[4] == pytest.approx(0.0)

    # ------------------------------------------------------------------
    # TimeWindowSequence — aware datetime (Europe/Berlin), 1-hour steps
    # floor 08:10 Berlin → 08:00 Berlin; timezone must be preserved
    # ------------------------------------------------------------------

    def test_tws_aware_floor_utc(self, set_other_timezone):
        set_other_timezone("UTC")
        seq = self._tws_naive()
        start = aware_dt(2024, 6, 15, 8, 10, tz="Europe/Berlin")
        end   = aware_dt(2024, 6, 15, 10, 10, tz="Europe/Berlin")
        arr = seq.to_array(start, end, pendulum.duration(hours=1), align_to_interval=True)
        assert arr.shape == (3,)
        assert arr[0] == pytest.approx(1.0)
        assert arr[1] == pytest.approx(1.0)
        assert arr[2] == pytest.approx(0.0)

    def test_tws_aware_floor_non_utc(self, set_other_timezone):
        set_other_timezone()
        seq = self._tws_naive()
        start = aware_dt(2024, 6, 15, 8, 10, tz="Europe/Berlin")
        end   = aware_dt(2024, 6, 15, 10, 10, tz="Europe/Berlin")
        arr = seq.to_array(start, end, pendulum.duration(hours=1), align_to_interval=True)
        assert arr.shape == (3,)
        assert arr[0] == pytest.approx(1.0)
        assert arr[1] == pytest.approx(1.0)
        assert arr[2] == pytest.approx(0.0)

    # ------------------------------------------------------------------
    # ValueTimeWindowSequence — naive datetime, 1-hour steps
    # floor 08:10 → 08:00; values 0.25 at 08:00, 09:00; 0.0 at 10:00
    # ------------------------------------------------------------------

    def test_vtws_naive_floor_utc(self, set_other_timezone):
        set_other_timezone("UTC")
        seq = ValueTimeWindowSequence(windows=[
            ValueTimeWindow(start_time="08:00:00", duration="2 hours", value=0.25)
        ])
        start = naive_dt(2024, 6, 15, 8, 10)
        end   = naive_dt(2024, 6, 15, 10, 10)
        arr = seq.to_array(start, end, pendulum.duration(hours=1), align_to_interval=True)
        assert arr.shape == (3,)
        assert arr[0] == pytest.approx(0.25)
        assert arr[1] == pytest.approx(0.25)
        assert arr[2] == pytest.approx(0.0)

    def test_vtws_naive_floor_non_utc(self, set_other_timezone):
        set_other_timezone()
        seq = ValueTimeWindowSequence(windows=[
            ValueTimeWindow(start_time="08:00:00", duration="2 hours", value=0.25)
        ])
        start = naive_dt(2024, 6, 15, 8, 10)
        end   = naive_dt(2024, 6, 15, 10, 10)
        arr = seq.to_array(start, end, pendulum.duration(hours=1), align_to_interval=True)
        assert arr.shape == (3,)
        assert arr[0] == pytest.approx(0.25)
        assert arr[1] == pytest.approx(0.25)
        assert arr[2] == pytest.approx(0.0)
