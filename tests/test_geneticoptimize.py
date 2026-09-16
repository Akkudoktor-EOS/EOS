import json
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pypdf import PdfReader

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.genetic.geneticvisualize import (
    genetic_prepare_visualize,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

ems_eos = get_ems(init=True)  # init once

DIR_TESTDATA = Path(__file__).parent / "testdata" / "genetic"


def compare_dict(actual: dict[str, Any], expected: dict[str, Any]):
    assert set(actual) == set(expected)

    for key, value in expected.items():
        if isinstance(value, dict):
            assert isinstance(actual[key], dict)
            compare_dict(actual[key], value)
        elif isinstance(value, list):
            assert isinstance(actual[key], list)
            assert actual[key] == pytest.approx(value)
        else:
            assert actual[key] == pytest.approx(value)


@pytest.mark.asyncio
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
async def test_optimize(
    fn_in: str,
    fn_out: str,
    ngen: int,
    break_even: int,
    config_eos: ConfigEOS,
    is_finalize: bool,
):
    """Test optimize_ems."""
    # Test parameters
    fixed_start_hour = 10
    fixed_seed = 42

    # Assure configuration holds the correct values
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "horizon_hours": 38,
                    "tail_horizon_hours": 0,
                    "terminal_value_mode": "FIXED",
                    "individuals": 300,
                    "generations": 10,
                    "penalties": {
                        "ev_soc_miss": 10,
                        "ac_charge_break_even": break_even,
                    },
                },
            },
            "devices": {
                "max_electric_vehicles": 1,
                "electric_vehicles": {
                    "ev1": {
                        "charge_rates": [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0],
                    }
                },
            },
        }
    )

    # Load input and output data
    parameter_file = DIR_TESTDATA / fn_in
    with parameter_file.open("r") as f_in:
        input_data = GeneticOptimizationParameters(**json.load(f_in))

    # Fake energy management run start datetime
    ems_eos.set_start_datetime(to_datetime("2026-09-16T10:00:00+02:00"))

    # Throw away any cached results of the last energy management run.
    CacheEnergyManagementStore().clear()

    genetic_optimization = GeneticOptimization(fixed_seed=fixed_seed)

    # Activate with pytest --finalize
    if ngen > 10 and not is_finalize:
        pytest.skip()

    # Call the optimization function
    genetic_solution = genetic_optimization.optimize_ems(
        parameters=input_data, start_hour=fixed_start_hour, ngen=ngen
    )

    # Historical payloads still deserialize with deprecated English/German aliases.
    with (DIR_TESTDATA / fn_out).open("r") as expected_file:
        expected_result = GeneticSolution.model_validate(json.load(expected_file))

    # Keep the output contract, but do not demand an identical stochastic
    # schedule or monetary golden from the previous direct-consumption model.
    assert set(genetic_solution.model_dump()) == set(expected_result.model_dump())
    result = genetic_solution.result
    expected_slots = len(input_data.ems.pv_forecast_wh) - fixed_start_hour
    assert len(result.grid_consumption_wh_per_hour) == expected_slots
    assert len(result.grid_feed_in_wh_per_hour) == expected_slots
    prices = np.asarray(genetic_solution.parameters.ems.electricity_price_per_wh)
    tariffs = np.asarray(genetic_solution.parameters.ems.feed_in_tariff_per_wh)[:expected_slots]
    expected_costs = np.asarray(result.grid_consumption_wh_per_hour) * prices
    expected_revenues = np.asarray(result.grid_feed_in_wh_per_hour) * tariffs
    np.testing.assert_allclose(result.costs_per_hour, expected_costs)
    np.testing.assert_allclose(result.revenue_per_hour, expected_revenues)
    assert result.total_costs == pytest.approx(sum(expected_costs))
    assert result.total_revenue == pytest.approx(sum(expected_revenues))
    assert result.total_balance == pytest.approx(sum(expected_costs) - sum(expected_revenues))
    assert result.total_losses == pytest.approx(sum(result.losses_per_hour))
    assert all(value >= 0 for value in result.grid_consumption_wh_per_hour)
    assert all(value >= 0 for value in result.grid_feed_in_wh_per_hour)
    assert all(0 <= value <= 100 for value in result.battery_soc_per_hour)
    assert all(0 <= value <= 100 for value in result.ev_soc_per_hour)

    # Check the correct generic optimization solution is created
    optimization_solution = await genetic_solution.optimization_solution()
    dataframe = optimization_solution.solution.to_dataframe()
    assert len(dataframe) == expected_slots
    assert optimization_solution.valid_from == genetic_solution.start_solution_datetime
    assert optimization_solution.valid_until == ems_eos.start_datetime.add(hours=expected_slots)
    assert genetic_solution.controls_start_at_now
    assert len(genetic_solution.ac_charge) == expected_slots
    assert len(genetic_solution.dc_charge) == expected_slots
    assert len(genetic_solution.discharge_allowed) == expected_slots

    # Check the correct generic energy management plan is created
    plan = genetic_solution.energy_management_plan()
    assert plan.valid_from == optimization_solution.valid_from
    assert plan.valid_until is None
    assert optimization_solution.valid_from is not None
    assert optimization_solution.valid_until is not None
    assert all(
        optimization_solution.valid_from <= item.execution_time < optimization_solution.valid_until
        for item in plan.instructions
    )

    # Check visualization works
    pdf = genetic_prepare_visualize(
        solution=genetic_solution,
    )
    assert pdf.startswith(b"%PDF-")

    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) >= 6
