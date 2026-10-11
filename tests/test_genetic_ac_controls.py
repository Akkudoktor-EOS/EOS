"""Executable AC controls agree with the inverter limits and simulated charging."""

# ruff: noqa: S101

from unittest.mock import Mock

import numpy as np
import pytest

from akkudoktoreos.devices.genetic.battery import Battery, SolarPanelBatteryParameters
from akkudoktoreos.devices.genetic.inverter import Inverter, InverterParameters
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
)


@pytest.mark.parametrize("interval", [900, 3600])
@pytest.mark.parametrize("ac_limits_total", [False, True])
@pytest.mark.parametrize("normalize_dc", [False, True])
@pytest.mark.parametrize(
    "soc,pv_power,max_ac_power,expected_factor",
    [
        pytest.param(100, 0, 6000, 0, id="full-night"),
        pytest.param(100, 2000, 6000, 0, id="full-pv"),
        pytest.param(50, 0, 6000, 6000 * 0.97 / 7000, id="inverter-limit"),
        pytest.param(50, 0, 0, 0, id="disabled-inverter"),
        pytest.param(50, 0, None, 1, id="unlimited-inverter"),
        pytest.param(99, 2000, 6000, 0, id="pv-fills-battery"),
    ],
)
def test_ac_controls_reproduce_simulated_energy(
    config_eos, interval, ac_limits_total, normalize_dc, soc, pv_power, max_ac_power,
    expected_factor
):
    config_eos.merge_settings_from_dict(
        {"optimization": {"genetic": {"horizon_hours": 1, "interval_sec": interval}}}
    )
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.optimize_dc_charge = True
    opt.optimize_battery_grid_export = True
    opt.bat_possible_charge_values = [1.0]
    slots = opt.control_slots
    duration = interval / 3600
    battery = Battery(
        SolarPanelBatteryParameters(
            device_id="pv_akku",
            capacity_wh=26400,
            max_charge_power_w=7000,
            initial_soc_percentage=soc,
            charging_efficiency=0.95,
            discharging_efficiency=0.95,
        ),
        prediction_hours=slots,
        slot_duration_h=duration,
    )
    inverter = Inverter(
        InverterParameters(
            device_id="inverter",
            battery_id="pv_akku",
            max_power_wh=10000,
            ac_to_dc_efficiency=0.97,
            dc_to_ac_efficiency=0.97,
            max_ac_charge_power_w=max_ac_power,
            ac_charge_limits_total_charge=ac_limits_total,
        ),
        battery=battery,
        slot_duration_h=duration,
    )
    inverter.self_consumption_predictor = Mock()
    inverter.self_consumption_predictor.calculate_expected_direct_consumption.side_effect = min
    opt.simulation.prepare(
        parameters=GeneticEnergyManagementParameters(
            total_load=[0.0] * slots,
            pv_forecast_wh=[pv_power * duration] * slots,
            electricity_price_per_wh=[0.0003] * slots,
            feed_in_tariff_per_wh=[0.0001] * slots,
            price_per_wh_battery=0.00005,
        ),
        optimization_hours=slots,
        prediction_hours=slots,
        inverter=inverter,
        direct_marketing_enabled=True,
    )
    individual = [2] * slots
    ac, dc, discharge, export = opt.decode_charge_discharge(np.array(individual))
    opt.simulation.ac_charge_hours = ac.copy()
    opt.simulation.dc_charge_hours = dc.copy()
    opt.simulation.bat_discharge_hours = discharge.copy()
    opt.simulation.bat_grid_export_hours = export.copy()
    actual = opt.simulation.simulate(0, prefer_dc_charge_when_neutral=normalize_dc)
    final_soc = battery.soc_wh
    assert opt.simulation.ac_charge_hours is not None
    assert opt.simulation.dc_charge_hours is not None
    ac = opt.simulation.ac_charge_hours.copy()
    dc = opt.simulation.dc_charge_hours.copy()
    assert ac[0] == pytest.approx(expected_factor)
    np.testing.assert_array_equal(opt.simulation.bat_discharge_hours, 0)
    np.testing.assert_array_equal(opt.simulation.bat_grid_export_hours, 0)

    # Replaying the returned controls must reproduce every energy/cost value.
    battery.reset()
    opt.simulation.ac_charge_hours = ac.copy()
    opt.simulation.dc_charge_hours = dc.copy()
    replay = opt.simulation.simulate(0)
    for key, value in actual.items():
        np.testing.assert_array_equal(replay[key], value, err_msg=key)
    assert battery.soc_wh == final_soc
    assert individual == [2] * slots


@pytest.mark.parametrize("interval", [900, 3600])
def test_pv_only_charge_cap_is_not_removed(config_eos, interval):
    """An AC setpoint may cap PV charging without drawing any grid energy."""
    config_eos.merge_settings_from_dict(
        {"optimization": {"genetic": {"horizon_hours": 1, "interval_sec": interval}}}
    )
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.optimize_dc_charge = True
    opt.optimize_battery_grid_export = False
    opt.bat_possible_charge_values = [0.1]
    slots = opt.control_slots
    duration = interval / 3600
    battery = Battery(
        SolarPanelBatteryParameters(
            device_id="pv_akku", capacity_wh=26400, max_charge_power_w=7000,
            initial_soc_percentage=50, charging_efficiency=0.95,
        ), prediction_hours=slots, slot_duration_h=duration,
    )
    inverter = Inverter(
        InverterParameters(
            device_id="inverter", battery_id="pv_akku", max_power_wh=10000,
            ac_charge_limits_total_charge=True,
        ), battery=battery, slot_duration_h=duration,
    )
    inverter.self_consumption_predictor = Mock()
    inverter.self_consumption_predictor.calculate_expected_direct_consumption.side_effect = min
    opt.simulation.prepare(
        parameters=GeneticEnergyManagementParameters(
            total_load=[0.0] * slots, pv_forecast_wh=[2000 * duration] * slots,
            electricity_price_per_wh=[0.0003] * slots, feed_in_tariff_per_wh=[0.0001] * slots,
            price_per_wh_battery=0.00005,
        ), optimization_hours=slots, prediction_hours=slots, inverter=inverter,
    )
    result = opt.evaluate_inner([2] * slots)
    np.testing.assert_array_equal(result["Netzbezug_Wh_pro_Stunde"], 0)
    np.testing.assert_array_equal(opt.simulation.ac_charge_hours, 0.1)
