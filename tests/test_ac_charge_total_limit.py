"""Tests for inverters whose AC charge setpoint caps the total battery charge.

With ``ac_charge_limits_total_charge`` an AC slot charges the battery with at
most ``ac_charge x max_charge_power_w``, PV included (e.g. Deye time-of-use grid
charging). PV surplus above that cap is exported; the grid only fills what PV
leaves of the cap. Without the flag the default model applies: PV charges first
and the grid adds ``ac_charge`` of the remaining charge power on top.
"""

from unittest.mock import Mock, patch

import numpy as np
import pytest

from akkudoktoreos.devices.genetic.battery import Battery, SolarPanelBatteryParameters
from akkudoktoreos.devices.genetic.inverter import Inverter, InverterParameters
from akkudoktoreos.devices.settings.invertersettings import InverterCommonSettings

MAX_CHARGE_W = 5000


def _build(limits_total: bool, max_ac_charge_power_w=None) -> tuple[Inverter, Battery]:
    battery = Battery(
        SolarPanelBatteryParameters(
            device_id="battery1",
            capacity_wh=20000,
            initial_soc_percentage=20,
            charging_efficiency=0.9,
            discharging_efficiency=0.9,
            min_soc_percentage=0,
            max_soc_percentage=100,
            max_charge_power_w=MAX_CHARGE_W,
        ),
        prediction_hours=4,
    )
    battery.reset()
    # reset() creates an integer array; charge factors are fractions.
    battery.charge_array = np.zeros(4)
    predictor = Mock()
    predictor.calculate_expected_direct_consumption.side_effect = min
    with patch(
        "akkudoktoreos.devices.genetic.inverter.get_eos_load_interpolator",
        return_value=predictor,
    ):
        inverter = Inverter(
            InverterParameters(
                device_id="inverter1",
                max_power_wh=10000,
                battery_id="battery1",
                ac_to_dc_efficiency=1.0,
                max_ac_charge_power_w=max_ac_charge_power_w,
                ac_charge_limits_total_charge=limits_total,
            ),
            battery=battery,
        )
    return inverter, battery


def _run_ac_slot(inverter: Inverter, battery: Battery, pv_wh: float, factor: float):
    """Run one AC slot the way GeneticSimulation.simulate() does."""
    battery.charge_array[0] = factor
    battery.discharge_array[0] = 0
    rate = inverter.ac_charge_factor(factor)
    inverter.begin_ac_charge_slot(0, rate)
    export, grid_import, _, _ = inverter.process_energy(pv_wh, 0.0, 0)
    ac_wh, _ = inverter.charge_battery_from_grid(0, rate)
    return export, grid_import + ac_wh, battery._charged_raw_wh_per_slot[0]


class TestPvSurplusAboveCap:
    """PV alone exceeds the AC setpoint - the Deye situation that exported PV."""

    def test_total_limit_exports_pv_above_the_cap(self):
        inverter, battery = _build(limits_total=True)
        export, grid, charged_raw = _run_ac_slot(inverter, battery, pv_wh=6000, factor=0.5)

        assert charged_raw == pytest.approx(0.5 * MAX_CHARGE_W)
        assert export == pytest.approx(6000 - 0.5 * MAX_CHARGE_W)
        assert grid == pytest.approx(0.0)

    def test_default_model_stores_pv_up_to_full_charge_power(self):
        inverter, battery = _build(limits_total=False)
        export, grid, charged_raw = _run_ac_slot(inverter, battery, pv_wh=6000, factor=0.5)

        assert charged_raw == pytest.approx(MAX_CHARGE_W)
        assert export == pytest.approx(6000 - MAX_CHARGE_W)
        assert grid == pytest.approx(0.0)


class TestPvSurplusBelowCap:
    """PV does not reach the setpoint - the grid tops up."""

    def test_total_limit_grid_fills_up_to_the_cap(self):
        inverter, battery = _build(limits_total=True)
        export, grid, charged_raw = _run_ac_slot(inverter, battery, pv_wh=1000, factor=0.5)

        assert charged_raw == pytest.approx(0.5 * MAX_CHARGE_W)
        assert grid == pytest.approx(0.5 * MAX_CHARGE_W - 1000)
        assert export == pytest.approx(0.0)

    def test_default_model_grid_adds_factor_of_remaining_power(self):
        inverter, battery = _build(limits_total=False)
        export, grid, charged_raw = _run_ac_slot(inverter, battery, pv_wh=1000, factor=0.5)

        assert grid == pytest.approx(0.5 * (MAX_CHARGE_W - 1000))
        assert charged_raw == pytest.approx(1000 + 0.5 * (MAX_CHARGE_W - 1000))
        assert export == pytest.approx(0.0)


def test_max_ac_charge_power_caps_the_total_charge():
    """max_ac_charge_power_w lowers the setpoint and with it the total cap."""
    inverter, battery = _build(limits_total=True, max_ac_charge_power_w=2000)
    export, grid, charged_raw = _run_ac_slot(inverter, battery, pv_wh=6000, factor=1.0)

    assert charged_raw == pytest.approx(2000)
    assert export == pytest.approx(4000)
    assert grid == pytest.approx(0.0)


def test_dc_slot_is_not_capped():
    """Only AC slots are capped: a DC slot takes PV up to max_charge_power_w."""
    inverter, battery = _build(limits_total=True)
    battery.charge_array[0] = 1
    battery.discharge_array[0] = 0
    export, _, _, _ = inverter.process_energy(6000, 0.0, 0)

    assert battery._charged_raw_wh_per_slot[0] == pytest.approx(MAX_CHARGE_W)
    assert export == pytest.approx(1000)


def test_reset_lifts_the_slot_cap():
    inverter, battery = _build(limits_total=True)
    _run_ac_slot(inverter, battery, pv_wh=6000, factor=0.5)
    battery.reset()
    battery.charge_array = np.zeros(4)
    battery.charge_array[0] = 1
    inverter.process_energy(6000, 0.0, 0)

    assert battery._charged_raw_wh_per_slot[0] == pytest.approx(MAX_CHARGE_W)


class TestGeneticSimulation:
    """The flag changes the simulated plan, so the optimizer can prefer DC."""

    @pytest.fixture
    def simulate(self, config_eos):
        from akkudoktoreos.optimization.genetic.genetic import GeneticSimulation
        from akkudoktoreos.optimization.genetic.geneticparams import (
            GeneticEnergyManagementParameters,
        )

        config_eos.merge_settings_from_dict(
            {"prediction": {"hours": 48}, "optimization": {"hours": 24}}
        )
        hours = config_eos.prediction.hours

        def _simulate(limits_total: bool, ac_factor: float, dc_factor: float):
            battery = Battery(
                SolarPanelBatteryParameters(
                    device_id="battery1",
                    capacity_wh=30000,
                    initial_soc_percentage=20,
                    charging_efficiency=0.9,
                    discharging_efficiency=0.9,
                    min_soc_percentage=0,
                    max_soc_percentage=100,
                    max_charge_power_w=MAX_CHARGE_W,
                ),
                prediction_hours=hours,
            )
            battery.reset()
            inverter = Inverter(
                InverterParameters(
                    device_id="inverter1",
                    max_power_wh=10000,
                    battery_id="battery1",
                    ac_charge_limits_total_charge=limits_total,
                ),
                battery=battery,
            )
            sim = GeneticSimulation()
            sim.prepare(
                GeneticEnergyManagementParameters.model_validate(
                    dict(
                        pv_prognose_wh=[8000.0] * hours,
                        strompreis_euro_pro_wh=[0.0003] * hours,
                        einspeiseverguetung_euro_pro_wh=0.00008,
                        preis_euro_pro_wh_akku=0.0001,
                        gesamtlast=[0.0] * hours,
                    )
                ),
                optimization_hours=config_eos.optimization.genetic.horizon_hours,
                prediction_hours=hours,
                inverter=inverter,
                ev=None,
                home_appliance=None,
            )
            ac_hours, dc_hours = sim.ac_charge_hours, sim.dc_charge_hours
            discharge_hours = sim.bat_discharge_hours
            assert ac_hours is not None and dc_hours is not None and discharge_hours is not None
            ac_hours[:] = 0
            dc_hours[:] = 0
            discharge_hours[:] = 0
            ac_hours[1] = ac_factor
            dc_hours[1] = dc_factor
            return sim.simulate(start_hour=0)

        return _simulate

    def test_ac_slot_under_pv_surplus_exports_more_than_dc(self, simulate):
        ac = simulate(limits_total=True, ac_factor=0.5, dc_factor=0)
        dc = simulate(limits_total=True, ac_factor=0, dc_factor=1)

        assert ac["Netzeinspeisung_Wh_pro_Stunde"][1] == pytest.approx(
            dc["Netzeinspeisung_Wh_pro_Stunde"][1] + 0.5 * MAX_CHARGE_W
        )
        assert ac["akku_soc_pro_stunde"][2] < dc["akku_soc_pro_stunde"][2]

    def test_default_model_treats_ac_slot_like_dc_under_pv_surplus(self, simulate):
        ac = simulate(limits_total=False, ac_factor=0.5, dc_factor=0)
        dc = simulate(limits_total=False, ac_factor=0, dc_factor=1)

        assert ac["Netzeinspeisung_Wh_pro_Stunde"][1] == pytest.approx(
            dc["Netzeinspeisung_Wh_pro_Stunde"][1]
        )


class TestConfigOption:
    """The flag is a device config option that reaches the GENETIC optimizer."""

    @pytest.mark.parametrize("enabled", [False, True])
    def test_config_passes_the_flag_to_the_optimizer(self, enabled):
        settings = InverterCommonSettings(
            device_id="inverter",
            max_power_w=10000,
            battery_id="battery1",
            ac_charge_limits_total_charge=enabled,
        )
        assert settings.to_genetic_param().ac_charge_limits_total_charge is enabled

    def test_default_keeps_the_existing_model(self):
        settings = InverterCommonSettings(device_id="inverter", max_power_w=10000)
        assert settings.ac_charge_limits_total_charge is False
        assert settings.to_genetic_param().ac_charge_limits_total_charge is False
