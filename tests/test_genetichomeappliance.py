"""Regression test suite for the repaired HomeAppliance module.

TODO: fix this import to match wherever HomeApplianceParameters / HomeAppliance
actually live in the repo.
"""
from unittest.mock import Mock

import numpy as np
import pytest

from akkudoktoreos.config.configabc import CycleTimeWindowSequence, ValueTimeWindow
from akkudoktoreos.devices.genetic.homeappliance import (
    HomeAppliance,
    HomeApplianceParameters,
)
from akkudoktoreos.utils.datetimeutil import to_duration, to_time

# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------

def make_params(**overrides) -> HomeApplianceParameters:
    defaults = dict(
        device_id="dishwasher",
        consumption_wh=2000,
        duration_h=2,
        num_cycles=1,
        min_cycle_gap_h=0,
        time_windows=None,
    )
    defaults.update(overrides)
    return HomeApplianceParameters(**defaults)


def make_appliance(
    prediction_hours: int = 24,
    optimization_hours: int = 24,
    **param_overrides,
) -> HomeAppliance:
    params = make_params(**param_overrides)
    return HomeAppliance(
        parameters=params,
        optimization_hours=optimization_hours,
        prediction_hours=prediction_hours,
    )


def cycle_window(cycle: int, start: str, duration: str) -> CycleTimeWindowSequence:
    """Build a CycleTimeWindowSequence with a single window for one cycle."""
    return CycleTimeWindowSequence(
        windows=[
            ValueTimeWindow(
                start_time=to_time(start),
                duration=to_duration(duration),
                value=float(cycle),
            )
        ]
    )


def mock_cycle_windows(cycle_matrix: dict[int, np.ndarray]) -> Mock:
    """Build a Mock standing in for CycleTimeWindowSequence.

    ``Mock(spec=CycleTimeWindowSequence)`` satisfies both the pydantic
    field-type check on ``HomeApplianceParameters.time_windows`` and any
    isinstance check in the module, without needing real windows/pendulum
    datetimes -- useful for isolating _build_duration_feasibility and the
    scheduling/repair logic from the real cycles_to_matrix() implementation.
    """
    cycle_indices = list(cycle_matrix.keys())
    matrix = np.array([cycle_matrix[c] for c in cycle_indices])
    mock = Mock(spec=CycleTimeWindowSequence)
    mock.cycles_to_matrix = Mock(return_value=(cycle_indices, matrix))
    return mock


# ---------------------------------------------------------------------------
# Setup / defaults
# ---------------------------------------------------------------------------

class TestSetup:
    def test_default_time_windows_created_when_none_given(self):
        appliance = make_appliance(time_windows=None, num_cycles=1)
        assert appliance.parameters.time_windows is not None
        assert isinstance(appliance.parameters.time_windows, CycleTimeWindowSequence)

    def test_default_time_window_value_left_unset(self):
        # A None-valued window is invisible to cycles_to_matrix() and so
        # never masquerades as a real per-cycle window; every remaining
        # cycle instead falls through to the "unconstrained" fallback.
        appliance = make_appliance(time_windows=None, num_cycles=3)
        assert appliance.parameters.time_windows is not None
        windows = appliance.parameters.time_windows.windows
        assert len(windows) == 1
        assert windows[0].value is None

    def test_default_time_window_serializes_without_a_cycle_value(self):
        appliance = make_appliance(time_windows=None, num_cycles=1)
        assert appliance.parameters.time_windows is not None
        dumped = appliance.parameters.time_windows.model_dump()
        assert dumped["windows"][0]["value"] is None

    def test_num_remaining_cycles_initial(self):
        appliance = make_appliance(num_cycles=3)
        assert appliance.num_remaining_cycles == 3

    def test_num_remaining_cycles_never_negative(self):
        appliance = make_appliance(num_cycles=2)
        appliance.completed_cycles = 5
        assert appliance.num_remaining_cycles == 0


# ---------------------------------------------------------------------------
# Allowed-start computation: default (unconstrained) per-cycle windows
# ---------------------------------------------------------------------------

class TestDefaultStartAllowed:
    def test_starts_allowed_up_to_horizon_minus_duration(self):
        appliance = make_appliance(prediction_hours=10, duration_h=3)
        max_start = 10 - 3
        allowed = appliance.start_allowed[0]
        assert allowed[: max_start + 1].all()

    def test_starts_beyond_horizon_minus_duration_forbidden(self):
        appliance = make_appliance(prediction_hours=10, duration_h=3)
        max_start = 10 - 3
        allowed = appliance.start_allowed[0]
        assert not allowed[max_start + 1 :].any()

    def test_start_earliest_and_latest(self):
        appliance = make_appliance(prediction_hours=10, duration_h=3)
        assert appliance.start_earliest[0] == 0
        assert appliance.start_latest[0] == 10 - 3

    def test_each_cycle_gets_its_own_unconstrained_mask(self):
        appliance = make_appliance(prediction_hours=10, duration_h=2, num_cycles=2)
        max_start = 10 - 2
        assert appliance.start_allowed[0][: max_start + 1].all()
        assert appliance.start_allowed[1][: max_start + 1].all()


# ---------------------------------------------------------------------------
# Allowed-start computation: explicit single-cycle window
# ---------------------------------------------------------------------------

class TestExplicitCycleWindow:
    def test_only_hours_inside_window_allowed(self):
        appliance = make_appliance(
            prediction_hours=24,
            duration_h=2,
            num_cycles=1,
            time_windows=cycle_window(0, "10:00", "3 hours"),
        )
        allowed = appliance.start_allowed[0]
        # Window is 10:00-13:00, appliance needs 2h -> valid starts 10, 11.
        assert allowed[10] and allowed[11]
        assert not allowed[9]
        assert not allowed[12]

    def test_earliest_latest_reflect_window(self):
        appliance = make_appliance(
            prediction_hours=24,
            duration_h=2,
            num_cycles=1,
            time_windows=cycle_window(0, "10:00", "3 hours"),
        )
        assert appliance.start_earliest[0] == 10
        assert appliance.start_latest[0] == 11

    def test_window_with_no_valid_start_falls_back(self):
        # Window shorter than the appliance's own duration -> nothing fits.
        appliance = make_appliance(
            prediction_hours=24,
            duration_h=3,
            num_cycles=1,
            time_windows=cycle_window(0, "10:00", "1 hour"),
        )
        allowed = appliance.start_allowed[0]
        assert not allowed.any()
        assert appliance.start_earliest[0] == 0
        assert appliance.start_latest[0] == 24 - 3


# ---------------------------------------------------------------------------
# Allowed-start computation: mocked per-cycle windows (CycleTimeWindowSequence)
# ---------------------------------------------------------------------------

class TestCycleStartAllowed:
    def test_missing_cycle_row_is_unconstrained(self):
        # num_cycles=2 but the mock only provides a window for cycle 0.
        prediction_hours = 10
        duration_h = 2
        steps = np.zeros(prediction_hours)
        steps[2:6] = 1.0
        windows = mock_cycle_windows({0: steps})

        appliance = make_appliance(
            prediction_hours=prediction_hours,
            duration_h=duration_h,
            num_cycles=2,
            time_windows=windows,
        )

        max_start = prediction_hours - duration_h
        # Cycle 1 has no matrix row -> should be allowed everywhere it fits.
        assert appliance.start_allowed[1][: max_start + 1].all()

    def test_duration_feasibility_uses_correct_window(self):
        # Steps 2,3,4,5 are inside the window (1.0); everything else is 0.
        # duration_h=2 -> valid starts are 2, 3, 4 (each 2h block fully inside).
        prediction_hours = 10
        duration_h = 2
        steps = np.zeros(prediction_hours)
        steps[2:6] = 1.0
        windows = mock_cycle_windows({0: steps})

        appliance = make_appliance(
            prediction_hours=prediction_hours,
            duration_h=duration_h,
            num_cycles=1,
            time_windows=windows,
        )

        allowed = appliance.start_allowed[0]
        expected = np.zeros(prediction_hours, dtype=bool)
        expected[2:5] = True  # starts 2, 3, 4
        np.testing.assert_array_equal(allowed, expected)


# ---------------------------------------------------------------------------
# set_completed_cycles
# ---------------------------------------------------------------------------

class TestSetCompletedCycles:
    def test_resets_start_hours_and_load_curve(self):
        appliance = make_appliance(num_cycles=2)
        appliance.start_hours = [1, 5]
        appliance.load_curve[0] = 999

        appliance.set_completed_cycles(1)

        assert appliance.start_hours == []
        assert (appliance.load_curve == 0).all()

    def test_remaining_cycle_indices_updated(self):
        appliance = make_appliance(num_cycles=4)
        appliance.set_completed_cycles(2)
        assert appliance.remaining_cycle_indices == [2, 3]
        assert appliance.num_remaining_cycles == 2

    def test_clamped_to_valid_range(self):
        appliance = make_appliance(num_cycles=3)
        appliance.set_completed_cycles(-5)
        assert appliance.completed_cycles == 0
        appliance.set_completed_cycles(99)
        assert appliance.completed_cycles == 3


# ---------------------------------------------------------------------------
# set_starting_times -- the core scheduling / repair logic
# ---------------------------------------------------------------------------

class TestSetStartingTimes:
    def test_single_cycle_schedule_returns_requested_start(self):
        appliance = make_appliance(prediction_hours=24, duration_h=2, num_cycles=1)
        result = appliance.set_starting_times([5])
        assert result == [5]

    def test_two_cycles_enforce_minimum_gap(self):
        appliance = make_appliance(
            prediction_hours=24, duration_h=2, num_cycles=2, min_cycle_gap_h=1
        )
        # Requested starts overlap; cycle 1 must be pushed to start >= 0+2+1=3.
        result = appliance.set_starting_times([0, 1])
        assert result[0] == 0
        assert result[1] >= 3

    def test_three_cycles_are_all_gap_repaired(self):
        appliance = make_appliance(
            prediction_hours=24, duration_h=1, num_cycles=3, min_cycle_gap_h=0
        )
        result = appliance.set_starting_times([0, 0, 0])
        # Each cycle is 1h with no gap -> expect 0, 1, 2.
        assert result == [0, 1, 2]

    def test_load_curve_reflects_final_start_hours(self):
        appliance = make_appliance(
            prediction_hours=10, duration_h=2, consumption_wh=2000, num_cycles=1
        )
        appliance.set_starting_times([3])
        expected = np.zeros(10)
        expected[3:5] = 1000  # 2000 Wh over 2h
        np.testing.assert_array_equal(appliance.get_load_curve(), expected)

    def test_sorting_preserves_per_cycle_window_alignment(self):
        prediction_hours = 24
        duration_h = 1
        # Cycle 0 only allowed late (hour 20), cycle 1 only allowed early (hour 2).
        steps0 = np.zeros(prediction_hours)
        steps0[20] = 1.0
        steps1 = np.zeros(prediction_hours)
        steps1[2] = 1.0
        windows = mock_cycle_windows({0: steps0, 1: steps1})

        appliance = make_appliance(
            prediction_hours=prediction_hours,
            duration_h=duration_h,
            num_cycles=2,
            time_windows=windows,
        )

        result = appliance.set_starting_times([20, 2])
        # Cycle 0 must land at 20 (its only allowed hour), cycle 1 at 2,
        # regardless of chronological sorting during repair.
        assert 20 in result
        assert 2 in result

    def test_raises_on_wrong_number_of_start_times(self):
        appliance = make_appliance(prediction_hours=24, duration_h=2, num_cycles=2)
        with pytest.raises(ValueError):
            appliance.set_starting_times([5])

    def test_no_remaining_cycles_returns_empty_list(self):
        appliance = make_appliance(prediction_hours=24, duration_h=2, num_cycles=1)
        appliance.completed_cycles = 1
        result = appliance.set_starting_times([])
        assert result == []
        assert (appliance.load_curve == 0).all()


# ---------------------------------------------------------------------------
# Backwards-compatible single-cycle interface
# ---------------------------------------------------------------------------

class TestSetStartingTimeBackCompat:
    def test_single_cycle_wrapper_returns_int(self):
        appliance = make_appliance(prediction_hours=24, duration_h=2, num_cycles=1)
        result = appliance.set_starting_time(5)
        assert isinstance(result, int)
        assert result == 5

    def test_no_remaining_cycles_returns_input_unchanged(self):
        appliance = make_appliance(prediction_hours=24, duration_h=2, num_cycles=1)
        appliance.completed_cycles = 1  # nothing left to schedule
        result = appliance.set_starting_time(7)
        assert result == 7
        assert (appliance.load_curve == 0).all()


# ---------------------------------------------------------------------------
# Load curve utilities
# ---------------------------------------------------------------------------

class TestLoadCurve:
    def test_reset_load_curve_zeros_array(self):
        appliance = make_appliance(prediction_hours=6)
        appliance.load_curve[:] = 42
        appliance.reset_load_curve()
        assert (appliance.load_curve == 0).all()
        assert len(appliance.load_curve) == 6

    def test_get_load_for_hour_valid(self):
        appliance = make_appliance(prediction_hours=6)
        appliance.load_curve[2] = 123.0
        assert appliance.get_load_for_hour(2) == 123.0

    @pytest.mark.parametrize("hour", [-1, 6, 100])
    def test_get_load_for_hour_out_of_range_raises(self, hour):
        appliance = make_appliance(prediction_hours=6)
        with pytest.raises(ValueError):
            appliance.get_load_for_hour(hour)
