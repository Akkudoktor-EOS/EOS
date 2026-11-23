"""Optimization parameters of GENETIC0: the complete input of one run (ADR 0006, 0009).

Transitional (migration step 1): the parameters still carry today's request model; step 3 replaces
it with the optimizer's own dataclasses.
"""

from dataclasses import dataclass
from typing import Optional

from akkudoktoreos.optimization.genetic0.genetic0params import (
    Genetic0OptimizationParameters,
)


@dataclass(frozen=True, slots=True)
class Genetic0Parameters:
    """Everything a GENETIC0 run needs, including the algorithm settings.

    Attributes:
        request: Forecast and device parameters of the run.
        start_hour: Hour of the day the run starts at; indexes the hourly arrays of ``request``.
        generations: Number of generations to evolve; ``None`` uses the optimizer's default.
        seed: Random seed; ``None`` gives a random seed.
        verbose: Log the progress of the run.
    """

    request: Genetic0OptimizationParameters
    start_hour: int
    generations: Optional[int]
    seed: Optional[int]
    verbose: bool
