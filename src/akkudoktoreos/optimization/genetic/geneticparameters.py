"""Optimization parameters of GENETIC: the complete input of one run (ADR 0006, 0009).

Transitional (migration step 1): the parameters still carry today's Pydantic parameter model;
step 3 replaces it with the optimizer's own dataclasses.
"""

from dataclasses import dataclass
from typing import Optional

from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)


@dataclass(frozen=True, slots=True)
class GeneticParameters:
    """Everything a GENETIC run needs, including the algorithm settings.

    Attributes:
        request: Forecast and device parameters of the run, including the warm start.
        start_hour: Hour of the day the run starts at.
        generations: Number of generations to evolve; ``None`` uses the optimizer's default.
        individuals: Population size; ``None`` uses the configured or default size.
        seed: Random seed; ``None`` gives a random seed.
        verbose: Log the progress of the run.
    """

    request: GeneticOptimizationParameters
    start_hour: int
    generations: Optional[int]
    individuals: Optional[int]
    seed: Optional[int]
    verbose: bool
