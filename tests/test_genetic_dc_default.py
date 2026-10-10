"""Neutral DC permissions stay enabled without changing simulated energy flows."""

# ruff: noqa: S101

from unittest.mock import Mock

import numpy as np
import pytest

from akkudoktoreos.devices.devicesabc import BatteryOperationMode
from akkudoktoreos.devices.genetic.battery import Battery, SolarPanelBatteryParameters
from akkudoktoreos.devices.genetic.inverter import Inverter, InverterParameters
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution


@pytest.mark.parametrize("interval", [900, 3600])
@pytest.mark.parametrize("ac_limits_total", [False, True])
@pytest.mark.parametrize(
    "gene,pv_power,load_power,soc,expected_dc",
    [
        pytest.param(0, 0, 400, 50, 1, id="night-idle"),
        pytest.param(1, 0, 400, 50, 1, id="night-local-discharge"),
        pytest.param(2, 0, 400, 50, 1, id="night-ac-charge"),
        pytest.param(5, 0, 400, 50, 1, id="night-grid-export"),
        pytest.param(0, 2000, 0, 50, 0, id="pv-with-headroom"),
        pytest.param(1, 2000, 400, 100, 0, id="full-but-discharge-creates-headroom"),
        pytest.param(0, 2000, 0, 100, 1, id="pv-with-full-battery"),
        pytest.param(2, 2000, 0, 50, 1, id="pv-during-ac-charge"),
    ],
)
def test_neutral_dc_default_preserves_energy_and_other_controls(
    config_eos, interval, ac_limits_total, gene, pv_power, load_power, soc, expected_dc
):
    config_eos.merge_settings_from_dict(
        {"optimization": {"genetic": {"horizon_hours": 1, "interval_sec": interval}}}
    )
    opt = GeneticOptimization(fixed_seed=42)
    opt.optimize_ev = False
    opt.optimize_dc_charge = True
    opt.optimize_battery_grid_export = True
    opt.bat_possible_charge_values = [0.5]
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
            max_ac_charge_power_w=6000,
            ac_charge_limits_total_charge=ac_limits_total,
        ),
        battery=battery,
        slot_duration_h=duration,
    )
    # Coarse slots can have both direct consumption and a PV surplus.
    inverter.self_consumption_predictor = Mock()
    inverter.self_consumption_predictor.calculate_expected_direct_consumption.side_effect = (
        lambda load, pv: min(load, pv) * 0.5
    )
    opt.simulation.prepare(
        parameters=GeneticEnergyManagementParameters(
            total_load=[load_power * duration] * slots,
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
    individual = [gene] * slots
    ac, dc, discharge, export = opt.decode_charge_discharge(np.array(individual))
    opt.simulation.ac_charge_hours = ac.copy()
    opt.simulation.dc_charge_hours = dc.copy()
    opt.simulation.bat_discharge_hours = discharge.copy()
    opt.simulation.bat_grid_export_hours = export.copy()
    reference = opt.simulation.simulate(0)
    reference_final_soc = battery.soc_wh

    actual = opt.evaluate_inner(individual)
    for key, value in reference.items():
        np.testing.assert_array_equal(actual[key], value, err_msg=key)
    assert battery.soc_wh == reference_final_soc
    np.testing.assert_array_equal(opt.simulation.dc_charge_hours, expected_dc)
    np.testing.assert_array_equal(opt.simulation.ac_charge_hours, ac)
    np.testing.assert_array_equal(opt.simulation.bat_discharge_hours, discharge)
    np.testing.assert_array_equal(opt.simulation.bat_grid_export_hours, export)

    # Existing warm-start gene indices retain their meaning and reproduce the
    # same executable permissions when evaluated again.
    assert individual == [gene] * slots
    repeated = opt.evaluate_inner(individual)
    for key, value in actual.items():
        np.testing.assert_array_equal(repeated[key], value, err_msg=key)
    np.testing.assert_array_equal(opt.simulation.dc_charge_hours, expected_dc)


def test_neutral_dc_permission_is_valid_during_grid_export():
    mode, factor = GeneticSolution._battery_operation_from_solution(
        ac_charge=0.0,
        dc_charge=1.0,
        discharge_allowed=False,
        battery_grid_export_allowed=True,
        battery_grid_export_factor=0.5,
    )
    assert mode == BatteryOperationMode.GRID_SUPPORT_EXPORT
    assert factor == 0.5


def test_ac_charge_and_grid_export_remain_incompatible():
    with pytest.raises(ValueError, match="cannot be combined with AC charging"):
        GeneticSolution._battery_operation_from_solution(
            ac_charge=0.5,
            dc_charge=1.0,
            discharge_allowed=False,
            battery_grid_export_allowed=True,
        )
