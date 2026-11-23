"""GENETIC's own time windows give the same coverage as the shared settings windows (ADR 0004)."""

from typing import Any

import numpy as np
import pytest

from akkudoktoreos.config.configabc import CycleTimeWindowSequence
from akkudoktoreos.optimization.genetic.devices.genetichomeappliance import (
    GeneticHomeAppliance,
)
from akkudoktoreos.optimization.genetic.geneticrequest import (
    GeneticHomeApplianceRequest,
)
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

WINDOWS: list[dict[str, Any]] = [
    # Plain window per cycle, two windows sharing cycle 1.
    {"start_time": "08:00", "duration": "3 hours", "value": 0},
    {"start_time": "13:00", "duration": "2 hours", "value": 1},
    {"start_time": "16:00", "duration": "1 hour", "value": 1},
    # Overnight with an explicit end time, restricted to a weekday.
    {"start_time": "22:00", "duration": "1 hour", "end_time": "02:30", "day_of_week": 5,
     "value": 2},
    # Date restriction (within the tested ranges).
    {"start_time": "10:00", "duration": "2 hours", "date": "2026-09-17", "value": 4},
    {"start_time": "10:00", "duration": "2 hours", "date": "2026-03-29", "value": 4},
    # Month restriction and a window that belongs to no cycle.
    {"start_time": "00:00", "duration": "6 hours", "month": 3, "value": 3},
    {"start_time": "00:00", "duration": "24 hours"},
]


@pytest.mark.parametrize(
    "start, interval_s",
    [
        ("2026-09-16T00:00:00", 3600),
        ("2026-09-16T10:20:00", 900),
        # Spring forward and fall back in Berlin.
        ("2026-03-28T00:00:00", 900),
        ("2026-10-24T00:00:00", 3600),
    ],
)
def test_window_coverage_matches_the_settings_windows(start: str, interval_s: int) -> None:
    """Per cycle, the run's window check covers the same grid steps as the settings windows.

    A one-second run is allowed where a window contains its start (the end is exclusive), so the
    run's check (``_windows_allow_run``) is compared with the settings' per-step coverage.
    """
    settings = CycleTimeWindowSequence.model_validate({"windows": WINDOWS})
    request = GeneticHomeApplianceRequest(
        device_id="washer", consumption_wh=1000, duration_h=1, time_windows=settings
    )
    windows = request.to_parameters().time_windows
    assert windows is not None
    first = to_datetime(start, in_timezone="Europe/Berlin")
    last = first.add(hours=72)
    interval = to_duration(interval_s)

    expected_cycles, expected = settings.cycles_to_matrix(first, last, interval)
    # The settings floor the grid start to the interval in wall-clock time.
    wall_s = first.hour * 3600 + first.minute * 60 + first.second + first.microsecond / 1_000_000
    grid = [
        first.subtract(seconds=wall_s % interval_s).add(seconds=step * interval_s)
        for step in range(expected.shape[1])
    ]
    one_second = to_duration(1)
    cycles = sorted({int(window.value) for window in windows if window.value is not None})
    matrix = np.array(
        [
            [
                1.0
                if GeneticHomeAppliance._windows_allow_run(
                    [w for w in windows if w.value is not None and int(w.value) == cycle],
                    moment,
                    one_second,
                    merge=True,
                )
                else 0.0
                for moment in grid
            ]
            for cycle in cycles
        ]
    )

    assert cycles == expected_cycles
    np.testing.assert_array_equal(matrix, expected)

def test_window_start_and_end_match_the_settings_windows() -> None:
    settings = CycleTimeWindowSequence.model_validate({"windows": WINDOWS})
    request = GeneticHomeApplianceRequest(
        device_id="washer", consumption_wh=1000, duration_h=1, time_windows=settings
    )
    windows = request.to_parameters().time_windows
    assert windows is not None
    for day in range(7):
        anchor = to_datetime("2026-10-24T12:00:00", in_timezone="Europe/Berlin").add(days=day)
        for own, shared in zip(windows, settings.windows):
            assert own.start_end(anchor) == shared._window_start_end(anchor)
