"""Tests for threshold peak shaving at the grid import limit of GENETIC plans.

With ``max_grid_import_power_w`` on the inverter, a battery that the plan
holds in a slot still covers the part of the grid import above the limit, as a
site controller with threshold peak shaving does. Released slots are
unchanged: the battery covers the whole load.

The exported battery instructions carry the threshold
(``FRBCInstruction.peak_shaving_threshold_w``). A site controller that executes
the plan with that threshold reaches the simulated grid import and state of
charge; the replay tests below check that with the instructions of real runs.

All expected values are worked out by hand in the tests.
"""

import json
from types import SimpleNamespace
from typing import Any, Optional, cast

import numpy as np
import pytest
from pydantic import ValidationError

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.core.emplan import EnergyManagementPlan, FRBCInstruction
from akkudoktoreos.devices.devicesabc import BatteryOperationMode
from akkudoktoreos.devices.genetic.battery import (
    Battery,
    ElectricVehicleParameters,
    SolarPanelBatteryParameters,
)
from akkudoktoreos.devices.genetic.inverter import Inverter, InverterParameters
from akkudoktoreos.optimization.genetic.genetic import (
    GeneticOptimization,
    GeneticSimulation,
)
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.utils.datetimeutil import to_datetime

LIMIT_W = 8000.0
SLOTS = 4


class _OtherBattery(Battery):
    """A battery implementation other than the plain Battery (generic code path)."""


def _devices(
    *,
    slot_duration_h: float = 1.0,
    limit_w: Optional[float] = LIMIT_W,
    capacity_wh: int = 10000,
    soc_percentage: int = 100,
    min_soc_percentage: int = 0,
    battery_power_w: float = 5000.0,
    discharging_efficiency: float = 1.0,
    inverter_power_w: float = 10000.0,
    dc_to_ac_efficiency: float = 1.0,
    dc_to_ac_efficiency_curve: Optional[list[tuple[float, float]]] = None,
    total_charge: bool = False,
    battery_class: type[Battery] = Battery,
) -> tuple[Inverter, Battery]:
    """Real battery and inverter; the battery is held (not released) in every slot."""
    battery = battery_class(
        SolarPanelBatteryParameters(
            device_id="battery1",
            capacity_wh=capacity_wh,
            initial_soc_percentage=soc_percentage,
            min_soc_percentage=min_soc_percentage,
            max_soc_percentage=100,
            charging_efficiency=1.0,
            discharging_efficiency=discharging_efficiency,
            max_charge_power_w=battery_power_w,
        ),
        prediction_hours=SLOTS,
        slot_duration_h=slot_duration_h,
    )
    battery.reset()
    battery.charge_array = np.zeros(SLOTS)
    battery.discharge_array = np.zeros(SLOTS, dtype=np.int_)
    inverter = Inverter(
        InverterParameters(
            device_id="inverter1",
            max_power_wh=inverter_power_w,
            battery_id="battery1",
            dc_to_ac_efficiency=dc_to_ac_efficiency,
            dc_to_ac_efficiency_curve=dc_to_ac_efficiency_curve,
            ac_charge_limits_total_charge=total_charge,
            max_grid_import_power_w=limit_w,
        ),
        battery=battery,
        slot_duration_h=slot_duration_h,
    )
    return inverter, battery


# ---------------------------------------------------------------------------
# A held battery covers the import above the limit
# ---------------------------------------------------------------------------


class TestHeldBatteryShavesTheExcess:
    def test_held_battery_covers_only_the_import_above_the_limit(self):
        """12 kWh load in one hour, limit 8 kW: the battery delivers 4 kWh, not 12."""
        inverter, battery = _devices()

        grid_export, grid_import, losses, self_consumption = inverter.process_energy(
            0.0, 12000.0, 0
        )

        assert grid_import == 8000.0
        assert self_consumption == pytest.approx(4000.0)
        assert battery.soc_wh == pytest.approx(6000.0)
        assert losses == pytest.approx(0.0)
        assert grid_export == 0.0

    def test_shaving_is_limited_by_the_battery_power(self):
        """16 kWh load: 8 kWh excess, but the battery delivers at most 5 kWh."""
        inverter, battery = _devices()

        _, grid_import, _, _ = inverter.process_energy(0.0, 16000.0, 0)

        assert grid_import == pytest.approx(11000.0)
        assert battery.soc_wh == pytest.approx(5000.0)

    def test_shaving_is_limited_by_the_inverter_power(self):
        """A 3 kW inverter delivers at most 3 kWh of the 4 kWh excess."""
        inverter, battery = _devices(inverter_power_w=3000.0)

        _, grid_import, _, _ = inverter.process_energy(0.0, 12000.0, 0)

        assert grid_import == pytest.approx(9000.0)
        assert battery.soc_wh == pytest.approx(7000.0)

    def test_shaving_stops_at_the_minimum_soc(self):
        """30 % of 10 kWh with a 20 % reserve: 1 kWh of the 4 kWh excess."""
        inverter, battery = _devices(soc_percentage=30, min_soc_percentage=20)

        _, grid_import, _, _ = inverter.process_energy(0.0, 12000.0, 0)

        assert grid_import == pytest.approx(11000.0)
        assert battery.soc_wh == pytest.approx(2000.0)

    def test_shaving_includes_the_conversion_losses(self):
        """4 kWh AC at 95 % inverter and 90 % battery efficiency: 4678.4 Wh from the cells."""
        inverter, battery = _devices(discharging_efficiency=0.9, dc_to_ac_efficiency=0.95)

        _, grid_import, losses, self_consumption = inverter.process_energy(0.0, 12000.0, 0)

        raw_wh = 4000.0 / 0.95 / 0.9
        assert grid_import == 8000.0
        assert self_consumption == pytest.approx(4000.0)
        assert battery.soc_wh == pytest.approx(10000.0 - raw_wh)
        assert losses == pytest.approx(raw_wh - 4000.0)

    def test_shaving_with_an_efficiency_curve(self):
        """The curve is evaluated at the 4 kWh the battery delivers (0.4 of 10 kW)."""
        curve = [(0.1, 0.9), (0.5, 0.95), (1.0, 0.96)]
        inverter, battery = _devices(dc_to_ac_efficiency_curve=curve)

        _, grid_import, _, _ = inverter.process_energy(0.0, 12000.0, 0)

        efficiency = 0.9 + (0.4 - 0.1) / (0.5 - 0.1) * (0.95 - 0.9)
        assert grid_import == 8000.0
        assert battery.soc_wh == pytest.approx(10000.0 - 4000.0 / efficiency)

    def test_quarter_hour_slot_uses_the_limit_per_slot(self):
        """3 kWh in 15 minutes (12 kW), limit 8 kW = 2 kWh per slot: 1 kWh shaved."""
        inverter, battery = _devices(slot_duration_h=0.25)

        _, grid_import, _, _ = inverter.process_energy(0.0, 3000.0, 0)

        assert grid_import == 2000.0
        assert battery.soc_wh == pytest.approx(9000.0)

    def test_load_at_the_limit_keeps_the_battery(self):
        inverter, battery = _devices()

        _, grid_import, _, _ = inverter.process_energy(0.0, 8000.0, 0)

        assert grid_import == pytest.approx(8000.0)
        assert battery.soc_wh == pytest.approx(10000.0)

    def test_without_a_limit_a_held_battery_stays_idle(self):
        inverter, battery = _devices(limit_w=None)

        _, grid_import, _, _ = inverter.process_energy(0.0, 12000.0, 0)

        assert grid_import == pytest.approx(12000.0)
        assert battery.soc_wh == pytest.approx(10000.0)

    def test_released_battery_still_covers_the_whole_load(self):
        inverter, battery = _devices()
        battery.discharge_array[0] = 1

        _, grid_import, _, _ = inverter.process_energy(0.0, 12000.0, 0)

        assert grid_import == pytest.approx(7000.0)
        assert battery.soc_wh == pytest.approx(5000.0)

    @pytest.mark.parametrize("load_wh", [12000.0, 16000.0])
    def test_other_battery_implementations_shave_the_same(self, load_wh):
        plain_inverter, plain_battery = _devices(
            discharging_efficiency=0.9, dc_to_ac_efficiency=0.95
        )
        other_inverter, other_battery = _devices(
            discharging_efficiency=0.9, dc_to_ac_efficiency=0.95, battery_class=_OtherBattery
        )

        plain = plain_inverter.process_energy(0.0, load_wh, 0)
        other = other_inverter.process_energy(0.0, load_wh, 0)

        assert other == pytest.approx(plain)
        assert other_battery.soc_wh == pytest.approx(plain_battery.soc_wh)

    def test_pv_in_the_slot_is_used_before_the_battery(self):
        """Direct PV lowers the import first; the battery covers what is left above 8 kWh."""
        unlimited, _ = _devices(limit_w=None)
        _, import_without_battery, _, _ = unlimited.process_energy(3000.0, 12000.0, 0)
        inverter, battery = _devices()

        _, grid_import, _, _ = inverter.process_energy(3000.0, 12000.0, 0)

        assert import_without_battery > LIMIT_W
        assert grid_import == 8000.0
        # The battery delivered the excess, and PV surplus (if any) refilled it.
        assert battery.discharged_energy_wh(0) == pytest.approx(import_without_battery - 8000.0)


# ---------------------------------------------------------------------------
# Simulation of a whole plan
# ---------------------------------------------------------------------------


def _simulate(
    config_eos: ConfigEOS,
    *,
    load: list[float],
    ac: Optional[list[float]] = None,
    discharge: Optional[list[float]] = None,
    ev_charge: Optional[list[float]] = None,
    pv: Optional[list[float]] = None,
    dc: Optional[list[float]] = None,
    price: Optional[list[float]] = None,
    limit_w: Optional[float] = LIMIT_W,
    **device_kwargs: Any,
) -> tuple[dict[str, Any], GeneticSimulation]:
    config_eos.merge_settings_from_dict(
        {"prediction": {"hours": 48}, "optimization": {"genetic": {"horizon_hours": 24}}}
    )
    slots = len(load)
    inverter, battery = _devices(limit_w=limit_w, **device_kwargs)
    battery = Battery(battery.parameters, prediction_hours=slots)
    inverter = Inverter(inverter.parameters, battery=battery)
    ev = None
    if ev_charge is not None:
        ev = Battery(
            ElectricVehicleParameters(
                device_id="ev1",
                capacity_wh=60000,
                charging_efficiency=1.0,
                discharging_efficiency=1.0,
                max_charge_power_w=7000.0,
                initial_soc_percentage=0,
                min_soc_percentage=100,
            ),
            prediction_hours=slots,
        )
    sim = GeneticSimulation()
    sim.prepare(
        GeneticEnergyManagementParameters(
            pv_forecast_wh=pv or [0.0] * slots,
            electricity_price_per_wh=price or [0.0003] * slots,
            feed_in_tariff_per_wh=0.00008,
            price_per_wh_battery=0.0,
            total_load=load,
        ),
        optimization_hours=slots,
        prediction_hours=slots,
        inverter=inverter,
        ev=ev,
    )
    sim.reset()
    sim.ac_charge_hours = np.array(ac or [0.0] * slots, dtype=float)
    sim.dc_charge_hours = np.array(dc or [1.0] * slots, dtype=float)
    sim.bat_discharge_hours = np.array(discharge or [0.0] * slots, dtype=float)
    if ev_charge is not None:
        sim.ev_charge_hours = np.array(ev_charge, dtype=float)
    return sim.simulate(0), sim


class TestSimulation:
    def test_ac_slot_with_peak_load_shaves_and_does_not_charge(self, config_eos):
        """Slot 1 plans AC charging during a 9 kWh load: the battery covers 1 kWh."""
        result, sim = _simulate(
            config_eos,
            load=[1000.0, 9000.0, 1000.0],
            ac=[0.0, 1.0, 0.0],
            soc_percentage=50,
        )

        assert list(result["Netzbezug_Wh_pro_Stunde"]) == [1000.0, 8000.0, 1000.0]
        assert result["akku_soc_pro_stunde"][2] == pytest.approx(40.0)
        # No AC charge is left in the slot, not even a rounding residue.
        assert sim.ac_charge_hours is not None
        assert list(sim.ac_charge_hours) == [0.0, 0.0, 0.0]

    def test_ev_charging_is_shaved_too(self, config_eos):
        """6 kWh house load plus 7 kWh EV charging: the battery covers 5 kWh."""
        result, _ = _simulate(config_eos, load=[6000.0], ev_charge=[1.0])

        assert result["Netzbezug_Wh_pro_Stunde"][0] == 8000.0
        assert result["akku_soc_pro_stunde"][0] == pytest.approx(100.0)

    def test_an_almost_empty_battery_shaves_what_it_has(self, config_eos):
        """1 kWh above the reserve: 11 kWh import, the rest stays above the limit."""
        result, _ = _simulate(config_eos, load=[12000.0, 1000.0], soc_percentage=10)

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(11000.0)
        assert result["akku_soc_pro_stunde"][1] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# The instruction carries the threshold
# ---------------------------------------------------------------------------


class TestInstruction:
    def test_threshold_defaults_to_none(self):
        instruction = FRBCInstruction(
            resource_id="battery1",
            actuator_id="battery1",
            execution_time=to_datetime("2026-10-26T00:00:00"),
            operation_mode_id=BatteryOperationMode.IDLE,
            operation_mode_factor=1.0,
        )

        assert instruction.peak_shaving_threshold_w is None

    def test_threshold_survives_serialisation(self):
        plan = EnergyManagementPlan(
            id="plan",
            generated_at=to_datetime("2026-10-26T00:00:00"),
            instructions=[
                FRBCInstruction(
                    resource_id="battery1",
                    actuator_id="battery1",
                    execution_time=to_datetime("2026-10-26T00:00:00"),
                    operation_mode_id=BatteryOperationMode.IDLE,
                    operation_mode_factor=1.0,
                    peak_shaving_threshold_w=8000.0,
                )
            ],
        )

        reloaded = EnergyManagementPlan.model_validate(json.loads(plan.model_dump_json()))

        instruction = reloaded.instructions[0]
        assert isinstance(instruction, FRBCInstruction)
        assert instruction.peak_shaving_threshold_w == 8000.0

    @pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
    def test_invalid_threshold_is_rejected(self, value):
        with pytest.raises(ValidationError):
            FRBCInstruction(
                resource_id="battery1",
                actuator_id="battery1",
                execution_time=to_datetime("2026-10-26T00:00:00"),
                operation_mode_id=BatteryOperationMode.IDLE,
                operation_mode_factor=1.0,
                peak_shaving_threshold_w=value,
            )


# ---------------------------------------------------------------------------
# The exported plan reproduces the simulated plan
# ---------------------------------------------------------------------------

# Battery controls (ac_charge, dc_charge, discharge_allowed) that an
# instruction's operation mode stands for, factor given as "f".
_MODE_CONTROLS = {
    "IDLE": lambda f: (0.0, 0.0, 0.0),
    "PEAK_SHAVING": lambda f: (0.0, 0.0, 1.0),
    "SELF_CONSUMPTION": lambda f: (0.0, f, 1.0),
    "NON_EXPORT": lambda f: (0.0, f, 0.0),
    "GRID_SUPPORT_IMPORT": lambda f: (f, 0.0, 0.0),
    "FORCED_CHARGE": lambda f: (f, 1.0, 0.0),
}

# Hourly scenarios (load, PV, prices, battery capacity, initial SoC, limit).
# Battery: 5 kW, lossless, no AC charging; inverter 10 kW, lossless.
_SCENARIOS: dict[str, tuple[list[float], list[float], list[float], int, int, float]] = {
    # Two 10 kWh hours at an 8 kW limit and 4 kWh in the battery. Releasing
    # the battery for the first peak empties it, so the second one exceeds the
    # limit. Holding it shaves 2 kWh in each peak and stays within the limit;
    # a plan without threshold shaving cannot.
    "two_peaks_small_battery": (
        [10000.0, 10000.0, 1000.0, 1000.0],
        [0.0, 0.0, 0.0, 0.0],
        [0.0003, 0.0003, 0.0003, 0.0003],
        4000,
        100,
        LIMIT_W,
    ),
    # A full 6 kWh battery is worth more in the expensive hours after a cheap
    # 12 kWh peak hour: hold it and shave 4 kWh rather than release it.
    "cheap_peak_then_expensive_hours": (
        [12000.0, 6000.0, 6000.0, 6000.0],
        [0.0, 0.0, 0.0, 0.0],
        [0.0003, 0.0006, 0.0006, 0.0006],
        6000,
        100,
        LIMIT_W,
    ),
    # A full battery shaves a 12 kWh peak hour with 6 kWh PV at a 2 kW limit;
    # PV refills it within the same hour. The plan holds the battery for the
    # expensive hours after it and lets PV charge it.
    "full_battery_with_pv_in_the_peak": (
        [12000.0, 3000.0, 3000.0, 3000.0],
        [6000.0, 0.0, 0.0, 0.0],
        [0.0003, 0.0009, 0.0009, 0.0009],
        10000,
        100,
        2000.0,
    ),
}


def _optimize_scenario(
    config_eos: ConfigEOS, name: str, with_limit: bool = True, seed: int = 42
) -> GeneticSolution:
    """Hourly 4-slot run of a scenario without AC charging."""
    load, pv, prices, capacity_wh, soc_percentage, scenario_limit_w = _SCENARIOS[name]
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": 4,
                    "tail_horizon_hours": 0,
                    "interval_sec": 3600,
                    "terminal_value_mode": "FIXED",
                    "terminal_value_euro_per_kwh": 0.0,
                },
            },
            "feedintariff": {"direct_marketing_enabled": False},
        }
    )
    get_ems(init=True).set_start_datetime(to_datetime("2026-10-26T00:00:00"))
    parameters = GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_forecast_wh": pv,
                "total_load": load,
                "electricity_price_per_wh": prices,
                "feed_in_tariff_per_wh": 0.0,
                "price_per_wh_battery": 0.0,
            },
            "pv_battery": {
                "device_id": "battery1",
                "capacity_wh": capacity_wh,
                "max_charge_power_w": 5000,
                "charging_efficiency": 1.0,
                "discharging_efficiency": 1.0,
                "initial_soc_percentage": soc_percentage,
                "min_soc_percentage": 0,
            },
            "inverter": {
                "device_id": "inverter1",
                "max_power_wh": 10000,
                "battery_id": "battery1",
                "ac_to_dc_efficiency": 1.0,
                "dc_to_ac_efficiency": 1.0,
                "max_ac_charge_power_w": 0,
                "max_grid_import_power_w": scenario_limit_w if with_limit else None,
            },
            "ev": None,
            "forecast_interval_seconds": 3600,
        }
    )
    return GeneticOptimization(fixed_seed=seed).optimize_ems(parameters, ngen=30, individuals=40)


def _battery_controls(
    plan: EnergyManagementPlan,
) -> tuple[list[float], list[float], list[float], Optional[float]]:
    """Battery controls per slot and the threshold, read from the plan's instructions."""
    start = get_ems().start_datetime
    controls: list[Optional[tuple[float, float, float]]] = [None] * SLOTS
    thresholds = set()
    for instruction in plan.instructions:
        if instruction.resource_id != "battery1":
            continue
        assert isinstance(instruction, FRBCInstruction)
        slot = int((instruction.execution_time - start).total_seconds() // 3600)
        mode = str(instruction.operation_mode_id)
        controls[slot] = _MODE_CONTROLS[mode](float(instruction.operation_mode_factor))
        thresholds.add(instruction.peak_shaving_threshold_w)
    for slot in range(1, SLOTS):
        if controls[slot] is None:
            controls[slot] = controls[slot - 1]
    assert all(control is not None for control in controls)
    assert len(thresholds) == 1
    ac, dc, discharge = (list(values) for values in zip(*cast(Any, controls)))
    return ac, dc, discharge, thresholds.pop()


def _replay(
    config_eos: ConfigEOS, name: str, plan: EnergyManagementPlan, use_threshold: bool = True
) -> dict[str, Any]:
    """Execute the plan's battery instructions the way a site controller does.

    The controller knows nothing of the optimizer's settings. It follows the
    operation modes and, if ``use_threshold``, shaves the grid import above
    the instructions' ``peak_shaving_threshold_w``.
    """
    load, pv, prices, capacity_wh, soc_percentage, _ = _SCENARIOS[name]
    ac, dc, discharge, threshold_w = _battery_controls(plan)
    replay, _ = _simulate(
        config_eos,
        limit_w=threshold_w if use_threshold else None,
        load=load,
        pv=pv,
        price=prices,
        ac=ac,
        dc=dc,
        discharge=discharge,
        capacity_wh=capacity_wh,
        soc_percentage=soc_percentage,
    )
    return replay


class TestExportedPlan:
    @pytest.mark.parametrize("name", list(_SCENARIOS))
    def test_replayed_instructions_reproduce_the_simulated_plan(self, config_eos, name):
        """A site controller with the plan's threshold gets the simulated import and SoC."""
        solution = _optimize_scenario(config_eos, name)
        plan = solution.energy_management_plan()

        replay = _replay(config_eos, name, plan)

        np.testing.assert_allclose(
            replay["Netzbezug_Wh_pro_Stunde"], solution.result.grid_consumption_wh_per_hour
        )
        np.testing.assert_allclose(
            replay["akku_soc_pro_stunde"], solution.result.battery_soc_per_hour
        )

    def test_threshold_keeps_two_peaks_within_the_limit(self, config_eos):
        """Holding the battery and shaving both peaks is the plan; it stays at 8 kWh."""
        solution = _optimize_scenario(config_eos, "two_peaks_small_battery")
        plan = solution.energy_management_plan()

        assert max(solution.result.grid_consumption_wh_per_hour) <= LIMIT_W + 1e-6
        # A controller that ignores the threshold (holds the battery) exceeds it.
        without_threshold = _replay(config_eos, "two_peaks_small_battery", plan, False)
        assert max(without_threshold["Netzbezug_Wh_pro_Stunde"]) > LIMIT_W

    def test_held_slot_with_pv_keeps_its_pv_charge_permission(self, config_eos):
        """The peak slot starts full, shaves and takes PV back: not IDLE."""
        solution = _optimize_scenario(config_eos, "full_battery_with_pv_in_the_peak")
        plan = solution.energy_management_plan()

        assert solution.discharge_allowed[0] == 0
        assert solution.dc_charge[0] > 0.0
        assert solution.result.battery_soc_per_hour[0] == pytest.approx(100.0)
        ac, dc, discharge, _ = _battery_controls(plan)
        assert (ac[0], discharge[0]) == (0.0, 0.0)
        assert dc[0] > 0.0

    def test_all_battery_instructions_carry_the_limit(self, config_eos):
        solution = _optimize_scenario(config_eos, "cheap_peak_then_expensive_hours")
        plan = solution.energy_management_plan()

        battery_instructions = [i for i in plan.instructions if i.resource_id == "battery1"]
        assert battery_instructions
        for instruction in battery_instructions:
            assert isinstance(instruction, FRBCInstruction)
            assert instruction.peak_shaving_threshold_w == LIMIT_W

    def test_no_threshold_without_a_limit(self, config_eos):
        solution = _optimize_scenario(
            config_eos, "cheap_peak_then_expensive_hours", with_limit=False
        )
        plan = solution.energy_management_plan()

        for instruction in plan.instructions:
            assert isinstance(instruction, FRBCInstruction)
            assert instruction.peak_shaving_threshold_w is None

    def test_total_charge_setpoint_of_a_full_held_slot_is_exported(self, config_eos):
        """Total-charge inverter, full battery, 0.1 setpoint (500 Wh cap per hour).

        12 kWh load and 9 kWh PV at a 2 kW limit: the held battery shaves the
        peak and PV refills it up to the 500 Wh cap. The exported controls of
        the slot, derived by the plan export from the slot's start SoC (full),
        must keep that cap; without it PV refills more than simulated.
        """
        kwargs: dict[str, Any] = {
            "load": [12000.0],
            "pv": [9000.0],
            "ac": [0.1],
            "dc": [1.0],
            "total_charge": True,
            "limit_w": 2000.0,
        }
        result, sim = _simulate(config_eos, **kwargs)
        assert sim.ac_charge_hours is not None and sim.dc_charge_hours is not None
        assert sim.bat_discharge_hours is not None
        inverter = sim.inverter
        assert inverter is not None and inverter.battery is not None
        # Only what the export reads from a solution: its parameters and slot length.
        solution = cast(
            GeneticSolution,
            SimpleNamespace(
                parameters=SimpleNamespace(
                    pv_battery=inverter.battery.parameters, inverter=inverter.parameters
                ),
                interval_seconds=3600,
            ),
        )

        ac, dc, discharge, export = GeneticSolution._soc_clamped_operation_factors(
            solution,
            float(sim.ac_charge_hours[0]),
            float(sim.dc_charge_hours[0]),
            bool(sim.bat_discharge_hours[0]),
            float(result["akku_soc_pro_stunde"][0]),
            peak_shaving=True,
        )
        mode, factor = GeneticSolution._battery_operation_from_solution(
            ac, dc, discharge, export
        )
        replay_ac, replay_dc, replay_discharge = _MODE_CONTROLS[str(mode)](factor)
        replay, replay_sim = _simulate(
            config_eos,
            **{
                **kwargs,
                "ac": [replay_ac],
                "dc": [replay_dc],
                "discharge": [replay_discharge],
            },
        )
        assert replay_sim.inverter is not None and replay_sim.inverter.battery is not None

        assert result["Netzbezug_Wh_pro_Stunde"][0] == 2000.0
        assert inverter.battery.soc_wh < 10000.0
        np.testing.assert_allclose(
            replay["Netzbezug_Wh_pro_Stunde"], result["Netzbezug_Wh_pro_Stunde"]
        )
        assert replay_sim.inverter.battery.soc_wh == pytest.approx(inverter.battery.soc_wh)
