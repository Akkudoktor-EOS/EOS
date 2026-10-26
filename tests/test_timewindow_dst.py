"""Time window boundaries and grids must distinguish the repeated DST hour."""

import numpy as np
import pendulum
import pytest

from akkudoktoreos.config.configabc import (
    CycleTimeWindowSequence,
    TimeWindow,
    TimeWindowSequence,
    ValueTimeWindow,
    ValueTimeWindowSequence,
)


def autumn_time(hour, minute=0, fold=1):
    return pendulum.datetime(2026, 10, 25, hour, minute, tz="Europe/Berlin", fold=fold)


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        ("PT1H45M", "2026-10-25T02:30:00+01:00"),
        ("PT2H45M", "2026-10-25T02:45:00+01:00"),
    ],
)
@pytest.mark.parametrize("fold", [0, 1])
def test_effective_end_uses_later_instant(duration, expected, fold):
    window = TimeWindow.model_validate(
        {"start_time": "01:00", "end_time": "02:30", "duration": duration}
    )

    _, end = window._window_start_end(autumn_time(2, fold=fold))

    assert end.isoformat() == expected


@pytest.mark.parametrize(
    ("hour", "minute", "fold", "duration", "expected"),
    [
        (2, 45, 0, None, True),
        (2, 30, 0, None, True),
        (2, 30, 1, None, False),
        (2, 45, 1, None, False),
        (2, 45, 0, 45, True),
        (2, 45, 0, 46, False),
    ],
)
def test_membership_uses_elapsed_time(hour, minute, fold, duration, expected):
    window = TimeWindow.model_validate({"start_time": "01:00", "end_time": "02:30"})
    run_duration = pendulum.duration(minutes=duration) if duration is not None else None

    assert window.contains(autumn_time(hour, minute, fold), run_duration) is expected


def test_membership_rejects_first_fold_before_second_fold_start():
    window = TimeWindow.model_validate({"start_time": "02:30", "end_time": "04:00"})

    assert not window.contains(autumn_time(2, 45, fold=0))
    assert window.contains(autumn_time(2, 45, fold=1))


@pytest.mark.parametrize("minute", [0, 30])
@pytest.mark.parametrize("start_hour", [0, 2])
def test_cycle_grid_ends_in_second_fold(start_hour, minute):
    sequence = CycleTimeWindowSequence.model_validate(
        {"windows": [{"start_time": "00:00", "end_time": "06:00", "value": 0}]}
    )
    start = autumn_time(start_hour, fold=0)
    end = autumn_time(2, minute, fold=1)
    interval = pendulum.duration(minutes=30)
    expected = np.ones(int((end - start).total_seconds() / interval.total_seconds()))

    actual = sequence.cycle_to_array(0, start, end, interval)
    indices, matrix = sequence.cycles_to_matrix(start, end, interval)

    assert indices == [0]
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(matrix, expected[np.newaxis, :])


@pytest.mark.parametrize("sequence_type", [TimeWindowSequence, ValueTimeWindowSequence])
def test_array_and_series_keep_both_folds(sequence_type):
    window = ValueTimeWindow.model_validate(
        {"start_time": "00:00", "end_time": "06:00", "value": 1}
    )
    sequence = sequence_type(windows=[window])
    start = autumn_time(0)
    end = autumn_time(2, fold=1)
    interval = pendulum.duration(hours=1)

    np.testing.assert_array_equal(sequence.to_array(start, end, interval), np.ones(3))
    series = sequence.to_series(start, end, interval)
    np.testing.assert_array_equal(series.to_numpy(), np.ones(3))
    assert [stamp.timestamp() for stamp in series.index] == [
        start.add(hours=i).timestamp() for i in range(3)
    ]
