import json
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
            assert actual[key] == pytest.approx(value)
        else:
            assert actual[key] == pytest.approx(value)


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
    ems_eos.set_start_datetime(to_datetime().set(hour=fixed_start_hour))

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
