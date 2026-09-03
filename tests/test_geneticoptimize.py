import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.utils.datetimeutil import to_datetime
from akkudoktoreos.utils.visualize import (
    prepare_visualize,  # Import the new prepare_visualize
)

ems_eos = get_ems(init=True) # init once

DIR_TESTDATA = Path(__file__).parent / "testdata"


def compare_dict(actual: dict[str, Any], expected: dict[str, Any]):
    assert set(actual) == set(expected)

    for key, value in expected.items():
        if isinstance(value, dict):
            assert isinstance(actual[key], dict)
            compare_dict(actual[key], value)
        elif isinstance(value, list):
            assert isinstance(actual[key], list)
            if value and isinstance(value[0], datetime):
                assert actual[key] == value
            else:
                assert actual[key] == pytest.approx(value)
        else:
            assert actual[key] == pytest.approx(value)


def test_direct_marketing_uses_market_price_as_feed_in_tariff(config_eos: ConfigEOS):
    config_eos.merge_settings_from_dict(
        {"feedintariff": {"direct_marketing_enabled": True}}
    )
    parameters = GeneticOptimizationParameters(
        ems={
            "pv_prognose_wh": [0.0, 0.0],
            "strompreis_euro_pro_wh": [0.0002, -0.0001],
            "einspeiseverguetung_euro_pro_wh": [0.00007, 0.00007],
            "preis_euro_pro_wh_akku": 0.0,
            "gesamtlast": [0.0, 0.0],
        },
        pv_akku=None,
        # Without an inverter the simulation books no grid energy at all, so the
        # price signal would never reach the fitness.
        inverter={"device_id": "inverter1", "max_power_wh": 20000},
        eauto=None,
    )

    adjusted = GeneticOptimization()._parameters_for_config(parameters)

    assert adjusted.ems.einspeiseverguetung_euro_pro_wh == [0.0002, -0.0001]
    assert parameters.ems.einspeiseverguetung_euro_pro_wh == [0.00007, 0.00007]


def test_direct_marketing_keeps_variable_feed_in_tariff(config_eos: ConfigEOS):
    config_eos.merge_settings_from_dict(
        {"feedintariff": {"direct_marketing_enabled": True}}
    )
    parameters = GeneticOptimizationParameters(
        ems={
            "pv_prognose_wh": [0.0, 0.0],
            "strompreis_euro_pro_wh": [0.0002, 0.0003],
            "einspeiseverguetung_euro_pro_wh": [0.0001, -0.00005],
            "preis_euro_pro_wh_akku": 0.0,
            "gesamtlast": [0.0, 0.0],
        },
        pv_akku=None,
        inverter=None,
        eauto=None,
    )

    adjusted = GeneticOptimization()._parameters_for_config(parameters)

    assert adjusted.ems.einspeiseverguetung_euro_pro_wh == [0.0001, -0.00005]


def test_grid_export_rates_reach_the_solution(config_eos: ConfigEOS):
    """Configured export rates end up as per-slot export levels in the solution."""
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 24},
            "optimization": {
                "horizon_hours": 24,
                "interval": 3600,
                "genetic": {"individuals": 40, "generations": 10},
            },
            "feedintariff": {"direct_marketing_enabled": True},
            "devices": {
                "max_batteries": 1,
                "batteries": [{"device_id": "battery1", "grid_export_rates": [0.5, 1.0]}],
            },
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=0, minute=0))
    CacheEnergyManagementStore().clear()

    hours = 24
    parameters = GeneticOptimizationParameters(
        ems={
            "pv_prognose_wh": [0.0] * hours,
            "strompreis_euro_pro_wh": [0.0003] * hours,
            # A pronounced tariff peak makes exporting worthwhile at all.
            "einspeiseverguetung_euro_pro_wh": [0.0001] * 12 + [0.0009] * 12,
            "preis_euro_pro_wh_akku": 0.0,
            "gesamtlast": [200.0] * hours,
        },
        pv_akku={
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
        eauto=None,
    )

    optimization = GeneticOptimization(fixed_seed=42)
    solution = optimization.optimierung_ems(parameters=parameters, start_hour=0, ngen=3)

    # Full power first, so the full-power state keeps the lowest export index.
    assert optimization.bat_possible_grid_export_values == [1.0, 0.5]
    assert len(solution.battery_grid_export_factor) == len(solution.battery_grid_export_allowed)
    assert set(solution.battery_grid_export_factor) <= {0.0, 0.5, 1.0}
    assert [
        1 if factor > 0.0 else 0 for factor in solution.battery_grid_export_factor
    ] == solution.battery_grid_export_allowed


@pytest.mark.parametrize(
    "fn_in, fn_out, ngen, break_even",
    [
        ("optimize_input_1.json", "optimize_result_1.json", 3, 0),
        ("optimize_input_2.json", "optimize_result_2.json", 3, 0),
        ("optimize_input_2.json", "optimize_result_2_full.json", 400, 0),
        ("optimize_input_1.json", "optimize_result_1_be.json", 3, 1),
        ("optimize_input_2.json", "optimize_result_2_be.json", 3, 1),
    ],
)
def test_optimize(
    fn_in: str,
    fn_out: str,
    ngen: int,
    break_even: int,
    config_eos: ConfigEOS,
    is_finalize: bool,
):
    """Test optimierung_ems."""
    # Test parameters
    fixed_start_hour = 10
    fixed_seed = 42

    # Assure configuration holds the correct values
    config_eos.merge_settings_from_dict(
        {
            "prediction": {
                "hours": 48
            },
            "optimization": {
                "horizon_hours": 48,
                "genetic": {
                    "individuals": 300,
                    "generations": 10,
                    "penalties": {
                        "ev_soc_miss": 10,
                        "ac_charge_break_even": break_even,
                    }
                }
            },
            "devices": {
                "max_electric_vehicles": 1,
                "electric_vehicles": [
                    {
                        "charge_rates": [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0],
                    }
                ],
             }
         }
    )

    # Load input and output data
    file = DIR_TESTDATA / fn_in
    with file.open("r") as f_in:
        input_data = GeneticOptimizationParameters(**json.load(f_in))

    file = DIR_TESTDATA / fn_out
    # In case a new test case is added, we don't want to fail here, so the new output is written
    # to disk before
    try:
        with file.open("r") as f_out:
            expected_data = json.load(f_out)
            expected_result = GeneticSolution(**expected_data)
    except FileNotFoundError:
        pass

    # Fake energy management run start datetime
    ems_eos.set_start_datetime(to_datetime("2025-01-15T10:00:00+01:00"))

    # Throw away any cached results of the last energy management run.
    CacheEnergyManagementStore().clear()

    genetic_optimization = GeneticOptimization(fixed_seed=fixed_seed)

    # Activate with pytest --finalize
    if ngen > 10 and not is_finalize:
        pytest.skip()

    visualize_filename = str((DIR_TESTDATA / f"new_{fn_out}").with_suffix(".pdf"))

    with patch(
        "akkudoktoreos.utils.visualize.prepare_visualize",
        side_effect=lambda parameters, results, *args, **kwargs: prepare_visualize(
            parameters, results, filename=visualize_filename, **kwargs
        ),
    ) as prepare_visualize_patch:
        # Call the optimization function
        genetic_solution = genetic_optimization.optimierung_ems(
            parameters=input_data, start_hour=fixed_start_hour, ngen=ngen
        )
        # The function creates a visualization result PDF as a side-effect.
        prepare_visualize_patch.assert_called_once()
        assert Path(visualize_filename).exists()

    # Write test output to file, so we can take it as new data on intended change
    TESTDATA_FILE = DIR_TESTDATA / f"new_{fn_out}"
    with TESTDATA_FILE.open("w", encoding="utf-8", newline="\n") as f_out:
        f_out.write(genetic_solution.model_dump_json(indent=4, exclude_unset=True))

    assert genetic_solution.result.Gesamtbilanz_Euro == pytest.approx(
        expected_result.result.Gesamtbilanz_Euro
    )

    # Assert that the output contains all expected entries.
    # This does not assert that the optimization always gives the same result!
    # Reproducibility and mathematical accuracy should be tested on the level of individual components.
    compare_dict(genetic_solution.model_dump(), expected_result.model_dump())

    # Check the correct generic optimization solution is created
    optimization_solution = genetic_solution.optimization_solution()
    # @TODO

    # Check the correct generic energy management plan is created
    plan = genetic_solution.energy_management_plan()
    # @TODO


def _ev_deadline_parameters(hours: int, **ev_extra) -> GeneticOptimizationParameters:
    """Optimization parameters with an EV that has to be charged."""
    return GeneticOptimizationParameters(
        ems={
            "pv_prognose_wh": [0.0] * hours,
            # Expensive for the first six hours, dirt cheap afterwards: without a
            # deadline the optimizer would always wait for the cheap slots.
            "strompreis_euro_pro_wh": [0.0009] * 6 + [0.00001] * (hours - 6),
            "einspeiseverguetung_euro_pro_wh": [0.00007] * hours,
            "preis_euro_pro_wh_akku": 0.0,
            "gesamtlast": [300.0] * hours,
        },
        pv_akku=None,
        inverter=None,
        eauto={
            "device_id": "ev1",
            "capacity_wh": 60000,
            "charging_efficiency": 0.95,
            "max_charge_power_w": 11040,
            "initial_soc_percentage": 20,
            "min_soc_percentage": 60,
            **ev_extra,
        },
    )


def test_ev_deadline_slot_resolution(config_eos: ConfigEOS):
    """Datetime and maximum duration resolve to a slot; the earlier one wins."""
    config_eos.merge_settings_from_dict(
        {"prediction": {"hours": 48}, "optimization": {"horizon_hours": 48, "interval": 3600}}
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=10, minute=0))
    optimization = GeneticOptimization(fixed_seed=1)
    optimization._slot0_datetime = optimization.ems.start_datetime.set(
        hour=0, minute=0, second=0, microsecond=0
    )
    slot0 = optimization._slot0_datetime

    # Duration only: 6 h after the start hour 10.
    parameters = _ev_deadline_parameters(48, min_soc_max_duration_h=6)
    assert optimization._ev_deadline_slot(parameters) == 16

    # Datetime only.
    parameters = _ev_deadline_parameters(48, min_soc_deadline_datetime=slot0.add(hours=14))
    assert optimization._ev_deadline_slot(parameters) == 14

    # Both: the earlier one wins.
    parameters = _ev_deadline_parameters(
        48, min_soc_deadline_datetime=slot0.add(hours=20), min_soc_max_duration_h=6
    )
    assert optimization._ev_deadline_slot(parameters) == 16

    # Beyond the horizon: no deadline, the end-of-horizon target already covers it.
    parameters = _ev_deadline_parameters(48, min_soc_deadline_datetime=slot0.add(hours=100))
    assert optimization._ev_deadline_slot(parameters) is None

    # In the past: due right now.
    parameters = _ev_deadline_parameters(48, min_soc_deadline_datetime=slot0.add(hours=2))
    assert optimization._ev_deadline_slot(parameters) == optimization._start_day_slot()

    # No deadline at all.
    assert optimization._ev_deadline_slot(_ev_deadline_parameters(48)) is None


def test_ev_soc_penalty_reads_the_deadline_slot(config_eos: ConfigEOS):
    """With a deadline the penalty checks the SoC at that slot, not at the end."""
    config_eos.merge_settings_from_dict(
        {"prediction": {"hours": 48}, "optimization": {"horizon_hours": 48, "interval": 3600}}
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=10, minute=0))
    optimization = GeneticOptimization(fixed_seed=1)
    simulation_result = {"EAuto_SoC_pro_Stunde": [20.0, 35.0, 50.0, 80.0]}

    class _Ev:
        def current_soc_percentage(self):
            return 80.0

    optimization.simulation.ev = _Ev()

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
                "horizon_hours": hours,
                "interval": 3600,
                "genetic": {"individuals": 100, "generations": 40},
            },
        }
    )
    ems_eos.set_start_datetime(to_datetime().set(hour=0, minute=0))
    CacheEnergyManagementStore().clear()

    parameters = _ev_deadline_parameters(hours, min_soc_max_duration_h=6)
    solution = GeneticOptimization(fixed_seed=42).optimierung_ems(
        parameters=parameters, start_hour=0, ngen=40
    )

    soc_per_hour = solution.result.EAuto_SoC_pro_Stunde
    # Slot 6 is the first slot at or after the deadline, so its start-of-slot SoC
    # is what the target is checked against.
    assert soc_per_hour[6] >= 60.0
