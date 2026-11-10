import json
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from pypdf import PdfReader

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic0.genetic0 import Genetic0Optimization
from akkudoktoreos.optimization.genetic0.genetic0params import (
    Genetic0OptimizationParameters,
)
from akkudoktoreos.optimization.genetic0.genetic0solution import Genetic0Solution
from akkudoktoreos.optimization.genetic0.genetic0visualize import (
    genetic0_prepare_visualize,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

ems_eos = get_ems(init=True) # init once

DIR_TESTDATA = Path(__file__).parent / "testdata" / "genetic0"


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
            "prediction": {
                "hours": 48
            },
            "optimization": {
                "algorithm": "GENETIC0",
                "genetic0": {
                    "horizon_hours": 48,
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
                "electric_vehicles": { "ev1":
                    {
                        "charge_rates": [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0],
                    }
                },
             }
         }
    )

    # Load input and output data
    parameter_file = DIR_TESTDATA / fn_in
    with parameter_file.open("r") as f_in:
        input_data = Genetic0OptimizationParameters(**json.load(f_in))

    # Fake energy management run start datetime
    ems_eos.set_start_datetime(to_datetime().set(hour=fixed_start_hour))

    # Throw away any cached results of the last energy management run.
    CacheEnergyManagementStore().clear()

    genetic0_optimization = Genetic0Optimization(fixed_seed=fixed_seed)

    # Activate with pytest --finalize
    if ngen > 10 and not is_finalize:
        pytest.skip()

    # Call the optimization function
    genetic0_solution = genetic0_optimization.optimize_ems(
        parameters=input_data, start_hour=fixed_start_hour, ngen=ngen
    )

    # Write test output to file, so we can take it as new data on intended change
    TESTDATA_FILE = DIR_TESTDATA / f"new_{fn_out}"
    with TESTDATA_FILE.open("w", encoding="utf-8", newline="\n") as f_out:
        f_out.write(genetic0_solution.model_dump_json(indent=4, exclude_unset=True))

    solution_file = DIR_TESTDATA / fn_out
    # In case a new test case is added, we don't want to fail here, so the new output is written
    # to disk before
    try:
        with solution_file.open("r") as f_out:
            expected_data = json.load(f_out)
            expected_result = Genetic0Solution(**expected_data)
    except ValidationError:
        # Expected genetic solution data does not fit to Genetic0Solution data schema
        # Possibly the Genetic0Solution class changed.
        pytest.fail(
            f"ValidationError: Can not load expected solution from {solution_file}\n"
            f"cp {TESTDATA_FILE} {solution_file}\n"
        )
    except FileNotFoundError:
        # Should not happen
        pytest.fail(
            f"FileNotFoundError: Can not load expected solution from {solution_file}\n"
            f"cp {TESTDATA_FILE} {solution_file}\n"
        )

    assert genetic0_solution.result.Gesamtbilanz_Euro == pytest.approx(
        expected_result.result.Gesamtbilanz_Euro
    )

    # Assert that the output contains all expected entries.
    # This does not assert that the optimization always gives the same result!
    # Reproducibility and mathematical accuracy should be tested on the level of individual components.
    compare_dict(genetic0_solution.model_dump(), expected_result.model_dump())

    # Check the correct generic optimization solution is created
    optimization_solution = await genetic0_solution.optimization_solution()
    # @TODO

    # Check the correct generic energy management plan is created
    plan = genetic0_solution.energy_management_plan()
    # @TODO

    # Check visualization works
    pdf = genetic0_prepare_visualize(
        solution=genetic0_solution,
    )
    assert pdf.startswith(b"%PDF-")

    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) == 6


    # Everything passed, remove generated files
    TESTDATA_FILE.unlink()


class TestGenetic0SocClampedOperationFactors:
    """The SOC clamping uses the optimizer battery parameters, not the settings.

    Battery settings no longer hold capacity / efficiency / power fields (they
    come from the battery profile), so reading them from the settings fails.
    """

    @pytest.fixture
    def solution(self, config_eos: ConfigEOS) -> Genetic0Solution:
        # A configured battery whose settings differ from the solution parameters.
        config_eos.merge_settings_from_dict(
            {
                "devices": {
                    "max_batteries": 1,
                    "batteries": {
                        "battery1": {"device_id": "battery1", "profile_adjustment": {"nominal_capacity_wh": 8000}}
                    },
                    "max_inverters": 0,
                    "inverters": None,
                }
            }
        )
        data = json.loads((DIR_TESTDATA / "optimize_result_1.json").read_text())
        return Genetic0Solution.model_validate(data)

    def test_uses_solution_battery_parameters(self, solution):
        bat = solution.parameters.pv_battery
        assert bat is not None
        assert bat.capacity_wh == 26400
        # 1 % headroom of 26400 Wh against one hour at 5000 W and the charging
        # efficiency of the parameters (no inverter configured).
        headroom_wh = 0.01 * bat.capacity_wh
        expected = headroom_wh / (bat.max_charge_power_w * bat.charging_efficiency)
        ac, dc, dis = solution._soc_clamped_operation_factors(
            1.0, 1.0, True, float(bat.max_soc_percentage) - 1.0
        )
        assert ac == pytest.approx(expected)
        assert dc == pytest.approx(1.0)
        assert dis is True

    def test_full_and_empty_battery(self, solution):
        bat = solution.parameters.pv_battery
        assert solution._soc_clamped_operation_factors(
            1.0, 1.0, True, float(bat.max_soc_percentage)
        ) == (0.0, 0.0, True)
        assert solution._soc_clamped_operation_factors(
            0.0, 0.0, True, float(bat.min_soc_percentage)
        ) == (0.0, 0.0, False)

    def test_without_battery_parameters_factors_are_unchanged(self, solution):
        solution.parameters.pv_battery = None
        assert solution._soc_clamped_operation_factors(1.0, 0.5, True, 50.0) == (1.0, 0.5, True)

    @pytest.mark.asyncio
    async def test_optimization_solution_with_configured_battery(self, solution):
        optimization_solution = await solution.optimization_solution()
        assert optimization_solution is not None


class TestGenetic0SolutionWithProfileInverter:
    """The solution uses the inverter parameters of the run, not the inverter settings.

    Inverter settings no longer hold efficiencies or power fields (they come
    from the inverter profile), so reading them from the settings fails.
    """

    @pytest.fixture
    def solution(self, config_eos: ConfigEOS) -> Genetic0Solution:
        config_eos.merge_settings_from_dict(
            {
                "devices": {
                    "max_batteries": 1,
                    "batteries": {
                        "battery1": {
                            "device_id": "battery1",
                            "profile_adjustment": {"nominal_capacity_wh": 8000},
                        }
                    },
                    "max_inverters": 1,
                    "inverters": {
                        "inverter1": {
                            "device_id": "inverter1",
                            "battery_id": "battery1",
                            "profile_adjustment": {
                                "nominal_power_w": 10000,
                                "max_ac_charge_power_w": 2000,
                            },
                        }
                    },
                }
            }
        )
        data = json.loads((DIR_TESTDATA / "optimize_result_1.json").read_text())
        return Genetic0Solution.model_validate(data)

    def test_uses_solution_inverter_parameters(self, solution):
        bat = solution.parameters.pv_battery
        inverter = solution.parameters.inverter
        assert bat is not None and inverter is not None
        # The stored inverter has no AC charge limit and efficiency 1.0, unlike
        # the configured one: the battery charge power limits the AC charge.
        headroom_wh = 0.01 * bat.capacity_wh
        expected = headroom_wh / (
            bat.max_charge_power_w * inverter.ac_to_dc_efficiency * bat.charging_efficiency
        )
        ac, _, _ = solution._soc_clamped_operation_factors(
            1.0, 1.0, True, float(bat.max_soc_percentage) - 1.0
        )
        assert ac == pytest.approx(expected)

    def test_without_stored_parameters_the_configured_inverter_is_resolved(self, solution):
        inverter = solution._inverter_parameters(None)
        assert inverter is not None
        assert inverter.max_power_wh == pytest.approx(10000)
        assert inverter.max_ac_charge_power_w == pytest.approx(2000)
        assert 0.0 < inverter.ac_to_dc_efficiency < 1.0

    def test_unresolvable_inverter_counts_as_no_inverter(self, solution, config_eos):
        config_eos.merge_settings_from_dict(
            {"devices": {"inverters": {"inverter1": {"profile_adjustment": {"nominal_power_w": None}}}}}
        )
        assert solution._inverter_parameters(None) is None

    @pytest.mark.asyncio
    async def test_optimization_solution_with_configured_inverter(self, solution):
        """Regression: AttributeError 'ac_to_dc_efficiency' on InverterCommonSettings."""
        optimization_solution = await solution.optimization_solution()
        assert optimization_solution is not None
