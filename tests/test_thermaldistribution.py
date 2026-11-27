"""Independent energy and validation checks for lumped thermal distribution."""

from typing import Any

import numpy as np
import pytest
from numpy.testing import assert_allclose
from pydantic import TypeAdapter, ValidationError
from scipy.linalg import expm

from akkudoktoreos.thermal.thermaldistribution import (
    AirDistributionSettings,
    AirFanLevel,
    DirectDistributionSettings,
    DistributionTemperatureBounds,
    ThermalDistributionSettings,
    WaterBufferSettings,
    WaterDistributionSettings,
    build_distribution_dynamics,
    distribution_additional_state_names,
    distribution_capacities_j_per_k,
    distribution_electric_power_w,
    distribution_quality_flags,
    distribution_state_names,
    distribution_temperature_bounds,
    source_flow_conductance_w_per_k,
    source_temperature_state_index,
    step_distribution,
)
from akkudoktoreos.thermal.thermalmodels import (
    ThermalBuildingRC2Parameters,
    ThermalRC2Parameters,
)


def building() -> ThermalBuildingRC2Parameters:
    return ThermalBuildingRC2Parameters(
        room_loss_per_hour=0.12,
        room_mass_exchange_per_hour=0.6,
        mass_capacity_ratio=4.0,
        heat_gain_kelvin_per_kwh=1.8,
    )


def bounds(lower: float = -20.0, upper: float = 90.0) -> dict[str, float]:
    return {"min_temperature_c": lower, "max_temperature_c": upper}


def air(**updates: Any) -> AirDistributionSettings:
    values: dict[str, Any] = {
        "air_capacity_j_per_k": 180_000.0,
        "air_temperature_bounds": bounds(),
        "fan_levels": [
            {"level_id": "low", "conductance_w_per_k": 80.0},
            {
                "level_id": "high",
                "conductance_w_per_k": 320.0,
                "electric_power_w": 45.0,
                "electricity_included_in_cop": False,
            },
        ],
        "default_fan_level_id": "low",
    }
    return AirDistributionSettings.model_validate({**values, **updates})


def water(**updates: Any) -> WaterDistributionSettings:
    values: dict[str, Any] = {
        "emitter_type": "underfloor",
        "water_capacity_j_per_k": 100_000.0,
        "emitter_capacity_j_per_k": 4_000_000.0,
        "water_to_emitter_conductance_w_per_k": 300.0,
        "emitter_to_room_conductance_w_per_k": 120.0,
        "water_temperature_bounds": bounds(0.0, 70.0),
        "emitter_temperature_bounds": bounds(0.0, 50.0),
        "source_mass_flow_kg_per_s": 0.1,
        "fluid_specific_heat_j_per_kg_k": 4200.0,
    }
    return WaterDistributionSettings.model_validate({**values, **updates})


def buffered(**updates: Any) -> WaterDistributionSettings:
    return water(
        buffer={
            "capacity_j_per_k": 800_000.0,
            "temperature_bounds": bounds(0.0, 80.0),
            "transfer_conductance_w_per_k": 200.0,
        },
        **updates,
    )


def all_settings() -> list[ThermalDistributionSettings]:
    return [DirectDistributionSettings(), air(), water(), buffered()]


@pytest.mark.parametrize("settings", all_settings())
def test_discriminated_roundtrip_and_immutable_schema(
    settings: ThermalDistributionSettings,
) -> None:
    adapter = TypeAdapter(ThermalDistributionSettings)
    assert adapter.validate_json(settings.model_dump_json()) == settings
    with pytest.raises(ValidationError):
        adapter.validate_python({**settings.model_dump(), "unused": 1})
    with pytest.raises(ValidationError):
        setattr(settings, "kind", "direct")


@pytest.mark.parametrize("kind", ["unknown", "AIR", None])
def test_unknown_distribution_kind_is_rejected(kind: Any) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ThermalDistributionSettings).validate_python({"kind": kind})


@pytest.mark.parametrize("value", [0, -1, True, "200", float("inf"), float("nan")])
@pytest.mark.parametrize(
    "factory,field",
    [
        (air, "air_capacity_j_per_k"),
        (water, "water_capacity_j_per_k"),
        (water, "emitter_capacity_j_per_k"),
        (water, "water_to_emitter_conductance_w_per_k"),
        (water, "emitter_to_room_conductance_w_per_k"),
    ],
)
def test_positive_physical_parameters_are_strict(factory: Any, field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        factory(**{field: value})


@pytest.mark.parametrize("value", [True, "10", float("nan"), float("inf")])
def test_temperature_bounds_require_finite_numeric_values(value: Any) -> None:
    with pytest.raises(ValidationError):
        DistributionTemperatureBounds.model_validate(bounds(value, 90.0))


def test_bounds_and_fan_references_are_validated() -> None:
    with pytest.raises(ValidationError, match="minimum"):
        DistributionTemperatureBounds.model_validate(bounds(30.0, 20.0))
    with pytest.raises(ValidationError, match="unique"):
        air(fan_levels=[{"level_id": "low", "conductance_w_per_k": 1.0}] * 2)
    with pytest.raises(ValidationError, match="Default"):
        air(default_fan_level_id="missing")
    with pytest.raises(ValidationError):
        air(fan_levels=[])
    with pytest.raises(ValidationError):
        AirFanLevel.model_validate(
            {"level_id": "low", "conductance_w_per_k": 10.0, "electricity_included_in_cop": 1}
        )


def test_buffer_requires_one_explicit_transfer_and_fluid_cp() -> None:
    base = {"capacity_j_per_k": 800_000.0, "temperature_bounds": bounds()}
    for invalid in (
        {},
        {"transfer_conductance_w_per_k": 10.0, "transfer_mass_flow_kg_per_s": 0.1},
        {"transfer_mass_flow_kg_per_s": 0.1},
        {"transfer_conductance_w_per_k": 10.0, "fluid_specific_heat_j_per_kg_k": 4200.0},
    ):
        with pytest.raises(ValidationError):
            WaterBufferSettings.model_validate({**base, **invalid})
    model = WaterBufferSettings.model_validate(
        {**base, "transfer_mass_flow_kg_per_s": 0.0, "fluid_specific_heat_j_per_kg_k": 4200.0}
    )
    assert model.transfer_mass_flow_kg_per_s == 0
    with pytest.raises(ValidationError, match="specific heat"):
        water(fluid_specific_heat_j_per_kg_k=None)


@pytest.mark.parametrize(
    "settings,additional,expected_bounds,index",
    [
        (DirectDistributionSettings(), (), (), None),
        (air(), ("air_temperature_c",), ((-20.0, 90.0),), None),
        (
            water(),
            ("water_temperature_c", "emitter_temperature_c"),
            ((0.0, 70.0), (0.0, 50.0)),
            2,
        ),
        (
            buffered(),
            ("water_temperature_c", "emitter_temperature_c", "buffer_temperature_c"),
            ((0.0, 70.0), (0.0, 50.0), (0.0, 80.0)),
            4,
        ),
    ],
)
def test_state_order_bounds_and_source_recipient(
    settings: ThermalDistributionSettings,
    additional: tuple[str, ...],
    expected_bounds: tuple[tuple[float, float], ...],
    index: int | None,
) -> None:
    assert distribution_state_names(settings) == (
        "room_temperature_c",
        "mass_temperature_c",
        *additional,
    )
    assert distribution_additional_state_names(settings) == additional
    assert distribution_temperature_bounds(settings) == expected_bounds
    assert source_temperature_state_index(settings) == index


def test_absolute_capacities_use_thermal_gain_only() -> None:
    assert_allclose(
        distribution_capacities_j_per_k(building(), buffered()),
        [2_000_000.0, 8_000_000.0, 100_000.0, 4_000_000.0, 800_000.0],
    )
    legacy: Any = ThermalRC2Parameters(
        room_loss_per_hour=0.12,
        room_mass_exchange_per_hour=0.6,
        mass_capacity_ratio=4.0,
        electric_gain_kelvin_per_kwh=1.8,
    )
    with pytest.raises(TypeError, match="thermal building"):
        distribution_capacities_j_per_k(legacy, DirectDistributionSettings())


def test_direct_exact_zoh_matches_independent_two_node_building() -> None:
    # Independent hourly equation transformed to seconds and thermal watts.
    matrix = np.array([[-0.72, 0.6, 0.0018, 0.12], [0.15, -0.15, 0.0, 0.0]]) / 3600
    augmented = np.zeros((4, 4))
    augmented[:2, :] = matrix
    expected = expm(augmented * 900.0)
    a, b, e = build_distribution_dynamics(building(), DirectDistributionSettings(), seconds=900.0)
    assert_allclose(a, expected[:2, :2], atol=1e-14)
    assert_allclose(b, expected[:2, 2], atol=1e-14)
    assert_allclose(e, expected[:2, 3], atol=1e-14)


@pytest.mark.parametrize("settings", all_settings())
def test_zero_duration_fractional_semigroup_passivity_and_celsius_invariance(
    settings: ThermalDistributionSettings,
) -> None:
    a0, b0, e0 = build_distribution_dynamics(building(), settings, seconds=0.0)
    n = len(distribution_state_names(settings))
    assert_allclose(a0, np.eye(n), atol=0)
    assert_allclose(b0, np.zeros(n), atol=0)
    assert_allclose(e0, np.zeros(n), atol=0)
    a, b, e = build_distribution_dynamics(building(), settings, seconds=123.45)
    aa, bb, ee = build_distribution_dynamics(building(), settings, seconds=246.9)
    assert_allclose(aa, a @ a, atol=1e-13)
    assert_allclose(bb, a @ b + b, atol=1e-13)
    assert_allclose(ee, a @ e + e, atol=1e-13)
    assert np.min(a) >= -1e-14
    assert np.min(b) >= -1e-14
    assert np.min(e) >= -1e-14
    assert_allclose(a @ np.ones(n) + e, np.ones(n), atol=1e-13)
    cold = a @ np.full(n, 20.0)
    assert np.all(cold <= 20.0 + 1e-12)
    assert np.all(cold >= 0)


@pytest.mark.parametrize("settings", all_settings())
@pytest.mark.parametrize("power,outside", [(2000.0, 3.0), (0.0, 35.0)])
def test_integrated_source_building_and_total_heat_balances(
    settings: ThermalDistributionSettings, power: float, outside: float
) -> None:
    initial = [21.0, 19.0, *([30.0] * len(distribution_additional_state_names(settings)))]
    result = step_distribution(
        building(),
        settings,
        initial,
        thermal_power_w=power,
        outdoor_temperature_c=outside,
        seconds=900.125,
    )
    a, b, e = build_distribution_dynamics(building(), settings, seconds=900.125)
    expected = a @ initial + b * power + e * outside
    assert_allclose(result.temperatures_c, expected, atol=1e-12)
    capacities = distribution_capacities_j_per_k(building(), settings)
    delta = capacities * (np.array(result.temperatures_c) - initial)
    assert result.source_thermal_energy_j == pytest.approx(power * 900.125)
    assert result.stored_energy_change_j == pytest.approx(sum(delta), abs=1e-5)
    assert abs(result.total_energy_balance_error_j) < 1e-5
    assert abs(result.building_energy_balance_error_j) < 1e-5
    assert result.distribution_stored_energy_change_j == pytest.approx(
        result.source_thermal_energy_j - result.delivered_to_room_energy_j, abs=1e-5
    )
    assert result.return_temperature_is_measured is False
    if isinstance(settings, WaterDistributionSettings):
        assert result.modeled_return_temperature_c == result.temperatures_c[2]
    else:
        assert result.modeled_return_temperature_c is None
    if outside == 35.0:
        assert result.outdoor_heat_loss_j < 0


def test_high_fan_transfers_hot_local_air_faster_without_creating_heat() -> None:
    kwargs = dict(thermal_power_w=0.0, outdoor_temperature_c=21.0, seconds=300.0)
    low = step_distribution(building(), air(), [21.0, 21.0, 40.0], flow_or_fan="low", **kwargs)
    high = step_distribution(building(), air(), [21.0, 21.0, 40.0], flow_or_fan="high", **kwargs)
    assert high.temperatures_c[0] > low.temperatures_c[0]
    assert high.temperatures_c[2] < low.temperatures_c[2]
    assert high.delivered_to_room_energy_j > low.delivered_to_room_energy_j > 0
    assert high.source_thermal_energy_j == low.source_thermal_energy_j == 0
    assert abs(high.total_energy_balance_error_j) < 1e-5


def test_cold_emitter_absorbs_room_heat_with_correct_signed_accounting() -> None:
    result = step_distribution(
        building(),
        water(),
        [24.0, 24.0, 10.0, 10.0],
        thermal_power_w=0.0,
        outdoor_temperature_c=24.0,
        seconds=600.0,
    )
    assert result.delivered_to_room_energy_j < 0
    assert result.distribution_stored_energy_change_j > 0
    assert abs(result.total_energy_balance_error_j) < 1e-5


def test_water_and_slab_are_distinct_and_buffer_transfer_can_be_off() -> None:
    response = step_distribution(
        building(),
        water(),
        [20.0] * 4,
        thermal_power_w=2000.0,
        outdoor_temperature_c=20.0,
        seconds=60.0,
    )
    assert response.temperatures_c[2] > response.temperatures_c[3] > response.temperatures_c[0]
    isolated = buffered().model_copy(
        update={
            "buffer": WaterBufferSettings(
                capacity_j_per_k=800_000.0,
                temperature_bounds=DistributionTemperatureBounds.model_validate(bounds()),
                transfer_mass_flow_kg_per_s=0.0,
                fluid_specific_heat_j_per_kg_k=4200.0,
            )
        }
    )
    result = step_distribution(
        building(),
        isolated,
        [20.0] * 5,
        thermal_power_w=2000.0,
        outdoor_temperature_c=20.0,
        seconds=600.0,
    )
    assert_allclose(result.temperatures_c[:4], [20.0] * 4, atol=1e-12)
    assert result.temperatures_c[4] == pytest.approx(21.5)
    assert result.buffer_to_water_energy_j == 0
    assert result.delivered_to_room_energy_j == pytest.approx(0, abs=1e-6)


def test_known_buffer_flow_equals_identical_declared_conductance() -> None:
    first = buffered()
    second = water(
        buffer={
            "capacity_j_per_k": 800_000.0,
            "temperature_bounds": bounds(0.0, 80.0),
            "transfer_mass_flow_kg_per_s": 0.05,
            "fluid_specific_heat_j_per_kg_k": 4000.0,
        }
    )
    for one, two in zip(
        build_distribution_dynamics(building(), first, seconds=900.0),
        build_distribution_dynamics(building(), second, seconds=900.0),
        strict=True,
    ):
        assert_allclose(one, two, atol=1e-14)


def test_primary_flow_guard_has_known_units_and_does_not_modify_separate_buffer_loop() -> None:
    setting = buffered()
    assert source_flow_conductance_w_per_k(setting) == pytest.approx(420.0)
    assert source_flow_conductance_w_per_k(setting, 0.2) == pytest.approx(840.0)
    assert source_flow_conductance_w_per_k(setting, 0.0) == 0
    assert source_flow_conductance_w_per_k(water(source_mass_flow_kg_per_s=None)) is None
    for one, two in zip(
        build_distribution_dynamics(building(), setting, seconds=900.0),
        build_distribution_dynamics(building(), setting, seconds=900.0, flow_or_fan=0.2),
        strict=True,
    ):
        assert_allclose(one, two, atol=0)
    with pytest.raises(ValueError, match="specific heat"):
        source_flow_conductance_w_per_k(
            water(source_mass_flow_kg_per_s=None, fluid_specific_heat_j_per_kg_k=None), 0.1
        )


@pytest.mark.parametrize("selector", [-1.0, True, float("nan"), "high"])
def test_invalid_water_flow_is_rejected(selector: Any) -> None:
    with pytest.raises(ValueError):
        build_distribution_dynamics(building(), water(), seconds=1.0, flow_or_fan=selector)


@pytest.mark.parametrize("selector", [1.0, True, "missing"])
def test_invalid_air_fan_is_rejected(selector: Any) -> None:
    with pytest.raises(ValueError):
        build_distribution_dynamics(building(), air(), seconds=1.0, flow_or_fan=selector)


def test_endpoint_bounds_are_reported_not_clipped() -> None:
    result = step_distribution(
        building(),
        air(air_temperature_bounds=bounds(0.0, 30.0)),
        [20.0, 20.0, 40.0],
        thermal_power_w=0.0,
        outdoor_temperature_c=20.0,
        seconds=0.0,
    )
    assert result.temperatures_c[2] == 40
    assert result.endpoint_temperature_bound_violation_c == 10


@pytest.mark.parametrize("value", [-1.0, True, "1", float("nan"), float("inf")])
def test_invalid_step_duration_is_rejected(value: Any) -> None:
    with pytest.raises(ValueError):
        build_distribution_dynamics(building(), air(), seconds=value)


@pytest.mark.parametrize(
    "updates",
    [
        {"thermal_power_w": -1.0},
        {"thermal_power_w": True},
        {"outdoor_temperature_c": float("nan")},
        {"temperatures_c": [20.0]},
        {"temperatures_c": [20.0, 20.0, True]},
    ],
)
def test_step_rejects_invalid_inputs(updates: dict[str, Any]) -> None:
    values: dict[str, Any] = {
        "temperatures_c": [20.0] * 3,
        "thermal_power_w": 1000.0,
        "outdoor_temperature_c": 5.0,
        "seconds": 60.0,
    }
    with pytest.raises(ValueError):
        step_distribution(building(), air(), **{**values, **updates})


def test_distribution_electricity_requires_explicit_operation_and_cop_boundary() -> None:
    assert distribution_electric_power_w(DirectDistributionSettings()) == (0.0, ())
    power, flags = distribution_electric_power_w(air(), "high")
    assert power is None
    assert "distribution_circulation_operation_unknown" in flags
    settings = air(circulation_operation="fixed_entire_horizon")
    power, flags = distribution_electric_power_w(settings, "high")
    assert power == 45
    assert "distribution_electricity_additional_outside_cop" in flags
    assert distribution_electric_power_w(settings, "low")[0] is None
    included = air(
        circulation_operation="fixed_entire_horizon",
        fan_levels=[
            {"level_id": "low", "conductance_w_per_k": 80.0, "electricity_included_in_cop": True}
        ],
    )
    assert distribution_electric_power_w(included)[0] == 0


def test_water_pump_electricity_is_separate_bus_load_not_useful_heat() -> None:
    settings = water(
        circulation_operation="fixed_entire_horizon",
        pump_electric_power_w=25.0,
        pump_electricity_included_in_cop=False,
    )
    assert distribution_electric_power_w(settings)[0] == 25.0
    unknown = settings.model_copy(update={"pump_electricity_included_in_cop": None})
    assert distribution_electric_power_w(unknown)[0] is None
    included = settings.model_copy(update={"pump_electricity_included_in_cop": True})
    assert distribution_electric_power_w(included)[0] == 0
    result = step_distribution(
        building(),
        settings,
        [20.0] * 4,
        thermal_power_w=0.0,
        outdoor_temperature_c=20.0,
        seconds=900.0,
    )
    assert_allclose(result.temperatures_c, [20.0] * 4, atol=1e-12)
    assert result.source_thermal_energy_j == 0


def test_missing_flow_and_model_approximations_are_visible() -> None:
    flags = distribution_quality_flags(water(source_mass_flow_kg_per_s=None))
    assert "supply_output_consistency_unknown" in flags
    assert "slab_capacity_must_not_duplicate_building_mass" in flags
    assert "return_temperature_is_mixed_model_proxy" in flags
    assert "supply_output_consistency_unknown" not in distribution_quality_flags(water())
    assert (
        "lumped_air_mixing_not_cfd_or_verified_native_fan_behavior"
        in distribution_quality_flags(air())
    )
