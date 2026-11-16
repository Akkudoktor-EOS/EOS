"""Independent analytical and causal tests for conditional thermal learning."""

import math
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from conftest import LEARNING_NOW as NOW
from conftest import learning_history as history
from conftest import learning_model as model
from conftest import learning_observer as observer
from conftest import learning_sample as sample

from akkudoktoreos.thermal.thermallearning import (
    annual_heat_loss_prior,
    fit_room_model,
    latest_measurement,
    measured_samples,
    rc2_transition,
    sample_values,
)
from akkudoktoreos.thermal.thermalmodels import (
    ThermalBuildingRC2Parameters,
    ThermalRC2Parameters,
)


def test_annual_prior_is_duration_weighted_space_heat_in_watts_per_kelvin():
    assert annual_heat_loss_prior(8760.0, [5.0] * 8760, heating_limit_c=15.0) == pytest.approx(
        100.0
    )
    assert annual_heat_loss_prior(
        8760.0, [5.0] * 35040, heating_limit_c=15.0, interval_seconds=900
    ) == pytest.approx(100.0)
    assert annual_heat_loss_prior(8784.0, [5.0] * 8784, heating_limit_c=15.0) == pytest.approx(
        100.0
    )


def test_annual_prior_separates_old_fuel_efficiency_and_dhw_before_call():
    fuel_kwh, annual_efficiency, dhw_share = 20000.0, 0.9, 0.2
    delivered_space_heat = fuel_kwh * annual_efficiency * (1 - dhw_share)
    result = annual_heat_loss_prior(delivered_space_heat, [5.0] * 8760, heating_limit_c=15.0)
    assert result == pytest.approx(14400.0 * 1000 / (8760 * 10))
    # Passing all delivered heat is deliberately a different input, not an
    # automatic deduction: only the caller knows its DHW energy and boundary.
    unseparated = annual_heat_loss_prior(
        fuel_kwh * annual_efficiency, [5.0] * 8760, heating_limit_c=15.0
    )
    assert result == pytest.approx(unseparated * 0.8)


@pytest.mark.parametrize("amount", [0.0, -1.0, math.nan, math.inf])
def test_annual_prior_rejects_invalid_delivered_space_heat(amount):
    with pytest.raises(ValueError):
        annual_heat_loss_prior(amount, [5.0] * 8760, heating_limit_c=15.0)


@pytest.mark.parametrize(
    "weather_values,seconds",
    [
        ([5.0] * 8759, 3600),
        ([5.0] * 8761, 3600),
        ([5.0] * 8760, 0),
        ([5.0] * 8760, -3600),
        ([math.nan] * 8760, 3600),
        ([16.0] * 8760, 3600),
    ],
)
def test_annual_prior_requires_complete_finite_heating_weather(weather_values, seconds):
    with pytest.raises(ValueError):
        annual_heat_loss_prior(
            10000.0, weather_values, heating_limit_c=15.0, interval_seconds=seconds
        )


@pytest.mark.parametrize("limit", [math.nan, math.inf, -math.inf])
def test_annual_prior_rejects_nonfinite_heating_limit(limit):
    with pytest.raises(ValueError):
        annual_heat_loss_prior(10000.0, [5.0] * 8760, heating_limit_c=limit)


def test_latest_event_invalidates_an_older_good_hold_and_future_is_ignored():
    old = sample("room", NOW - timedelta(seconds=20), 20.0)
    invalid = sample("room", NOW - timedelta(seconds=10), None, "invalid")
    future = sample("room", NOW + timedelta(seconds=1), 99.0)
    with pytest.raises(ValueError, match="invalid"):
        latest_measurement([future, old, invalid], NOW, 180)
    assert sample_values(measured_samples([old, invalid, future], NOW), [NOW], 180)[0] is None
    assert latest_measurement([future, old], NOW, 180).value == 20.0


def test_same_timestamp_quality_replacement_does_not_resurrect_previous_value():
    good = sample("room", NOW, 20.0)
    invalid = sample("room", NOW, None, "unavailable")
    assert measured_samples([good, invalid], NOW) == [invalid]
    with pytest.raises(ValueError):
        latest_measurement([good, invalid], NOW, 180)


def test_sample_hold_age_boundary_is_inclusive_without_future_bin_averaging():
    row = sample("room", NOW - timedelta(seconds=180), 20.0)
    assert latest_measurement([row], NOW, 180).value == 20.0
    assert sample_values([row], [NOW], 180)[0] == 20.0
    assert sample_values([row], [NOW + timedelta(microseconds=1)], 180)[0] is None
    assert sample_values([row], [row.date_time - timedelta(microseconds=1)], 180)[0] is None
    with pytest.raises(ValueError):
        latest_measurement([row], NOW + timedelta(microseconds=1), 180)


def test_public_sample_lookup_handles_unsorted_native_events_causally():
    rows = [
        sample("room", NOW - timedelta(seconds=30), 19.0),
        sample("room", NOW, 20.0),
        sample("room", NOW - timedelta(seconds=60), 18.0),
    ]
    assert sample_values(rows, [NOW], 180)[0] == 20.0


def test_vector_sample_grid_preserves_order_gaps_and_invalidation():
    rows = [
        sample("room", NOW, 20.0),
        sample("room", NOW + timedelta(seconds=120), None, "invalid"),
        sample("room", NOW + timedelta(seconds=300), 21.0),
    ]
    grid = [
        NOW + timedelta(seconds=300),
        NOW - timedelta(seconds=1),
        NOW + timedelta(seconds=60),
        NOW + timedelta(seconds=180),
    ]
    assert sample_values(rows, grid, 180) == [21.0, None, 20.0, None]
    assert sample_values(rows, [], 180) == []


def test_rc2_watt_units_equilibrium_and_thermal_electrical_gain_separation():
    transition = rc2_transition(model(), 900.0)
    balanced = transition @ np.array([20.0, 20.0, 1500.0, 5.0])
    assert balanced[:2] == pytest.approx([20.0, 20.0], abs=1e-12)
    thermal = ThermalBuildingRC2Parameters(
        room_loss_per_hour=0.1,
        room_mass_exchange_per_hour=0.3,
        mass_capacity_ratio=4.0,
        heat_gain_kelvin_per_kwh=1.0,
    )
    assert rc2_transition(thermal, 900.0) == pytest.approx(transition)
    # Heat is one declared kW, not one watt and not multiplied by an inferred COP.
    steady = transition @ np.array([20.0, 20.0, 0.0, 20.0])
    heated = transition @ np.array([20.0, 20.0, 1000.0, 20.0])
    assert 0.20 < heated[0] - steady[0] < 0.25
    assert rc2_transition(model(), 0.0) == pytest.approx(np.eye(4))


@pytest.mark.parametrize("seconds", [-1.0, math.nan, math.inf])
def test_rc2_transition_rejects_nonphysical_or_nonfinite_duration(seconds):
    with pytest.raises(ValueError):
        rc2_transition(model(), seconds)


def test_mass_observer_is_causal_and_does_not_claim_mass_measurement():
    expected, quality = observer()
    assert expected.room_temperature_c == pytest.approx(20.0, abs=1e-12)
    assert expected.mass_temperature_c == pytest.approx(20.0, abs=1e-12)
    assert expected.based_on_observed_at == NOW
    assert quality["mass_is_measured"] is False
    assert quality["room_history_coverage"] == 1.0
    assert quality["projection_input_hold_assumed"] is True
    room, outside, power = history()
    for rows, key, value in (
        (room, "room", 100.0),
        (outside, "outdoor", -100.0),
        (power, "power", 30000.0),
    ):
        rows.append(sample(key, NOW + timedelta(seconds=1), value))
    poisoned, _ = observer(room=room, outdoor=outside, power=power)
    assert poisoned == expected


def test_state_projection_uses_observed_power_change_before_plan_start():
    room, _, _ = history()
    room = room[:-2]
    outdoor = [sample("outdoor", NOW - timedelta(minutes=2), 20.0)]
    power = [
        sample("power", NOW - timedelta(minutes=2), 0.0),
        sample("power", NOW - timedelta(minutes=1), 1000.0),
    ]
    state, quality = observer(
        room=room,
        outdoor=outdoor,
        power=power,
        start=NOW,
    )
    one_minute = rc2_transition(model(), 60.0)
    expected = one_minute @ np.array([20.0, 20.0, 0.0, 20.0])
    expected = one_minute @ np.array([expected[0], expected[1], 1000.0, 20.0])
    assert state.room_temperature_c == pytest.approx(expected[0], abs=1e-10)
    assert quality["projection_measured_input_seconds"] == 120.0
    assert quality["projection_input_hold_assumed"] is False


def test_thermal_projection_uses_heat_history_not_current_heat_retroactively():
    room, _, _ = history()
    heat = [sample("heat", NOW - timedelta(minutes=2), 0.0),
            sample("heat", NOW - timedelta(minutes=1), 3000.0),
            sample("heat", NOW + timedelta(seconds=1), 9000.0)]
    state, quality = observer(
        room=room[:-2], start=NOW,
        outdoor=[sample("outdoor", NOW - timedelta(minutes=2), 20.0)],
        power=[sample("power", NOW - timedelta(minutes=2), 500.0)],
        model_input_power_w=3000.0, model_input_history=heat,
    )
    transition = rc2_transition(model(), 60.0)
    expected = transition @ np.array([20.0, 20.0, 0.0, 20.0])
    expected = transition @ np.array([expected[0], expected[1], 3000.0, 20.0])
    assert state.room_temperature_c == pytest.approx(expected[0], abs=1e-10)
    assert quality["projection_thermal_history_used"]
    assert quality["projection_measured_input_seconds"] == 120
    assert not quality["projection_thermal_input_estimate_held"]


def test_missing_thermal_history_is_not_silently_replaced_by_electricity():
    with pytest.raises(ValueError, match="thermal input history"):
        observer(model_input_history=[], model_input_power_w=3000.0)


@pytest.mark.parametrize(
    "change",
    [
        "room_stale",
        "power_invalid",
        "outdoor_stale",
        "no_history",
        "long_gap",
        "low_coverage",
        "past_start",
        "distant_start",
    ],
)
def test_mass_observer_rejects_invalid_history_and_projection(change):
    room, outside, power = history()
    updates: dict[str, Any] = {}
    if change == "room_stale":
        room = room[:-4]
    elif change == "power_invalid":
        power = [sample("power", NOW, None, "invalid")]
    elif change == "outdoor_stale":
        outside = [sample("outdoor", NOW - timedelta(seconds=181), 5.0)]
    elif change == "no_history":
        room = room[-1:]
    elif change == "long_gap":
        room = [row for k, row in enumerate(room) if not 180 <= k < 220]
    elif change == "low_coverage":
        room = [row for k, row in enumerate(room) if k % 20 < 13 or k == 240]
    elif change == "past_start":
        updates["start"] = NOW - timedelta(seconds=1)
    else:
        updates["start"] = NOW + timedelta(seconds=901)
    with pytest.raises(ValueError):
        observer(room=room, outdoor=outside, power=power, **updates)


def test_mass_observer_recovers_from_old_gap_after_fresh_history():
    room, outside, power = history()
    room = [row for k, row in enumerate(room) if not 60 <= k < 100]
    state, quality = observer(room=room, outdoor=outside, power=power)
    assert state.mass_temperature_c == pytest.approx(20.0)
    assert quality["mass_history_restarted_after_gap"] is True
    assert quality["mass_history_hours"] == pytest.approx(140 / 60)
    assert quality["room_history_coverage"] == 1.0


@pytest.mark.parametrize("gain", [-1.0, 0.0, math.nan, math.inf])
def test_mass_observer_rejects_invalid_power_to_model_conversion(gain):
    with pytest.raises(ValueError):
        observer(power_to_model_input=gain)


def test_mass_observer_does_not_silently_accept_large_negative_heater_power():
    with pytest.raises(ValueError):
        observer(power=[sample("power", NOW, -1000.0)])


def test_negative_idle_noise_requires_declared_bound_and_has_quality_flag():
    room, outside, power = history()
    power = [sample("power", NOW, -2.0)]
    state, quality = observer(
        room=room, outdoor=outside, power=power, negative_idle_tolerance_w=5.0
    )
    assert math.isfinite(state.room_temperature_c)
    assert quality["negative_power_floored_to_zero"] is True
    with pytest.raises(ValueError):
        observer(power=[sample("power", NOW, -5.001)], negative_idle_tolerance_w=5.0)


@pytest.fixture(scope="module")
def synthetic_room():
    """Generate known dynamics by an independent two-eigenvalue solution."""
    n = 576
    times = [NOW - timedelta(minutes=5 * (n - 1 - k)) for k in range(n)]
    outside = [5.0 + 3 * math.sin(k * 2 * math.pi / 288) for k in range(n)]
    power = [900.0 + 700 * ((k // 12) % 2) + 400 * (k % 21 < 7) for k in range(n)]
    a = np.array([[-0.42, 0.3], [0.075, -0.075]])
    values, vectors = np.linalg.eig(a)
    transition = vectors @ np.diag(np.exp(values / 12)) @ np.linalg.inv(vectors)
    state = np.array([20.0, 20.0])
    room = []
    for k in range(n):
        room.append(float(state[0]))
        forcing = np.array([0.12 * outside[k] + 1.2 * power[k] / 1000, 0.0])
        equilibrium = np.linalg.solve(a, -forcing)
        state = equilibrium + transition @ (state - equilibrium)
    return times, room, outside, power


@pytest.mark.parametrize("basis", ["electric", "thermal"])
def test_fit_known_rc2_response_beats_persistence_without_claiming_savings(synthetic_room, basis):
    times, room, outside, power = synthetic_room
    result = fit_room_model(times, room, outside, power, input_basis=basis)
    assert result.input_basis == basis
    assert isinstance(
        result.model, ThermalRC2Parameters if basis == "electric" else ThermalBuildingRC2Parameters
    )
    assert result.physical_savings_proven is False
    assert result.training_intervals >= 120 and result.validation_origins >= 8
    assert result.fitted_until == times[len(times) * 2 // 3]
    origins = range(len(times) * 2 // 3, len(times) - 6, 6)
    persistence = np.mean([abs(room[k + 6] - room[k]) for k in origins])
    assert result.heldout_known_input_mae_c < 0.05
    assert result.heldout_known_input_mae_c < persistence / 3


def test_fit_preserves_missing_rows_instead_of_compressing_time(synthetic_room):
    times, original, outside, power = synthetic_room
    room = list(original)
    room[60:66] = [None] * 6
    result = fit_room_model(times, room, outside, power)
    assert result.training_intervals < 378
    assert result.heldout_known_input_mae_c < 0.1
    broken_times = times[:60] + times[66:]
    with pytest.raises(ValueError, match="preserved five-minute"):
        fit_room_model(
            broken_times,
            room[:60] + room[66:],
            outside[:60] + outside[66:],
            power[:60] + power[66:],
        )


def test_fit_unused_future_tail_cannot_change_parameters_or_forecasts(synthetic_room):
    times, room, outside, power = synthetic_room
    baseline = fit_room_model(times, room, outside, power)
    changed_room, changed_outside, changed_power = list(room), list(outside), list(power)
    # Last complete six-step origin is564 and ends570. Rows571..575
    # are beyond every declared validation horizon and cannot influence it.
    changed_room[-5:] = [99.0] * 5
    changed_outside[-5:] = [-50.0] * 5
    changed_power[-5:] = [30000.0] * 5
    poisoned = fit_room_model(times, changed_room, changed_outside, changed_power)
    assert poisoned.model.model_dump() == baseline.model.model_dump()
    assert poisoned.heldout_known_input_mae_c == baseline.heldout_known_input_mae_c


def test_fit_heldout_targets_do_not_train_the_parameters(synthetic_room):
    times, room, outside, power = synthetic_room
    baseline = fit_room_model(times, room, outside, power)
    changed = list(room)
    split = len(times) * 2 // 3
    changed[split:] = [value + 0.4 for value in changed[split:]]
    heldout_changed = fit_room_model(times, changed, outside, power)
    assert heldout_changed.model.model_dump() == baseline.model.model_dump()
    assert heldout_changed.heldout_known_input_mae_c > baseline.heldout_known_input_mae_c


def test_fit_open_loop_window_does_not_inject_intermediate_measured_room(
    synthetic_room, monkeypatch
):
    from scipy import optimize

    times, room, outside, power = synthetic_room
    first_residual = []
    theta = np.log([0.12, 0.3, 4.0, 1.2])

    def fixed_fit(fun, initial, **kwargs):
        first_residual.append(float(fun(theta)[0]))
        return SimpleNamespace(x=theta, success=True)

    monkeypatch.setattr(optimize, "least_squares", fixed_fit)
    fit_room_model(times, room, outside, power)
    poisoned = list(room)
    poisoned[1:6] = [value + 5.0 for value in poisoned[1:6]]
    fit_room_model(times, poisoned, outside, power)
    assert first_residual[0] == first_residual[1]


def test_fit_rejects_a_large_unexplained_heldout_response(synthetic_room):
    times, room, outside, power = synthetic_room
    changed = list(room)
    split = len(times) * 2 // 3
    changed[split:] = [value + 20.0 for value in changed[split:]]
    with pytest.raises(ValueError, match="held-out"):
        fit_room_model(times, changed, outside, power)


@pytest.mark.parametrize(
    "problem",
    ["short", "mismatched", "negative_power", "no_variation", "no_room", "bad_basis", "naive_time"],
)
def test_fit_rejects_unusable_or_ambiguous_input_contract(synthetic_room, problem):
    times, room, outside, power = synthetic_room
    options: dict[str, Any] = {}
    if problem == "short":
        times, room, outside, power = times[:200], room[:200], outside[:200], power[:200]
    elif problem == "mismatched":
        room = room[:-1]
    elif problem == "negative_power":
        power = [-1000.0] * len(power)
    elif problem == "no_variation":
        power = [1000.0] * len(power)
    elif problem == "no_room":
        room = [None] * len(room)
    elif problem == "bad_basis":
        options["input_basis"] = "gas"
    else:
        times = [value.replace(tzinfo=None) for value in times]
    with pytest.raises(ValueError):
        fit_room_model(times, room, outside, power, **options)
