from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.devices.genetic.battery import Battery
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.utils.datetimeutil import to_datetime

ems_eos = get_ems(init=True)  # init once

DIR_TESTDATA = Path(__file__).parent / "testdata"


def test_direct_marketing_preserves_constant_supplied_feed_in_tariff(config_eos: ConfigEOS):
    config_eos.merge_settings_from_dict({"feedintariff": {"direct_marketing_enabled": True}})
    parameters = GeneticOptimizationParameters.model_validate(
        dict(
            ems={
                "pv_prognose_wh": [0.0, 0.0],
                "strompreis_euro_pro_wh": [0.0002, -0.0001],
                "einspeiseverguetung_euro_pro_wh": [0.00007, 0.00007],
                "preis_euro_pro_wh_akku": 0.0,
                "gesamtlast": [0.0, 0.0],
            },
            pv_battery=None,
            # Without an inverter the simulation books no grid energy at all, so the
            # price signal would never reach the fitness.
            inverter={"device_id": "inverter1", "max_power_wh": 20000},
            ev=None,
        )
    )

    adjusted = GeneticOptimization()._parameters_for_config(parameters)

    assert adjusted.ems.einspeiseverguetung_euro_pro_wh == [0.00007, 0.00007]
    assert parameters.ems.einspeiseverguetung_euro_pro_wh == [0.00007, 0.00007]


def test_direct_marketing_keeps_variable_feed_in_tariff(config_eos: ConfigEOS):
    config_eos.merge_settings_from_dict({"feedintariff": {"direct_marketing_enabled": True}})
    parameters = GeneticOptimizationParameters.model_validate(
        dict(
            ems={
                "pv_prognose_wh": [0.0, 0.0],
                "strompreis_euro_pro_wh": [0.0002, 0.0003],
                "einspeiseverguetung_euro_pro_wh": [0.0001, -0.00005],
                "preis_euro_pro_wh_akku": 0.0,
                "gesamtlast": [0.0, 0.0],
            },
            pv_battery=None,
            inverter=None,
            ev=None,
        )
    )

    adjusted = GeneticOptimization()._parameters_for_config(parameters)

    assert adjusted.ems.einspeiseverguetung_euro_pro_wh == [0.0001, -0.00005]


def test_grid_export_rates_reach_the_solution(config_eos: ConfigEOS):
    """Configured export rates end up as per-slot export levels in the solution."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 24},
            "optimization": {
                "genetic": {
                    "individuals": 40,
                    "generations": 10,
                    "tail_horizon_hours": 0,
                    "horizon_hours": 24,
                    "interval_sec": 3600,
                }
            },
            "feedintariff": {"direct_marketing_enabled": True},
            "devices": {
                "max_batteries": 1,
                "batteries": {
                    "battery1": {"device_id": "battery1", "grid_export_rates": [0.5, 1.0]}
                },
            },
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=0, minute=0))
    CacheEnergyManagementStore().clear()

    hours = 24
    parameters = GeneticOptimizationParameters.model_validate(
        dict(
            ems={
                "pv_prognose_wh": [0.0] * hours,
                "strompreis_euro_pro_wh": [0.0003] * hours,
                # A pronounced tariff peak makes exporting worthwhile at all.
                "einspeiseverguetung_euro_pro_wh": [0.0001] * 12 + [0.0009] * 12,
                "preis_euro_pro_wh_akku": 0.0,
                "gesamtlast": [200.0] * hours,
            },
            pv_battery={
                "device_id": "battery1",
                "capacity_wh": 10000,
                "initial_soc_percentage": 100,
                "min_soc_percentage": 0,
                "max_charge_power_w": 5000,
            },
            inverter={
                "device_id": "inverter1",
                "max_power_wh": 10000,
                "battery_id": "battery1",
            },
            ev=None,
        )
    )

    optimization = GeneticOptimization(fixed_seed=42)
    solution = optimization.optimize_ems(parameters=parameters, start_hour=0, ngen=3)

    # Full power first, so the full-power state keeps the lowest export index.
    assert optimization.bat_possible_grid_export_values == [1.0, 0.5]
    assert len(solution.battery_grid_export_factor) == len(solution.battery_grid_export_allowed)
    assert set(solution.battery_grid_export_factor) <= {0.0, 0.5, 1.0}
    assert [
        1 if factor > 0.0 else 0 for factor in solution.battery_grid_export_factor
    ] == solution.battery_grid_export_allowed

    # @TODO


def _ev_deadline_parameters(hours: int, **ev_extra) -> GeneticOptimizationParameters:
    """Optimization parameters with an EV that has to be charged."""
    return GeneticOptimizationParameters.model_validate(
        dict(
            ems={
                "pv_prognose_wh": [0.0] * hours,
                # Expensive for the first six hours, dirt cheap afterwards: without a
                # deadline the optimizer would always wait for the cheap slots.
                "strompreis_euro_pro_wh": [0.0009] * 6 + [0.00001] * (hours - 6),
                "einspeiseverguetung_euro_pro_wh": [0.00007] * hours,
                "preis_euro_pro_wh_akku": 0.0,
                "gesamtlast": [300.0] * hours,
            },
            pv_battery=None,
            inverter=None,
            ev={
                "device_id": "ev1",
                "capacity_wh": 60000,
                "charging_efficiency": 0.95,
                "max_charge_power_w": 11040,
                "initial_soc_percentage": 20,
                "min_soc_percentage": 60,
                **ev_extra,
            },
        )
    )


def test_ev_deadline_slot_resolution(config_eos: ConfigEOS):
    """Datetime and maximum duration resolve to a slot; the earlier one wins."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "genetic": {"tail_horizon_hours": 0, "horizon_hours": 48, "interval_sec": 3600}
            },
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=10, minute=0))
    optimization = GeneticOptimization(fixed_seed=1)
    optimization._slot0_datetime = optimization.ems.start_datetime
    slot0 = optimization._slot0_datetime

    # Duration only: 6 h after the start hour 10.
    parameters = _ev_deadline_parameters(48, min_soc_max_duration_h=6)
    assert optimization._ev_deadline_slot(parameters) == 6

    # Datetime only.
    parameters = _ev_deadline_parameters(48, min_soc_deadline_datetime=slot0.add(hours=14))
    assert optimization._ev_deadline_slot(parameters) == 14

    # Both: the earlier one wins.
    parameters = _ev_deadline_parameters(
        48, min_soc_deadline_datetime=slot0.add(hours=20), min_soc_max_duration_h=6
    )
    assert optimization._ev_deadline_slot(parameters) == 6

    # Beyond the horizon: no deadline, the end-of-horizon target already covers it.
    parameters = _ev_deadline_parameters(48, min_soc_deadline_datetime=slot0.add(hours=100))
    assert optimization._ev_deadline_slot(parameters) is None

    # In the past: due right now.
    parameters = _ev_deadline_parameters(48, min_soc_deadline_datetime=slot0.subtract(hours=2))
    assert optimization._ev_deadline_slot(parameters) == 0

    # No deadline at all.
    assert optimization._ev_deadline_slot(_ev_deadline_parameters(48)) is None


def test_ev_soc_penalty_reads_the_deadline_slot(config_eos: ConfigEOS):
    """With a deadline the penalty checks the SoC at that slot, not at the end."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "genetic": {"tail_horizon_hours": 0, "horizon_hours": 48, "interval_sec": 3600}
            },
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=10, minute=0))
    optimization = GeneticOptimization(fixed_seed=1)
    simulation_result = {"EAuto_SoC_pro_Stunde": [20.0, 35.0, 50.0, 80.0]}

    optimization.simulation.ev = MagicMock(spec=Battery)
    optimization.simulation.ev.current_soc_percentage.return_value = 80.0

    # Without a deadline the final SoC counts.
    optimization._ev_soc_deadline_slot = None
    assert optimization._ev_soc_at_deadline(simulation_result, 10) == 80.0

    # With one, the SoC at the beginning of the deadline slot counts.
    optimization._ev_soc_deadline_slot = 12
    assert optimization._ev_soc_at_deadline(simulation_result, 10) == 50.0

    # A deadline beyond the reported slots falls back to the final SoC.
    optimization._ev_soc_deadline_slot = 99
    assert optimization._ev_soc_at_deadline(simulation_result, 10) == 80.0


def test_ev_deadline_charges_before_departure(config_eos: ConfigEOS):
    """The EV reaches its target before the deadline even when energy is cheaper later."""
    hours = 24
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": hours},
            "optimization": {
                "genetic": {
                    "individuals": 100,
                    "generations": 40,
                    "tail_horizon_hours": 0,
                    "horizon_hours": hours,
                    "interval_sec": 3600,
                }
            },
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=0, minute=0))
    CacheEnergyManagementStore().clear()

    parameters = _ev_deadline_parameters(hours, min_soc_max_duration_h=6)
    solution = GeneticOptimization(fixed_seed=42).optimize_ems(
        parameters=parameters, start_hour=0, ngen=40
    )

    soc_per_hour = solution.result.ev_soc_per_hour
    # Slot 6 is the first slot at or after the deadline, so its start-of-slot SoC
    # is what the target is checked against.
    assert soc_per_hour[6] >= 60.0


def _terminal_value_run(
    config_eos: ConfigEOS, mode: str, prices: Optional[list[float]] = None
) -> GeneticSolution:
    """48 h with expensive energy and two dirt-cheap slots at the very end.

    Charging in those last slots only pays off when the stored energy keeps a
    value beyond the horizon.
    """
    hours = 48
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": hours},
            "optimization": {
                "genetic": {
                    "individuals": 80,
                    "generations": 20,
                    "tail_horizon_hours": 0,
                    "horizon_hours": hours,
                    "interval_sec": 3600,
                    "terminal_value_mode": mode,
                    "terminal_value_euro_per_kwh": 0.0,
                }
            },
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=0, minute=0))
    CacheEnergyManagementStore().clear()

    if prices is None:
        prices = [0.0004] * (hours - 2) + [0.00002] * 2
    parameters = GeneticOptimizationParameters.model_validate(
        dict(
            ems={
                "pv_prognose_wh": [0.0] * hours,
                "strompreis_euro_pro_wh": prices,
                "einspeiseverguetung_euro_pro_wh": [0.00007] * hours,
                "preis_euro_pro_wh_akku": 0.0,
                "gesamtlast": [200.0] * hours,
            },
            pv_battery={
                "device_id": "battery1",
                "capacity_wh": 10000,
                "initial_soc_percentage": 20,
                "min_soc_percentage": 0,
                "max_soc_percentage": 100,
                "charging_efficiency": 1.0,
                "discharging_efficiency": 1.0,
                "max_charge_power_w": 5000,
            },
            inverter={
                "device_id": "inverter1",
                "max_power_wh": 10000,
                "battery_id": "battery1",
                "ac_to_dc_efficiency": 1.0,
                "dc_to_ac_efficiency": 1.0,
                "max_ac_charge_power_w": 5000,
            },
            ev=None,
        )
    )
    return GeneticOptimization(fixed_seed=7).optimize_ems(
        parameters=parameters, start_hour=0, ngen=20
    )


def test_terminal_value_auto_keeps_energy_that_fixed_zero_throws_away(config_eos: ConfigEOS):
    """AUTO values the energy left in the battery, a fixed zero does not."""
    auto = _terminal_value_run(config_eos, "AUTO")
    fixed = _terminal_value_run(config_eos, "FIXED")

    assert auto.terminal_value is not None
    assert auto.terminal_value.mode == "AUTO"
    assert auto.terminal_value.curve is not None
    assert auto.terminal_value.credited_euro > 0.0

    assert fixed.terminal_value is not None
    assert fixed.terminal_value.mode == "FIXED"
    assert fixed.terminal_value.credited_euro == 0.0

    # The cheap slots at the end are only worth using with a terminal value.
    assert auto.result.battery_soc_per_hour[-1] > fixed.result.battery_soc_per_hour[-1]


def test_terminal_value_curve_is_concave_and_reported(config_eos: ConfigEOS):
    """The reported curve is what the credit was read from."""
    solution = _terminal_value_run(config_eos, "AUTO")
    assert solution.terminal_value is not None
    curve = solution.terminal_value.curve
    assert curve is not None

    assert curve.window_slots == 24
    assert len(curve.energy_wh) == len(curve.value_euro)
    assert len(curve.marginal_euro_per_kwh) == len(curve.energy_wh) - 1
    marginals = curve.marginal_euro_per_kwh
    assert all(a >= b for a, b in zip(marginals, marginals[1:]))

    # The credit is the curve evaluated at the energy left in the battery.
    expected = curve.value(solution.terminal_value.battery_energy_wh)
    assert solution.terminal_value.credited_euro == pytest.approx(expected)


def test_terminal_value_reports_why_it_fell_back_to_fixed(config_eos: ConfigEOS):
    """AUTO without any prices cannot build a curve - and has to say so.

    A request whose price forecast is all zeros used to be indistinguishable
    from a run configured for FIXED.
    """
    hours = 48
    solution = _terminal_value_run(config_eos, "AUTO", prices=[0.0] * hours)

    assert solution.terminal_value is not None
    assert solution.terminal_value.mode == "FIXED"
    assert solution.terminal_value.curve is None
    assert solution.terminal_value.reason is not None
    assert "no priced residual load" in solution.terminal_value.reason

    configured = _terminal_value_run(config_eos, "FIXED")
    assert configured.terminal_value is not None
    assert configured.terminal_value.reason == "terminal_value_mode is FIXED"
