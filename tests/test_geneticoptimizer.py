"""Tests of the GENETIC adapter of the optimizer interface."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from akkudoktoreos.optimization.genetic import geneticalgorithm
from akkudoktoreos.optimization.genetic.geneticoptimizer import GeneticOptimizer
from akkudoktoreos.utils.datetimeutil import to_datetime


class TestGeneticOptimizer:
    """The GENETIC adapter hands the run parameters to the algorithm."""

    def test_run_starts_from_the_start_of_the_parameters(self) -> None:
        """The run aligns the start solution with the start of the parameters, not the request."""
        start = to_datetime("2026-09-12T09:45:00+00:00", in_timezone="UTC")
        parameters = SimpleNamespace(
            settings=SimpleNamespace(generations=10, individuals=10),
            start_hour=10,
            request=SimpleNamespace(start_solution_datetime=None),
            start_solution_datetime=start,
        )

        with patch.object(geneticalgorithm, "GeneticOptimization") as optimization:
            GeneticOptimizer().run(cast(Any, parameters))

        kwargs = optimization.return_value.optimize_ems.call_args.kwargs
        assert kwargs["start_solution_datetime"] == start
        assert kwargs["parameters"].start_solution_datetime is None
