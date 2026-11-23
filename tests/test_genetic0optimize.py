import dataclasses
import inspect
import json
import math
import re
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest
from pydantic import ValidationError
from pypdf import PdfReader

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.optimization.genetic0 import genetic0convert
from akkudoktoreos.optimization.genetic0.genetic0api import (
    Genetic0Solution,
    solution_response,
)
from akkudoktoreos.optimization.genetic0.genetic0optimizer import Genetic0Optimizer
from akkudoktoreos.optimization.genetic0.genetic0parameters import (
    parameters_from_dict,
    parameters_to_dict,
)
from akkudoktoreos.optimization.genetic0.genetic0request import (
    Genetic0Request,
)
from akkudoktoreos.optimization.genetic0.genetic0result import (
    Genetic0ElectricVehicleState,
    Genetic0Result,
    Genetic0SimulationOutput,
)
from akkudoktoreos.optimization.genetic0.genetic0visualize import (
    Genetic0VisualizationReport,
    genetic0_prepare_visualize,
)
from akkudoktoreos.utils.datetimeutil import DateTime, Duration, to_datetime

ems_eos = get_ems(init=True) # init once

DIR_TESTDATA = Path(__file__).parent / "testdata" / "genetic0"

# Fixed run starts, so that the date-bearing output can be pinned (ADR 0008).
FIXED_START = "2026-01-14T10:00:00"
# The horizon crosses the switch to daylight saving time (Europe/Berlin, 2026-03-29).
DST_START = "2026-03-28T10:00:00"

# Origin of the fake prediction series: a value depends on its time, not its index.
PREDICTION_ORIGIN = "2026-01-01T00:00:00+00:00"
# The fill method the conversion asks for shifts the fake series, so it is pinned too.
FILL_METHOD_OFFSET = {"linear": 0.0, "ffill": 1000.0}

# Prediction keys the solution conversion reads; each gets its own rising series,
# so a shifted or swapped series changes the pinned output.
PREDICTION_KEYS = (
    "pvforecast_ac_power",
    "pvforecast_dc_power",
    "elecprice_marketprice_wh",
    "feed_in_tariff_wh",
    "weather_temp_air",
    "loadforecast_power_w",
    "loadakkudoktor_std_power_w",
    "loadakkudoktor_mean_power_w",
)

# Report methods whose arguments are the input of the visualization.
REPORT_METHODS = (
    "create_line_chart_date",
    "create_line_chart",
    "create_scatter_plot",
    "create_bar_chart",
    "create_violin_plot",
    "add_text_page",
    "add_json_page",
)

# Placeholder for the wall-clock time of the conversion in IDs and timestamps.
NOW = "<now>"

# Random UUIDs, e.g. in instruction IDs ("battery1@<uuid>").
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class FakePrediction:
    """Deterministic predictions for the solution conversion."""

    record_keys = list(PREDICTION_KEYS)

    async def key_to_array(
        self,
        key: str,
        start_datetime: DateTime,
        end_datetime: DateTime,
        interval: Duration,
        fill_method: str,
        **kwargs: Any,
    ) -> np.ndarray:
        """A rising series per key and fill method whose values follow the absolute time."""
        step_hours = interval.total_seconds() / 3600
        steps = int((end_datetime - start_datetime).total_seconds() // interval.total_seconds())
        first_hour = (start_datetime - to_datetime(PREDICTION_ORIGIN)).total_seconds() / 3600
        base = 10.0 * (PREDICTION_KEYS.index(key) + 1) + FILL_METHOD_OFFSET[fill_method]
        return base + 0.5 * (first_hour + step_hours * np.arange(steps))


def mask_now(dump: dict[str, Any]) -> dict[str, Any]:
    """Replace the wall-clock time of the conversion in ``id`` and ``generated_at``."""
    prefix, _, _ = dump["id"].partition("@")
    return {**dump, "id": f"{prefix}@{NOW}", "generated_at": NOW}


def to_jsonable(value: Any) -> Any:
    """Convert the output of a run into plain JSON values for golden files."""
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return to_jsonable(value.tolist())
    if isinstance(value, np.generic):
        return to_jsonable(value.item())
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        return UUID_PATTERN.sub("<uuid>", value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def compare_jsonable(actual: Any, expected: Any, path: str = "$") -> None:
    """Compare plain JSON values; numbers approximately."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        assert set(actual) == set(expected), path
        for key, value in expected.items():
            compare_jsonable(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), path
        assert len(actual) == len(expected), path
        for index, (a, e) in enumerate(zip(actual, expected)):
            compare_jsonable(a, e, f"{path}[{index}]")
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        assert actual == pytest.approx(expected), path
    else:
        assert actual == expected, path


def check_golden(actual: Any, golden: str) -> None:
    """Compare ``actual`` with the golden file; write ``new_<golden>`` for review on a miss."""
    actual = to_jsonable(actual)
    new_file = DIR_TESTDATA / f"new_{golden}"
    new_file.write_text(json.dumps(actual, indent=4) + "\n", encoding="utf-8", newline="\n")
    golden_file = DIR_TESTDATA / golden
    if not golden_file.exists():
        pytest.fail(f"Missing golden file {golden_file}\ncp {new_file} {golden_file}\n")
    compare_jsonable(actual, json.loads(golden_file.read_text(encoding="utf-8")))
    new_file.unlink()


def record_report_calls() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Spies on the report methods: the recorded calls and the patches to apply."""
    calls: list[dict[str, Any]] = []

    def spy(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(original)

        def method(self: Any, *args: Any, **kwargs: Any) -> Any:
            # Bound by name with defaults, so the call style does not matter.
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            arguments = dict(bound.arguments)
            arguments.pop("self")
            calls.append({"method": name, "arguments": arguments})
            return original(self, *args, **kwargs)

        return method

    patches = {
        name: spy(name, getattr(Genetic0VisualizationReport, name)) for name in REPORT_METHODS
    }
    return calls, patches


def check_replay(parameters: Any, golden_stem: str, expected_result: str) -> None:
    """Compare the run's parameters with its replay file; write ``new_<replay>`` on a miss."""
    replay = {"expected_result": expected_result, "parameters": parameters_to_dict(parameters)}
    # Lossless: what is written reads back to the same values.
    restored = parameters_from_dict(json.loads(json.dumps(replay["parameters"])))
    assert parameters_to_dict(restored) == replay["parameters"]
    check_golden(replay, f"replay_{golden_stem}.json")


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
    "fn_in, fn_out, ngen, break_even, start, golden_stem",
    [
        ("optimize_input_1.json", "optimize_result_1.json", 3, 0, FIXED_START, "optimize_result_1"),
        ("optimize_input_2.json", "optimize_result_2.json", 3, 0, FIXED_START, "optimize_result_2"),
        (
            "optimize_input_2.json",
            "optimize_result_2_full.json",
            400,
            0,
            FIXED_START,
            "optimize_result_2_full",
        ),
        (
            "optimize_input_1.json",
            "optimize_result_1_be.json",
            3,
            1,
            FIXED_START,
            "optimize_result_1_be",
        ),
        (
            "optimize_input_2.json",
            "optimize_result_2_be.json",
            3,
            1,
            FIXED_START,
            "optimize_result_2_be",
        ),
        ("optimize_input_2.json", "optimize_result_2.json", 3, 0, DST_START, "optimize_result_2_dst"),
    ],
)
async def test_optimize(
    fn_in: str,
    fn_out: str,
    ngen: int,
    break_even: int,
    start: str,
    golden_stem: str,
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
                # Covers the home appliance part of the solution conversion.
                "max_home_appliances": 1,
                "home_appliances": {"dishwasher1": {"device_id": "dishwasher1"}},
             }
         }
    )

    # Load input and output data
    parameter_file = DIR_TESTDATA / fn_in
    with parameter_file.open("r") as f_in:
        input_data = Genetic0Request(**json.load(f_in))

    # Fake energy management run start datetime
    ems_eos.set_start_datetime(
        to_datetime(start, in_timezone=config_eos.general.timezone)
    )
    assert ems_eos.start_datetime.hour == fixed_start_hour

    # Throw away any cached results of the last energy management run.
    CacheEnergyManagementStore().clear()

    # Activate with pytest --finalize
    if ngen > 10 and not is_finalize:
        pytest.skip()

    # Prepare the parameters through the optimizer interface, then run.
    optimizer = Genetic0Optimizer()
    run_start = ems_eos.start_datetime
    with patch(
        "akkudoktoreos.optimization.genetic0.genetic0prepare.get_prediction",
        return_value=FakePrediction(),
    ):
        parameters = await optimizer.prepare_from_request(
            input_data, run_start, run_start, None, {"generations": ngen, "seed": fixed_seed}
        )
    result = optimizer.run(parameters)
    # The public solution, as the REST API returns it, is what the golden files hold.
    genetic0_solution = solution_response(parameters, result)

    # The parameters of this run are the replay input of test_replay (ADR 0008).
    check_replay(parameters, golden_stem, fn_out)

    # Write test output to file, so we can take it as new data on intended change
    TESTDATA_FILE = DIR_TESTDATA / f"new_{golden_stem}.json"
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

    # Check the correct generic optimization solution is created; the prediction series were
    # read from FakePrediction by the parameter preparation.
    optimization_solution = await genetic0convert.optimization_solution(
        parameters, result
    )
    check_golden(
        mask_now(optimization_solution.model_dump(mode="json")),
        f"{golden_stem}_optimization_solution.json",
    )

    # Check the correct generic energy management plan is created
    plan = genetic0convert.energy_management_plan(parameters, result)
    check_golden(
        mask_now(plan.model_dump(mode="json")),
        f"{golden_stem}_plan.json",
    )

    # Check visualization works, and pin what it is drawn from
    report_calls, report_patches = record_report_calls()
    with patch.multiple(Genetic0VisualizationReport, **report_patches):
        pdf = genetic0_prepare_visualize(parameters=parameters, solution=result)
    assert pdf.startswith(b"%PDF-")
    check_golden(report_calls, f"{golden_stem}_visualize.json")

    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) == 6


    # Everything passed, remove generated files
    TESTDATA_FILE.unlink()


def result_of(solution: Genetic0Solution) -> Genetic0Result:
    """The run result a public solution was built from."""
    simulation = solution.result.model_dump()
    output_fields = {field.name for field in dataclasses.fields(Genetic0SimulationOutput)}
    ev_fields = {field.name for field in dataclasses.fields(Genetic0ElectricVehicleState)}
    return Genetic0Result(
        ac_charge=solution.ac_charge,
        dc_charge=solution.dc_charge,
        discharge_allowed=solution.discharge_allowed,
        ev_charge_hours_float=solution.ev_charge_hours_float,
        result=Genetic0SimulationOutput(
            **{key: value for key, value in simulation.items() if key in output_fields}
        ),
        ev_obj=(
            None
            if solution.ev_obj is None
            else Genetic0ElectricVehicleState(
                **{k: v for k, v in solution.ev_obj.model_dump().items() if k in ev_fields}
            )
        ),
        start_hour=solution.start_hour,
        start_solution=solution.start_solution,
        washingstart=solution.washingstart,
        extra_data=solution.extra_data,
        fitness_history=solution.fitness_history,
        fixed_seed=solution.fixed_seed,
    )


# The parameters of golden run 1 (optimize_result_1.json), from its replay file.
RUN_1 = parameters_from_dict(
    json.loads((DIR_TESTDATA / "replay_optimize_result_1.json").read_text())["parameters"]
)


class TestGenetic0SocClampedOperationFactors:
    """The SOC clamping uses the optimizer battery parameters, not the settings.

    Battery settings no longer hold capacity / efficiency / power fields (they
    come from the battery profile), so reading them from the settings fails.
    """

    @pytest.fixture
    def solution(self, config_eos: ConfigEOS) -> Genetic0Result:
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
        return result_of(Genetic0Solution.model_validate(data))

    def test_uses_solution_battery_parameters(self, solution):
        bat = RUN_1.request.pv_battery
        assert bat is not None
        assert bat.capacity_wh == 26400
        # 1 % headroom of 26400 Wh against one hour at 5000 W and the charging
        # efficiency of the parameters (no inverter configured).
        headroom_wh = 0.01 * bat.capacity_wh
        assert bat.max_charge_power_w is not None
        expected = headroom_wh / (bat.max_charge_power_w * bat.charging_efficiency)
        ac, dc, dis = genetic0convert._soc_clamped_operation_factors(
            RUN_1, solution,
            1.0, 1.0, True, float(bat.max_soc_percentage) - 1.0
        )
        assert ac == pytest.approx(expected)
        assert dc == pytest.approx(1.0)
        assert dis is True

    def test_full_and_empty_battery(self, solution):
        bat = RUN_1.request.pv_battery
        assert bat is not None
        assert genetic0convert._soc_clamped_operation_factors(
            RUN_1, solution,
            1.0, 1.0, True, float(bat.max_soc_percentage)
        ) == (0.0, 0.0, True)
        assert genetic0convert._soc_clamped_operation_factors(
            RUN_1, solution,
            0.0, 0.0, True, float(bat.min_soc_percentage)
        ) == (0.0, 0.0, False)

    def test_without_battery_parameters_factors_are_unchanged(self, solution):
        without_battery = dataclasses.replace(
            RUN_1, request=RUN_1.request.model_copy(update={"pv_battery": None})
        )
        assert genetic0convert._soc_clamped_operation_factors(
            without_battery, solution, 1.0, 0.5, True, 50.0
        ) == (1.0, 0.5, True)

    @pytest.mark.asyncio
    async def test_optimization_solution_with_configured_battery(self, solution):
        optimization_solution = await genetic0convert.optimization_solution(RUN_1, solution)
        assert optimization_solution is not None


class TestGenetic0SolutionWithProfileInverter:
    """The solution uses the inverter parameters of the run, not the inverter settings.

    Inverter settings no longer hold efficiencies or power fields (they come
    from the inverter profile), so reading them from the settings fails.
    """

    @pytest.fixture
    def solution(self, config_eos: ConfigEOS) -> Genetic0Result:
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
        return result_of(Genetic0Solution.model_validate(data))

    def test_uses_solution_inverter_parameters(self, solution):
        bat = RUN_1.request.pv_battery
        inverter = RUN_1.request.inverter
        assert bat is not None and inverter is not None
        assert bat.max_charge_power_w is not None
        # The stored inverter has no AC charge limit and efficiency 1.0, unlike
        # the configured one: the battery charge power limits the AC charge.
        headroom_wh = 0.01 * bat.capacity_wh
        expected = headroom_wh / (
            bat.max_charge_power_w * inverter.ac_to_dc_efficiency * bat.charging_efficiency
        )
        ac, _, _ = genetic0convert._soc_clamped_operation_factors(
            RUN_1, solution,
            1.0, 1.0, True, float(bat.max_soc_percentage) - 1.0
        )
        assert ac == pytest.approx(expected)

    def test_the_preparation_resolves_the_configured_inverter(self, solution, config_eos):
        """The inverter profile is resolved by the parameter preparation, not by the solution."""
        from akkudoktoreos.optimization.genetic0 import genetic0prepare

        settings = list(config_eos.devices.inverters.values())[0]
        inverter = genetic0prepare.inverter_request(settings)
        assert inverter.max_power_wh == pytest.approx(10000)
        assert inverter.max_ac_charge_power_w == pytest.approx(2000)
        assert 0.0 < inverter.ac_to_dc_efficiency < 1.0

    def test_the_stored_inverter_is_used_whatever_the_configuration(self, solution, config_eos):
        """The conversion reads no configuration (ADR 0003)."""
        config_eos.merge_settings_from_dict(
            {"devices": {"inverters": {"inverter1": {"profile_adjustment": {"nominal_power_w": None}}}}}
        )
        assert genetic0convert._inverter_parameters(RUN_1) is RUN_1.request.inverter

    @pytest.mark.asyncio
    async def test_optimization_solution_with_configured_inverter(self, solution):
        """Regression: AttributeError 'ac_to_dc_efficiency' on InverterCommonSettings."""
        optimization_solution = await genetic0convert.optimization_solution(RUN_1, solution)
        assert optimization_solution is not None


@pytest.mark.parametrize(
    "golden_stem",
    [
        "optimize_result_1",
        "optimize_result_2",
        "optimize_result_1_be",
        "optimize_result_2_be",
        "optimize_result_2_dst",
        "optimize_result_2_full",
    ],
)
def test_replay(golden_stem: str, config_eos: ConfigEOS, is_finalize: bool):
    """A run reproduces its golden result from the stored parameters alone (ADR 0006, 0008).

    The configuration is set to different values on purpose: the run must not read it.
    """
    replay = json.loads((DIR_TESTDATA / f"replay_{golden_stem}.json").read_text())
    parameters = parameters_from_dict(replay["parameters"])
    if parameters.settings.generations and parameters.settings.generations > 10 and not is_finalize:
        pytest.skip()
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 24},
            "optimization": {
                "genetic0": {
                    "horizon_hours": 12,
                    "individuals": 11,
                    "generations": 12,
                    "seed": 1,
                    "penalties": {"ev_soc_miss": 99, "ac_charge_break_even": 3},
                },
                "self_consumption_interpolator": "numpy",
            },
            "devices": {"max_electric_vehicles": 0, "max_home_appliances": 0},
        }
    )

    result = solution_response(parameters, Genetic0Optimizer().run(parameters))

    expected = Genetic0Solution(
        **json.loads((DIR_TESTDATA / replay["expected_result"]).read_text())
    )
    assert result.result.Gesamtbilanz_Euro == pytest.approx(expected.result.Gesamtbilanz_Euro)
    compare_dict(result.model_dump(), expected.model_dump())
