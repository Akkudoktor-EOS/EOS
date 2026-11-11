"""Tests for the grid import power limit of the GENETIC optimizer.

Commercial grid connections with interval metering pay a capacity price for
their highest 15-minute import. ``max_grid_import_power_w`` on the inverter
models that limit:

- AC (grid) charging of the battery is reduced, so that load plus charging
  stays within the limit.
- Where the plan holds the battery, it covers only the import above the limit
  (threshold peak shaving, see ``test_geneticpeakshavingthreshold.py``, which
  also replays the exported instructions).
- Import the battery cannot cover adds a penalty to the fitness, so the
  optimizer learns to keep reserve and to charge earlier.

The limit is a power. Per slot it is an energy of
``max_grid_import_power_w x slot duration``. All expected values are worked
out by hand in the tests.
"""

import math
from types import SimpleNamespace
from typing import Any, Optional, cast
from unittest.mock import Mock, patch

import numpy as np
import pytest
from deap import creator
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
        """Load 9 kWh in an AC slot of an empty battery: no grid charging."""
        inverter, battery = _devices(soc_percentage=0)

        grid, ac_wh, ac_losses, _ = _ac_slot(inverter, battery, load_wh=9000.0, factor=1.0)

        assert ac_wh == 0.0
        assert ac_losses == 0.0
        assert grid == pytest.approx(9000.0)
        assert battery.soc_wh == pytest.approx(0.0)

    def test_shaved_peak_leaves_no_ac_charge(self):
        """Load 9 kWh in an AC slot: the held battery shaves 1 kWh, the grid charges nothing."""
        inverter, battery = _devices(soc_percentage=50)

        grid, ac_wh, ac_losses, _ = _ac_slot(inverter, battery, load_wh=9000.0, factor=1.0)

        assert ac_wh == 0.0
        assert ac_losses == 0.0
        assert grid == 8000.0
        assert battery.soc_wh == pytest.approx(4000.0)

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
    ev_charge_rates: Optional[list[float]] = None,
    ev_soc: int = 0,
    slot_duration_h: float = 1.0,
    **device_kwargs: Any,
) -> tuple[dict[str, Any], GeneticSimulation]:
    config_eos.merge_settings_from_dict(
        {"prediction": {"hours": 48}, "optimization": {"genetic": {"horizon_hours": 24}}}
    )
    slots = len(load)
    inverter, battery = _devices(limit_w=limit_w, slot_duration_h=slot_duration_h, **device_kwargs)
    battery = Battery(battery.parameters, prediction_hours=slots, slot_duration_h=slot_duration_h)
    inverter = Inverter(inverter.parameters, battery=battery, slot_duration_h=slot_duration_h)
    ev = None
    if ev_charge is not None:
        ev = Battery(
            ElectricVehicleParameters(
                device_id="ev1",
                capacity_wh=60000,
                charging_efficiency=1.0,
                discharging_efficiency=1.0,
                max_charge_power_w=ev_power_w,
                initial_soc_percentage=ev_soc,
                min_soc_percentage=100,
                charge_rates=ev_charge_rates,
            ),
            prediction_hours=slots,
            slot_duration_h=slot_duration_h,
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
        """Slot 1 plans AC charging during a 9 kWh load: an empty battery stays empty."""
        result, sim = _simulate(
            config_eos,
            load=[1000.0, 9000.0, 1000.0],
            ac=[0.0, 1.0, 0.0],
            soc_percentage=0,
        )

        np.testing.assert_allclose(result["Netzbezug_Wh_pro_Stunde"], [1000.0, 9000.0, 1000.0])
        assert result["akku_soc_pro_stunde"][2] == pytest.approx(0.0)
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


class TestEvChargingWithinTheLimit:
    """EV charging is lowered to the highest charge rate that keeps the slot within the limit.

    PV, then the battery (released: for the whole load, held: for the import
    above the limit), then the grid up to the limit supply the EV; what does not
    fit is not charged. The EV rates are the default 0.0, 0.1, ... 1.0 of a 7 kW
    charger unless a test sets others.
    """

    @pytest.mark.parametrize("soc_percentage", [0, 10])
    def test_rate_is_lowered_to_the_highest_rate_within_the_limit(
        self, config_eos, soc_percentage
    ):
        """2 kWh load + 7 kWh EV, empty battery (or one at its minimum SoC): 0.8 fits."""
        result, sim = _simulate(
            config_eos,
            load=[2000.0, 0.0],
            ev_charge=[1.0, 0.0],
            soc_percentage=soc_percentage,
            min_soc_percentage=soc_percentage,
        )

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(7600.0)
        assert result["EAuto_SoC_pro_Stunde"][1] == pytest.approx(5600 / 60000 * 100)
        assert sim.ev_charge_hours is not None
        assert sim.ev_charge_hours[0] == pytest.approx(0.8)
        assert result["ev_grid_limited_charge_factors"] == {0: pytest.approx(0.8)}

    def test_held_battery_shaves_for_the_ev_up_to_the_limit(self, config_eos):
        """7 kWh load + 7 kWh EV, full held battery (5 kW): 0.8 fits with 4.6 kWh shaved."""
        result, sim = _simulate(config_eos, load=[7000.0, 0.0], ev_charge=[1.0, 0.0])

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(8000.0)
        assert result["peak_shaving_wh_per_hour"][0] == pytest.approx(4600.0)
        assert result["akku_soc_pro_stunde"][1] == pytest.approx(54.0)
        assert result["EAuto_SoC_pro_Stunde"][1] == pytest.approx(5600 / 60000 * 100)
        assert sim.ev_charge_hours is not None
        assert sim.ev_charge_hours[0] == pytest.approx(0.8)

    def test_released_battery_covers_the_ev_with_the_load(self, config_eos):
        """Released, the battery discharges its 5 kW for load and EV: 0.8 fits at 7.6 kWh."""
        result, sim = _simulate(
            config_eos, load=[7000.0, 0.0], ev_charge=[1.0, 0.0], discharge=[1.0, 0.0]
        )

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(7600.0)
        assert result["akku_soc_pro_stunde"][1] == pytest.approx(50.0)
        assert sim.ev_charge_hours is not None
        assert sim.ev_charge_hours[0] == pytest.approx(0.8)

    def test_full_rate_that_fits_with_the_battery_is_kept(self, config_eos):
        """6 kWh load + 7 kWh EV: the held battery shaves 5 kWh, nothing is lowered."""
        result, sim = _simulate(config_eos, load=[6000.0, 0.0], ev_charge=[1.0, 0.0])

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(8000.0)
        assert sim.ev_charge_hours is not None
        assert sim.ev_charge_hours[0] == 1.0
        assert result["ev_grid_limited_charge_factors"] == {}

    def test_house_load_above_the_limit_stops_the_ev(self, config_eos):
        """9 kWh load, empty battery: the EV charges nothing, the load's excess stays."""
        result, sim = _simulate(
            config_eos, load=[9000.0, 0.0], ev_charge=[1.0, 0.0], soc_percentage=0
        )

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(9000.0)
        assert result["EAuto_SoC_pro_Stunde"][1] == 0.0
        assert sim.ev_charge_hours is not None
        assert sim.ev_charge_hours[0] == 0.0

    def test_ev_takes_the_headroom_before_ac_charging(self, config_eos):
        """1 kWh load, AC 1.0 and EV 1.0 (7 kWh): the EV keeps its rate, AC gets 0 kWh left."""
        result, sim = _simulate(
            config_eos, load=[1000.0, 0.0], ac=[1.0, 0.0], ev_charge=[1.0, 0.0], soc_percentage=0
        )

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(8000.0)
        assert sim.ev_charge_hours is not None
        assert sim.ev_charge_hours[0] == 1.0
        assert result["akku_soc_pro_stunde"][1] == pytest.approx(0.0)

    def test_quarter_hour_slot_lowers_to_the_next_rate(self, config_eos):
        """15 min, 11 kW EV with 0 / 0.5 / 1: 500 Wh load + 2750 Wh exceed 2000 Wh, 0.5 fits."""
        result, sim = _simulate(
            config_eos,
            load=[500.0, 0.0],
            ev_charge=[1.0, 0.0],
            ev_power_w=11000.0,
            ev_charge_rates=[0.0, 0.5, 1.0],
            slot_duration_h=0.25,
            soc_percentage=0,
        )

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(1875.0)
        assert sim.ev_charge_hours is not None
        assert sim.ev_charge_hours[0] == 0.5

    def test_ev_that_fills_up_within_the_slot_is_not_lowered(self, config_eos):
        """EV at 97 % (1.8 kWh room) with 6 kWh load: 7.8 kWh fit, the rate stays 1.0.

        The limit is an energy per slot (interval metering); the EV draws only
        what it has room for.
        """
        result, sim = _simulate(
            config_eos, load=[6000.0, 0.0], ev_charge=[1.0, 0.0], soc_percentage=0, ev_soc=97
        )

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(7800.0)
        assert sim.ev_charge_hours is not None
        assert sim.ev_charge_hours[0] == 1.0

    def test_without_limit_the_ev_keeps_its_rate(self, config_eos):
        result, sim = _simulate(
            config_eos, load=[7000.0, 0.0], ev_charge=[1.0, 0.0], soc_percentage=0, limit_w=None
        )

        assert result["Netzbezug_Wh_pro_Stunde"][0] == pytest.approx(14000.0)
        assert sim.ev_charge_hours is not None
        assert sim.ev_charge_hours[0] == 1.0
        assert "ev_grid_limited_charge_factors" not in result


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

        # Holding the battery shaves the 4 kWh above the limit; releasing it
        # covers 5 kWh.
        assert hold["grid_import_wh"] == pytest.approx(8000.0)
        assert hold["next_state_wh"] == pytest.approx(6000.0)
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

    @pytest.mark.parametrize("value", [-1.0, float("nan"), True, False])
    def test_invalid_values_are_rejected(self, value):
        """A boolean is no power: True must not be read as a 1 W limit."""
        with pytest.raises(ValidationError):
            InverterParameters(device_id="i", max_power_wh=1, max_grid_import_power_w=value)
        with pytest.raises(ValidationError):
            InverterCommonSettings(device_id="i", max_power_w=1, max_grid_import_power_w=value)

    @pytest.mark.parametrize("value", [-0.0, "-0"])
    def test_negative_zero_is_a_zero_limit(self, value):
        parameters = InverterParameters(
            device_id="i", max_power_wh=1, max_grid_import_power_w=value
        )
        settings = InverterCommonSettings(
            device_id="i", max_power_w=1, max_grid_import_power_w=value
        )

        for limit in (parameters.max_grid_import_power_w, settings.max_grid_import_power_w):
            assert limit == 0.0
            assert math.copysign(1.0, cast(float, limit)) == 1.0

    @pytest.mark.parametrize(
        "value", [-10.0, "-1", float("nan"), "nan", float("inf"), "inf", "ten", True, False]
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
        # Only a limit reports the (here empty) peak shaving series.
        assert never_binding.result.peak_shaving_wh_per_hour == [0.0] * len(
            never_binding.result.grid_consumption_wh_per_hour
        )
        assert unlimited.result.peak_shaving_wh_per_hour is None
        assert never_binding.result.model_dump(
            exclude={"peak_shaving_wh_per_hour"}
        ) == unlimited.result.model_dump(exclude={"peak_shaving_wh_per_hour"})
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
        # Only a limit reports the (here empty) peak shaving series.
        assert never_binding.result.peak_shaving_wh_per_hour == [0.0] * len(
            never_binding.result.grid_consumption_wh_per_hour
        )
        assert unlimited.result.peak_shaving_wh_per_hour is None
        assert never_binding.result.model_dump(
            exclude={"peak_shaving_wh_per_hour"}
        ) == unlimited.result.model_dump(exclude={"peak_shaving_wh_per_hour"})
        assert never_binding.fitness_history == unlimited.fitness_history


# ---------------------------------------------------------------------------
# The limit wins against the EV target; the battery helps the EV
# ---------------------------------------------------------------------------

TARGET_LIMIT_W = 5000.0
TARGET_LOAD_W = 1000.0
TARGET_EV_POWER_W = 11000.0
TARGET_EV_RATES = [0.0, 0.25, 0.5, 1.0]
TARGET_BATTERY_WH = 10000
TARGET_BATTERY_W = 5000.0
TARGET_HOURS = 6
TARGET_DEADLINE_HOURS = 4


def _optimize_ev_target(
    config_eos: ConfigEOS,
    *,
    target: int,
    battery_soc: int,
    interval_sec: int = 900,
    limit_w: Optional[float] = TARGET_LIMIT_W,
    seed: int = 3,
) -> GeneticSolution:
    """Six hours, 5 kW limit, 1 kW load, empty 60 kWh EV with an 11 kW charger.

    EV rates 0 / 0.25 / 0.5 / 1, target by 04:00. Battery 10 kWh / 5 kW and
    inverter lossless, no AC charging, no PV. Per 15-minute slot the limit
    leaves 1000 Wh for the EV: 0.25 (687.5 Wh) fits from the grid alone, 0.5
    (1375 Wh) only with 375 Wh from the battery, 1.0 (2750 Wh) not even with
    the battery's 1250 Wh. Within the limit the EV reaches at most 22 kWh
    (36.7 %) by the deadline, 11 kWh (18.3 %) without the battery.
    """
    slots_per_hour = 3600 // interval_sec
    slots = TARGET_HOURS * slots_per_hour
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": TARGET_HOURS,
                    "tail_horizon_hours": 0,
                    "interval_sec": interval_sec,
                    "terminal_value_mode": "FIXED",
                    "terminal_value_euro_per_kwh": 0.0,
                },
            },
            "feedintariff": {"direct_marketing_enabled": False},
        }
    )
    get_ems(init=True).set_start_datetime(to_datetime("2026-11-05T00:00:00"))
    parameters = GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_forecast_wh": [0.0] * slots,
                "total_load": [TARGET_LOAD_W / slots_per_hour] * slots,
                "electricity_price_per_wh": [0.0003] * slots,
                "feed_in_tariff_per_wh": 0.0,
                "price_per_wh_battery": 0.0,
            },
            "pv_battery": {
                "device_id": "battery1",
                "capacity_wh": TARGET_BATTERY_WH,
                "max_charge_power_w": TARGET_BATTERY_W,
                "charging_efficiency": 1.0,
                "discharging_efficiency": 1.0,
                "initial_soc_percentage": battery_soc,
                "min_soc_percentage": 0,
            },
            "inverter": {
                "device_id": "inverter1",
                "max_power_wh": 30000,
                "battery_id": "battery1",
                "ac_to_dc_efficiency": 1.0,
                "dc_to_ac_efficiency": 1.0,
                "max_ac_charge_power_w": 0,
                "max_grid_import_power_w": limit_w,
            },
            "ev": {
                "device_id": "car",
                "capacity_wh": 60000,
                "initial_soc_percentage": 0,
                "min_soc_percentage": target,
                "max_soc_percentage": 100,
                "charging_efficiency": 1.0,
                "max_charge_power_w": TARGET_EV_POWER_W,
                "charge_rates": TARGET_EV_RATES,
                "min_soc_deadline_datetime": "2026-11-05T04:00:00",
            },
            "forecast_interval_seconds": interval_sec,
        }
    )
    return GeneticOptimization(fixed_seed=seed).optimize_ems(parameters, ngen=40, individuals=60)


def _replay_ev_plan(
    solution: GeneticSolution, *, battery_soc: int, interval_sec: int
) -> list[float]:
    """Grid import per slot of a site controller executing the exported plan.

    Written independently of the EOS device code (lossless, no PV). The EV
    charger draws its instruction's factor x 11 kW. The battery discharges for
    load and EV in discharging modes and, with the instructions' threshold,
    for the import above it in the other modes.
    """
    slot_h = interval_sec / 3600
    slots = len(solution.result.grid_consumption_wh_per_hour)
    start = get_ems().start_datetime
    battery: dict[int, tuple[float, float, float]] = {}
    thresholds: dict[int, Optional[float]] = {}
    ev: dict[int, float] = {}
    for instruction in solution.energy_management_plan().instructions:
        assert isinstance(instruction, FRBCInstruction)
        slot = int((instruction.execution_time - start).total_seconds() // interval_sec)
        controls = _MODE_CONTROLS[str(instruction.operation_mode_id)](
            float(instruction.operation_mode_factor)
        )
        if instruction.resource_id == "battery1":
            battery[slot] = controls
            thresholds[slot] = instruction.peak_shaving_threshold_w
        elif instruction.resource_id == "car":
            ev[slot] = controls[0]
    soc_wh = TARGET_BATTERY_WH * battery_soc / 100.0
    ev_wh = 0.0
    discharge = ev_rate = 0.0
    threshold: Optional[float] = None
    grid = []
    for slot in range(slots):
        if slot in battery:
            _, _, discharge = battery[slot]
            threshold = thresholds[slot]
        ev_rate = ev.get(slot, ev_rate)
        ev_draw_wh = min(ev_rate * TARGET_EV_POWER_W * slot_h, 60000.0 - ev_wh)
        ev_wh += ev_draw_wh
        total_wh = TARGET_LOAD_W * slot_h + ev_draw_wh
        power_wh = TARGET_BATTERY_W * slot_h
        if discharge:
            delivered_wh = min(total_wh, power_wh, soc_wh)
        elif threshold is not None:
            delivered_wh = min(max(total_wh - threshold * slot_h, 0.0), power_wh, soc_wh)
        else:
            delivered_wh = 0.0
        soc_wh -= delivered_wh
        grid.append(total_wh - delivered_wh)
    return grid


class TestEvTargetWithinTheLimit:
    @pytest.mark.parametrize("interval_sec", [900, 3600])
    def test_unreachable_target_is_missed_within_the_limit(self, config_eos, interval_sec):
        """50 % by 04:00 needs more than the limit and the battery allow: the limit wins."""
        solution = _optimize_ev_target(
            config_eos, target=50, battery_soc=100, interval_sec=interval_sec
        )

        slot_limit_wh = TARGET_LIMIT_W * interval_sec / 3600
        deadline_slot = TARGET_DEADLINE_HOURS * 3600 // interval_sec
        reached = solution.result.ev_soc_per_hour[deadline_slot]
        assert max(solution.result.grid_consumption_wh_per_hour) <= slot_limit_wh + 1e-6
        assert reached < 50.0
        # The EV still charges as much as the limit and the battery allow.
        assert reached > 18.4
        # The missed target is in the fitness (ev_soc_miss, 10 per point).
        assert solution.fitness_history is not None
        assert solution.fitness_history["min"][-1] >= (50.0 - reached) * 10.0

    @pytest.mark.parametrize("interval_sec", [900, 3600])
    def test_battery_helps_the_ev_to_its_target(self, config_eos, interval_sec):
        """20 % (12 kWh) by 04:00: only with the battery's share above the grid's limit."""
        solution = _optimize_ev_target(
            config_eos, target=20, battery_soc=100, interval_sec=interval_sec
        )

        slot_limit_wh = TARGET_LIMIT_W * interval_sec / 3600
        deadline_slot = TARGET_DEADLINE_HOURS * 3600 // interval_sec
        assert max(solution.result.grid_consumption_wh_per_hour) <= slot_limit_wh + 1e-6
        assert solution.result.ev_soc_per_hour[deadline_slot] >= 20.0 - 1e-6
        assert solution.result.battery_soc_per_hour[deadline_slot] < 100.0
        assert solution.ev_charge_hours_float is not None
        assert set(solution.ev_charge_hours_float) <= set(TARGET_EV_RATES)

    def test_empty_battery_leaves_the_ev_at_the_limit(self, config_eos):
        """Same target, empty battery: 0.25 is the most that fits, 18.3 % by 04:00."""
        solution = _optimize_ev_target(config_eos, target=20, battery_soc=0)

        deadline_slot = TARGET_DEADLINE_HOURS * 4
        assert max(solution.result.grid_consumption_wh_per_hour) <= 1250.0 + 1e-6
        assert solution.result.ev_soc_per_hour[deadline_slot] < 20.0
        assert solution.ev_charge_hours_float is not None
        assert max(solution.ev_charge_hours_float) <= 0.25

    @pytest.mark.parametrize("interval_sec", [900, 3600])
    @pytest.mark.parametrize(("target", "battery_soc"), [(20, 100), (50, 100), (20, 0)])
    def test_replayed_plan_stays_within_the_limit(
        self, config_eos, interval_sec, target, battery_soc
    ):
        """A site controller executing the exported EV factors and battery instructions
        imports what was simulated and never more than the limit."""
        solution = _optimize_ev_target(
            config_eos, target=target, battery_soc=battery_soc, interval_sec=interval_sec
        )

        grid = _replay_ev_plan(solution, battery_soc=battery_soc, interval_sec=interval_sec)

        np.testing.assert_allclose(grid, solution.result.grid_consumption_wh_per_hour, atol=1e-6)
        assert max(grid) <= TARGET_LIMIT_W * interval_sec / 3600 + 1e-6

    def test_without_limit_the_target_is_reached_above_it(self, config_eos):
        """Control: without the limit the plan reaches 50 % with imports above 5 kW."""
        solution = _optimize_ev_target(config_eos, target=50, battery_soc=100, limit_w=None)

        assert solution.result.ev_soc_per_hour[TARGET_DEADLINE_HOURS * 4] >= 50.0 - 1e-6
        assert max(solution.result.grid_consumption_wh_per_hour) > 1250.0


# ---------------------------------------------------------------------------
# The local search moves EV charging the limit cut to slots with room
# ---------------------------------------------------------------------------

EV_SLOTS = 24
EV_DEADLINE = 16
EV_LIMIT_W = 9000.0
EV_SLOT_LIMIT_WH = EV_LIMIT_W * 0.25
EV_LOAD_WH = 250.0  # 1 kW house load per quarter hour
EV_FULL_RATE_WH = 2750.0  # 11 kW per quarter hour, lossless charger
EV_CHEAP_SLOTS = range(4, 12)  # 01:00-03:00


def _ev_limit_optimizer(
    config_eos: ConfigEOS,
    limit_w: Optional[float],
    *,
    interval_sec: int = 900,
    load: Optional[list[float]] = None,
    pv: Optional[list[float]] = None,
    prices: Optional[list[float]] = None,
    charge_rates: Optional[list[float]] = None,
    deadline: str = "2026-11-05T04:00:00",
) -> GeneticOptimization:
    """Short run; afterwards the optimizer holds the run's inputs.

    Default: six quarter-hour hours. EV 60 kWh, 11 kW with the rates
    0 / 0.5 / 1, empty, 18 % (10.8 kWh) by 04:00 (slot 16); 1 kW house load;
    cheapest import 01:00-03:00. The battery is at its minimum SoC and cannot
    shave. At a 9 kW limit only the half rate (5.5 kW) fits next to the load.
    """
    slots_per_hour = 3600 // interval_sec
    slots = len(load) if load is not None else EV_SLOTS
    if load is None:
        load = [EV_LOAD_WH] * slots
    if prices is None:
        prices = [0.0003] * slots
        for slot in EV_CHEAP_SLOTS:
            prices[slot] = 0.00015
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": slots // slots_per_hour,
                    "tail_horizon_hours": 0,
                    "interval_sec": interval_sec,
                    "terminal_value_mode": "FIXED",
                    "terminal_value_euro_per_kwh": 0.0,
                },
            },
            "feedintariff": {"direct_marketing_enabled": False},
        }
    )
    get_ems(init=True).set_start_datetime(to_datetime("2026-11-05T00:00:00"))
    parameters = GeneticOptimizationParameters.model_validate(
        {
            "ems": {
                "pv_forecast_wh": pv if pv is not None else [0.0] * slots,
                "total_load": load,
                "electricity_price_per_wh": prices,
                "feed_in_tariff_per_wh": 0.0,
                "price_per_wh_battery": 0.0,
            },
            "pv_battery": {
                "device_id": "battery1",
                "capacity_wh": 5000,
                "max_charge_power_w": 2500,
                "initial_soc_percentage": 10,
                "min_soc_percentage": 10,
            },
            "inverter": {
                "device_id": "inverter1",
                "max_power_wh": 20000,
                "battery_id": "battery1",
                "max_ac_charge_power_w": 0,
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
                "charge_rates": charge_rates or [0.0, 0.5, 1.0],
                "min_soc_deadline_datetime": deadline,
            },
            "forecast_interval_seconds": interval_sec,
        }
    )
    optimizer = GeneticOptimization(fixed_seed=1)
    optimizer.optimize_ems(parameters, ngen=1, individuals=10)
    return optimizer


def _incumbent(optimizer: GeneticOptimization, ev_rates: list[float]) -> list[int]:
    """Battery idle (no PV charging) in every slot, EV at the given rates."""
    ev_genes = np.array(
        [optimizer.ev_possible_charge_values.index(rate) for rate in ev_rates], dtype=np.int_
    )
    return optimizer.merge_individual(
        np.zeros(optimizer.control_end_slot, dtype=np.int_), ev_genes, None
    )


def _full_rate_incumbent(optimizer: GeneticOptimization) -> list[int]:
    """EV at the full rate in the first four cheap slots (11 kWh asked).

    Each of them would import 12 kW, 3 kW above the 9 kW limit; the simulation
    lowers them to the half rate (5.5 kWh charged).
    """
    return _incumbent(optimizer, [1.0 if 4 <= slot < 8 else 0.0 for slot in range(EV_SLOTS)])


def _ev_rates(optimizer: GeneticOptimization, genome: list[int]) -> list[float]:
    _, ev_genes, _ = optimizer.split_individual(genome)
    assert ev_genes is not None
    return [optimizer.ev_possible_charge_values[index] for index in ev_genes]


class TestEvGridLimitLocalSearch:
    def test_candidate_moves_half_a_slot_to_the_cheapest_slot_with_headroom(self, config_eos):
        optimizer = _ev_limit_optimizer(config_eos, EV_LIMIT_W)
        incumbent = _full_rate_incumbent(optimizer)

        candidates = [
            _ev_rates(optimizer, genome)
            for genome in optimizer._grid_limit_ev_shift_candidates(incumbent)
        ]

        # Slot 4 down to the half rate, slot 8 (the first cheap slot with
        # headroom: 250 + 1375 Wh <= 2250 Wh) up to it; the EV keeps 11 kWh.
        expected = [0.0] * EV_SLOTS
        for slot in range(5, 8):
            expected[slot] = 1.0
        expected[4] = 0.5
        expected[8] = 0.5
        assert expected in candidates
        # Battery genes stay as they are.
        for genome in optimizer._grid_limit_ev_shift_candidates(incumbent):
            assert genome[: optimizer.control_end_slot] == incumbent[: optimizer.control_end_slot]

    def test_local_search_refills_the_energy_the_limit_cut(self, config_eos):
        """Four full-rate slots become eight half-rate slots in the cheap hours."""
        optimizer = _ev_limit_optimizer(config_eos, EV_LIMIT_W)
        incumbent = creator.Individual(_full_rate_incumbent(optimizer))
        cut = optimizer.evaluate_inner(list(incumbent))
        # The limit holds, the EV gets half of its 11 kWh and misses 18 %.
        assert max(cut["Netzbezug_Wh_pro_Stunde"]) <= EV_SLOT_LIMIT_WH + 1e-6
        assert cut["ev_grid_limited_charge_factors"] == {slot: 0.5 for slot in range(4, 8)}
        assert cut["EAuto_SoC_pro_Stunde"][EV_DEADLINE] < 18.0

        best, _, improvements, initial, final = optimizer._locally_improve_grid_export(
            incumbent, max_evaluations=96
        )

        result = optimizer.evaluate_inner(list(best))
        assert improvements > 0
        assert final < initial
        assert max(result["Netzbezug_Wh_pro_Stunde"]) <= EV_SLOT_LIMIT_WH + 1e-6
        assert result["EAuto_SoC_pro_Stunde"][EV_DEADLINE] >= 18.0 - 1e-6
        rates = _ev_rates(optimizer, best)
        assert [slot for slot, rate in enumerate(rates) if rate > 0.0] == list(EV_CHEAP_SLOTS)
        assert set(rates) == {0.0, 0.5}

    def test_destination_keeps_rising_while_it_has_room(self, config_eos):
        """Rates 0 / .3 / .4 / .5 / .6, hourly: 8 kW and 1 kW load, EV at .3 in both hours.

        Hour 0 would import 11.3 kW; the limit cuts its EV charging to 0
        (3.3 kWh). Hour 1 (4.3 kW) has room for all of it, three steps up to
        .6 (7.6 kW).
        """
        rates = [0.0, 0.3, 0.4, 0.5, 0.6]
        optimizer = _ev_limit_optimizer(
            config_eos,
            EV_LIMIT_W,
            interval_sec=3600,
            load=[8000.0, 1000.0],
            prices=[0.0003, 0.0003],
            charge_rates=rates,
            deadline="2026-11-05T02:00:00",
        )
        incumbent = creator.Individual(_incumbent(optimizer, [0.3, 0.3]))

        candidates = [
            _ev_rates(optimizer, genome)
            for genome in optimizer._grid_limit_ev_shift_candidates(incumbent)
        ]
        best, _, _, initial, final = optimizer._locally_improve_grid_export(
            incumbent, max_evaluations=96
        )

        assert candidates[0] == [0.0, 0.6]
        assert final < initial
        assert max(optimizer.evaluate_inner(list(best))["Netzbezug_Wh_pro_Stunde"]) <= 9000.0

    def test_pv_surplus_of_the_destination_counts_as_room(self, config_eos):
        """Hourly, rates 0 / 1: the EV's 11 kWh, cut in a 12 kW hour, move into a PV hour.

        Hour 1 has 1 kW load and 8 kW PV: 11 kW EV charging there imports
        about 4 kW, well within the 9 kW limit, although 11 kWh exceed the
        9 kWh the limit leaves without counting the PV surplus.
        """
        optimizer = _ev_limit_optimizer(
            config_eos,
            EV_LIMIT_W,
            interval_sec=3600,
            load=[1000.0, 1000.0],
            pv=[0.0, 8000.0],
            prices=[0.0002, 0.0003],
            charge_rates=[0.0, 1.0],
            deadline="2026-11-05T02:00:00",
        )
        incumbent = creator.Individual(_incumbent(optimizer, [1.0, 0.0]))

        candidates = [
            _ev_rates(optimizer, genome)
            for genome in optimizer._grid_limit_ev_shift_candidates(incumbent)
        ]
        best, _, _, initial, final = optimizer._locally_improve_grid_export(
            incumbent, max_evaluations=96
        )

        assert candidates[0] == [0.0, 1.0]
        assert final < initial
        assert max(optimizer.evaluate_inner(list(best))["Netzbezug_Wh_pro_Stunde"]) <= 9000.0

    @pytest.mark.parametrize("prices", [[0.0003] * 3, [0.0001, 0.0001, 0.0004]])
    def test_exact_and_cheapest_refills_are_both_candidates(self, config_eos, prices):
        """Rates 0 / .1 / .3 / 1, hourly: the limit cuts .3 to .1, 2.2 kWh.

        The next hour can take .3 (3.3 kWh, 1.1 kWh more than removed); one
        .1 step in each of the two following hours replaces the energy
        exactly. Which is better depends on the prices and the EV target, so
        the fitness check gets both.
        """
        optimizer = _ev_limit_optimizer(
            config_eos,
            EV_LIMIT_W,
            interval_sec=3600,
            load=[5720.0, 1000.0, 1000.0],
            prices=prices,
            charge_rates=[0.0, 0.1, 0.3, 1.0],
            deadline="2026-11-05T03:00:00",
        )
        incumbent = _incumbent(optimizer, [0.3, 0.0, 0.0])

        candidates = [
            _ev_rates(optimizer, genome)
            for genome in optimizer._grid_limit_ev_shift_candidates(incumbent)
        ]

        assert [0.1, 0.1, 0.1] in candidates
        assert [0.1, 0.3, 0.0] in candidates

    def test_source_gene_is_lowered_to_the_executed_rate(self, config_eos):
        """Rates 0 / .25 / .5 / 1, 1 kWh load in slot 4: the limit cuts 1.0 to .25 there.

        Every candidate sets the gene of slot 4 to .25 in one move, not one
        step (.5) at a time, and refills the 2062.5 Wh cut elsewhere.
        """
        load = [EV_LOAD_WH] * EV_SLOTS
        load[4] = 1000.0
        optimizer = _ev_limit_optimizer(
            config_eos, EV_LIMIT_W, load=load, charge_rates=[0.0, 0.25, 0.5, 1.0]
        )
        incumbent = _incumbent(optimizer, [1.0 if slot == 4 else 0.0 for slot in range(EV_SLOTS)])

        assert optimizer.evaluate_inner(list(incumbent))["ev_grid_limited_charge_factors"] == {
            4: 0.25
        }
        candidates = [
            _ev_rates(optimizer, genome)
            for genome in optimizer._grid_limit_ev_shift_candidates(incumbent)
        ]
        assert candidates
        for rates in candidates:
            assert rates[4] == 0.25
            refilled = sum(rate for slot, rate in enumerate(rates) if slot != 4)
            assert refilled * EV_FULL_RATE_WH >= 0.75 * EV_FULL_RATE_WH - 1e-6

    @pytest.mark.parametrize("limit_w", [None, 1e9])
    def test_no_candidates_without_a_binding_limit(self, config_eos, limit_w):
        optimizer = _ev_limit_optimizer(config_eos, limit_w)

        assert optimizer._grid_limit_ev_shift_candidates(_full_rate_incumbent(optimizer)) == []


# ---------------------------------------------------------------------------
# Battery controls of exported instructions (the replay of whole plans with the
# peak shaving threshold is in test_geneticpeakshavingthreshold.py)
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

    def test_rated_reading_check_uses_the_ac_to_dc_efficiency(self, config_eos):
        """Gene 0.19 at 90 % AC-to-DC efficiency, 3 kWh PV, 1 kW limit.

        Read as 0.19 x 5 kW the controller would charge 950 Wh DC, but the
        1 kWh AC headroom carries only 900 Wh DC. The simulation charges
        0.19 x 2 kWh = 380 Wh DC; the reported 380 / 5000 = 0.076 draws exactly
        that, while 0.19 would draw 1056 Wh AC, above the limit.
        """
        result, sim = _simulate(
            config_eos,
            load=[0.0],
            pv=[3000.0],
            ac=[0.19],
            soc_percentage=20,
            limit_w=1000.0,
            ac_to_dc_efficiency=0.9,
        )

        grid_wh = float(result["Netzbezug_Wh_pro_Stunde"][0])
        assert grid_wh == pytest.approx(380.0 / 0.9)
        assert sim.ac_charge_hours is not None
        factor = float(sim.ac_charge_hours[0])
        assert factor == pytest.approx(0.076)
        controller_wh = _controller_grid_charge_wh(factor, 5000.0, 3000.0, 8000.0, 0.9)
        assert controller_wh == pytest.approx(grid_wh)
        assert controller_wh <= 1000.0

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

    def test_import_just_beyond_the_tolerance_is_penalised(self, config_eos):
        """1 mWh above the limit is an excess (the tolerance only absorbs rounding)."""
        inverter, _ = _devices()

        fitness = _fitness(config_eos, _result([8000.0 + 1e-3, 8000.0]), inverter)

        assert fitness == pytest.approx(1.5 + 1e-3 * 0.01, rel=0, abs=1e-9)

    @staticmethod
    def _tail_hold_reward(load_wh: float) -> float:
        """Reward of a held tail slot of an empty battery at 0.3 EUR/kWh, 0.01 EUR/Wh penalty."""
        inverter, battery = _devices(soc_percentage=0)
        battery.charge_array = np.zeros(1)
        battery.discharge_array = np.zeros(1, dtype=np.int_)
        return _simulate_action(
            bat=battery,
            inv=inverter,
            energy_wh=0.0,
            action=(1, 0, 0.0, 0.0),
            price=0.0003,
            load=load_wh,
            pv=0.0,
            tariff=0.0,
            direct_marketing=False,
            grid_import_penalty_euro_per_wh=0.01,
        )["reward_euro"]

    def test_tail_rounding_above_the_limit_is_not_penalised(self):
        load_wh = 8000.0 + 2e-12

        # Exactly the energy cost: not even the 2e-14 EUR penalty of the residue.
        assert self._tail_hold_reward(load_wh) == -load_wh * 0.0003

    def test_tail_import_just_beyond_the_tolerance_is_penalised(self):
        load_wh = 8000.0 + 1e-3

        assert self._tail_hold_reward(load_wh) == pytest.approx(
            -load_wh * 0.0003 - 1e-3 * 0.01, rel=0, abs=1e-12
        )

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
