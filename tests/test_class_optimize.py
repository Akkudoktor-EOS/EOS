import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from akkudoktoreos.config.config import get_config
from akkudoktoreos.optimization.genetic import (
    OptimizationParameters,
    OptimizeResponse,
    optimization_problem,
)

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
    "fn_in, fn_out, ngen",
    [
        ("optimize_input_1.json", "optimize_result_1.json", 3),
        ("optimize_input_2.json", "optimize_result_2.json", 3),
        ("optimize_input_2.json", "optimize_result_2_full.json", 400),
    ],
)
@patch("akkudoktoreos.optimization.genetic.visualisiere_ergebnisse")
def test_optimize(
    visualisiere_ergebnisse_patch, fn_in: str, fn_out: str, ngen: int, is_full_run: bool
):
    """Test optimierung_ems."""
    # Assure configuration holds the correct values
    config_eos = get_config()
    config_eos.merge_settings_from_dict({"prediction_hours": 48, "optimization_hours": 24})

    # Load input and output data
    file = DIR_TESTDATA / fn_in
    with file.open("r") as f_in:
        input_data = OptimizationParameters(**json.load(f_in))

    file = DIR_TESTDATA / fn_out
    with file.open("r") as f_out:
        expected_result = OptimizeResponse(**json.load(f_out))

    opt_class = optimization_problem(fixed_seed=42)
    start_hour = 10

    if ngen > 10 and not is_full_run:
        pytest.skip()

    # Call the optimization function
    ergebnis = opt_class.optimierung_ems(parameters=input_data, start_hour=start_hour, ngen=ngen)
    # with open(f"new_{fn_out}", "w") as f_out:
    #     from akkudoktoreos.utils import NumpyEncoder
    #     json_data_str = NumpyEncoder.dumps(ergebnis)
    #     json.dump(json.loads(json_data_str), f_out, indent=4)

    # Assert that the output contains all expected entries.
    # This does not assert that the optimization always gives the same result!
    # Reproducibility and mathematical accuracy should be tested on the level of individual components.
    compare_dict(ergebnis.model_dump(), expected_result.model_dump())

    # The function creates a visualization result PDF as a side-effect.
    visualisiere_ergebnisse_patch.assert_called_once()
