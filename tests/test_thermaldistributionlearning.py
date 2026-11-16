"""Analytical bounds and causality of the optional slab/emitter observer."""

import math
from datetime import datetime, timedelta, timezone

import pytest

from akkudoktoreos.measurement.quality import MeasurementSample
from akkudoktoreos.thermal.thermaldistributionlearning import (
    estimate_emitter_temperature,
)

NOW = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)


def samples(key, value):
    return [
        MeasurementSample(date_time=NOW - timedelta(minutes=240 - k), key=key, value=value)
        for k in range(241)
    ]


def estimate(**changes):
    options = dict(
        now=NOW,
        capacity_j_per_k=3600000.0,
        water_conductance_w_per_k=700.0,
        room_conductance_w_per_k=300.0,
        minimum_temperature_c=10.0,
        maximum_temperature_c=50.0,
        maximum_initial_uncertainty_c=1.0,
        max_age_seconds=180,
        minimum_history_hours=2.0,
    )
    room = changes.pop("room", samples("room", 20.0))
    water = changes.pop("water", samples("water", 35.0))
    return estimate_emitter_temperature(room, water, **{**options, **changes})


def test_constant_forcing_matches_analytic_interval_and_no_warm_default():
    actual = estimate()
    eq = 30.5
    decay = math.exp(-4)
    assert actual.temperature_c == pytest.approx(eq + (30 - eq) * decay)
    assert actual.lower_temperature_c == pytest.approx(eq + (10 - eq) * decay)
    assert actual.upper_temperature_c == pytest.approx(eq + (50 - eq) * decay)
    assert actual.remaining_initial_uncertainty_c == pytest.approx(40 * decay)
    assert actual.continuous_history_seconds == 14400
    assert "emitter_state_is_estimated_not_measured" in actual.quality_flags


def test_variable_water_temperature_preserves_elapsed_time_with_exact_zoh():
    water = samples("water", 35.0)
    water = [
        row.model_copy(update={"value": 25.0}) if row.date_time >= NOW - timedelta(hours=2) else row
        for row in water
    ]
    first = 30.5 + (30 - 30.5) * math.exp(-2)
    second_eq = 0.7 * 25 + 0.3 * 20
    result = estimate(water=water)
    assert result.temperature_c == pytest.approx(second_eq + (first - second_eq) * math.exp(-2))
    assert result.remaining_initial_uncertainty_c == pytest.approx(40 * math.exp(-4))


def test_future_rows_cannot_change_estimate_or_shrink_uncertainty():
    before = estimate()
    room = samples("room", 20.0)
    water = samples("water", 35.0)
    room.append(MeasurementSample(date_time=NOW + timedelta(seconds=1), key="room", value=99.0))
    water.append(MeasurementSample(date_time=NOW + timedelta(seconds=1), key="water", value=99.0))
    after = estimate(room=room, water=water)
    assert after == before


def test_insufficient_rest_uncertainty_blocks_instead_of_hiding_initial_state():
    with pytest.raises(ValueError, match="uncertainty"):
        estimate(maximum_initial_uncertainty_c=0.1)


def test_missing_history_is_not_compressed_and_latest_tail_must_be_long_enough():
    room = [
        r
        for r in samples("room", 20.0)
        if not NOW - timedelta(hours=2, minutes=10) < r.date_time < NOW - timedelta(hours=2)
    ]
    with pytest.raises(ValueError, match="continuous"):
        estimate(room=room, minimum_history_hours=2.1)


def test_old_gap_restarts_full_interval_then_qualifies_only_after_decay():
    room = [
        r
        for r in samples("room", 20.0)
        if not NOW - timedelta(hours=3, minutes=30)
        < r.date_time
        < NOW - timedelta(hours=3, minutes=20)
    ]
    result = estimate(room=room, maximum_initial_uncertainty_c=2.0)
    assert result.discarded_gap_segments > 0
    assert result.continuous_history_seconds == 12000
    assert result.remaining_initial_uncertainty_c == pytest.approx(40 * math.exp(-12000 / 3600))


@pytest.mark.parametrize("status", ["invalid", "unavailable", "estimated"])
def test_latest_invalid_or_estimated_sample_does_not_reuse_older_good_data(status):
    water = samples("water", 35.0)
    water[-1] = water[-1].model_copy(
        update={"quality": water[-1].quality.model_copy(update={"status": status})}
    )
    with pytest.raises(ValueError):
        estimate(water=water)


def test_stale_inputs_step_budget_and_no_history_are_explicit_errors():
    with pytest.raises(ValueError):
        estimate(water=samples("water", 35.0)[:-4])
    with pytest.raises(ValueError, match="step budget"):
        estimate(maximum_steps=10)
    with pytest.raises(ValueError):
        estimate(room=[])


@pytest.mark.parametrize(
    "update",
    [
        {"capacity_j_per_k": 0.0},
        {"water_conductance_w_per_k": -1.0},
        {"room_conductance_w_per_k": -1.0},
        {"maximum_temperature_c": 10.0},
        {"maximum_initial_uncertainty_c": 0.0},
        {"capacity_j_per_k": float("nan")},
        {"minimum_history_hours": 0.0},
    ],
)
def test_nonphysical_parameters_rejected(update):
    with pytest.raises(ValueError):
        estimate(**update)
