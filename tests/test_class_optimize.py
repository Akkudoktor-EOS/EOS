import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from akkudoktoreos.config import AppConfig
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
def test_optimize(
    fn_in: str,
    fn_out: str,
    ngen: int,
    is_full_run: bool,
    tmp_config: AppConfig,
):
    """Test optimierung_ems."""
    # Load input and output data
    file = DIR_TESTDATA / fn_in
    with file.open("r") as f_in:
        input_data = OptimizationParameters(**json.load(f_in))

    file = DIR_TESTDATA / fn_out
    # In case a new test case is added, we don't want to fail here, so the new output is written to disk before
    try:
        with file.open("r") as f_out:
            expected_result = OptimizeResponse(**json.load(f_out))
    except FileNotFoundError:
        pass

    opt_class = optimization_problem(tmp_config, fixed_seed=42)
    start_hour = 10

    if ngen > 10 and not is_full_run:
        pytest.skip()

    visualize_filename = str((DIR_TESTDATA / f"new_{fn_out}").with_suffix(".pdf"))

    def visualize_to_file(*args, **kwargs):
        from akkudoktoreos.visualize import visualisiere_ergebnisse

        # Write test output pdf to file, so we can look at it manually
        kwargs["filename"] = visualize_filename
        return visualisiere_ergebnisse(*args, **kwargs)

    with patch(
        "akkudoktoreos.optimization.genetic.visualisiere_ergebnisse", side_effect=visualize_to_file
    ) as visualisiere_ergebnisse_patch:
        # Call the optimization function
        ergebnis = opt_class.optimierung_ems(
            parameters=input_data, start_hour=start_hour, ngen=ngen
        )
        # Write test output to file, so we can take it as new data on intended change
        with open(DIR_TESTDATA / f"new_{fn_out}", "w") as f_out:
            f_out.write(ergebnis.model_dump_json(indent=4, exclude_unset=True))

        assert ergebnis.result.Gesamtbilanz_Euro == pytest.approx(
            expected_result.result.Gesamtbilanz_Euro
        )

        # Assert that the output contains all expected entries.
        # This does not assert that the optimization always gives the same result!
        # Reproducibility and mathematical accuracy should be tested on the level of individual components.
        compare_dict(ergebnis.model_dump(), expected_result.model_dump())

        # The function creates a visualization result PDF as a side-effect.
        visualisiere_ergebnisse_patch.assert_called_once()
