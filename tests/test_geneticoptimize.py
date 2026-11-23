import inspect
import json
import math
import re
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any, NamedTuple, Optional
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pandas as pd
import pytest
from conftest import genetic_optimization, genetic_run_parameters
from pypdf import PdfReader

from akkudoktoreos.config.config import ConfigEOS
from akkudoktoreos.core.cache import CacheEnergyManagementStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.core.ems import EnergyManagement
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.optimization.genetic import (
    geneticconvert,
    geneticprepare,
    geneticsolution,
)
from akkudoktoreos.optimization.genetic.geneticalgorithm import (
    GeneticOptimization,
)
from akkudoktoreos.optimization.genetic.geneticoptimizer import GeneticOptimizer
from akkudoktoreos.optimization.genetic.geneticparameters import (
    GeneticParameters,
    parameters_from_dict,
    parameters_to_dict,
)
from akkudoktoreos.optimization.genetic.geneticrequest import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.genetic.geneticvisualize import (
    GeneticVisualizationReport,
    genetic_prepare_visualize,
)
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

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
        # 250 generations reach the same solution as 400 for this input and seed:
        # the best fitness no longer changes after generation 213.
        ("optimize_input_2.json", "optimize_result_2_full.json", 250, 0),
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
    ems_eos.set_start_datetime(
        to_datetime("2026-09-16T10:00:00+02:00", in_timezone="Europe/Berlin")
    )

    # Throw away any cached results of the last energy management run.
    CacheEnergyManagementStore().clear()

    optimization = genetic_optimization(seed=fixed_seed)

    # Activate with pytest --finalize
    if ngen > 10 and not is_finalize:
        pytest.skip()

    # Call the optimization function
    genetic_solution = optimization.optimize_ems(
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
    optimization_solution = await geneticconvert.optimization_solution(genetic_run_parameters(genetic_solution.parameters), genetic_solution)
    dataframe = optimization_solution.solution.to_dataframe()
    assert len(dataframe) == expected_slots
    assert optimization_solution.valid_from == genetic_solution.start_solution_datetime
    assert optimization_solution.valid_until == ems_eos.start_datetime.add(hours=expected_slots)
    assert genetic_solution.controls_start_at_now
    assert len(genetic_solution.ac_charge) == expected_slots
    assert len(genetic_solution.dc_charge) == expected_slots
    assert len(genetic_solution.discharge_allowed) == expected_slots

    # Check the correct generic energy management plan is created
    plan = geneticconvert.energy_management_plan(genetic_run_parameters(genetic_solution.parameters), genetic_solution)
    assert plan.valid_from == optimization_solution.valid_from
    assert plan.valid_until is None
    assert optimization_solution.valid_from is not None
    assert optimization_solution.valid_until is not None
    assert all(
        optimization_solution.valid_from <= item.execution_time < optimization_solution.valid_until
        for item in plan.instructions
    )

    # Check visualization works
    pdf = genetic_prepare_visualize(genetic_run_parameters(genetic_solution.parameters), genetic_solution)
    assert pdf.startswith(b"%PDF-")

    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) >= 6


def test_solution_inverter_parameters_come_from_the_run(config_eos: ConfigEOS):
    """Inverter settings hold no efficiencies; the solution never reads them directly."""
    config_eos.merge_settings_from_dict(
        {
            "devices": {
                "max_inverters": 1,
                "inverters": {
                    "inverter1": {
                        "device_id": "inverter1",
                        "profile_adjustment": {"nominal_power_w": 8000},
                    }
                },
            }
        }
    )
    with (DIR_TESTDATA / "optimize_result_1.json").open("r") as f_in:
        solution = GeneticSolution.model_validate(json.load(f_in))
    stored = geneticconvert._inverter_parameters_list(solution.parameters)
    assert stored == ([solution.parameters.inverter] if solution.parameters.inverter else [])
    # Without stored parameters there is no inverter: the conversion reads no configuration.
    assert geneticconvert._inverter_parameters_list(None) == []


# --- Characterization of the whole output (ADR 0008) ---------------------------------------
#
# Pins, per golden input, the raw result, the optimization solution, the energy management plan
# and the visualization of the current code, so that the migration to the optimizer architecture
# can prove that it keeps GENETIC's results. Generated once from the current code; a refactoring
# never regenerates them.

GOLDEN_TIMEZONE = "Europe/Berlin"
GOLDEN_START = "2026-09-16T10:00:00"
# The horizon crosses the switch to daylight saving time (Europe/Berlin, 2026-03-29).
DST_START = "2026-03-28T10:00:00"
GOLDEN_SEED = 42

# Report methods whose arguments are the input of the visualization; charts added directly
# through add_chart_to_group are pinned by their titles.
REPORT_METHODS = (
    "create_line_chart_date",
    "create_line_chart",
    "create_scatter_plot",
    "create_bar_chart",
    "create_violin_plot",
    "add_text_page",
    "add_json_page",
    "add_chart_to_group",
)

# Placeholder for the wall-clock time of the conversion in IDs and timestamps.
NOW = "<now>"

# Random UUIDs, e.g. in instruction IDs.
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class GoldenCase(NamedTuple):
    """One golden run: input file, settings and run start."""

    stem: str
    input_file: str
    break_even: int
    start: str
    interval_sec: int
    observation_offset_minutes: int
    tail: bool
    generations: int


def golden_case(stem: str, input_file: str, break_even: int, **changes: Any) -> GoldenCase:
    """A golden case: hourly slots from GOLDEN_START, no tail, 3 generations unless changed."""
    case = GoldenCase(stem, input_file, break_even, GOLDEN_START, 3600, 0, False, 3)
    return case._replace(**changes)


GOLDEN_CASES = (
    golden_case("1", "optimize_input_1.json", break_even=0),
    golden_case("2", "optimize_input_2.json", break_even=1),
    # Quarter-hour slots, the run starts within a slot.
    golden_case(
        "1_quarter",
        "optimize_input_1.json",
        break_even=0,
        interval_sec=900,
        observation_offset_minutes=20,
    ),
    golden_case("2_dst", "optimize_input_2.json", break_even=1, start=DST_START),
    # Tail horizon and automatic terminal value.
    golden_case("2_tail", "optimize_input_2.json", break_even=1, tail=True),
    golden_case("2_full", "optimize_input_2.json", break_even=1, generations=400),
)
# Runs with more generations take minutes; they run only with --finalize.
QUICK_GENERATIONS = 10


def mask_now(value: Any) -> Any:
    """Replace the wall-clock time of the conversion in ``id`` and ``generated_at``."""
    if isinstance(value, dict):
        masked = {key: mask_now(item) for key, item in value.items()}
        if isinstance(masked.get("id"), str) and "@" in masked["id"]:
            prefix, _, _ = masked["id"].partition("@")
            masked["id"] = f"{prefix}@{NOW}"
        if "generated_at" in masked:
            masked["generated_at"] = NOW
        return masked
    if isinstance(value, list):
        return [mask_now(item) for item in value]
    return value


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
    if callable(value):
        return f"<{type(value).__name__}>"
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
        name: spy(name, getattr(GeneticVisualizationReport, name)) for name in REPORT_METHODS
    }
    return calls, patches


def golden_settings(case: GoldenCase, workers: Optional[int] = None) -> dict[str, Any]:
    """The configuration of a golden run."""
    genetic: dict[str, Any] = {
        "interval_sec": case.interval_sec,
        "horizon_hours": 38,
        "tail_horizon_hours": 0,
        "terminal_value_mode": "FIXED",
        "individuals": 300,
        # The run's ngen argument (case.generations) takes precedence.
        "generations": 10,
        "seed": GOLDEN_SEED,
        "penalties": {"ev_soc_miss": 10, "ac_charge_break_even": case.break_even},
        "workers": workers,
    }
    if case.interval_sec == 900:
        # The 48 hourly input values cover 24 hours of quarter-hour slots from a 10:15 start.
        genetic["horizon_hours"] = 24
    if case.tail:
        genetic.update(
            {"horizon_hours": 24, "tail_horizon_hours": 24, "terminal_value_mode": "AUTO"}
        )
    return {
        "general": {"timezone": GOLDEN_TIMEZONE},
        "prediction": {"hours": 48},
        "optimization": {"algorithm": "GENETIC", "genetic": genetic},
        "devices": {
            "max_electric_vehicles": 1,
            "electric_vehicles": {
                "ev1": {"charge_rates": [0.0, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]}
            },
        },
    }


def golden_parameters(
    case: GoldenCase, config_eos: ConfigEOS, workers: Optional[int] = None
) -> GeneticParameters:
    """The parameters of a golden input with its settings and run start."""
    config_eos.merge_settings_from_dict(golden_settings(case, workers))
    with (DIR_TESTDATA / case.input_file).open("r") as f_in:
        request = GeneticOptimizationParameters(**json.load(f_in))
    observation = to_datetime(case.start, in_timezone=GOLDEN_TIMEZONE).add(
        minutes=case.observation_offset_minutes
    )
    start = ems_eos.set_start_datetime(observation, interval_seconds=case.interval_sec)
    CacheEnergyManagementStore().clear()
    # The golden input holds complete forecast and device parameters; the preparation adds the
    # run settings.
    settings = geneticprepare.run_settings(
        start, observation, {"generations": case.generations, "seed": GOLDEN_SEED}
    )
    return GeneticParameters(request=request, start_hour=start.hour, settings=settings)


def run_golden_case(
    case: GoldenCase, config_eos: ConfigEOS, workers: Optional[int] = None
) -> GeneticSolution:
    """Run a golden input with its settings and run start; check its replay file."""
    parameters = golden_parameters(case, config_eos, workers)
    solution = GeneticOptimizer().run(parameters)
    if workers is None:
        check_replay(parameters, f"golden_{case.stem}", f"golden_{case.stem}_result.json")
    return solution


def check_replay(parameters: GeneticParameters, stem: str, expected_result: str) -> None:
    """Compare the run's parameters with its replay file; write ``new_<replay>`` on a miss."""
    replay = {"expected_result": expected_result, "parameters": parameters_to_dict(parameters)}
    # Lossless: what is written reads back to the same values (compared as JSON text, so a
    # NaN of a forecast gap compares equal).
    written = json.dumps(replay["parameters"])
    assert json.dumps(parameters_to_dict(parameters_from_dict(json.loads(written)))) == written
    check_golden(replay, f"replay_{stem}.json")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[case.stem for case in GOLDEN_CASES])
async def test_golden_output(
    case: GoldenCase, config_eos: ConfigEOS, is_finalize: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A golden input gives the pinned result, solution, plan and visualization."""
    if case.generations > QUICK_GENERATIONS and not is_finalize:
        pytest.skip("Activate with pytest --finalize")
    solution = run_golden_case(case, config_eos)
    check_golden(solution.model_dump(mode="json"), f"golden_{case.stem}_result.json")

    optimization_solution = await geneticconvert.optimization_solution(genetic_run_parameters(solution.parameters), solution)
    check_golden(
        mask_now(optimization_solution.model_dump(mode="json")),
        f"golden_{case.stem}_optimization_solution.json",
    )
    plan = geneticconvert.energy_management_plan(genetic_run_parameters(solution.parameters), solution)
    check_golden(mask_now(plan.model_dump(mode="json")), f"golden_{case.stem}_plan.json")

    calls, patches = record_report_calls()
    with patch.multiple(GeneticVisualizationReport, **patches):
        pdf = genetic_prepare_visualize(genetic_run_parameters(solution.parameters), solution)
    pages = len(PdfReader(BytesIO(pdf)).pages)
    check_golden({"pages": pages, "calls": calls}, f"golden_{case.stem}_visualize.json")


@pytest.mark.parametrize("workers", [1, 2])
def test_golden_result_does_not_depend_on_the_workers(workers: int, config_eos: ConfigEOS) -> None:
    """Serial and parallel evaluation give the pinned result (the default picks by CPU count)."""
    case = next(case for case in GOLDEN_CASES if case.stem == "2")
    solution = run_golden_case(case, config_eos, workers)
    check_golden(solution.model_dump(mode="json"), f"golden_{case.stem}_result.json")


# Origin of the fake prediction series: a value depends on its time, not its index.
PREDICTION_ORIGIN = "2026-01-01T00:00:00+00:00"
PREDICTION_DAYS = 4


def fake_prediction_series(key: str, around: DateTime, gap: bool = False) -> pd.Series:
    """An hourly series per key from the day before ``around``; values follow the time (UTC).

    With ``gap`` the load misses one value before the run start, which the preparation keeps.
    """
    first = to_datetime(around.in_timezone("UTC").start_of("day").subtract(days=1))
    index = pd.date_range(first.isoformat(), periods=24 * PREDICTION_DAYS, freq="h")
    hours = (index - pd.Timestamp(PREDICTION_ORIGIN)).total_seconds().to_numpy() / 3600
    day_hour = hours % 24
    week_hour = hours % (24 * 7)
    if key == "pvforecast_ac_power":
        values = np.maximum(0.0, 4000.0 * np.sin(np.pi * (day_hour - 4.0) / 14.0))
    elif key == "loadforecast_power_w":
        values = 350.0 + 15.0 * day_hour + 0.5 * week_hour
    elif key == "elecprice_marketprice_wh":
        values = 0.00020 + 0.000008 * day_hour + 0.0000002 * week_hour
    elif key == "feed_in_tariff_wh":
        values = np.full(len(index), 0.00008)
    else:
        raise KeyError(key)
    if gap and key == "loadforecast_power_w":
        # 01:00 UTC of the run's day; the series starts at 00:00 UTC of the day before.
        values = values.copy()
        values[24 + 1] = np.nan
    return pd.Series(values, index=index)


class FakeConfigMeasurement:
    """Measured states of charge shortly before the run; with ``rich`` also appliance states."""

    def __init__(self, observation: DateTime, rich: bool = False) -> None:
        self.records = {
            "storage-soc-factor": ([observation.subtract(seconds=60)], [0.42]),
            "car-soc-factor": ([observation.subtract(seconds=60)], [0.30]),
        }
        if rich:
            # A full charge three days ago, the dishwasher runs since 30 minutes, no cycle done.
            self.records["storage-soc-factor"] = (
                [observation.subtract(days=3), observation.subtract(seconds=60)],
                [1.0, 0.42],
            )
            self.records["dishwasher.run_started_at"] = (
                [observation.subtract(minutes=30)],
                [observation.subtract(minutes=30).timestamp()],
            )
            self.records["dishwasher.cycles_completed"] = ([observation.start_of("day")], [0])

    async def key_to_lists(self, key: str, **kwargs: Any) -> tuple[list[Any], list[Any]]:
        """The records of ``key``.

        Args:
            key: Measurement key.
            **kwargs: Time range and options of the real method; ignored.

        Returns:
            Dates and values of the key.

        Raises:
            KeyError: Nothing is measured for ``key``.
        """
        if key not in self.records:
            raise KeyError(key)
        return self.records[key]


class ConfigCase(NamedTuple):
    """One golden run that prepares its parameters from the configuration."""

    stem: str
    start: str
    interval_sec: int
    observation_offset_minutes: int
    tail: bool
    # Appliance measurements, periodic full charge, direct marketing, a warm start and a
    # forecast gap before the run start.
    rich: bool = False


CONFIG_CASES = (
    ConfigCase(
        "config_hour", GOLDEN_START, interval_sec=3600, observation_offset_minutes=0, tail=True
    ),
    ConfigCase(
        "config_quarter", GOLDEN_START, interval_sec=900, observation_offset_minutes=20, tail=False
    ),
    ConfigCase(
        "config_dst", DST_START, interval_sec=3600, observation_offset_minutes=0, tail=False
    ),
    ConfigCase(
        "config_rich",
        GOLDEN_START,
        interval_sec=3600,
        observation_offset_minutes=0,
        tail=False,
        rich=True,
    ),
)


def config_case_settings(case: ConfigCase) -> dict[str, Any]:
    """Configured devices and settings of a configuration-driven golden run."""
    genetic: dict[str, Any] = {
        "interval_sec": case.interval_sec,
        "horizon_hours": 24,
        "tail_horizon_hours": 24 if case.tail else 0,
        "terminal_value_mode": "AUTO" if case.tail else "FIXED",
        "terminal_value_euro_per_kwh": 0.23,
        "individuals": 300,
        "generations": 10,
        "seed": GOLDEN_SEED,
    }
    settings: dict[str, Any] = {
        "general": {"timezone": GOLDEN_TIMEZONE},
        "prediction": {"hours": 48},
        "optimization": {"algorithm": "GENETIC", "genetic": genetic},
        "devices": {
            "max_batteries": 1,
            "max_electric_vehicles": 1,
            "max_inverters": 1,
            "max_home_appliances": 1,
            "batteries": {
                "storage": {
                    "profile_adjustment": {
                        "nominal_capacity_wh": 10000,
                        "max_charge_power_w": 5000,
                    },
                    "levelized_cost_of_storage_amt_kwh": 0.01,
                    "measurement_key_soc_factor": "storage-soc-factor",
                }
            },
            "electric_vehicles": {
                "car": {
                    "profile_adjustment": {
                        "nominal_capacity_wh": 40000,
                        "max_charge_power_w": 11000,
                    },
                    "min_soc_percentage": 60,
                    "charge_rates": [0.0, 0.5, 1.0],
                    "measurement_key_soc_factor": "car-soc-factor",
                }
            },
            "inverters": {
                "inverter": {
                    "battery_id": "storage",
                    "profile_adjustment": {"nominal_power_w": 8000},
                }
            },
            "home_appliances": {
                "dishwasher": {"consumption_wh": 2000, "duration_h": 2},
            },
        },
    }
    if case.rich:
        storage = settings["devices"]["batteries"]["storage"]
        storage["profile_adjustment"]["full_charge_interval_days"] = 3
        storage["full_charge_allow_grid"] = True
        settings["feedintariff"] = {"direct_marketing_enabled": True}
    return settings


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CONFIG_CASES, ids=[case.stem for case in CONFIG_CASES])
async def test_golden_output_from_configuration(
    case: ConfigCase, config_eos: ConfigEOS, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run prepared from the configuration gives the pinned parameters and output.

    The prepared parameters are pinned as the preparation returns them; the result holds them
    trimmed to the run start.
    """
    config_eos.merge_settings_from_dict(config_case_settings(case))
    observation = to_datetime(case.start, in_timezone=GOLDEN_TIMEZONE).add(
        minutes=case.observation_offset_minutes
    )
    prediction = Mock(
        update_data=AsyncMock(),
        key_to_raw_series=AsyncMock(
            side_effect=lambda key, **kwargs: fake_prediction_series(key, observation, case.rich)
        ),
    )
    monkeypatch.setattr(EnergyManagement, "prediction", prediction)
    monkeypatch.setattr(geneticprepare, "get_prediction", Mock(return_value=prediction))
    monkeypatch.setattr(EnergyManagement, "adapter", Mock(update_data=AsyncMock()))
    monkeypatch.setattr(EnergyManagement, "_optimizer_runs", {})
    if case.rich:
        # The previous run, an hour earlier, is the warm start of the pinned one.
        previous = observation.subtract(hours=1)
        monkeypatch.setattr(
            geneticprepare,
            "get_measurement",
            Mock(return_value=FakeConfigMeasurement(previous, rich=True)),
        )
        CacheEnergyManagementStore().clear()
        warm_start = await ems_eos.run(
            start_datetime=previous,
            mode=EnergyManagementMode.OPTIMIZATION,
            overrides={"generations": 3, "seed": GOLDEN_SEED},
        )
        assert warm_start is not None
    monkeypatch.setattr(
        geneticprepare,
        "get_measurement",
        Mock(return_value=FakeConfigMeasurement(observation, case.rich)),
    )
    prepared: list[GeneticOptimizationParameters] = []
    resolve = geneticprepare.resolve_request

    async def record_resolve(*args: Any) -> GeneticOptimizationParameters:
        parameters = await resolve(*args)
        prepared.append(parameters)
        return parameters

    monkeypatch.setattr(geneticprepare, "resolve_request", record_resolve)
    CacheEnergyManagementStore().clear()

    solution = await ems_eos.run(
        start_datetime=observation,
        mode=EnergyManagementMode.OPTIMIZATION,
        overrides={"generations": 3, "seed": GOLDEN_SEED},
    )
    assert solution is not None
    assert len(prepared) == 1
    check_golden(prepared[0].model_dump(mode="json"), f"golden_{case.stem}_parameters.json")
    run = ems_eos.optimizer_run("GENETIC")
    assert run is not None
    check_replay(run.parameters, f"golden_{case.stem}", f"golden_{case.stem}_result.json")
    check_golden(solution.model_dump(mode="json"), f"golden_{case.stem}_result.json")
    optimization_solution = ems_eos.optimization_solution()
    assert optimization_solution is not None
    check_golden(
        mask_now(optimization_solution.model_dump(mode="json")),
        f"golden_{case.stem}_optimization_solution.json",
    )
    plan = ems_eos.plan()
    assert plan is not None
    check_golden(mask_now(plan.model_dump(mode="json")), f"golden_{case.stem}_plan.json")

    calls, patches = record_report_calls()
    with patch.multiple(GeneticVisualizationReport, **patches):
        pdf = genetic_prepare_visualize(genetic_run_parameters(solution.parameters), solution)
    pages = len(PdfReader(BytesIO(pdf)).pages)
    check_golden({"pages": pages, "calls": calls}, f"golden_{case.stem}_visualize.json")


REPLAY_STEMS = [f"golden_{case.stem}" for case in GOLDEN_CASES] + [
    f"golden_{case.stem}" for case in CONFIG_CASES
]


@pytest.mark.parametrize("stem", REPLAY_STEMS)
def test_replay(stem: str, config_eos: ConfigEOS, is_finalize: bool) -> None:
    """A run reproduces its golden result from the stored parameters alone (ADR 0006, 0008).

    The configuration and the run start are set to different values on purpose: the run must
    not read them.
    """
    replay = json.loads((DIR_TESTDATA / f"replay_{stem}.json").read_text())
    parameters = parameters_from_dict(replay["parameters"])
    generations = parameters.settings.generations
    if generations is not None and generations > QUICK_GENERATIONS and not is_finalize:
        pytest.skip("Activate with pytest --finalize")
    config_eos.merge_settings_from_dict(
        {
            "general": {"timezone_override": "America/New_York"},
            "prediction": {"hours": 30},
            "optimization": {
                "genetic": {
                    "interval_sec": 3600 if parameters.settings.interval_sec == 900 else 900,
                    "horizon_hours": 6,
                    "tail_horizon_hours": 0,
                    "individuals": 11,
                    "generations": 12,
                    "seed": 1,
                    "terminal_value_mode": "FIXED",
                    "penalties": {"ev_soc_miss": 99, "ac_charge_break_even": 3},
                }
            },
            "feedintariff": {"direct_marketing_enabled": False},
            "devices": {"max_electric_vehicles": 0, "max_home_appliances": 0},
        }
    )
    config_eos.merge_settings_from_dict({"optimization": {"self_consumption_interpolator": "numpy"}})
    ems_eos.set_start_datetime(to_datetime("2027-01-05T03:00:00+00:00", in_timezone="UTC"))

    result = GeneticOptimizer().run(parameters)

    check_golden(result.model_dump(mode="json"), replay["expected_result"])


def test_replay_keeps_a_forecast_gap() -> None:
    """A gap the preparation keeps is ``null`` in the replay file and NaN in the parameters."""
    replay = json.loads((DIR_TESTDATA / "replay_golden_config_rich.json").read_text())
    assert replay["parameters"]["request"]["ems"]["total_load"][3] is None
    parameters = parameters_from_dict(replay["parameters"])
    assert math.isnan(parameters.request.ems.total_load[3])
    assert parameters_to_dict(parameters)["request"]["ems"]["total_load"][3] is None
