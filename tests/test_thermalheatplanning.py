"""Source-separated building physics and native-weather shadow adapter tests."""

from datetime import timedelta
from typing import Any

import numpy as np
import pytest
from conftest import heat_pump_request_data as data
from pydantic import ValidationError

from akkudoktoreos.thermal import thermalheatplanning as generic
from akkudoktoreos.thermal import thermaloptimization as core
from akkudoktoreos.thermal.thermalheatplanning import (
    ThermalHeatPlanningRequest,
    optimize_heat_pump_shadow,
)
from akkudoktoreos.thermal.thermaloptimization import (
    ThermalPlanningInputs,
    ThermalShadowRequest,
    optimize_thermal_shadow,
)


def test_joint_timeout_optimizes_fixed_pattern_instead_of_copying_reference(monkeypatch):
    import scipy.optimize
    from scipy.optimize import OptimizeResult

    monkeypatch.setattr(scipy.optimize, "milp", lambda *a, **k: OptimizeResult(
        status=1, x=None, message="forced timeout", fun=None
    ))
    p = core._Program()
    on = p.var(1, high=1, binary=True)[0]
    heat = p.var(1, high=10)[0]
    p.row({heat: 1, on: -2}, low=0)
    p.stage = "joint_candidate"
    p.integer_hint_factory = lambda: np.array([1.0])
    p.reference_witness_validator = lambda x: x[heat] >= 2 - 1e-8
    result = p.solve(np.array([0.0, 1.0]), 5.0)
    assert result.status == "feasible"  # No global optimality claim for fixed integers.
    assert result.x[on] == 1
    assert result.x[heat] == pytest.approx(2.0)
    assert result.objective < 5.0  # Reference heat=5 was legal, but more expensive.




def test_separate_thermal_gain_matches_electrical_equivalent_without_double_cop():
    request = ThermalHeatPlanningRequest.model_validate(data())
    before = request.model_dump_json(exclude_computed_fields=True)
    result = optimize_heat_pump_shadow(request)
    common = request.model_dump(include=set(ThermalPlanningInputs.model_fields))
    legacy = ThermalShadowRequest.model_validate(
        {
            **common,
            "model": {
                "room_loss_per_hour": 0.1,
                "room_mass_exchange_per_hour": 0.2,
                "mass_capacity_ratio": 5.0,
                "electric_gain_kelvin_per_kwh": 4.0,
            },
            "outdoor_temperature_c": [0.0] * 8,
            "max_electric_power_w": 2000.0,
            "min_electric_power_w": 250.0,
        }
    )
    equivalent = optimize_thermal_shadow(legacy)
    assert result.status == "optimal", result.reason
    assert result.comparison_qualified and result.reference is not None
    assert equivalent.reference is not None and equivalent.candidate is not None
    assert result.candidate is not None
    assert result.reference.objective_eur == pytest.approx(
        equivalent.reference.objective_eur, abs=1e-6
    )
    assert result.candidate.objective_eur == pytest.approx(
        equivalent.candidate.objective_eur, abs=1e-6
    )
    assert result.reference_thermal_power_w == pytest.approx(
        [p * 4 for p in result.reference_heat_power_w]
    )
    assert request.model_dump_json(exclude_computed_fields=True) == before
    assert result.source_horizon is not None
    assert result.checks["source_system_boundary_known"] is True
    assert result.checks["source_minimum_modulation_known"] is True
    assert result.checks["model_input_kind"] == "thermal_building_with_heat_pump"
    assert not result.execution_allowed


@pytest.mark.parametrize("basis", ["unspecified", "compressor_only"])
def test_unknown_system_boundary_keeps_conditional_plan_but_never_claims_savings(basis):
    payload = data()
    payload["heat_pump_profile"]["provenance"]["electric_power_basis"] = basis
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.status == "optimal", result.reason
    assert result.plan is not None and result.reference is not None and result.candidate is not None
    assert not result.comparison_qualified
    assert result.savings_cash_eur is None and result.savings_objective_eur is None
    assert result.checks["source_system_boundary_known"] is False
    assert result.checks["source_minimum_modulation_known"] is True
    assert "Conditional source calculation only" in result.reason
    assert result.source_horizon is not None
    assert all(
        f"electric_power_basis:{basis}" in value.quality_flags
        for value in result.source_horizon.performance
    )


def test_unknown_minimum_keeps_explicit_relaxation_without_savings_claim():
    payload = data()
    payload["heat_pump_profile"].pop("thermal_min_points")
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.status == "optimal", result.reason
    assert result.plan is not None and result.reference is not None and result.candidate is not None
    assert not result.comparison_qualified
    assert result.savings_cash_eur is None and result.savings_objective_eur is None
    assert result.checks["source_system_boundary_known"] is True
    assert result.checks["source_minimum_modulation_known"] is False
    assert result.source_horizon is not None
    assert result.source_horizon.effective_min_electric_power_w == (0.0,) * 8
    assert any(
        "Minimum modulation is unknown" in text for text in result.source_horizon.approximations
    )


def test_legacy_electrical_gain_cannot_be_passed_as_thermal_building():
    payload = data()
    payload["building"]["electric_gain_kelvin_per_kwh"] = payload["building"].pop(
        "heat_gain_kelvin_per_kwh"
    )
    with pytest.raises(ValidationError):
        ThermalHeatPlanningRequest.model_validate(payload)


@pytest.mark.parametrize(
    "key", ["weather_temp_air", "weather_relative_humidity", "weather_dew_point"]
)
def test_boolean_weather_data_is_not_coerced_to_a_number(key):
    payload = data(2)
    payload["weather"][0][key] = True
    with pytest.raises(ValidationError):
        ThermalHeatPlanningRequest.model_validate(payload)


def test_source_cop_and_capacity_change_per_native_weather_slot():
    payload = data(4)
    for point in payload["heat_pump_profile"]["cop_points"]:
        point["cop"] = 2.0 if point["outdoor_temperature_c"] < 0 else 4.0
    for k, record in enumerate(payload["weather"]):
        record["weather_temp_air"] = -10.0 if k % 2 == 0 else 10.0
    payload["max_electric_power_w"] = 3000.0
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.candidate is not None, result.reason
    assert result.source_horizon is not None
    assert [p.cop for p in result.source_horizon.performance] == [2.0, 4.0, 2.0, 4.0]
    assert result.source_horizon.effective_max_electric_power_w == (3000.0, 2000.0, 3000.0, 2000.0)
    assert result.candidate_thermal_power_w == pytest.approx(
        [
            q * p.cop
            for q, p in zip(result.candidate_heat_power_w, result.source_horizon.performance)
        ]
    )
    assert all(
        q <= cap + 1e-5
        for q, cap in zip(
            result.candidate_heat_power_w, result.source_horizon.effective_max_electric_power_w
        )
    )


@pytest.mark.parametrize("change", ["delivery", "outdoor"])
def test_outside_map_support_is_not_optimized_at_clamped_values(monkeypatch, change):
    payload = data(2)
    if change == "delivery":
        payload["delivery_temperature_c"] = [55.0] * 2
    else:
        payload["weather"][0]["weather_temp_air"] = -20.0
    monkeypatch.setattr(
        generic, "_optimize_problem", lambda *a: pytest.fail("Unsupported map entered solver")
    )
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.status == "unsupported"
    assert result.candidate is None and result.plan is None
    assert result.source_horizon is not None
    assert any(not p.supported for p in result.source_horizon.performance)


@pytest.mark.parametrize(
    "change", ["missing_time", "wrong_time", "missing_temperature", "missing_record"]
)
def test_native_weather_must_be_complete_and_exactly_aligned(change):
    payload = data(2)
    if change == "missing_time":
        payload["weather"][0]["date_time"] = None
    elif change == "wrong_time":
        payload["weather"][0]["date_time"] += timedelta(minutes=1)
    elif change == "missing_temperature":
        payload["weather"][0]["weather_temp_air"] = None
    else:
        payload["weather"].pop()
    with pytest.raises(ValidationError):
        ThermalHeatPlanningRequest.model_validate(payload)


def test_static_deicing_uses_explicit_load_and_is_labelled_an_approximation():
    payload = data(2)
    payload["heat_pump_profile"]["thermal_min_points"] = [
        {"outdoor_temperature_c": t, "delivery_temperature_c": 35.0, "thermal_power_w": 1000.0}
        for t in [-10.0, 10.0]
    ]
    payload["defrost"] = {"mode": "html_heuristic"}
    for record in payload["weather"]:
        record["weather_relative_humidity"] = 95.0
    request = ThermalHeatPlanningRequest.model_validate(payload)
    source = generic._source_horizon(request)
    assert source.assumed_thermal_load_fraction == 1.0
    assert all(p.cop == pytest.approx(3.4) for p in source.performance)
    assert all(
        "defrost_heuristic_cop_penalty_applied" in p.quality_flags for p in source.performance
    )
    assert any("not optimized" in text for text in source.approximations)


def test_requested_defrost_without_humidity_never_silently_becomes_zero_humidity():
    payload = data(2)
    payload["defrost"] = {"mode": "html_heuristic"}
    payload["heat_pump_profile"]["thermal_min_points"] = [
        {"outdoor_temperature_c": t, "delivery_temperature_c": 35.0, "thermal_power_w": 1000.0}
        for t in [-10.0, 10.0]
    ]
    for record in payload["weather"]:
        record["weather_relative_humidity"] = None
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.status == "unsupported" and result.plan is None
    assert result.source_horizon is not None
    assert all(
        "defrost_correction_skipped_missing_inputs" in p.quality_flags
        for p in result.source_horizon.performance
    )


def test_defrost_inclusive_cop_is_never_penalized_again():
    payload = data(2)
    payload["defrost"] = {"mode": "html_heuristic"}
    payload["heat_pump_profile"]["cop_includes_defrost"] = True
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.source_horizon is not None
    assert all(p.cop == 4.0 for p in result.source_horizon.performance)
    assert result.status == "optimal", result.reason


def test_air_to_air_requires_its_own_indoor_rating_slice():
    payload = data(2)
    payload["heat_pump_profile"]["heat_pump_type"] = "air_to_air"
    for key in ["cop_points", "thermal_max_points", "thermal_min_points"]:
        for point in payload["heat_pump_profile"][key]:
            point["delivery_temperature_c"] = 20.0
    assert (
        optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload)).status
        == "unsupported"
    )
    payload["delivery_temperature_c"] = [20.0] * 2
    assert (
        optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload)).status
        == "optimal"
    )


@pytest.mark.parametrize("initial_running", [False, True])
def test_electrical_extras_are_paid_once_without_extra_delivered_heat(initial_running):
    payload = data(8)
    payload["initial_running"] = initial_running
    payload["electrical"] = {
        "standby_power_w": 5.0,
        "running_auxiliary_power_w": 50.0,
        "startup_energy_wh": 30.0,
        "extras_are_outside_cop": True,
    }
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.status == "optimal", result.reason
    assert result.reference is not None and result.candidate is not None
    for fleet, base, total, breakdown in [
        (
            result.reference,
            result.reference_heat_power_w,
            result.reference_source_electric_power_w,
            result.reference_electricity,
        ),
        (
            result.candidate,
            result.candidate_heat_power_w,
            result.candidate_source_electric_power_w,
            result.candidate_electricity,
        ),
    ]:
        previous = initial_running
        for p, net, components in zip(base, total, breakdown):
            running = p > 1e-5
            started = running and not previous
            assert components.base_energy_wh == pytest.approx(p * 0.25, abs=1e-5)
            assert components.standby_energy_wh == 1.25
            assert components.auxiliary_energy_wh == (12.5 if running else 0)
            assert components.startup_energy_wh == (30.0 if started else 0)
            assert components.mean_electric_power_w == pytest.approx(net, abs=1e-5)
            assert net == pytest.approx(p + 5 + (50 if running else 0) + (120 if started else 0))
            previous = running
        assert fleet.parameters is not None
        assert fleet.parameters.load_w == pytest.approx([300 + net for net in total])
        expected_cash = sum(
            imported * price * 0.25 / 1000
            for imported, price in zip(fleet.import_w, payload["fleet"]["import_price_eur_per_kwh"])
        )
        assert fleet.grid_cost_eur == pytest.approx(expected_cash, abs=1e-6)
    assert result.candidate_thermal_power_w == pytest.approx(
        [p * 4 for p in result.candidate_heat_power_w], abs=1e-5
    )


def test_unknown_initial_state_keeps_startup_assumption_but_blocks_savings():
    payload = data()
    payload["electrical"] = {"startup_energy_wh": 30.0, "extras_are_outside_cop": True}
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.plan is not None and result.candidate is not None, result.reason
    assert result.checks["source_initial_running_known_for_startup"] is False
    assert not result.comparison_qualified
    assert result.savings_cash_eur is None and result.savings_objective_eur is None


@pytest.mark.parametrize("remove", [None, "startup_energy_wh"])
def test_compressor_only_requires_explicit_complete_auxiliary_boundary(remove):
    payload = data()
    payload["heat_pump_profile"]["provenance"]["electric_power_basis"] = "compressor_only"
    payload["initial_running"] = False
    payload["electrical"] = {
        "standby_power_w": 0.0,
        "running_auxiliary_power_w": 0.0,
        "startup_energy_wh": 0.0,
        "extras_are_outside_cop": True,
    }
    if remove is not None:
        payload["electrical"].pop(remove)
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.candidate is not None, result.reason
    assert result.checks["source_system_boundary_known"] is (remove is None)
    assert result.comparison_qualified is (remove is None)


def test_total_source_cap_includes_auxiliary_and_startup_slot_power():
    payload = data(8)
    payload["initial_running"] = False
    payload["state"].update(room_temperature_c=19.0, mass_temperature_c=19.0)
    payload["recovery"] = {"max_duration_seconds": 7200}
    payload["max_electric_power_w"] = 2000.0
    payload["electrical"] = {
        "standby_power_w": 5.0,
        "running_auxiliary_power_w": 50.0,
        "startup_energy_wh": 30.0,
        "extras_are_outside_cop": True,
    }
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.candidate is not None and result.recovery is not None, result.reason
    assert result.recovery.heat_power_w[0] == pytest.approx(1825.0)
    assert max(result.candidate_source_electric_power_w) <= 2000.0 + 1e-5
    assert max(result.reference_source_electric_power_w) <= 2000.0 + 1e-5


@pytest.mark.parametrize("extra", ["running_auxiliary_power_w", "startup_energy_wh"])
def test_state_dependent_extras_require_known_positive_minimum(extra):
    payload = data()
    payload["heat_pump_profile"].pop("thermal_min_points")
    payload["electrical"] = {extra: 10.0, "extras_are_outside_cop": True}
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.status == "unsupported" and result.plan is None
    assert "positive minimum modulation" in result.reason


def paired_data(n: int = 8) -> dict[str, Any]:
    """Synthetic measured-pair shape, not a VZ12 performance claim."""
    payload = data(n)
    payload["heat_pump_profile"]["operating_points"] = [
        {
            "outdoor_temperature_c": 0.0,
            "delivery_temperature_c": 35.0,
            "thermal_power_w": q,
            "electric_power_w": p,
            "point_id": f"test{i}",
            "source": "Synthetic paired test fixture",
        }
        for i, (q, p) in enumerate([(1000.0, 200.0), (2000.0, 1000.0), (3000.0, 1200.0)])
    ]
    return payload


def test_generic_paired_load_axis_keeps_thermal_and_electric_input_separate():
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(paired_data()))
    assert result.status == "optimal", result.reason
    assert result.comparison_qualified
    assert result.source_horizon is not None and result.source_horizon.operating_points
    for thermal_power, base, total, breakdown in [
        (
            result.reference_thermal_power_w,
            result.reference_heat_power_w,
            result.reference_source_electric_power_w,
            result.reference_electricity,
        ),
        (
            result.candidate_thermal_power_w,
            result.candidate_heat_power_w,
            result.candidate_source_electric_power_w,
            result.candidate_electricity,
        ),
    ]:
        expected = [
            float(np.interp(q, [1000, 2000, 3000], [200, 1000, 1200])) if q > 1e-5 else 0.0
            for q in thermal_power
        ]
        assert base == pytest.approx(expected, abs=1e-5)
        assert total == pytest.approx(base, abs=1e-5)
        assert [value.base_energy_wh * 4 for value in breakdown] == pytest.approx(base, abs=1e-5)
    assert any(
        abs(q - p * performance.cop) > 1e-3
        for q, p, performance in zip(
            result.reference_thermal_power_w,
            result.reference_heat_power_w,
            result.source_horizon.performance,
        )
    )
    assert result.checks["source_power_input_kind"] == "thermal"


def test_paired_slice_is_not_borrowed_at_another_outdoor_temperature():
    payload = paired_data()
    payload["weather"][0]["weather_temp_air"] = 0.1
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.status == "unsupported" and result.plan is None
    assert "No exact paired operating-point" in result.reason


def test_lowest_paired_rating_is_not_relabelled_physical_minimum_modulation():
    payload = paired_data()
    for point in payload["heat_pump_profile"]["thermal_min_points"]:
        point["thermal_power_w"] = 600.0
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.plan is not None and result.candidate is not None, result.reason
    assert not result.comparison_qualified
    assert result.checks["source_minimum_modulation_known"] is False
    assert result.savings_cash_eur is None and result.savings_objective_eur is None
    assert result.source_horizon is not None
    assert all(
        "minimum_supported_operating_point_not_verified_device_minimum" in p.quality_flags
        for p in result.source_horizon.performance
    )


def test_paired_thermal_recovery_prefix_and_auxiliary_electricity_are_consistent():
    payload = paired_data(16)
    payload["state"].update(room_temperature_c=19.0, mass_temperature_c=19.0)
    payload["recovery"] = {"max_duration_seconds": 14400}
    payload["initial_running"] = False
    payload["electrical"] = {
        "standby_power_w": 5.0,
        "running_auxiliary_power_w": 50.0,
        "startup_energy_wh": 30.0,
        "extras_are_outside_cop": True,
    }
    payload["max_electric_power_w"] = 1300.0
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.candidate is not None and result.recovery is not None, result.reason
    assert result.recovery.completed and result.recovery.power_input_kind == "thermal"
    slots = len(result.recovery.heat_power_w)
    assert result.reference_thermal_power_w[:slots] == pytest.approx(
        result.recovery.heat_power_w, abs=1e-5
    )
    assert result.candidate_thermal_power_w[:slots] == pytest.approx(
        result.recovery.heat_power_w, abs=1e-5
    )
    assert max(result.reference_source_electric_power_w) <= 1300 + 1e-5
    assert max(result.candidate_source_electric_power_w) <= 1300 + 1e-5


@pytest.mark.parametrize(
    "paired, minimum, expected_base", [(False, 650.0, 595.0), (True, 850.0, 795.0)]
)
def test_configured_minimum_applies_to_active_steady_total_not_startup_energy(
    paired, minimum, expected_base
):
    payload = paired_data(16) if paired else data(16)
    # This regression exercises a tight two-stage MIP tracking reference, not
    # solver speed. Leave headroom for loaded CI hosts without weakening floors
    # or changing the production time-limit/fail-closed contract. The binding
    # limit is the shared planning budget: it gives the first reference stage at
    # most a quarter and the second at most a third of what is left (15 s of the
    # default 60 s), so under load the reference stops short of optimal and the
    # comparison is not qualified. Both budgets are raised for this test only.
    payload["fleet"]["solver_time_limit_seconds"] = 30.0
    payload["planning_time_limit_seconds"] = 300.0
    payload["min_electric_power_w"] = minimum
    payload["initial_running"] = False
    payload["electrical"] = {
        "standby_power_w": 5.0,
        "running_auxiliary_power_w": 50.0,
        "startup_energy_wh": 300.0,
        "extras_are_outside_cop": True,
    }
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.candidate is not None and result.reference is not None, result.reason
    assert result.comparison_qualified
    assert result.source_horizon is not None
    assert result.source_horizon.configured_minimum_base_electric_power_w == expected_base
    assert result.checks["source_configured_minimum_components_known"] is True
    for components in [result.reference_electricity, result.candidate_electricity]:
        assert any(value.base_energy_wh > 0 for value in components)
        assert any(value.started for value in components)
        for value in components:
            if value.base_energy_wh > 1e-5:
                assert value.base_energy_wh * 4 >= expected_base - 1e-5
                steady = value.mean_electric_power_w - value.startup_energy_wh * 4
                assert steady >= minimum - 1e-5


@pytest.mark.parametrize("paired", [False, True])
def test_configured_minimum_with_unknown_outside_cop_components_blocks_savings(paired):
    payload = paired_data() if paired else data()
    payload["min_electric_power_w"] = 500.0
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.plan is not None and result.candidate is not None, result.reason
    assert result.checks["source_configured_minimum_components_known"] is False
    assert not result.comparison_qualified
    assert result.savings_cash_eur is None and result.savings_objective_eur is None


@pytest.mark.parametrize("minimum", [True, -1.0, float("nan"), 3001.0])
def test_configured_minimum_is_finite_nonnegative_and_not_above_total_cap(minimum):
    payload = data()
    payload["min_electric_power_w"] = minimum
    payload["max_electric_power_w"] = 3000.0
    with pytest.raises(ValidationError):
        ThermalHeatPlanningRequest.model_validate(payload)


def test_configured_minimum_never_extrapolates_beyond_paired_power_support():
    payload = paired_data()
    payload["min_electric_power_w"] = 1300.0
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.status == "unsupported" and result.plan is None
    assert "minimum electricity exceeds" in result.reason


def distribution_data(kind="water", *, buffered=False, n=8):
    """Synthetic thermal parameters, with explicit state and electrical scope."""
    payload = data(n)
    payload["distribution_observed_at"] = payload["issued_at"]
    payload["distribution_load_basis"] = "residual_excludes_external_distribution"
    if kind == "air":
        payload["heat_pump_profile"]["heat_pump_type"] = "air_to_air"
        for key in ("cop_points", "thermal_max_points", "thermal_min_points"):
            for point in payload["heat_pump_profile"][key]:
                point["delivery_temperature_c"] = 20.0
        payload["delivery_temperature_c"] = [20.0] * n
        payload["distribution"] = {
            "kind": "air",
            "air_capacity_j_per_k": 200000.0,
            "air_temperature_bounds": {"min_temperature_c": 10.0, "max_temperature_c": 45.0},
            "fan_levels": [
                {
                    "level_id": "quiet",
                    "conductance_w_per_k": 150.0,
                    "electric_power_w": 20.0,
                    "electricity_included_in_cop": False,
                },
                {
                    "level_id": "normal",
                    "conductance_w_per_k": 600.0,
                    "electric_power_w": 40.0,
                    "electricity_included_in_cop": False,
                },
            ],
            "default_fan_level_id": "normal",
            "circulation_operation": "fixed_entire_horizon",
        }
        payload["distribution_state"] = [25.0]
    else:
        payload["distribution"] = {
            "kind": "water",
            "emitter_type": "radiator",
            "water_capacity_j_per_k": 420000.0,
            "emitter_capacity_j_per_k": 200000.0,
            "water_to_emitter_conductance_w_per_k": 1200.0,
            "emitter_to_room_conductance_w_per_k": 500.0,
            "water_temperature_bounds": {"min_temperature_c": 5.0, "max_temperature_c": 60.0},
            "emitter_temperature_bounds": {"min_temperature_c": 5.0, "max_temperature_c": 50.0},
            "source_mass_flow_kg_per_s": 0.3,
            "fluid_specific_heat_j_per_kg_k": 4180.0,
            "pump_electric_power_w": 30.0,
            "pump_electricity_included_in_cop": False,
            "circulation_operation": "fixed_entire_horizon",
        }
        payload["distribution_state"] = [28.0, 24.5]
        if buffered:
            payload["distribution"]["buffer"] = {
                "capacity_j_per_k": 2090000.0,
                "transfer_conductance_w_per_k": 2000.0,
                "temperature_bounds": {"min_temperature_c": 5.0, "max_temperature_c": 60.0},
            }
            payload["distribution_state"].append(31.0)
    return payload


@pytest.mark.parametrize("kind,buffered", [("air", False), ("water", False), ("water", True)])
def test_distribution_stores_share_native_bus_and_cannot_pay_savings_with_heat_debt(kind, buffered):
    request = ThermalHeatPlanningRequest.model_validate(distribution_data(kind, buffered=buffered))
    result = optimize_heat_pump_shadow(request)
    assert (
        result.plan is not None and result.reference is not None and result.candidate is not None
    ), result.reason
    assert result.comparison_qualified, result.reason
    expected_names = (
        {"air_temperature_c"} if kind == "air" else {"water_temperature_c", "emitter_temperature_c"}
    )
    if buffered:
        expected_names.add("buffer_temperature_c")
    assert set(result.reference_distribution_temperatures_c) == expected_names
    for name in expected_names:
        ref = result.reference_distribution_temperatures_c[name]
        cand = result.candidate_distribution_temperatures_c[name]
        assert len(ref) == len(cand) == 9
        assert cand[-1] == pytest.approx(ref[-1], abs=1e-7)
    extra = 40.0 if kind == "air" else 30.0
    assert result.distribution_electric_power_w == pytest.approx([extra] * 8)
    # Distribution consumption is neither useful heat nor source nameplate power.
    assert result.candidate.import_w == pytest.approx(
        [
            load + extra + source
            for load, source in zip(request.fleet.load_w, result.candidate_source_electric_power_w)
        ],
        abs=1e-5,
    )
    assert result.candidate_thermal_power_w == pytest.approx(
        [p * 4.0 for p in result.candidate_heat_power_w]
    )
    assert result.checks["all_distribution_terminal_states_match"] is True
    assert float(result.checks["max_distribution_minute_constraint_violation_c"]) <= 1e-5
    assert result.execution_allowed is False


def test_underfloor_state_is_distinct_from_loop_water_and_building_mass():
    payload = distribution_data()
    payload["distribution"]["emitter_type"] = "underfloor"
    payload["distribution"]["emitter_capacity_j_per_k"] = 10000000.0
    request = ThermalHeatPlanningRequest.model_validate(payload)
    result = optimize_heat_pump_shadow(request)
    assert result.plan is not None, result.reason
    assert result.checks["all_distribution_terminal_states_match"] is True
    water = np.asarray(result.candidate_distribution_temperatures_c["water_temperature_c"])
    slab = np.asarray(result.candidate_distribution_temperatures_c["emitter_temperature_c"])
    assert np.max(np.abs(water - slab)) > 0.5
    assert len(result.candidate_mass_temperature_c) == len(slab)


def test_hot_water_store_can_coast_but_cannot_receive_heat_from_a_colder_supply():
    payload = distribution_data(n=4)
    payload["distribution_state"] = [38.0, 24.5]
    request = ThermalHeatPlanningRequest.model_validate(payload)
    result = optimize_heat_pump_shadow(request)
    assert result.plan is not None, result.reason
    assert result.reference_thermal_power_w[0] == pytest.approx(0.0, abs=1e-6)
    assert result.candidate_thermal_power_w[0] == pytest.approx(0.0, abs=1e-6)
    assert result.candidate_distribution_temperatures_c["water_temperature_c"][0] == 38.0


def test_every_active_water_slot_obeys_known_flow_and_supply_at_all_minutes():
    from akkudoktoreos.thermal.thermaldistribution import build_distribution_dynamics

    request = ThermalHeatPlanningRequest.model_validate(distribution_data(buffered=True))
    result = optimize_heat_pump_shadow(request)
    assert result.plan is not None, result.reason
    for branch in ("reference", "candidate"):
        stores = getattr(result, branch + "_distribution_temperatures_c")
        trajectories = [
            getattr(result, branch + "_room_temperature_c"),
            getattr(result, branch + "_mass_temperature_c"),
            stores["water_temperature_c"],
            stores["emitter_temperature_c"],
            stores["buffer_temperature_c"],
        ]
        power = getattr(result, branch + "_thermal_power_w")
        for k, heat in enumerate(power):
            if heat <= 1e-6:
                continue
            x = np.array([values[k] for values in trajectories])
            for minute in range(16):
                a, b, e = build_distribution_dynamics(
                    request.building, request.distribution, seconds=minute * 60
                )
                predicted = a @ x + b * heat  # Tout is exactly0 in this fixture.
                assert heat <= 0.3 * 4180 * (35.0 - predicted[4]) + 1e-4


@pytest.mark.parametrize(
    "missing", ["source_mass_flow_kg_per_s", "pump_electric_power_w", "circulation_operation"]
)
def test_unknown_water_details_never_turn_into_qualified_savings(missing):
    payload = distribution_data()
    del payload["distribution"][missing]
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.plan is not None, result.reason
    assert not result.comparison_qualified
    assert result.savings_cash_eur is None and result.savings_objective_eur is None
    assert result.distribution_quality_flags


def test_zero_primary_flow_does_not_produce_heat_or_invent_a_water_return():
    payload = distribution_data(n=2)
    payload["distribution"]["source_mass_flow_kg_per_s"] = 0.0
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.plan is not None, result.reason
    assert max(result.reference_thermal_power_w) <= 1e-6
    assert max(result.candidate_thermal_power_w) <= 1e-6


def test_fixed_fan_schedule_changes_mixing_and_accounts_external_fan_power():
    payload = distribution_data("air")
    payload["distribution_fan_levels"] = ["quiet"] * 4 + ["normal"] * 4
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.plan is not None, result.reason
    assert result.distribution_electric_power_w == pytest.approx([20.0] * 4 + [40.0] * 4)
    assert "fan_schedule_is_exogenous_not_optimized" in result.distribution_quality_flags


def test_distribution_power_already_in_cop_is_not_added_twice():
    payload = distribution_data()
    payload["distribution"]["pump_electricity_included_in_cop"] = True
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.plan is not None and result.candidate is not None, result.reason
    assert result.distribution_electric_power_w == pytest.approx([0.0] * 8)
    assert result.candidate.import_w == pytest.approx(
        [300.0 + p for p in result.candidate_source_electric_power_w], abs=1e-5
    )


def test_external_distribution_electricity_requires_explicit_residual_exclusion():
    payload = distribution_data()
    del payload["distribution_load_basis"]
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.status == "unsupported" and result.plan is None
    assert "residual-load exclusion" in result.reason


@pytest.mark.parametrize("change", ["type", "state", "stale", "future", "fan"])
def test_distribution_request_rejects_wrong_type_state_clock_or_unused_inputs(change):
    payload = distribution_data()
    if change == "type":
        payload["heat_pump_profile"]["heat_pump_type"] = "air_to_air"
    elif change == "state":
        payload["distribution_state"] = [25.0]
    elif change == "stale":
        payload["distribution_observed_at"] = payload["issued_at"] - timedelta(hours=1)
    elif change == "future":
        payload["distribution_observed_at"] = payload["issued_at"] + timedelta(seconds=1)
    else:
        payload["distribution_fan_levels"] = ["normal"] * 8
    with pytest.raises(ValidationError):
        ThermalHeatPlanningRequest.model_validate(payload)


def test_larger_water_system_preserves_temperature_physics_when_all_extensive_sizes_scale():
    import copy

    baseline = distribution_data()
    large = copy.deepcopy(baseline)
    factor = 10.0
    large["building"]["heat_gain_kelvin_per_kwh"] /= factor
    for key in (
        "water_capacity_j_per_k",
        "emitter_capacity_j_per_k",
        "water_to_emitter_conductance_w_per_k",
        "emitter_to_room_conductance_w_per_k",
        "source_mass_flow_kg_per_s",
        "pump_electric_power_w",
    ):
        large["distribution"][key] *= factor
    for key in ("thermal_min_points", "thermal_max_points"):
        for point in large["heat_pump_profile"][key]:
            point["thermal_power_w"] *= factor
    for key in ("load_w", "pv_w"):
        large["fleet"][key] = [value * factor for value in large["fleet"][key]]
    for key in ("import_limit_w", "export_limit_w"):
        large["fleet"][key] *= factor
    small_result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(baseline))
    large_result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(large))
    assert small_result.plan is not None and large_result.plan is not None, large_result.reason
    assert small_result.reference is not None and large_result.reference is not None
    assert large_result.reference_room_temperature_c == pytest.approx(
        small_result.reference_room_temperature_c, abs=1e-6
    )
    for name, trace in small_result.reference_distribution_temperatures_c.items():
        assert large_result.reference_distribution_temperatures_c[name] == pytest.approx(
            trace, abs=1e-6
        )
    assert small_result.reference.grid_cost_eur is not None
    assert large_result.reference.grid_cost_eur == pytest.approx(
        small_result.reference.grid_cost_eur * factor, abs=1e-5
    )
    assert large_result.reference_thermal_power_w == pytest.approx(
        [q * factor for q in small_result.reference_thermal_power_w], abs=1e-3
    )


def test_cold_underfloor_recovery_heats_physical_stores_before_reaching_room_comfort():
    from akkudoktoreos.thermal.thermaloptimization import ThermalRecoverySettings

    payload = distribution_data(n=24)
    payload["state"]["room_temperature_c"] = 18.0
    payload["state"]["mass_temperature_c"] = 18.0
    payload["distribution"]["emitter_type"] = "underfloor"
    payload["distribution"]["emitter_capacity_j_per_k"] = 10000000.0
    payload["distribution_state"] = [25.0, 21.0]
    payload["recovery"] = ThermalRecoverySettings(max_duration_seconds=14400)
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.plan is not None, result.reason
    assert result.recovery is not None and result.recovery.completed
    assert len(result.recovery.heat_power_w) > 1
    assert result.recovery.room_temperature_c[-1] >= 20.0 - 1e-5
    assert set(result.recovery.distribution_temperatures_c) == {
        "water_temperature_c",
        "emitter_temperature_c",
    }
    assert result.reference_distribution_temperatures_c["emitter_temperature_c"][
        -1
    ] == pytest.approx(
        result.candidate_distribution_temperatures_c["emitter_temperature_c"][-1], abs=1e-7
    )
    assert float(result.checks["max_distribution_minute_constraint_violation_c"]) <= 1e-5


@pytest.mark.parametrize("kind,buffered", [("air", False), ("water", False), ("water", True)])
def test_passive_cooldown_accounts_for_residual_distribution_heat(kind, buffered):
    payload = distribution_data(kind, buffered=buffered, n=24)
    payload["state"].update(room_temperature_c=22.0, mass_temperature_c=22.0)
    payload["recovery"] = dict(max_duration_seconds=14400, allow_passive_cooldown=True)
    result = optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))
    assert result.plan is not None and result.recovery is not None, result.reason
    assert result.recovery.direction == "passive_cooldown" and result.recovery.completed
    slots = len(result.recovery.heat_power_w)
    assert result.candidate_thermal_power_w[:slots] == pytest.approx([0.0] * slots)
    assert result.reference_thermal_power_w[:slots] == pytest.approx([0.0] * slots)
    assert min(result.candidate_room_temperature_c) >= 20.0 - 1e-5
    assert result.recovery.distribution_temperatures_c
    assert float(result.checks["max_distribution_minute_constraint_violation_c"]) <= 1e-5
    assert result.checks["all_distribution_terminal_states_match"]


@pytest.mark.parametrize("buffered", [False, True])
def test_passive_maximum_bound_has_proof_guard_and_eliminates_redundant_active_binary(
    monkeypatch, buffered
):
    from dataclasses import replace

    captured = []
    original = generic._optimize_problem

    def inspect(request, source, **kwargs):
        captured.append((request, source))
        return original(request, source, **kwargs)

    monkeypatch.setattr(generic, "_optimize_problem", inspect)
    request = ThermalHeatPlanningRequest.model_validate(distribution_data(buffered=buffered, n=4))
    result = optimize_heat_pump_shadow(request)
    assert result.plan is not None, result.reason
    prepared, source = captured[0]
    lower, upper, maximum = core._state_domains(prepared, source)
    assert maximum == 35.0
    assert np.max(upper) <= 35.0
    assert np.all(lower <= upper)
    program = core._thermal_program(prepared, source)[0]
    # Exactly one physical minimum-modulation ON binary per slot; no extra
    # source-supply ACTIVE binary is needed under the proved maximum principle.
    assert sum(program.integer) == 4
    for k in range(4):
        a, b, e = core._steps_at(source, k)[1]
        node = source.source_temperature_state_index
        assert node is not None
        assert np.all(b * source.source_flow_conductance_w_per_k[k] <= a[:, node])
        assert np.allclose(a @ np.ones(len(b)) + e, 1)
    # A fast/small water node with excessive flow need not satisfy this sampled
    # maximum theorem. Decline the strengthening rather than assuming it.
    unsupported = replace(source, source_flow_conductance_w_per_k=(1e12,) * 4)
    assert (
        core._passive_upper_bound(unsupported, core._initial_temperatures(prepared, unsupported))
        is None
    )


def test_generic_source_preparation_is_inside_same_total_deadline(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(core.time, "monotonic", lambda: now[0])
    original = generic._source_horizon

    def prepare(*args):
        result = original(*args)
        now[0] = 2.0
        return result

    monkeypatch.setattr(generic, "_source_horizon", prepare)
    monkeypatch.setattr(
        generic,
        "_optimize_problem",
        lambda *a, **kw: pytest.fail("Expired preparation entered solver"),
    )
    request = ThermalHeatPlanningRequest.model_validate(
        {**data(2), "planning_time_limit_seconds": 1.0}
    )
    result = optimize_heat_pump_shadow(request)
    assert result.status == "limit" and result.plan is None and result.candidate is None
    assert result.checks["planning_deadline_exceeded"] is True
    assert "source_map_preparation" in result.reason




@pytest.mark.parametrize("buffered", [False, True])
def test_water_distribution_reference_fallback_keeps_every_store_and_bus_load(
    monkeypatch, buffered
):
    original = core._Program.solve

    def timeout(program, objective, seconds):
        if program.stage == "joint_candidate":
            raise core._NoSolution("limit", "forced joint timeout")
        return original(program, objective, seconds)

    monkeypatch.setattr(core._Program, "solve", timeout)
    request = ThermalHeatPlanningRequest.model_validate(distribution_data(buffered=buffered, n=4))
    result = optimize_heat_pump_shadow(request)
    assert (
        result.plan is not None and result.reference is not None and result.candidate is not None
    ), result.reason
    assert result.status == "feasible" and not result.comparison_qualified
    assert result.checks["candidate_plan_origin"] == "verified_reference_fallback"
    assert result.savings_cash_eur is None and result.savings_objective_eur is None
    assert result.candidate.import_w == pytest.approx(result.reference.import_w)
    for key, trace in result.reference_distribution_temperatures_c.items():
        assert result.candidate_distribution_temperatures_c[key] == pytest.approx(trace)
    assert result.checks["all_distribution_terminal_states_match"] is True
    assert float(result.checks["max_distribution_minute_constraint_violation_c"]) <= 1e-5
