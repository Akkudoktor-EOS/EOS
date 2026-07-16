# ruff: noqa: S101

import numpy as np

from akkudoktoreos.devices.devicesabc import BatteryOperationMode
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution


def test_battery_discharge_allowed_remains_local_load_mode(config_eos):
    config_eos.merge_settings_from_dict(
        {"feedintariff": {"direct_marketing_enabled": True}}
    )
    solution = GeneticSolution.model_construct()

    operation_mode, operation_mode_factor = solution._battery_operation_from_solution(
        ac_charge=0.0,
        dc_charge=0.0,
        discharge_allowed=True,
    )

    assert operation_mode == BatteryOperationMode.PEAK_SHAVING
    assert operation_mode_factor == 1.0


def test_battery_grid_export_signal_maps_to_grid_support_export(config_eos):
    config_eos.merge_settings_from_dict(
        {"feedintariff": {"direct_marketing_enabled": True}}
    )
    solution = GeneticSolution.model_construct()

    operation_mode, operation_mode_factor = solution._battery_operation_from_solution(
        ac_charge=0.0,
        dc_charge=0.0,
        discharge_allowed=False,
        battery_grid_export_allowed=True,
    )

    assert operation_mode == BatteryOperationMode.GRID_SUPPORT_EXPORT
    assert operation_mode_factor == 1.0


def test_decode_charge_discharge_has_separate_battery_grid_export_state():
    optimization = GeneticOptimization()
    optimization.bat_possible_charge_values = [1.0]
    optimization.optimize_dc_charge = True
    optimization.optimize_battery_grid_export = True

    ac_charge, dc_charge, discharge, battery_grid_export = (
        optimization.decode_charge_discharge(np.array([5]))
    )

    assert ac_charge.tolist() == [0.0]
    assert dc_charge.tolist() == [0]
    assert discharge.tolist() == [0]
    assert battery_grid_export.tolist() == [1]


def test_decode_charge_discharge_has_self_consumption_state_after_legacy_export():
    optimization = GeneticOptimization()
    optimization.bat_possible_charge_values = [1.0]
    optimization.optimize_dc_charge = True
    optimization.optimize_battery_grid_export = True

    layout = optimization._battery_state_layout()
    ac_charge, dc_charge, discharge, battery_grid_export = (
        optimization.decode_charge_discharge(np.array([6]))
    )

    assert layout.total_states == 7
    assert layout.grid_export_state == 5
    assert layout.self_consumption_state == 6
    assert ac_charge.tolist() == [0.0]
    assert dc_charge.tolist() == [1]
    assert discharge.tolist() == [1]
    assert battery_grid_export.tolist() == [0]
