"""Tests for the grid import power limit of the GENETIC optimizer.

Commercial grid connections with interval metering pay a capacity price for
their highest 15-minute import. ``max_grid_import_power_w`` on the inverter
models that limit:

- AC (grid) charging of the battery is reduced, so that load plus charging
  stays within the limit.
- The battery discharges only where the plan releases it, as without a limit,
  so the exported instructions reproduce the simulated plan.
- Import above the limit is simulated as is and adds a penalty to the
  fitness, so the optimizer learns to release the battery for peaks, to keep
  reserve and to charge earlier.

The limit is a power. Per slot it is an energy of
``max_grid_import_power_w x slot duration``. All expected values are worked
out by hand in the tests.
"""

from types import SimpleNamespace
from typing import Any, Optional, cast
from unittest.mock import Mock, patch

import numpy as np
import pytest
from pydantic import ValidationError

from akkudoktoreos.config.config import ConfigEOS, SettingsEOS
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.core.emplan import FRBCInstruction
from akkudoktoreos.devices.genetic.battery import (
    Battery,
    ElectricVehicleParameters,
    SolarPanelBatteryParameters,
)
from akkudoktoreos.devices.genetic.inverter import Inverter, InverterParameters
from akkudoktoreos.devices.settings.invertersettings import InverterCommonSettings
from akkudoktoreos.optimization.genetic.genetic import (
    GeneticOptimization,
    GeneticSimulation,
)
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsettings import GeneticCommonSettings
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.genetic.tailvalue import (
    _simulate_action,
    build_tail_value_curve,
)
from akkudoktoreos.optimization.genetic.terminalvalue import TerminalValueCurve
from akkudoktoreos.utils.datetimeutil import to_datetime

LIMIT_W = 8000.0
SLOTS = 4


def _devices(
    *,
    slot_duration_h: float = 1.0,
    limit_w: Optional[float] = LIMIT_W,
    capacity_wh: int = 10000,
    soc_percentage: int = 100,
    min_soc_percentage: int = 0,
    battery_power_w: float = 5000.0,
    charging_efficiency: float = 1.0,
    discharging_efficiency: float = 1.0,
    inverter_power_w: float = 10000.0,
    ac_to_dc_efficiency: float = 1.0,
    dc_to_ac_efficiency: float = 1.0,
    total_charge: bool = False,
) -> tuple[Inverter, Battery]:
    """Real battery and inverter; the battery is held (not released) in every slot."""
    battery = Battery(
        SolarPanelBatteryParameters(
            device_id="battery1",
            capacity_wh=capacity_wh,
            initial_soc_percentage=soc_percentage,
            min_soc_percentage=min_soc_percentage,
            max_soc_percentage=100,
            charging_efficiency=charging_efficiency,
            discharging_efficiency=discharging_efficiency,
            max_charge_power_w=battery_power_w,
        ),
        prediction_hours=SLOTS,
        slot_duration_h=slot_duration_h,
    )
    battery.reset()
    # reset() creates integer arrays; charge factors are fractions.
    battery.charge_array = np.zeros(SLOTS)
    battery.discharge_array = np.zeros(SLOTS, dtype=np.int_)
    inverter = Inverter(
        InverterParameters(
            device_id="inverter1",
            max_power_wh=inverter_power_w,
            battery_id="battery1",
            ac_to_dc_efficiency=ac_to_dc_efficiency,
            dc_to_ac_efficiency=dc_to_ac_efficiency,
            ac_charge_limits_total_charge=total_charge,
            max_grid_import_power_w=limit_w,
        ),
        battery=battery,
        slot_duration_h=slot_duration_h,
    )
    return inverter, battery


def _ac_slot(
    inverter: Inverter, battery: Battery, load_wh: float, factor: float, pv_wh: float = 0.0
) -> tuple[float, float, float, float]:
    """Run one AC charge slot the way GeneticSimulation.simulate() does.

    Returns:
        Grid import of the slot including AC charging, AC charge energy, AC
        charge losses and the losses of process_energy().
    """
    battery.charge_array[0] = factor
    battery.discharge_array[0] = 0
    rate = inverter.ac_charge_factor(factor)
    inverter.begin_ac_charge_slot(0, rate)
    _, grid_import, losses, _ = inverter.process_energy(pv_wh, load_wh, 0)
    ac_wh, ac_losses = inverter.charge_battery_from_grid(0, rate, slot_grid_import_wh=grid_import)
    return grid_import + ac_wh, ac_wh, ac_losses, losses


# ---------------------------------------------------------------------------
# The battery discharges only where the plan releases it
# ---------------------------------------------------------------------------


class TestBatteryDischarge:
    def test_held_battery_stays_idle_above_the_limit(self):
        """12 kWh load in one hour, limit 8 kW: a held battery delivers nothing.

        A site controller that executes the plan's IDLE instruction does not
        discharge either, so the simulation imports the full load.
        """
        inverter, battery = _devices()

        grid_export, grid_import, losses, self_consumption = inverter.process_energy(
            0.0, 12000.0, 0
        )

        assert grid_import == pytest.approx(12000.0)
        assert self_consumption == 0.0
        assert battery.soc_wh == pytest.approx(10000.0)
        assert losses == 0.0
        assert grid_export == 0.0

    def test_released_battery_discharges_as_before(self):
        """A released battery serves the whole load up to its power, no extra."""
        limited, limited_battery = _devices(battery_power_w=5000.0)
        unlimited, unlimited_battery = _devices(battery_power_w=5000.0, limit_w=None)
        limited_battery.discharge_array[0] = 1
        unlimited_battery.discharge_array[0] = 1

        result = limited.process_energy(0.0, 12000.0, 0)
        reference = unlimited.process_energy(0.0, 12000.0, 0)

        assert result == reference
        assert result[1] == pytest.approx(7000.0)
        assert limited_battery.soc_wh == unlimited_battery.soc_wh == pytest.approx(5000.0)

    def test_released_battery_at_its_power_limit_is_not_discharged_twice(self):
        """The slot's discharge budget is shared: 3 kW battery, 12 kWh load."""
        inverter, battery = _devices(battery_power_w=3000.0)
        battery.discharge_array[0] = 1

        _, grid_import, _, _ = inverter.process_energy(0.0, 12000.0, 0)

        assert grid_import == pytest.approx(9000.0)
        assert battery.soc_wh == pytest.approx(7000.0)

    def test_load_below_limit_keeps_held_battery_idle(self):
        inverter, battery = _devices()

        _, grid_import, _, _ = inverter.process_energy(0.0, 7000.0, 0)

        assert grid_import == pytest.approx(7000.0)
        assert battery.soc_wh == pytest.approx(10000.0)


# ---------------------------------------------------------------------------
# AC charging is reduced first
# ---------------------------------------------------------------------------


class TestAcChargeReduction:
    def test_ac_charge_fills_up_to_the_limit(self):
        """Load 6 kWh, limit 8 kWh: 2 kWh AC instead of 5000 / 0.95 = 5263 Wh.

        DC into the battery 2000 x 0.95 = 1900 Wh, stored 1900 x 0.9 = 1710 Wh.
        Losses: 100 Wh conversion + 190 Wh battery.
        """
        inverter, battery = _devices(
            soc_percentage=0, ac_to_dc_efficiency=0.95, charging_efficiency=0.9
        )

        grid, ac_wh, ac_losses, _ = _ac_slot(inverter, battery, load_wh=6000.0, factor=1.0)

        assert ac_wh == pytest.approx(2000.0)
        assert grid == pytest.approx(8000.0)
        assert battery.soc_wh == pytest.approx(1710.0)
        assert ac_losses == pytest.approx(2000.0 - 1710.0)

    def test_quarter_hour_ac_charge_fills_up_to_the_limit(self):
        """15 min: load 1500 Wh, limit 2000 Wh, full-power charge 1250 Wh -> 500 Wh."""
        inverter, battery = _devices(slot_duration_h=0.25, soc_percentage=0)

        grid, ac_wh, _, _ = _ac_slot(inverter, battery, load_wh=1500.0, factor=1.0)

        assert ac_wh == pytest.approx(500.0)
        assert grid == pytest.approx(2000.0)
        assert battery.soc_wh == pytest.approx(500.0)

    def test_load_above_limit_stops_ac_charge(self):
        """Load 9 kWh in an AC slot: no grid charging; the held battery stays idle."""
        inverter, battery = _devices(soc_percentage=50)

        grid, ac_wh, ac_losses, _ = _ac_slot(inverter, battery, load_wh=9000.0, factor=1.0)

        assert ac_wh == 0.0
        assert ac_losses == 0.0
        assert grid == pytest.approx(9000.0)
        assert battery.soc_wh == pytest.approx(5000.0)

    def test_total_charge_model_is_capped_too(self):
        """Inverters whose AC setpoint caps the total charge respect the limit."""
        inverter, battery = _devices(soc_percentage=0, total_charge=True)

        grid, ac_wh, _, _ = _ac_slot(inverter, battery, load_wh=6000.0, factor=1.0)

        assert ac_wh == pytest.approx(2000.0)
        assert grid == pytest.approx(8000.0)

    def test_pv_charge_in_the_same_slot_counts_against_the_charge_budget(self):
        """PV took 3 kWh of the 5 kWh budget; the grid may add only 2 kWh of it.

        With a 1 kW limit the AC charge is capped further to 1 kWh.
        """
        inverter, battery = _devices(soc_percentage=0, limit_w=1000.0)

        grid, ac_wh, _, _ = _ac_slot(inverter, battery, load_wh=0.0, factor=1.0, pv_wh=3000.0)

        assert ac_wh == pytest.approx(1000.0)
        assert grid == pytest.approx(1000.0)
        assert battery.soc_wh == pytest.approx(4000.0)

    def test_limit_that_does_not_bind_changes_nothing(self):
        limited, limited_battery = _devices(soc_percentage=0, limit_w=1e9)
        unlimited, unlimited_battery = _devices(soc_percentage=0, limit_w=None)

        assert _ac_slot(limited, limited_battery, 2000.0, 1.0) == _ac_slot(
            unlimited, unlimited_battery, 2000.0, 1.0
        )
        assert limited_battery.soc_wh == unlimited_battery.soc_wh


# ---------------------------------------------------------------------------
# GeneticSimulation
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
    ev_power_w: float = 7000.0,
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
                max_charge_power_w=ev_power_w,
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


class TestGeneticSimulation:
    def test_ac_slot_with_peak_load_does_not_charge(self, config_eos):
        """Slot 1 plans AC charging during a 9 kWh load: no charge, no discharge."""
        result, sim = _simulate(
            config_eos,
            load=[1000.0, 9000.0, 1000.0],
            ac=[0.0, 1.0, 0.0],
            soc_percentage=50,
        )

        np.testing.assert_allclose(result["Netzbezug_Wh_pro_Stunde"], [1000.0, 9000.0, 1000.0])
        assert result["akku_soc_pro_stunde"][2] == pytest.approx(50.0)
        assert result["Kosten_Euro_pro_Stunde"][1] == pytest.approx(9000.0 * 0.0003)
        # The reported plan shows the AC charge that was simulated: none.
        assert sim.ac_charge_hours is not None
        assert list(sim.ac_charge_hours) == [0.0, 0.0, 0.0]

    def test_ac_charge_is_filled_up_to_the_limit(self, config_eos):
        result, sim = _simulate(config_eos, load=[6000.0, 1000.0], ac=[1.0, 0.0], soc_percentage=0)

        np.testing.assert_allclose(result["Netzbezug_Wh_pro_Stunde"], [8000.0, 1000.0])
        assert result["akku_soc_pro_stunde"][1] == pytest.approx(20.0)
        # 2000 Wh of the 5000 Wh charge budget: the reported factor is 0.4.
        assert sim.ac_charge_hours is not None
        assert sim.ac_charge_hours[0] == pytest.approx(0.4)
        replay = self._replay(config_eos, sim, load=[6000.0, 1000.0], soc_percentage=0)
        np.testing.assert_allclose(replay["akku_soc_pro_stunde"], result["akku_soc_pro_stunde"])

    def test_total_charge_model_reports_the_executed_total_factor(self, config_eos):
        """PV 1 kWh plus 2 kWh grid within a 2 kW limit: total 3 of 5 kWh -> 0.6."""
        result, sim = _simulate(
            config_eos,
            load=[0.0, 1000.0],
            ac=[1.0, 0.0],
            soc_percentage=0,
            total_charge=True,
            pv=[1000.0, 0.0],
            limit_w=2000.0,
        )

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(2000.0)
        assert result["akku_soc_pro_stunde"][1] == pytest.approx(30.0)
        assert sim.ac_charge_hours is not None
        assert sim.ac_charge_hours[0] == pytest.approx(0.6)

    @staticmethod
    def _replay(config_eos, sim: GeneticSimulation, **kwargs: Any) -> dict[str, Any]:
        """Simulate the reported controls again."""
        assert sim.ac_charge_hours is not None and sim.dc_charge_hours is not None
        replay, _ = _simulate(
            config_eos,
            ac=list(sim.ac_charge_hours),
            dc=list(sim.dc_charge_hours),
            **kwargs,
        )
        return replay

    @pytest.mark.parametrize(
        "pv_wh, expected_factor",
        [
            (6000.0, 0.5),  # PV fills the 2.5 kWh cap: the setpoint stays
            (1000.0, 0.2),  # PV charged 1 kWh under the cap, the grid nothing
        ],
    )
    def test_total_charge_model_keeps_the_pv_part_without_headroom(
        self, config_eos, pv_wh, expected_factor
    ):
        """No grid headroom does not mean a zero total-charge setpoint."""
        kwargs: dict[str, Any] = dict(
            load=[0.0, 1000.0],
            soc_percentage=0,
            total_charge=True,
            pv=[pv_wh, 0.0],
            limit_w=0.0,
        )
        result, sim = _simulate(config_eos, ac=[0.5, 0.0], **kwargs)

        assert sim.ac_charge_hours is not None
        assert sim.ac_charge_hours[0] == pytest.approx(expected_factor)
        replay = self._replay(config_eos, sim, **kwargs)
        np.testing.assert_allclose(replay["akku_soc_pro_stunde"], result["akku_soc_pro_stunde"])
        np.testing.assert_allclose(
            replay["Netzbezug_Wh_pro_Stunde"], result["Netzbezug_Wh_pro_Stunde"]
        )

    def test_cancelled_ac_slot_keeps_its_pv_charge_permission(self, config_eos):
        """Direct marketing: AC states carry dc_charge = 0, yet allow PV charging.

        Zero limit, empty battery, 3 kWh PV: the slot stores the PV. Reporting
        (ac=0, dc=0) would make it IDLE and lose that energy on replay.
        """
        kwargs: dict[str, Any] = dict(
            load=[0.0, 1000.0],
            soc_percentage=0,
            pv=[3000.0, 0.0],
            limit_w=0.0,
            discharge=[0.0, 1.0],
        )
        result, sim = _simulate(config_eos, ac=[1.0, 0.0], dc=[0.0, 0.0], **kwargs)

        assert result["akku_soc_pro_stunde"][1] == pytest.approx(30.0)
        assert sim.ac_charge_hours is not None and sim.dc_charge_hours is not None
        assert sim.ac_charge_hours[0] == 0.0
        assert sim.dc_charge_hours[0] == 1.0
        replay = self._replay(config_eos, sim, **kwargs)
        np.testing.assert_allclose(replay["akku_soc_pro_stunde"], result["akku_soc_pro_stunde"])
        np.testing.assert_allclose(
            replay["Netzbezug_Wh_pro_Stunde"], result["Netzbezug_Wh_pro_Stunde"]
        )

    def test_reported_factor_is_kept_when_the_limit_does_not_bind(self, config_eos):
        _, sim = _simulate(config_eos, load=[1000.0, 1000.0], ac=[0.5, 0.0], soc_percentage=0)

        assert sim.ac_charge_hours is not None
        assert list(sim.ac_charge_hours) == [0.5, 0.0]

    def test_ev_charging_counts_towards_the_limit(self, config_eos):
        """7 kWh EV charging leave 1 kWh of the 8 kWh limit for AC charging."""
        result, sim = _simulate(
            config_eos,
            load=[0.0, 1000.0],
            ac=[1.0, 0.0],
            ev_charge=[1.0, 0.0],
            soc_percentage=0,
        )

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(8000.0)
        assert result["akku_soc_pro_stunde"][1] == pytest.approx(10.0)
        assert sim.ac_charge_hours is not None
        assert sim.ac_charge_hours[0] == pytest.approx(0.2)

    @pytest.mark.parametrize(
        ("limit_w", "expected_import_wh", "expected_soc"),
        [
            # 1 kWh load + 11 kWh EV leave 17 kWh of the 29 kWh limit for the
            # battery: 17 / 60 kWh = 28.33 %.
            (29000.0, 29000.0, 17000 / 60000 * 100),
            # Without a limit the full 27 kWh AC charge is drawn on top.
            (None, 39000.0, 45.0),
        ],
    )
    def test_ac_charge_gives_way_to_ev_charging_at_the_house_connection(
        self, config_eos, limit_w, expected_import_wh, expected_soc
    ):
        """27 kW AC charging plus an 11 kW EV must not exceed a 29 kW connection."""
        result, sim = _simulate(
            config_eos,
            load=[1000.0, 0.0],
            ac=[1.0, 0.0],
            ev_charge=[1.0, 0.0],
            ev_power_w=11000.0,
            limit_w=limit_w,
            capacity_wh=60000,
            soc_percentage=0,
            battery_power_w=27000.0,
            inverter_power_w=30000.0,
        )

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(expected_import_wh)
        assert result["akku_soc_pro_stunde"][1] == pytest.approx(expected_soc)
        # EV charging is not cut: it keeps the 11 kWh the plan asked for.
        assert result["EAuto_SoC_pro_Stunde"][1] == pytest.approx(11000 / 60000 * 100)

    def test_without_limit_the_load_is_imported(self, config_eos):
        result, _ = _simulate(
            config_eos,
            load=[1000.0, 9000.0, 1000.0],
            ac=[0.0, 1.0, 0.0],
            soc_percentage=50,
            limit_w=None,
        )

        assert result["Netzbezug_Wh_pro_Stunde"][1] == pytest.approx(9000.0 + 5000.0)


# ---------------------------------------------------------------------------
# Fitness penalty
# ---------------------------------------------------------------------------


def _fitness(
    config_eos: ConfigEOS,
    simulation_result: dict[str, Any],
    inverter: Optional[Inverter],
    penalties: Optional[dict[str, Any]] = None,
) -> float:
    """Evaluate a fixed simulation result; only the penalties are added."""
    config_eos.optimization.genetic.penalties = penalties or {
        "ev_soc_miss": 10,
        "ac_charge_break_even": 1.0,
    }
    optimizer = GeneticOptimization.__new__(GeneticOptimization)
    optimizer.optimize_ev = False
    optimizer.simulation = cast(
        GeneticSimulation, SimpleNamespace(battery=None, inverter=inverter, ev=None)
    )

    class _Individual(list):
        pass

    with patch.object(optimizer, "evaluate_inner", return_value=simulation_result):
        fitness = optimizer.evaluate(
            _Individual([0] * 4),
            parameters=Mock(ems=Mock(price_per_wh_battery=0.0), ev=None),
            start_hour=0,
            worst_case=False,
        )
    return fitness[0]


def _result(grid_import: list[float], balance: float = 1.5) -> dict[str, Any]:
    return {
        "Gesamtbilanz_Euro": balance,
        "Gesamt_Verluste": 0.0,
        "Netzbezug_Wh_pro_Stunde": np.array(grid_import, dtype=float),
    }


class TestFitnessPenalty:
    def test_excess_import_is_penalised_per_kwh(self, config_eos):
        """15 min, 8 kW: excess 1000 + 0 + 572.5 Wh at the default 10 EUR/kWh."""
        inverter, _ = _devices(slot_duration_h=0.25)

        fitness = _fitness(config_eos, _result([3000.0, 1500.0, 2572.5]), inverter)

        assert fitness == pytest.approx(1.5 + 1.5725 * 10.0)

    def test_penalty_is_configurable(self, config_eos):
        inverter, _ = _devices(slot_duration_h=0.25)

        fitness = _fitness(
            config_eos,
            _result([3000.0, 1500.0, 2572.5]),
            inverter,
            penalties={"grid_import_limit_excess": 2.0},
        )

        assert fitness == pytest.approx(1.5 + 1.5725 * 2.0)

    def test_no_penalty_without_limit_or_without_excess(self, config_eos):
        unlimited, _ = _devices(limit_w=None)
        limited, _ = _devices()

        assert _fitness(config_eos, _result([12000.0, 9000.0]), unlimited) == 1.5
        assert _fitness(config_eos, _result([8000.0, 7999.0]), limited) == 1.5

    def test_penalty_follows_the_energy_the_battery_delivered(self, config_eos):
        """A released, power-limited battery delivers 427.5 Wh: 572.5 Wh are penalised."""
        result, sim = _simulate(
            config_eos,
            load=[3000.0],
            discharge=[1.0],
            slot_duration_h=1.0,
            limit_w=2000.0,
            battery_power_w=500.0,
            discharging_efficiency=0.9,
            dc_to_ac_efficiency=0.95,
        )
        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(3000.0 - 427.5)

        fitness = _fitness(config_eos, result, sim.inverter)

        assert fitness == pytest.approx(result["Gesamtbilanz_Euro"] + 0.5725 * 10.0)

    @staticmethod
    def _fitness_with_break_even(
        config_eos: ConfigEOS, result: dict[str, Any], sim: GeneticSimulation
    ) -> float:
        """Evaluate a real simulation result with the AC break-even penalty active."""
        config_eos.optimization.genetic.penalties = {"ac_charge_break_even": 1.0}
        optimizer = GeneticOptimization.__new__(GeneticOptimization)
        optimizer.optimize_ev = False
        optimizer.simulation = sim
        optimizer._run_control_end_slot = len(result["Netzbezug_Wh_pro_Stunde"])

        class _Individual(list):
            pass

        with patch.object(optimizer, "evaluate_inner", return_value=result):
            fitness = optimizer.evaluate(
                _Individual([0] * 2),
                parameters=Mock(ems=Mock(price_per_wh_battery=0.0), ev=None),
                start_hour=0,
                worst_case=False,
            )
        return fitness[0]

    def test_break_even_penalty_ignores_pv_kept_in_a_total_charge_setpoint(self, config_eos):
        """Zero limit: the reported 0.2 is PV only, no grid charge to penalise."""
        result, sim = _simulate(
            config_eos,
            load=[0.0, 1000.0],
            ac=[0.5, 0.0],
            discharge=[0.0, 1.0],
            soc_percentage=0,
            total_charge=True,
            pv=[1000.0, 0.0],
            price=[0.0003, 0.0001],
            limit_w=0.0,
        )
        assert sim.ac_charge_hours is not None
        assert sim.ac_charge_hours[0] == pytest.approx(0.2)

        fitness = self._fitness_with_break_even(config_eos, result, sim)

        assert fitness == pytest.approx(result["Gesamtbilanz_Euro"])

    def test_break_even_penalty_follows_the_grid_charge_drawn(self, config_eos):
        """2 kWh drawn at 0.30 for a 0.10 future price: 2 kWh x 0.20 EUR/kWh."""
        result, sim = _simulate(
            config_eos,
            load=[6000.0, 1000.0],
            ac=[1.0, 0.0],
            soc_percentage=0,
            price=[0.0003, 0.0001],
        )

        fitness = self._fitness_with_break_even(config_eos, result, sim)

        assert fitness == pytest.approx(result["Gesamtbilanz_Euro"] + 2000.0 * 0.0002)


# ---------------------------------------------------------------------------
# Tail lookahead
# ---------------------------------------------------------------------------


class TestTail:
    def test_tail_action_respects_the_limit(self):
        inverter, battery = _devices(soc_percentage=100)
        battery.charge_array = np.zeros(1)
        battery.discharge_array = np.zeros(1, dtype=np.int_)

        hold = _simulate_action(
            bat=battery,
            inv=inverter,
            energy_wh=10000.0,
            action=(1, 0, 0.0, 0.0),
            price=0.0003,
            load=12000.0,
            pv=0.0,
            tariff=0.0,
            direct_marketing=False,
        )
        charge = _simulate_action(
            bat=battery,
            inv=inverter,
            energy_wh=0.0,
            action=(1, 0, 1.0, 0.0),
            price=0.0003,
            load=6000.0,
            pv=0.0,
            tariff=0.0,
            direct_marketing=False,
        )

        discharge = _simulate_action(
            bat=battery,
            inv=inverter,
            energy_wh=10000.0,
            action=(1, 1, 0.0, 0.0),
            price=0.0003,
            load=12000.0,
            pv=0.0,
            tariff=0.0,
            direct_marketing=False,
        )

        # Holding the battery imports the whole load; releasing it covers 5 kWh.
        assert hold["grid_import_wh"] == pytest.approx(12000.0)
        assert hold["next_state_wh"] == pytest.approx(10000.0)
        assert discharge["grid_import_wh"] == pytest.approx(7000.0)
        assert discharge["next_state_wh"] == pytest.approx(5000.0)
        assert charge["ac_grid_charge_wh"] == pytest.approx(2000.0)
        assert charge["grid_import_wh"] == pytest.approx(8000.0)

    @staticmethod
    def _curve(limit_w: Optional[float], penalty_euro_per_wh: float):
        inverter, battery = _devices(limit_w=limit_w)
        return build_tail_value_curve(
            battery=battery,
            inverter=inverter,
            prices_euro_per_wh=np.array([0.0003]),
            load_wh=np.array([12000.0]),
            pv_wh=np.array([0.0]),
            feed_in_euro_per_wh=np.array([0.0]),
            continuation=TerminalValueCurve(),
            charge_rates=[1.0],
            export_rates=[1.0],
            direct_marketing=False,
            grid_import_penalty_euro_per_wh=penalty_euro_per_wh,
        )

    def test_tail_values_reserve_for_a_peak(self):
        """One 12 kWh tail hour, 8 kW limit, 0.01 EUR/Wh penalty.

        Empty: 12 kWh at 0.3 EUR/kWh plus 4 kWh excess = -3.6 - 40.
        2 kWh stored: 10 kWh import, 2 kWh excess = -3.0 - 20.
        Full: discharge 5 kWh (battery power), 7 kWh import = -2.1.
        """
        limited = self._curve(LIMIT_W, 0.01)
        unlimited = self._curve(None, 0.01)

        assert limited.value(0.0) == pytest.approx(-43.6)
        assert limited.value(2000.0) == pytest.approx(-23.0)
        assert limited.value(10000.0) == pytest.approx(-2.1)
        assert unlimited.value(0.0) == pytest.approx(-3.6)
        assert unlimited.value(10000.0) == pytest.approx(-2.1)

    def test_tail_plan_reports_the_penalised_slot(self):
        plan = self._curve(LIMIT_W, 0.01).diagnostic_plan(0.0, 0.0)

        assert plan[0].grid_import_wh == pytest.approx(12000.0)
        assert plan[0].slot_value_euro == pytest.approx(-43.6)


# ---------------------------------------------------------------------------
# Configuration and request path
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_device_setting_reaches_the_genetic_parameters(self):
        settings = InverterCommonSettings(
            device_id="inverter1", max_power_w=10000, max_grid_import_power_w=8000
        )

        assert settings.to_genetic_param().max_grid_import_power_w == 8000

    def test_configuration_round_trip_keeps_the_limit(self, config_eos):
        """Serialised and loaded again, the device setting and the config keep the limit."""
        settings = InverterCommonSettings(
            device_id="inverter1", max_power_w=10000, max_grid_import_power_w=8000
        )
        reloaded = InverterCommonSettings.model_validate_json(settings.model_dump_json())
        assert reloaded.max_grid_import_power_w == 8000

        config_eos.merge_settings_from_dict(
            {
                "devices": {
                    "inverters": {
                        "inverter1": {
                            "device_id": "inverter1",
                            "max_power_w": 10000,
                            "max_grid_import_power_w": 8000,
                        }
                    }
                }
            }
        )
        dumped = SettingsEOS.model_validate(config_eos.model_dump()).model_dump_json()
        config_reloaded = SettingsEOS.model_validate_json(dumped)
        assert config_reloaded.devices is not None
        assert config_reloaded.devices.inverters is not None
        assert config_reloaded.devices.inverters["inverter1"].max_grid_import_power_w == 8000

    def test_default_is_unlimited(self):
        settings = InverterCommonSettings(device_id="inverter1", max_power_w=10000)

        assert settings.max_grid_import_power_w is None
        assert settings.to_genetic_param().max_grid_import_power_w is None
        assert InverterParameters(device_id="i", max_power_wh=1).max_grid_import_power_w is None

    @pytest.mark.parametrize("value", [-1.0, float("nan")])
    def test_invalid_values_are_rejected(self, value):
        with pytest.raises(ValidationError):
            InverterParameters(device_id="i", max_power_wh=1, max_grid_import_power_w=value)
        with pytest.raises(ValidationError):
            InverterCommonSettings(device_id="i", max_power_w=1, max_grid_import_power_w=value)

    @pytest.mark.parametrize(
        "value", [-10.0, "-1", float("nan"), "nan", float("inf"), "inf", "ten"]
    )
    def test_invalid_penalty_is_rejected(self, value):
        with pytest.raises(ValidationError):
            GeneticCommonSettings(penalties={"grid_import_limit_excess": value})

    @pytest.mark.parametrize("value", [0, 0.0, 2.5, "3"])
    def test_valid_penalty_is_accepted(self, value):
        settings = GeneticCommonSettings(penalties={"grid_import_limit_excess": value})

        assert float(settings.penalties["grid_import_limit_excess"]) == float(value)

    def test_invalid_penalty_is_rejected_on_config_update(self, config_eos):
        with pytest.raises(ValueError):
            config_eos.merge_settings_from_dict(
                {"optimization": {"genetic": {"penalties": {"grid_import_limit_excess": -1}}}}
            )

    def test_penalty_default_applies_only_without_the_key(self, config_eos):
        inverter, _ = _devices(slot_duration_h=0.25)
        result = _result([3000.0])

        assert _fitness(config_eos, result, inverter, penalties={"ev_soc_miss": 10}) == (
            pytest.approx(1.5 + 1.0 * 10.0)
        )
        assert _fitness(
            config_eos, result, inverter, penalties={"grid_import_limit_excess": 0.0}
        ) == pytest.approx(1.5)

    def test_optimize_request_accepts_the_limit(self):
        parameters = GeneticOptimizationParameters.model_validate(
            {
                "ems": {
                    "pv_forecast_wh": [0.0],
                    "electricity_price_per_wh": [0.0003],
                    "feed_in_tariff_per_wh": 0.0,
                    "price_per_wh_battery": 0.0,
                    "total_load": [0.0],
                },
                "pv_battery": None,
                "ev": None,
                "inverter": {
                    "device_id": "inverter1",
                    "max_power_wh": 10000,
                    "max_grid_import_power_w": 8000,
                },
            }
        )

        assert parameters.inverter is not None
        assert parameters.inverter.max_grid_import_power_w == 8000


# ---------------------------------------------------------------------------
# Real optimizer runs
# ---------------------------------------------------------------------------

BASE_SLOTS = 12
PEAK_SLOTS = 4


def _optimize(
    config_eos: ConfigEOS, limit_w: Optional[float], seed: int = 42, peak_in_tail: bool = False
):
    """Four quarter-hour hours: 1 kW base load, then one hour at 12 kW.

    Flat prices make grid charging a pure loss, so without a limit the
    optimizer leaves the empty battery alone and imports the 12 kW peak.
    With ``peak_in_tail`` the four hours are all base load and the peak hour
    follows in a four-hour tail.
    """
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": 4,
                    "tail_horizon_hours": 4 if peak_in_tail else 0,
                    "interval_sec": 900,
                    "terminal_value_mode": "AUTO" if peak_in_tail else "FIXED",
                    "terminal_value_euro_per_kwh": 0.0,
                },
            },
        }
    )
    get_ems(init=True).set_start_datetime(to_datetime("2026-10-26T00:00:00"))
    if peak_in_tail:
        load = [250.0] * 16 + [3000.0] * PEAK_SLOTS + [250.0] * 12
    else:
        load = [250.0] * BASE_SLOTS + [3000.0] * PEAK_SLOTS
    slots = len(load)
    parameters = GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_forecast_wh": [0.0] * slots,
                "total_load": load,
                "electricity_price_per_wh": [0.0003] * slots,
                "feed_in_tariff_per_wh": 0.00008,
                "price_per_wh_battery": 0.0,
            },
            "pv_battery": {
                "device_id": "battery1",
                "capacity_wh": 10000,
                "max_charge_power_w": 5000,
                "charging_efficiency": 0.95,
                "discharging_efficiency": 0.95,
                "initial_soc_percentage": 0,
            },
            "inverter": {
                "device_id": "inverter1",
                "max_power_wh": 10000,
                "battery_id": "battery1",
                "ac_to_dc_efficiency": 0.95,
                "dc_to_ac_efficiency": 0.95,
                "max_grid_import_power_w": limit_w,
            },
            "ev": None,
            "forecast_interval_seconds": 900,
        }
    )
    optimizer = GeneticOptimization(fixed_seed=seed)
    return optimizer.optimize_ems(parameters, ngen=40, individuals=40)


class TestOptimization:
    def test_plan_charges_before_the_peak_and_stays_within_the_limit(self, config_eos):
        unlimited = _optimize(config_eos, None)
        limited = _optimize(config_eos, LIMIT_W)

        unlimited_import = unlimited.result.grid_consumption_wh_per_hour
        limited_import = limited.result.grid_consumption_wh_per_hour
        assert max(unlimited_import) == pytest.approx(3000.0)
        assert max(limited_import) <= LIMIT_W * 0.25 + 1e-6
        assert sum(limited.ac_charge[:BASE_SLOTS]) > 0.0

    def test_plan_keeps_reserve_for_a_peak_beyond_the_control_horizon(self, config_eos):
        """The peak lies in the tail: only the tail penalty makes charging worth it."""
        unlimited = _optimize(config_eos, None, peak_in_tail=True)
        limited = _optimize(config_eos, LIMIT_W, peak_in_tail=True)

        assert unlimited.result.battery_soc_per_hour[-1] == pytest.approx(0.0)
        assert sum(unlimited.ac_charge) == 0.0
        # Keeping the peak within the limit needs at least 4 x 1000 Wh AC from the
        # battery, i.e. 4000 / 0.95 / 0.95 = 4432 Wh stored.
        assert limited.terminal_value.battery_energy_wh >= 4000.0 - 1e-6
        assert sum(limited.ac_charge) > 0.0

    def test_limit_that_never_binds_gives_the_identical_plan(self, config_eos):
        """None and a limit above every import give bit-identical results."""
        unlimited = _optimize(config_eos, None)
        never_binding = _optimize(config_eos, 1e9)

        assert never_binding.ac_charge == unlimited.ac_charge
        assert never_binding.discharge_allowed == unlimited.discharge_allowed
        assert never_binding.start_solution == unlimited.start_solution
        assert never_binding.result.model_dump() == unlimited.result.model_dump()
        assert never_binding.fitness_history == unlimited.fitness_history


# ---------------------------------------------------------------------------
# A real run with an EV deadline at a 29 kW house connection
# ---------------------------------------------------------------------------

HOUSE_LIMIT_W = 29000.0
HOUSE_SLOTS = 24
EV_DEADLINE_SLOT = 16
CHEAP_EV_SLOTS = range(12, 16)


def _optimize_house(
    config_eos: ConfigEOS, limit_w: Optional[float], seed: int = 3
) -> GeneticSolution:
    """The 29 kW case as a run: battery 27 kW, EV 11 kW and 1 kW load want the same cheap hour.

    Six quarter-hour hours. The EV needs 10.8 kWh (18 % of 60 kWh, four full
    11 kW slots) by 04:00 (slot 16); the cheapest hour 03:00-04:00 is also
    where AC charging the empty battery for the 12 kW evening load pays most.
    """
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": 6,
                    "tail_horizon_hours": 0,
                    "interval_sec": 900,
                    "terminal_value_mode": "FIXED",
                    "terminal_value_euro_per_kwh": 0.0,
                },
            },
            "feedintariff": {"direct_marketing_enabled": False},
        }
    )
    get_ems(init=True).set_start_datetime(to_datetime("2026-11-05T00:00:00"))
    prices = [0.0003] * HOUSE_SLOTS
    for slot in CHEAP_EV_SLOTS:
        prices[slot] = 0.0001
    for slot in range(16, HOUSE_SLOTS):
        prices[slot] = 0.0005
    load = [250.0] * 16 + [3000.0] * 8
    parameters = GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_forecast_wh": [0.0] * HOUSE_SLOTS,
                "total_load": load,
                "electricity_price_per_wh": prices,
                "feed_in_tariff_per_wh": 0.0,
                "price_per_wh_battery": 0.0,
            },
            "pv_battery": {
                "device_id": "battery1",
                "capacity_wh": 60000,
                "max_charge_power_w": 27000,
                "charging_efficiency": 0.95,
                "discharging_efficiency": 0.95,
                "initial_soc_percentage": 5,
                "min_soc_percentage": 5,
                "charge_rates": [round(0.1 * i, 1) for i in range(11)],
            },
            "inverter": {
                "device_id": "inverter1",
                "max_power_wh": 30000,
                "battery_id": "battery1",
                "ac_to_dc_efficiency": 1.0,
                "dc_to_ac_efficiency": 0.97,
                "max_ac_charge_power_w": 27000,
                "max_grid_import_power_w": limit_w,
            },
            "ev": {
                "device_id": "car",
                "capacity_wh": 60000,
                "initial_soc_percentage": 0,
                "min_soc_percentage": 18,
                "max_soc_percentage": 100,
                "charging_efficiency": 1.0,
                "max_charge_power_w": 11000,
                "charge_rates": [0.0, 0.5, 1.0],
                "min_soc_deadline_datetime": "2026-11-05T04:00:00",
            },
            "forecast_interval_seconds": 900,
        }
    )
    return GeneticOptimization(fixed_seed=seed).optimize_ems(parameters, ngen=30, individuals=40)


class TestOptimizationWithEv:
    def test_ac_charge_and_ev_share_a_29_kw_connection(self, config_eos):
        unlimited = _optimize_house(config_eos, None)
        limited = _optimize_house(config_eos, HOUSE_LIMIT_W)

        slot_limit_wh = HOUSE_LIMIT_W * 0.25
        # Without the limit the plan draws 27 kW AC charge plus the EV on top.
        assert max(unlimited.result.grid_consumption_wh_per_hour) > slot_limit_wh
        assert max(limited.result.grid_consumption_wh_per_hour) <= slot_limit_wh + 1e-6
        assert limited.ev_charge_hours_float is not None
        # The EV still reaches its target by the deadline.
        assert limited.result.ev_soc_per_hour[EV_DEADLINE_SLOT] >= 18.0 - 1e-6
        # The battery still charges from the grid for the evening, only less per slot.
        assert sum(limited.ac_charge[:EV_DEADLINE_SLOT]) > 0.0
        assert any(0.0 < factor < 1.0 for factor in limited.ac_charge[:EV_DEADLINE_SLOT])

    def test_without_limit_the_plan_is_unchanged_by_the_feature(self, config_eos):
        """None and a limit above every import give bit-identical results with an EV."""
        unlimited = _optimize_house(config_eos, None)
        never_binding = _optimize_house(config_eos, 1e9)

        assert never_binding.ev_charge_hours_float == unlimited.ev_charge_hours_float
        assert never_binding.ac_charge == unlimited.ac_charge
        assert never_binding.start_solution == unlimited.start_solution
        assert never_binding.result.model_dump() == unlimited.result.model_dump()
        assert never_binding.fitness_history == unlimited.fitness_history


# ---------------------------------------------------------------------------
# The exported instructions reproduce the simulated plan
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


# Scenarios for the replay: (load, PV, prices, battery capacity, limit,
# inverter power). A full 6 kWh battery is worth more in the expensive hours
# after a cheap 12 kWh peak hour. If the simulation let a held battery shave
# the peak, holding it would be the best plan, and its IDLE instruction would
# make a site controller import 12 kWh instead of 8 kWh.
_REPLAY_SCENARIOS = {
    "cheap_peak_then_expensive_hours": (
        [12000.0, 6000.0, 6000.0, 6000.0],
        [0.0, 0.0, 0.0, 0.0],
        [0.0003, 0.0006, 0.0006, 0.0006],
        6000,
        LIMIT_W,
        10000,
    ),
}


def _optimize_scenario(config_eos: ConfigEOS, name: str, seed: int = 42) -> GeneticSolution:
    """Hourly 4-slot run of a replay scenario with a full battery and no AC charging."""
    load, pv, prices, capacity_wh, limit_w, inverter_power_w = _REPLAY_SCENARIOS[name]
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
                "initial_soc_percentage": 100,
                "min_soc_percentage": 0,
            },
            "inverter": {
                "device_id": "inverter1",
                "max_power_wh": inverter_power_w,
                "battery_id": "battery1",
                "ac_to_dc_efficiency": 1.0,
                "dc_to_ac_efficiency": 1.0,
                "max_ac_charge_power_w": 0,
                "max_grid_import_power_w": limit_w,
            },
            "ev": None,
            "forecast_interval_seconds": 3600,
        }
    )
    return GeneticOptimization(fixed_seed=seed).optimize_ems(parameters, ngen=30, individuals=40)


class TestExportedPlan:
    @pytest.mark.parametrize("name", list(_REPLAY_SCENARIOS))
    def test_replayed_instructions_reproduce_the_simulated_plan(self, config_eos, name):
        """A site controller executing energy_management_plan() gets the simulated import."""
        load, pv, prices, capacity_wh, limit_w, inverter_power_w = _REPLAY_SCENARIOS[name]
        solution = _optimize_scenario(config_eos, name)
        plan = solution.energy_management_plan()

        start = get_ems().start_datetime
        controls: list[Optional[tuple[float, float, float]]] = [None] * 4
        for instruction in plan.instructions:
            if instruction.resource_id != "battery1":
                continue
            assert isinstance(instruction, FRBCInstruction)
            slot = int((instruction.execution_time - start).total_seconds() // 3600)
            mode = str(instruction.operation_mode_id)
            controls[slot] = _MODE_CONTROLS[mode](float(instruction.operation_mode_factor))
        for slot in range(1, 4):
            if controls[slot] is None:
                controls[slot] = controls[slot - 1]
        assert all(control is not None for control in controls)
        ac, dc, discharge = (list(values) for values in zip(*cast(Any, controls)))

        # The controller knows nothing of the optimizer's limit, so the replay
        # runs without it.
        replay, _ = _simulate(
            config_eos,
            limit_w=None,
            load=load,
            pv=pv,
            price=prices,
            ac=ac,
            dc=dc,
            discharge=discharge,
            capacity_wh=capacity_wh,
            inverter_power_w=inverter_power_w,
            soc_percentage=100,
        )

        np.testing.assert_allclose(
            replay["Netzbezug_Wh_pro_Stunde"], solution.result.grid_consumption_wh_per_hour
        )
        np.testing.assert_allclose(
            replay["akku_soc_pro_stunde"], solution.result.battery_soc_per_hour
        )


# ---------------------------------------------------------------------------
# The reported AC factor read as a share of the rated charge power
# ---------------------------------------------------------------------------


def _controller_grid_charge_wh(
    factor: float,
    rated_wh: float,
    pv_charge_wh: float,
    room_wh: float,
    ac_to_dc_efficiency: float = 1.0,
) -> float:
    """AC energy an independent site controller draws for an AC charge factor.

    The controller charges ``factor`` x the rated charge power from the grid;
    the battery takes at most what PV left of its rated power and of its room.
    """
    dc_wh = min(factor * rated_wh, rated_wh - pv_charge_wh, room_wh)
    return max(dc_wh, 0.0) / ac_to_dc_efficiency


class TestExecutedAcFactor:
    @pytest.mark.parametrize("ac_to_dc_efficiency", [1.0, 0.95])
    @pytest.mark.parametrize("limit_w", [1000.0, 1500.0])
    def test_lowered_factor_reproduces_the_grid_charge_at_rated_power(
        self, config_eos, limit_w, ac_to_dc_efficiency
    ):
        """PV charges 3 kWh of the 5 kWh slot, the limit leaves 1 or 1.5 kWh for the grid.

        The simulation applies its factor to the 2 kWh PV left. Reported as a
        share of that budget (0.5 or 0.75), a controller charging factor x
        5 kW would draw 2 kWh. Reported as a share of the rated 5 kWh it
        draws exactly the simulated grid charge.
        """
        result, sim = _simulate(
            config_eos,
            load=[0.0],
            pv=[3000.0],
            ac=[1.0],
            soc_percentage=20,
            limit_w=limit_w,
            ac_to_dc_efficiency=ac_to_dc_efficiency,
        )

        grid_wh = float(result["Netzbezug_Wh_pro_Stunde"][0])
        assert grid_wh == pytest.approx(limit_w)
        assert sim.ac_charge_hours is not None
        factor = float(sim.ac_charge_hours[0])
        assert factor == pytest.approx(limit_w * ac_to_dc_efficiency / 5000.0)
        controller_wh = _controller_grid_charge_wh(
            factor, 5000.0, 3000.0, 8000.0, ac_to_dc_efficiency
        )
        assert controller_wh == pytest.approx(grid_wh)

    def test_factor_within_the_budget_is_reported_at_rated_power_too(self, config_eos):
        """Gene 0.5 with 3 kWh PV: the simulation draws 0.5 x 2 kWh = 1 kWh, within 1.5 kW.

        Read as 0.5 x 5 kW, a controller would draw the whole 2 kWh PV left and
        exceed the limit. The reported 0.2 draws the simulated 1 kWh.
        """
        result, sim = _simulate(
            config_eos, load=[0.0], pv=[3000.0], ac=[0.5], soc_percentage=20, limit_w=1500.0
        )

        grid_wh = float(result["Netzbezug_Wh_pro_Stunde"][0])
        assert grid_wh == pytest.approx(1000.0)
        assert sim.ac_charge_hours is not None
        factor = float(sim.ac_charge_hours[0])
        assert factor == pytest.approx(0.2)
        assert _controller_grid_charge_wh(factor, 5000.0, 3000.0, 8000.0) == pytest.approx(grid_wh)

    def test_factor_that_fits_at_rated_power_is_kept(self, config_eos):
        """Gene 0.2 x 5 kW = 1 kWh fits the 1.5 kW limit: the factor stays as on main."""
        _, sim = _simulate(
            config_eos, load=[0.0], pv=[3000.0], ac=[0.2], soc_percentage=20, limit_w=1500.0
        )

        assert sim.ac_charge_hours is not None
        assert float(sim.ac_charge_hours[0]) == 0.2

    def test_total_charge_model_reports_the_total_cap(self, config_eos):
        """The total-charge cap is PV plus grid: 3 kWh + 1 kWh of 5 kWh."""
        result, sim = _simulate(
            config_eos,
            load=[0.0],
            pv=[3000.0],
            ac=[1.0],
            soc_percentage=20,
            limit_w=1000.0,
            total_charge=True,
        )

        assert float(result["Netzbezug_Wh_pro_Stunde"][0]) == pytest.approx(1000.0)
        assert sim.ac_charge_hours is not None
        assert float(sim.ac_charge_hours[0]) == pytest.approx(0.8)

    def test_charge_without_the_slot_import_is_rejected_with_a_limit(self):
        inverter, _ = _devices(soc_percentage=0)

        with pytest.raises(ValueError, match="slot_grid_import_wh"):
            inverter.charge_battery_from_grid(0, 1.0)

    def test_charge_without_a_limit_needs_no_slot_import(self):
        inverter, battery = _devices(soc_percentage=0, limit_w=None)
        battery.charge_array[0] = 1.0

        assert inverter.charge_battery_from_grid(0, 1.0) == (5000.0, 0.0)


class TestFitnessPenaltyEdges:
    def test_limit_zero_penalises_every_import(self, config_eos):
        """A limit of 0 W is a limit, not "no limit": 100 Wh excess at 10 EUR/kWh."""
        inverter, _ = _devices(limit_w=0.0)

        assert _fitness(config_eos, _result([100.0, 0.0]), inverter) == pytest.approx(1.5 + 1.0)

    def test_rounding_above_the_limit_is_not_penalised(self, config_eos):
        inverter, _ = _devices()

        fitness = _fitness(config_eos, _result([8000.0 + 2e-12, 8000.0]), inverter)

        assert fitness == 1.5


def _optimize_pv_ac_slot(config_eos: ConfigEOS, seed: int = 42) -> GeneticSolution:
    """Hourly: negative price and 3 kWh PV in the first hour, expensive load afterwards.

    Grid charging pays in hour 0 while PV also charges the battery; the 1 kW
    limit leaves 1 kWh for the grid there.
    """
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
                "pv_forecast_wh": [3000.0, 0.0, 0.0, 0.0],
                "total_load": [0.0, 1000.0, 1000.0, 1000.0],
                "electricity_price_per_wh": [-0.0001, 0.0006, 0.0006, 0.0006],
                "feed_in_tariff_per_wh": 0.0,
                "price_per_wh_battery": 0.0,
            },
            "pv_battery": {
                "device_id": "battery1",
                "capacity_wh": 10000,
                "max_charge_power_w": 5000,
                "charging_efficiency": 1.0,
                "discharging_efficiency": 1.0,
                "initial_soc_percentage": 20,
                "min_soc_percentage": 0,
            },
            "inverter": {
                "device_id": "inverter1",
                "max_power_wh": 10000,
                "battery_id": "battery1",
                "ac_to_dc_efficiency": 1.0,
                "dc_to_ac_efficiency": 1.0,
                "max_grid_import_power_w": 1000.0,
            },
            "ev": None,
            "forecast_interval_seconds": 3600,
        }
    )
    return GeneticOptimization(fixed_seed=seed).optimize_ems(parameters, ngen=30, individuals=40)


class TestExportedLoweredFactor:
    def test_exported_factor_draws_the_simulated_grid_charge(self, config_eos):
        """energy_management_plan() of a grid-limited AC slot with PV charging.

        An independent controller charging factor x 5 kW from the grid next
        to the 3 kWh PV charge imports what the simulation planned, 1 kWh.
        """
        solution = _optimize_pv_ac_slot(config_eos)
        grid_wh = float(solution.result.grid_consumption_wh_per_hour[0])
        assert grid_wh == pytest.approx(1000.0)
        assert max(solution.result.grid_consumption_wh_per_hour) <= 1000.0 + 1e-6

        start = get_ems().start_datetime
        slot0 = [
            instruction
            for instruction in solution.energy_management_plan().instructions
            if instruction.resource_id == "battery1" and instruction.execution_time == start
        ]
        assert len(slot0) == 1
        instruction = slot0[0]
        assert isinstance(instruction, FRBCInstruction)
        ac, _, _ = _MODE_CONTROLS[str(instruction.operation_mode_id)](
            float(instruction.operation_mode_factor)
        )
        assert ac == pytest.approx(0.2)
        assert _controller_grid_charge_wh(ac, 5000.0, 3000.0, 8000.0) == pytest.approx(grid_wh)
