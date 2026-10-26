"""Regression tests for explicit window ends and cycle scheduling."""

import json

import numpy as np
import pendulum
import pytest

from akkudoktoreos.config.configabc import (
    CycleTimeWindowSequence,
    TimeWindow,
    ValueTimeWindow,
)
from akkudoktoreos.devices.genetic.homeappliance import (
    HomeAppliance,
    HomeApplianceParameters,
)


@pytest.mark.parametrize("model", [TimeWindow, ValueTimeWindow])
def test_end_only_window_json_round_trip(model):
    window = model.model_validate({"start_time": "08:00", "end_time": "12:00"})

    serialized = window.model_dump_json()

    assert json.loads(serialized)["duration"] == "PT0S"
    restored = model.model_validate_json(serialized)
    assert restored.model_dump() == window.model_dump()
    assert restored.contains(pendulum.datetime(2026, 10, 26, 9))


@pytest.mark.parametrize(
    ("start_date", "start_time", "end_time", "expected_end"),
    [
        # The missing 02:30 on the opening date must not shift Monday's end.
        ("2026-03-29", "22:00", "02:30", "2026-03-30T02:30:00+02:00"),
        # The explicit end lies after the spring transition.
        ("2026-03-28", "22:00", "06:00", "2026-03-29T06:00:00+02:00"),
        # The explicit end lies after the autumn transition.
        ("2026-10-24", "22:00", "06:00", "2026-10-25T06:00:00+01:00"),
    ],
)
def test_overnight_end_uses_clock_time_on_end_date(start_date, start_time, end_time, expected_end):
    window = TimeWindow.model_validate({"start_time": start_time, "end_time": end_time})
    reference = pendulum.from_format(start_date, "YYYY-MM-DD", tz="Europe/Berlin")

    _, end = window._window_start_end(reference)

    assert end.isoformat() == expected_end
    assert not window.contains(end)
    assert window.contains(end.subtract(minutes=1))


@pytest.mark.parametrize(
    ("config", "start", "hours", "active_steps"),
    [
        ({"start_time": "08:00", "end_time": "12:00"}, "2026-10-26", 24, [8, 9, 10, 11]),
        # Duration may extend the explicit end.
        (
            {"start_time": "08:00", "end_time": "10:00", "duration": "4 hours"},
            "2026-10-26",
            24,
            [8, 9, 10, 11],
        ),
        ({"start_time": "08:00", "end_time": "12:00", "month": 11}, "2026-10-26", 24, []),
        ({"start_time": "08:00", "end_time": "12:00", "day_of_week": 1}, "2026-10-26", 24, []),
        ({"start_time": "08:00", "end_time": "12:00", "date": "2026-10-27"}, "2026-10-26", 24, []),
        # Include the tail of Friday's window on Saturday.
        (
            {"start_time": "22:00", "end_time": "06:00", "day_of_week": 4},
            "2026-10-24",
            24,
            list(range(6)),
        ),
        # Month restrictions belong to the opening date.
        (
            {"start_time": "22:00", "end_time": "06:00", "month": 10},
            "2026-11-01",
            24,
            list(range(6)),
        ),
        # Long durations can begin more than one calendar day earlier.
        (
            {"start_time": "08:00", "duration": "3 days", "date": "2026-10-24"},
            "2026-10-26",
            24,
            list(range(24)),
        ),
        # Recurring windows must also appear on later horizon days.
        ({"start_time": "08:00", "end_time": "10:00"}, "2026-10-26", 48, [8, 9, 32, 33]),
    ],
)
def test_cycle_arrays_use_effective_windows(config, start, hours, active_steps):
    window = ValueTimeWindow.model_validate({**config, "value": 0})
    sequence = CycleTimeWindowSequence(windows=[window])
    reference = pendulum.from_format(start, "YYYY-MM-DD", tz="UTC")
    end = reference.add(hours=hours)
    interval = pendulum.duration(hours=1)
    expected = np.zeros(hours)
    expected[active_steps] = 1

    actual = sequence.cycle_to_array(0, reference, end, interval)
    indices, matrix = sequence.cycles_to_matrix(reference, end, interval)

    assert indices == [0]
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(matrix[0], expected)


@pytest.mark.parametrize(("date", "hours"), [("2026-03-29", 23), ("2026-10-25", 25)])
def test_cycle_arrays_across_dst(date, hours):
    window = ValueTimeWindow.model_validate(
        {"start_time": "00:00", "end_time": "06:00", "value": 0}
    )
    sequence = CycleTimeWindowSequence(windows=[window])
    start = pendulum.from_format(date, "YYYY-MM-DD", tz="Europe/Berlin")
    end = start.add(days=1)
    expected = np.zeros(hours)
    expected[: hours - 18] = 1  # Five elapsed hours in spring; seven in autumn.

    actual = sequence.cycle_to_array(0, start, end, pendulum.duration(hours=1))
    _, matrix = sequence.cycles_to_matrix(start, end, pendulum.duration(hours=1))

    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(matrix[0], expected)


def test_end_only_cycle_window_allows_appliance_starts():
    sequence = CycleTimeWindowSequence.model_validate(
        {"windows": [{"start_time": "08:00", "end_time": "12:00", "value": 0}]}
    )
    # Exercise the serialized settings used by the dashboard/API as well.
    sequence = CycleTimeWindowSequence.model_validate_json(sequence.model_dump_json())
    appliance = HomeAppliance(
        parameters=HomeApplianceParameters(
            device_id="dishwasher",
            duration_h=2,
            consumption_wh=2000,
            num_cycles=1,
            time_windows=sequence,
        ),
        prediction_hours=24,
        optimization_hours=24,
    )
    start = pendulum.datetime(2026, 10, 26)
    appliance.start_allowed = []
    appliance.start_earliest = []
    appliance.start_latest = []

    appliance._build_cycle_start_allowed(
        sequence, start, start.add(days=1), pendulum.duration(hours=1)
    )

    np.testing.assert_array_equal(np.flatnonzero(appliance.start_allowed[0]), [8, 9, 10])
