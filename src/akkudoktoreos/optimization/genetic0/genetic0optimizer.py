"""GENETIC0 behind the optimizer interface (ADR 0006).

Runs today's ``Genetic0Optimization``; the result is still the Pydantic ``Genetic0Solution`` until
step 6 (ADR 0008).
"""

from collections.abc import Mapping
from typing import Any, Optional

from akkudoktoreos.core.emplan import EnergyManagementPlan
from akkudoktoreos.optimization.genetic0 import genetic0convert, genetic0prepare
from akkudoktoreos.optimization.genetic0.genetic0parameters import Genetic0Parameters
from akkudoktoreos.optimization.genetic0.genetic0request import (
    Genetic0Request,
)
from akkudoktoreos.optimization.genetic0.genetic0solution import Genetic0Solution
from akkudoktoreos.optimization.optimization import OptimizationSolution
from akkudoktoreos.utils.datetimeutil import DateTime


class Genetic0Optimizer:
    """The GENETIC0 optimizer."""

    name = "GENETIC0"

    async def prepare(
        self,
        start_datetime: DateTime,
        observation_datetime: DateTime,
        warm_start: Optional[Genetic0Solution],
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
        warm_start: Optional[Genetic0Solution],
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> Genetic0Parameters:
        """See ``Optimizer.prepare_from_request()``."""
        return await genetic0prepare.prepare_from_request(
            request, start_datetime, observation_datetime, warm_start, overrides
        )

    def run(self, parameters: Genetic0Parameters) -> Genetic0Solution:
        """See ``Optimizer.run()``."""
        # Imported on first use: the simulation is only needed for a run.
        from akkudoktoreos.optimization.genetic0.genetic0algorithm import (
            Genetic0Optimization,
        )

        optimization = Genetic0Optimization(parameters.settings)
        return optimization.optimize_ems(parameters)

    async def convert(
        self, parameters: Genetic0Parameters, result: Genetic0Solution
    ) -> tuple[OptimizationSolution, EnergyManagementPlan]:
        """See ``Optimizer.convert()``."""
        return await genetic0convert.convert(parameters, result)
