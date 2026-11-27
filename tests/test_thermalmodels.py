"""Pure validation tests for the optional thermal planning data contract."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from akkudoktoreos.thermal.thermalmodels import (
    TemperatureInterval,
    ThermalBuildingRC2Parameters,
    ThermalComfortBand,
    ThermalObservation,
    ThermalPlan,
    ThermalRC2Parameters,
    ThermalState,
)

UTC = timezone.utc
START = datetime(2026, 1, 10, 8, tzinfo=UTC)


def interval(start=START, end=None, **updates):
    values = {
        "start_datetime": start,
        "end_datetime": end or start + timedelta(minutes=15),
        "min_temperature_c": 20.45,
        "target_temperature_c": 20.451,
        "max_temperature_c": 20.55,
    }
    values.update(updates)
    return values


def plan(**updates):
    values = {
        "zone_id": "living-room",
        "plan_id": "forecast-1",
        "source": "EOS thermal planning",
        "revision": 1,
        "basis_control_revision": 7,
        "issued_at": START,
        "valid_until": START + timedelta(minutes=15),
        "intervals": [interval()],
    }
    values.update(updates)
    return ThermalPlan.model_validate(values)


def parameters(**updates):
    values = {
        "room_loss_per_hour": 0.1,
        "room_mass_exchange_per_hour": 0.5,
        "mass_capacity_ratio": 3.5,
        "electric_gain_kelvin_per_kwh": 3.0,
    }
    values.update(updates)
    return values


def test_building_parameters_have_thermal_not_electrical_gain():
    values = parameters()
    gain = values.pop("electric_gain_kelvin_per_kwh")
    building = ThermalBuildingRC2Parameters.model_validate(
        {**values, "heat_gain_kelvin_per_kwh": gain}
    )
    assert building.heat_gain_kelvin_per_kwh == gain
    with pytest.raises(ValidationError):
        ThermalBuildingRC2Parameters.model_validate(parameters())
    with pytest.raises(ValidationError):
        ThermalRC2Parameters.model_validate(building.model_dump())


@pytest.mark.parametrize("value", [0, -1, True, "3", float("nan"), float("inf")])
def test_building_thermal_gain_requires_positive_finite_numeric_input(value):
    values = parameters()
    values.pop("electric_gain_kelvin_per_kwh")
    with pytest.raises(ValidationError):
        ThermalBuildingRC2Parameters.model_validate({**values, "heat_gain_kelvin_per_kwh": value})


def test_continuous_room_targets_are_not_native_setpoints():
    band = ThermalComfortBand(
        min_temperature_c=20.45, target_temperature_c=20.451, max_temperature_c=20.55
    )
    assert band.target_temperature_c == 20.451
    assert TemperatureInterval.model_validate(interval()).target_temperature_c == 20.451


@pytest.mark.parametrize("value", [True, False, "20.5", float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize(
    "field", ["min_temperature_c", "target_temperature_c", "max_temperature_c"]
)
def test_temperature_rejects_non_numeric_or_non_finite_values(field, value):
    with pytest.raises(ValidationError):
        TemperatureInterval.model_validate(interval(**{field: value}))


@pytest.mark.parametrize("minimum,target,maximum", [(21, 20, 22), (19, 23, 22), (22, 21, 20)])
def test_reversed_comfort_band_is_rejected(minimum, target, maximum):
    with pytest.raises(ValidationError):
        ThermalComfortBand(
            min_temperature_c=minimum, target_temperature_c=target, max_temperature_c=maximum
        )


def test_equal_comfort_bounds_are_allowed():
    assert (
        ThermalComfortBand(
            min_temperature_c=20, target_temperature_c=20, max_temperature_c=20
        ).target_temperature_c
        == 20
    )


def test_aware_iso_timestamps_are_normalized_to_utc():
    value = TemperatureInterval.model_validate(
        interval(start="2026-01-10T09:00:00+01:00", end="2026-01-10T09:15:00+01:00")
    )
    assert value.start_datetime == START
    assert value.start_datetime.utcoffset() == timedelta(0)
    assert value.end_datetime == START + timedelta(minutes=15)


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-01-10T08:00:00", "2026-01-10T08:15:00Z"),
        ("2026-01-10T08:00:00Z", "2026-01-10T08:15:00"),
        (START, START),
        (START, START - timedelta(minutes=15)),
    ],
)
def test_interval_rejects_naive_or_nonpositive_times(start, end):
    with pytest.raises(ValidationError):
        TemperatureInterval.model_validate(interval(start=start, end=end))


def test_observations_keep_missing_channels_missing():
    value = ThermalObservation(observed_at=START, room_temperature_c=19.25)
    assert value.outdoor_temperature_c is None
    assert value.electric_power_w is None


@pytest.mark.parametrize(
    "field,value",
    [("room_temperature_c", 19.25), ("outdoor_temperature_c", -2.5), ("electric_power_w", 0)],
)
def test_independent_channel_observations_need_no_invented_room_sample(field, value):
    observation = ThermalObservation.model_validate({"observed_at": START, field: value})
    assert getattr(observation, field) == value
    assert observation.observed_at == START
    for other in {"room_temperature_c", "outdoor_temperature_c", "electric_power_w"} - {field}:
        assert getattr(observation, other) is None


@pytest.mark.parametrize("channels", [{}, {"room_temperature_c": None, "electric_power_w": None}])
def test_empty_observation_is_rejected(channels):
    with pytest.raises(ValidationError):
        ThermalObservation.model_validate({"observed_at": START, **channels})


@pytest.mark.parametrize(
    "field,value",
    [
        ("room_temperature_c", "19.5"),
        ("outdoor_temperature_c", float("nan")),
        ("electric_power_w", -1),
        ("electric_power_w", True),
    ],
)
def test_invalid_observations_do_not_become_zero_power(field, value):
    data = {"observed_at": START, "room_temperature_c": 19.25, field: value}
    with pytest.raises(ValidationError):
        ThermalObservation.model_validate(data)


def test_latent_state_cannot_use_future_observations():
    with pytest.raises(ValidationError):
        ThermalState(
            estimated_at=START,
            based_on_observed_at=START + timedelta(seconds=1),
            room_temperature_c=19.25,
            mass_temperature_c=18.7,
        )
    state = ThermalState(
        estimated_at=START,
        based_on_observed_at=START - timedelta(seconds=10),
        room_temperature_c=19.25,
        mass_temperature_c=18.7,
    )
    assert state.mass_temperature_c == 18.7


@pytest.mark.parametrize("field", list(parameters()))
@pytest.mark.parametrize("value", [0, -1, True, "1", float("nan"), float("inf")])
def test_rc_parameters_require_finite_positive_numbers(field, value):
    with pytest.raises(ValidationError):
        ThermalRC2Parameters.model_validate(parameters(**{field: value}))


def test_rc_parameters_require_explicit_values_without_calibration_defaults():
    with pytest.raises(ValidationError):
        ThermalRC2Parameters.model_validate({})
    assert ThermalRC2Parameters.model_validate(parameters()).electric_gain_kelvin_per_kwh == 3


@pytest.mark.parametrize("field", ["revision", "basis_control_revision"])
@pytest.mark.parametrize("value", [True, "1", 1.5, -1])
def test_revision_requires_a_nonnegative_integer(field, value):
    with pytest.raises(ValidationError):
        plan(**{field: value})


@pytest.mark.parametrize("field", ["zone_id", "plan_id", "source"])
def test_empty_plan_identity_is_rejected(field):
    with pytest.raises(ValidationError):
        plan(**{field: "  "})


def test_plan_does_not_rewrite_configured_zone_or_plan_identifiers():
    value = plan(zone_id=" living-room ", plan_id=" original-id ")
    assert value.zone_id == " living-room "
    assert value.plan_id == " original-id "


def test_long_horizon_is_independent_of_execution_lease():
    rows = [interval(start=START + timedelta(minutes=15 * k)) for k in range(288)]
    value = plan(intervals=rows, valid_until=START + timedelta(minutes=30))
    assert value.intervals[-1].end_datetime == START + timedelta(hours=72)
    assert value.valid_until == START + timedelta(minutes=30)
    # A configurable lease is not hard-coded to30min by the DTO.
    assert (
        plan(intervals=rows, valid_until=START + timedelta(hours=1)).valid_until > value.valid_until
    )


@pytest.mark.parametrize("lease_end", [START, START - timedelta(seconds=1)])
def test_nonpositive_plan_lease_is_rejected(lease_end):
    with pytest.raises(ValidationError):
        plan(valid_until=lease_end)


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [interval(), interval(start=START + timedelta(minutes=30))],
        [interval(), interval(start=START + timedelta(minutes=10))],
        [interval(start=START + timedelta(minutes=15)), interval()],
    ],
)
def test_empty_gapped_overlapping_or_reversed_horizon_is_rejected(rows):
    with pytest.raises(ValidationError):
        plan(intervals=rows)


def test_lease_must_overlap_horizon_and_not_outlive_it():
    with pytest.raises(ValidationError):
        plan(intervals=[interval(start=START + timedelta(hours=1))])
    with pytest.raises(ValidationError):
        plan(valid_until=START + timedelta(hours=1))


def test_unknown_actuator_fields_are_not_silently_accepted():
    with pytest.raises(ValidationError):
        plan(native_ir_temperature=25)
    with pytest.raises(ValidationError):
        TemperatureInterval.model_validate(interval(fan_speed="Max"))


def test_json_round_trip_preserves_curves_timestamps_and_revisions():
    value = plan()
    assert ThermalPlan.model_validate_json(value.model_dump_json()) == value
    assert value.revision == 1
    assert value.basis_control_revision == 7


def test_plan_data_cannot_be_reassigned_or_intervals_appended():
    value = plan()
    with pytest.raises(ValidationError):
        value.revision = 2
    assert isinstance(value.intervals, tuple)
