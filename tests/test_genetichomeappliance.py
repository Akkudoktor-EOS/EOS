"""Regression test suite for the repaired GENETIC home appliance simulation."""
from typing import Any

import numpy as np
import pendulum
import pytest

from akkudoktoreos.optimization.genetic.devices.genetichomeappliance import (
    GeneticHomeAppliance,
)
from akkudoktoreos.optimization.genetic.geneticrequest import (
    GeneticHomeApplianceRequest,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------

def make_params(**overrides) -> GeneticHomeApplianceRequest:
    defaults: dict[str, Any] = dict(
        device_id="dishwasher",
        consumption_wh=2000,
        duration_h=2,
        num_cycles=1,
        min_cycle_gap_h=0,
        time_windows=None,
    )
    defaults.update(overrides)
    return GeneticHomeApplianceRequest(**defaults)


def make_appliance(
    prediction_hours: int = 24,
    optimization_hours: int = 24,
    **param_overrides,
) -> GeneticHomeAppliance:
    """Build the simulated appliance from a request."""
    params = make_params(**param_overrides)
    return GeneticHomeAppliance(
        parameters=params.to_parameters(),
        optimization_hours=optimization_hours,
        prediction_hours=prediction_hours,
    )


# ---------------------------------------------------------------------------
# Setup / defaults
# ---------------------------------------------------------------------------

class TestSetup:
    """Building the appliance."""

    def test_simulation_leaves_the_request_unchanged(self):
        params = make_params(time_windows=None, num_cycles=1)
        GeneticHomeAppliance(params.to_parameters(), 24, 24)
        assert params.time_windows is None

    def test_num_remaining_cycles_initial(self):
        appliance = make_appliance(num_cycles=3)
        assert appliance.num_remaining_cycles == 3

    def test_num_remaining_cycles_never_negative(self):
        appliance = make_appliance(num_cycles=2)
        appliance.completed_cycles = 5
        assert appliance.num_remaining_cycles == 0


# ---------------------------------------------------------------------------
# Allowed start slots: daylight saving time
# ---------------------------------------------------------------------------

class TestDSTAllowedStartSlots:
    """The optimizer must distinguish both occurrences of the autumn hour."""

    @staticmethod
    def _appliance(window_field, windows, run_minutes=15):
        if window_field == "time_windows":
            windows = [{**window, "value": 0} for window in windows]
        return GeneticHomeAppliance(
            parameters=GeneticHomeApplianceRequest.model_validate(
                {
                    "device_id": "dishwasher",
                    "load_profile_power_w": [1000],
                    "load_profile_interval_seconds": run_minutes * 60,
                    window_field: {"windows": windows},
                }
            ).to_parameters(),
            optimization_hours=25,
            prediction_hours=100,
            slot_duration_h=0.25,
        )

    @pytest.mark.parametrize("window_field", ["shared_time_windows", "time_windows"])
    def test_first_fold_starts_before_second_fold_opening_are_rejected(self, window_field):
        appliance = self._appliance(
            window_field, [{"start_time": "02:30", "end_time": "04:00"}]
        )
        zero = pendulum.datetime(2026, 10, 25, tz="Europe/Berlin")

        allowed = appliance.allowed_start_slots(
            slot0_datetime=zero, earliest_slot=0, horizon_end_slot=24, cycle_index=0
        )

        # The second 02:30 is slot 14. The first 02:30/02:45 are slots 10/11
        # and precede the configured opening despite their matching clock times.
        assert allowed == list(range(14, 20))

    @pytest.mark.parametrize("window_field", ["shared_time_windows", "time_windows"])
    def test_first_fold_starts_before_second_fold_closing_are_retained(self, window_field):
        appliance = self._appliance(
            window_field, [{"start_time": "01:00", "end_time": "02:30"}]
        )
        zero = pendulum.datetime(2026, 10, 25, tz="Europe/Berlin")

        allowed = appliance.allowed_start_slots(
            slot0_datetime=zero, earliest_slot=0, horizon_end_slot=24, cycle_index=0
        )

        # Includes a run from the first 02:30 to the first 02:45 (slot 10),
        # which finishes before the second 02:30 closing boundary.
        assert allowed == list(range(4, 14))

    @pytest.mark.parametrize(
        ("first_duration", "expected"), [("1 hour", []), ("2 hours", [9])]
    )
    def test_cycle_window_union_distinguishes_touching_instants_from_clock_times(
        self, first_duration, expected
    ):
        appliance = self._appliance(
            "time_windows",
            [
                {"start_time": "01:30", "duration": first_duration},
                {"start_time": "02:30", "end_time": "03:30"},
            ],
            run_minutes=90,
        )
        zero = pendulum.datetime(2026, 10, 25, tz="Europe/Berlin")

        allowed = appliance.allowed_start_slots(
            slot0_datetime=zero, earliest_slot=9, horizon_end_slot=15, cycle_index=0
        )

        # The only candidate runs from the first 02:15 to the second 02:45.
        # A one-hour first window ends at the first 02:30, leaving a real
        # one-hour gap; a two-hour first window touches the second opening.
        assert allowed == expected


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

    def test_clamped_to_valid_range(self):
        appliance = make_appliance(num_cycles=3)
        appliance.set_completed_cycles(-5)
        assert appliance.completed_cycles == 0
        appliance.set_completed_cycles(99)
        assert appliance.completed_cycles == 3


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


# ---------------------------------------------------------------------------
# Run in progress (#1548)
# ---------------------------------------------------------------------------

class TestRunInProgress:
    """A started run is kept: its remainder is fixed load from the observation on."""

    SLOT0 = "2026-11-14T01:00:00+01:00"

    def pinned(self, started: str, now: str, slot0: str = SLOT0, **overrides) -> GeneticHomeAppliance:
        appliance = make_appliance(
            prediction_hours=overrides.pop("prediction_hours", 6),
            run_started_datetime=started,
            **overrides,
        )
        appliance.pin_run_in_progress(to_datetime(slot0), to_datetime(now))
        return appliance

    def test_remaining_flat_run_is_placed_from_now(self):
        # 2000 Wh in 2 h (1000 W), started 00:47, re-plan 01:05: 102 min left.
        appliance = self.pinned("2026-11-14T00:47:00+01:00", "2026-11-14T01:05:00+01:00")

        assert appliance.run_in_progress
        assert appliance.pinned_load[0] == pytest.approx(1000 * 55 / 60)
        assert appliance.pinned_load[1] == pytest.approx(1000 * 47 / 60)
        assert appliance.pinned_load[2:].sum() == 0
        assert appliance.pinned_load.sum() == pytest.approx(2000 * 102 / 120)
        assert appliance.pinned_end_slot == 2
        assert appliance.pinned_end_datetime == to_datetime("2026-11-14T02:47:00+01:00")

    def test_remaining_profile_run_follows_the_profile(self):
        params = make_params(
            consumption_wh=None,
            duration_h=None,
            load_profile_power_w=[400.0, 2000.0, 100.0],
            load_profile_interval_seconds=900,
            run_started_datetime="2026-11-14T00:00:00+01:00",
        )
        appliance = GeneticHomeAppliance(
            parameters=params.to_parameters(), optimization_hours=4, prediction_hours=16, slot_duration_h=0.25
        )
        appliance.pin_run_in_progress(
            to_datetime("2026-11-14T00:15:00+01:00"), to_datetime("2026-11-14T00:20:00+01:00")
        )

        # Step 2 (2000 W) has 10 of 15 min left, step 3 (100 W) is complete.
        assert appliance.pinned_load[0] == pytest.approx(2000 * 10 / 60)
        assert appliance.pinned_load[1] == pytest.approx(100 * 15 / 60)
        assert appliance.pinned_load[2:].sum() == 0
        assert appliance.pinned_end_slot == 2

    def test_run_past_its_duration_keeps_slot_zero_without_energy(self):
        # The device decides when the run ends: 2 h run started 2 h 30 min ago.
        appliance = self.pinned("2026-11-13T22:35:00+01:00", "2026-11-14T01:05:00+01:00")

        assert appliance.run_in_progress
        assert appliance.pinned_load.sum() == 0
        assert appliance.pinned_end_slot == 1
        assert appliance.pinned_end_datetime == to_datetime("2026-11-14T01:05:00+01:00")

    @pytest.mark.parametrize(
        "started",
        [
            "2026-11-13T21:04:00+01:00",  # more than 2 run durations ago: stale report
            "2026-11-14T01:06:00+01:00",  # in the future
        ],
    )
    def test_implausible_start_is_not_pinned(self, started):
        appliance = self.pinned(started, "2026-11-14T01:05:00+01:00")

        assert not appliance.run_in_progress
        assert appliance.pinned_load.sum() == 0
        assert appliance.pinned_end_slot == 0

    def test_no_report_changes_nothing(self):
        appliance = make_appliance(prediction_hours=6)
        appliance.pin_run_in_progress(to_datetime(self.SLOT0), to_datetime(self.SLOT0))

        assert not appliance.run_in_progress
        assert appliance.next_cycle_earliest_slot() == 0
        assert appliance.completed_cycles_on(to_datetime(self.SLOT0).date()) == 0

    def test_load_curve_contains_the_remainder(self):
        appliance = self.pinned("2026-11-14T00:47:00+01:00", "2026-11-14T01:05:00+01:00")

        appliance.build_load_curve([])
        np.testing.assert_allclose(appliance.get_load_curve(), appliance.pinned_load)
        appliance.build_load_curve([4])
        assert appliance.get_load_curve()[4] == pytest.approx(1000)
        assert appliance.get_load_curve()[0] == pytest.approx(1000 * 55 / 60)

    def test_counts_as_a_cycle_of_its_start_day_and_delays_the_next(self):
        # Local times without offset, so the start day is the same in every local timezone of the test run.
        slot0 = "2026-11-14T01:00:00"
        appliance = self.pinned(
            "2026-11-14T00:47:00",
            "2026-11-14T01:05:00",
            slot0=slot0,
            num_cycles=2,
            min_cycle_gap_h=1,
            schedule_mode="DAILY",
        )
        today = to_datetime(slot0).date()

        assert appliance.completed_cycles_on(today) == 1
        assert appliance.completed_cycles_on(today.add(days=1)) == 0
        assert appliance.completed_cycles_on(today.subtract(days=1)) == 0
        # Ends 02:47 (slot 2 is the first free slot) plus 1 h gap.
        assert appliance.next_cycle_earliest_slot() == 3

    def test_deadline_counts_the_expected_end_of_the_run(self):
        appliance = self.pinned(
            "2026-11-14T00:47:00+01:00",
            "2026-11-14T01:05:00+01:00",
            deadline_datetime="2026-11-14T02:30:00+01:00",
        )

        assert appliance.deadline_missed([], to_datetime(self.SLOT0))
        appliance.deadline_datetime = to_datetime("2026-11-14T03:00:00+01:00")
        assert not appliance.deadline_missed([], to_datetime(self.SLOT0))
