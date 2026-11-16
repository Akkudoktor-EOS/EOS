"""Joint shadow economics, native FLEET parity and strict model feasibility."""

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pytest
from conftest import fleet_request_data as request_data

from akkudoktoreos.optimization.fleet import FleetRequest, optimize_fleet
from akkudoktoreos.thermal import thermaloptimization as thermal
from akkudoktoreos.thermal.thermaloptimization import (
    ThermalShadowRequest,
    ThermalShadowSolution,
    optimize_thermal_shadow,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class TestThermalPlanningDeadline:
    @staticmethod
    def pattern_program():
        p = thermal._Program(thermal._PlanningBudget(60.0), "reference_tracking")
        power = p.var(1, high=2000.0)[0]
        on = p.var(1, high=1.0, binary=True)[0]
        p.eq({power: 1.0}, 1000.0)
        p.row({power: 1.0, on: -2000.0}, high=0.0)
        p.row({power: 1.0, on: -500.0}, low=0.0)
        p.adaptive_stage_cap_seconds = 60.0
        p.integer_hint_factory = lambda: np.ones(1)
        p.reference_witness_validator = lambda values: True
        return p, np.array([0.001, 0.0])













































def storage_devices(n: int = 4) -> dict[str, Any]:
    """Native battery/EV features whose constraints must survive embedding."""
    return {
        "devices": [
            {
                "device_id": "battery1",
                "kind": "battery",
                "capacity_wh": 4000.0,
                "max_energy_wh": 3500.0,
                "initial_energy_wh": 2000.0,
                "reserve_wh": 500.0,
                "max_charge_power_w": 2000.0,
                "min_charge_power_w": 400.0,
                "max_discharge_power_w": 2000.0,
                "charge_efficiency": 0.9,
                "discharge_efficiency": 0.9,
                "terminal_value_eur_per_kwh": 0.2,
                "discharge_cost_eur_per_kwh": 0.01,
                "power_group": "shared",
            },
            {
                "device_id": "ev1",
                "kind": "ev",
                "capacity_wh": 4000.0,
                "initial_energy_wh": 0.0,
                "max_charge_power_w": 2000.0,
                "min_charge_power_w": 1000.0,
                "charge_levels_w": [0.0, 1000.0, 2000.0],
                "target_energy_wh": 500.0,
                "target_slot": 3,
                "available": [True, False, True, False][:n],
                "power_group": "shared",
            },
        ],
        "power_groups": [
            {"group_id": "shared", "max_charge_power_w": 2500.0, "max_discharge_power_w": 2000.0}
        ],
    }


class TestThermalStateCondensation:

    @staticmethod
    def physical_program():
        p, objective = TestThermalStateCondensation.retry_program()
        p.physical_validator = lambda values: {
            "source_flow_violation_w": max(0.0, float(values[3] - 23.0) * 1e6),
            "thermal_terminal_error_c": abs(float(values[4] - 20.0)),
        }
        return p, objective





    @staticmethod
    def retry_program():
        p, heat, states, on, objective = TestThermalStateCondensation.program()
        p.initial_representation = "sparse"
        p.stage = "joint_candidate"
        witness = np.zeros(len(p.lower))
        witness[heat], witness[states], witness[on] = (
            [3200.0, 1400.0],
            [20.0, 23.0, 20.0],
            [1.0, 1.0],
        )
        p.set_witness(range(len(p.lower)), witness)
        return p, objective








    @staticmethod
    def program(condense: bool = True):
        """An exact synthetic RC recurrence, with genuinely free ON decisions."""
        p = thermal._Program(thermal._PlanningBudget(10.0), "condensation_unit")
        p.initial_representation = "condensed" if condense else "sparse"
        heat = p.var(2, high=4000.0)
        states = p.var(3, low=20.0, high=23.0)
        p.lower[states[0]] = p.upper[states[0]] = 20.0
        on = p.var(2, high=1.0, binary=True)
        recurrences = []
        for k in range(2):
            recurrences.append((states[k + 1], len(p.rows)))
            p.eq({states[k + 1]: 1.0, states[k]: -0.5, heat[k]: -0.0025}, 5.0)
            p.row({heat[k]: 1.0, on[k]: -4000.0}, high=0.0)
            p.row({heat[k]: 1.0, on[k]: -1000.0}, low=0.0)
        p.eq({states[-1]: 1.0}, 20.0)
        if condense:
            p.thermal_state_substitution = thermal._ThermalStateSubstitution(
                initial=(states[0],), inputs=tuple(heat), recurrences=tuple(recurrences)
            )
        objective = np.zeros(len(p.lower))
        objective[heat] = [0.000025, 0.0002]
        objective[states[0]] = 0.03  # Nonzero eliminated objective constant 0.6.
        return p, heat, states, on, objective


























class TestCommonColdRecovery:
    def test_small_cold_deficit_does_not_force_maximum_power_or_unpublished_preheat(self):
        data = request_data(8)
        data["state"].update(room_temperature_c=19.64, mass_temperature_c=19.26)
        data["comfort_bands"] = [
            dict(min_temperature_c=19.7, target_temperature_c=20.0, max_temperature_c=23.0)
        ] * 8
        data["max_electric_power_w"] = 2344.69
        data["recovery"] = dict(max_duration_seconds=3600)

        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))

        assert result.plan is not None and result.recovery is not None, result.reason
        assert result.recovery.completed
        assert result.recovery.heat_power_w[0] < 1000.0
        assert result.recovery.room_temperature_c[1] == pytest.approx(20.0)
        assert result.plan.intervals[0].target_temperature_c == pytest.approx(20.0)

    @pytest.mark.parametrize("hours", [24, 48])
    @pytest.mark.parametrize(
        "initial, goal, direction", [(21.5, 19.0, "passive_cooldown"), (19.0, 22.0, "heating")]
    )
    def test_manual_goal_transitions_keep_requested_bands_and_minute_comfort(
        self, hours, initial, goal, direction
    ):
        data = request_data(hours * 4)
        data["state"].update(room_temperature_c=initial, mass_temperature_c=initial)
        data["comfort_bands"] = [
            dict(
                min_temperature_c=goal - 0.3,
                target_temperature_c=goal,
                max_temperature_c=goal + 0.5,
            )
        ] * (hours * 4)
        data["recovery"] = dict(max_duration_seconds=14400, allow_passive_cooldown=True)
        # Negative prices cannot add heat during cooldown; expensive initial
        # slots cannot defer a requested warm-up until cheaper later slots.
        data["fleet"]["import_price_eur_per_kwh"][:16] = [
            -0.3 if direction == "passive_cooldown" else 0.8
        ] * 16
        request = ThermalShadowRequest.model_validate(data)
        result = optimize_thermal_shadow(request)
        assert result.plan is not None and result.candidate is not None, result.reason
        assert result.recovery is not None and result.recovery.completed
        assert result.recovery.direction == direction
        slots = len(result.recovery.heat_power_w)
        assert 0 < slots < hours * 4
        assert result.reference_heat_power_w[:slots] == pytest.approx(
            result.candidate_heat_power_w[:slots]
        )
        assert result.plan.intervals[0].target_temperature_c == goal
        assert all(
            b.min_temperature_c == goal - 0.3 and b.max_temperature_c == goal + 0.5
            for b in result.plan.intervals
        )
        if direction == "passive_cooldown":
            assert result.recovery.heat_power_w == (0.0,) * slots
            assert result.recovery.hot_minutes > 0 and result.recovery.cold_minutes == 0
        else:
            assert result.recovery.heat_power_w[0] == request.max_electric_power_w
            assert result.recovery.cold_minutes > 0 and result.recovery.hot_minutes == 0
        # Independent minute rollout against requested, not internally relaxed bounds.
        for powers in (result.reference_heat_power_w, result.candidate_heat_power_w):
            state = np.array([initial, initial])
            for k, power in enumerate(powers):
                for minute in range(16):
                    a, b, e = thermal._dynamics(request.model, minute * 60)
                    room = float((a @ state + b * power + e * request.outdoor_temperature_c[k])[0])
                    if direction == "passive_cooldown" or k >= slots:
                        assert room >= goal - 0.3 - 1e-5
                    if direction == "heating" or k >= slots:
                        assert room <= goal + 0.5 + 1e-5
                a, b, e = thermal._dynamics(request.model, 900)
                state = a @ state + b * power + e * request.outdoor_temperature_c[k]

    @pytest.mark.parametrize("failure", ["deadline", "lower_bound", "later_upper"])
    def test_passive_cooldown_cannot_waive_other_comfort_requirements(self, failure):
        data = request_data(16)
        data["state"].update(room_temperature_c=24.0, mass_temperature_c=24.0)
        data["recovery"] = dict(max_duration_seconds=14400, allow_passive_cooldown=True)
        if failure == "deadline":
            data["recovery"]["max_duration_seconds"] = 900
        elif failure == "lower_bound":
            data["model"]["room_loss_per_hour"] = 4.0
        else:
            data["comfort_bands"] = [dict(b) for b in data["comfort_bands"]]
            data["comfort_bands"][1] = dict(
                min_temperature_c=19.0, target_temperature_c=19.5, max_temperature_c=20.0
            )
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        assert result.status == "infeasible"
        assert result.recovery is not None and not result.recovery.completed
        assert result.plan is None and result.savings_cash_eur is None









def _battery(device_id: str, initial: float, terminal_value: float, **extra: Any) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "kind": "battery",
        "capacity_wh": 4000.0,
        "initial_energy_wh": initial,
        "reserve_wh": 400.0,
        "max_charge_power_w": 2000.0,
        "max_discharge_power_w": 2000.0,
        "charge_efficiency": 0.9,
        "discharge_efficiency": 0.9,
        "terminal_value_eur_per_kwh": terminal_value,
        "discharge_cost_eur_per_kwh": 0.01,
        **extra,
    }


def _storage_scenarios() -> dict[str, dict[str, Any]]:
    return {
        "one_battery_no_ev": {"devices": [_battery("battery1", 2000.0, 0.2)]},
        "two_batteries": {
            "devices": [_battery("battery1", 2000.0, 0.2), _battery("battery2", 1000.0, 0.4)]
        },
        "battery_and_ev": storage_devices(4),
    }


class TestFleetComparisonAfterJointPlan:
    """The joint plan comes first; the optimized storage comparison only uses leftover time."""

    @staticmethod
    def _run(fleet: dict[str, Any], n: int = 4) -> tuple[ThermalShadowSolution, dict]:
        data = request_data(n)
        data["fleet"].update(fleet)
        result = optimize_thermal_shadow(ThermalShadowRequest.model_validate(data))
        return result, data


    @pytest.mark.parametrize("scenario", list(_storage_scenarios()))
    def test_storage_end_respects_reserve_and_target(self, scenario):
        fleet = _storage_scenarios()[scenario]
        first = {**fleet["devices"][0], "reserve_wh": 300.0, "target_energy_wh": 1500.0}
        fleet["devices"] = [{**first, "target_slot": 4}, *fleet["devices"][1:]]
        result, _ = self._run(fleet)
        assert result.candidate is not None, result.reason
        plan = result.candidate.devices[first["device_id"]]
        assert plan.energy_wh[-1] >= 1500.0 - 1e-5
        assert min(plan.energy_wh) >= 300.0 - 1e-5




    def test_witness_failure_chain_still_precedes_the_plan(self, monkeypatch):
        # No result of the quick witness: the existing retry/idle chain still supplies a witness.
        original = thermal.optimize_fleet
        calls = []

        def fleet(request, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return original(request, **kwargs).model_copy(
                    update={"status": "limit", "objective_eur": None}
                )
            return original(request, **kwargs)

        monkeypatch.setattr(thermal, "optimize_fleet", fleet)
        result, _ = self._run(_storage_scenarios()["one_battery_no_ev"])
        assert result.plan is not None, result.reason
        assert result.checks["native_fleet_reference_retry_used"] is True





class TestFleetReferenceWarmStartFile:
    """The planner keeps its last verified fleet reference between disposable worker runs."""

    @staticmethod
    def _solved(hours: int = 0):
        request = FleetRequest.model_validate(
            {
                "start_datetime": datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
                + timedelta(hours=hours),
                "interval_seconds": 3600,
                "load_w": [300.0] * 4,
                "pv_w": [0.0] * 4,
                "import_price_eur_per_kwh": [0.1, 0.1, 0.8, 0.8],
                "export_price_eur_per_kwh": [0.05] * 4,
                "import_limit_w": 5000.0,
                "export_limit_w": 5000.0,
                "devices": [
                    {
                        "device_id": "storage",
                        "kind": "battery",
                        "capacity_wh": 2000.0,
                        "initial_energy_wh": 0.0,
                        "max_charge_power_w": 1000.0,
                        "max_discharge_power_w": 1000.0,
                    }
                ],
            }
        )
        return request, optimize_fleet(request)

    def test_roundtrip_is_used_as_start_and_drops_the_request_copy(self):
        request, solution = self._solved()
        assert thermal._load_warm_start(request) is None
        thermal._store_warm_start(request, solution)
        loaded = thermal._load_warm_start(request)
        assert loaded is not None
        assert loaded.parameters is None
        assert loaded.devices["storage"].energy_wh == pytest.approx(
            solution.devices["storage"].energy_wh
        )
        later, _ = self._solved(1)
        assert optimize_fleet(later, start=loaded).start_used is True

    def test_other_device_set_or_raster_has_its_own_file(self):
        request, solution = self._solved()
        thermal._store_warm_start(request, solution)
        other = request.model_copy(update={"interval_seconds": 900})
        assert thermal._load_warm_start(other) is None

    def test_damaged_or_unusable_files_are_ignored(self):
        request, solution = self._solved()
        thermal._store_warm_start(request, solution)
        thermal._warm_start_file(request).write_text("{not json", encoding="utf-8")
        assert thermal._load_warm_start(request) is None
        thermal._store_warm_start(request, solution.model_copy(update={"status": "limit"}))
        assert thermal._load_warm_start(request) is None

    def test_unwritable_location_never_fails_the_plan(self, tmp_path, monkeypatch):
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("EOS_THERMAL_WARM_START_DIR", str(blocker / "sub"))
        request, solution = self._solved()
        thermal._store_warm_start(request, solution)
        assert thermal._load_warm_start(request) is None
