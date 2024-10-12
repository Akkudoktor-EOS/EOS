import json
from pathlib import Path

import pytest
from mock import patch

from akkudoktoreos.class_optimize import optimization_problem

DIR_TESTDATA = Path(__file__).parent / "testdata"


@pytest.mark.parametrize("fn_in, fn_out", [("optimize_input_1.json", "optimize_result_1.json")])
@patch("akkudoktoreos.class_optimize.visualisiere_ergebnisse")
def test_optimize(visualisiere_ergebnisse_patch, fn_in, fn_out):
    # Load input and output data
    with open(DIR_TESTDATA / fn_in, "r") as f_in:
        input_data = json.load(f_in)

    with open(DIR_TESTDATA / fn_out, "r") as f_out:
        expected_output_data = json.load(f_out)

    opt_class = optimization_problem(
        prediction_hours=48, strafe=10, optimization_hours=24, fixed_seed=42
    )
    start_hour = 10

    # Call the optimization function
    ergebnis = opt_class.optimierung_ems(parameter=input_data, start_hour=start_hour, ngen=3)
    # with open("new.json", "w") as f_out:
    #     json.dump(ergebnis, f_out, indent=4)

    # Assert that the output contains all expected entries.
    # This does not assert that the optimization always gives the same result!
    # Reproducibility and mathematical accuracy should be tested on the level of individual components.
    assert set(ergebnis) == set(expected_output_data)

    # The function creates a visualization result PDF as a side-effect.
    visualisiere_ergebnisse_patch.assert_called_once()
