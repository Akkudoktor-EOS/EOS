"""GENETIC0 behind the optimizer interface (ADR 0006).

Runs ``Genetic0Optimization``; the result is the frozen ``Genetic0Result``.
"""

from collections.abc import Mapping
from typing import Any, Optional

from akkudoktoreos.core.emplan import EnergyManagementPlan
from akkudoktoreos.optimization.genetic0 import genetic0convert, genetic0prepare
from akkudoktoreos.optimization.genetic0.genetic0parameters import Genetic0Parameters
from akkudoktoreos.optimization.genetic0.genetic0request import (
    Genetic0Request,
)
from akkudoktoreos.optimization.genetic0.genetic0result import Genetic0Result
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.optimization.optimizer import OptimizerApi, RunClock
from akkudoktoreos.utils.datetimeutil import DateTime


class Genetic0Optimizer:
    """The GENETIC0 optimizer."""

    name = "GENETIC0"

    def run_clock(self) -> RunClock:
        """See ``Optimizer.run_clock()``: hourly slots in the server's local time."""
        return RunClock(slot_seconds=3600)

    async def prepare(
        self,
        start_datetime: DateTime,
        observation_datetime: DateTime,
        warm_start: Optional[Genetic0Result],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> Genetic0Parameters:
        """See ``Optimizer.prepare()``."""
        return await genetic0prepare.prepare(
            start_datetime, observation_datetime, warm_start, overrides
        )

    async def prepare_from_request(
        self,
        request: Genetic0Request,
        start_datetime: DateTime,
        observation_datetime: DateTime,
        warm_start: Optional[Genetic0Result],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> Genetic0Parameters:
        """See ``Optimizer.prepare_from_request()``."""
        return await genetic0prepare.prepare_from_request(
            request, start_datetime, observation_datetime, warm_start, overrides
        )

    def run(self, parameters: Genetic0Parameters) -> Genetic0Result:
        """See ``Optimizer.run()``."""
        # Imported on first use: the simulation is only needed for a run.
        from akkudoktoreos.optimization.genetic0.genetic0algorithm import (
            Genetic0Optimization,
        )

        optimization = Genetic0Optimization(parameters.settings)
        return optimization.optimize_ems(parameters)

    def api(self) -> OptimizerApi:
        """See ``Optimizer.api()``."""
        # Imported on first use: the router is only needed by the server.
        from akkudoktoreos.optimization.genetic0 import genetic0api

        return genetic0api.api()

    async def convert(
        self, parameters: Genetic0Parameters, result: Genetic0Result
    ) -> tuple[OptimizationSolution, EnergyManagementPlan]:
        """See ``Optimizer.convert()``."""
        return await genetic0convert.convert(parameters, result)
