"""Parameter preparation of GENETIC0 (ADR 0003, 0006).

The only part of GENETIC0 that reads the configuration. It completes the optimization parameters
once before each run; the energy management passes the run start, the warm start and run-time
overrides as arguments.

Transitional (migration step 1): the forecast and device parameters still come from the legacy
``Genetic0OptimizationParameters.prepare()``, which may fail; step 3 replaces it.
"""

from collections.abc import Mapping
from typing import Any, Optional

from loguru import logger

from akkudoktoreos.core.coreabc import get_config
from akkudoktoreos.optimization.genetic0.genetic0parameters import Genetic0Parameters
from akkudoktoreos.optimization.genetic0.genetic0params import (
    Genetic0OptimizationParameters,
)
from akkudoktoreos.optimization.genetic0.genetic0solution import Genetic0Solution
from akkudoktoreos.utils.datetimeutil import DateTime

# Settings that a run can override.
OVERRIDE_KEYS = ("generations", "seed")


async def prepare(
    start_datetime: DateTime,
    warm_start: Optional[Genetic0Solution],
    overrides: Optional[Mapping[str, Any]] = None,
) -> Genetic0Parameters:
    """Prepare the parameters of a run from configuration, predictions and measurements.

    Args:
        start_datetime: Start of the optimization run.
        warm_start: Result of the previous GENETIC0 run, if any.
        overrides: Run-time overrides of ``generations`` and ``seed``.

    Returns:
        The optimization parameters of the run.

    Raises:
        RuntimeError: The legacy preparation could not build the parameters (until step 3).
    """
    start_solution = None
    if warm_start is not None and warm_start.start_solution:
        start_solution = warm_start.start_solution
    request = await Genetic0OptimizationParameters.prepare(start_solution=start_solution)
    if request is None:
        raise RuntimeError("Could not prepare optimisation parameters.")
    return _complete(request, start_datetime, overrides)


async def prepare_from_request(
    request: Genetic0OptimizationParameters,
    start_datetime: DateTime,
    warm_start: Optional[Genetic0Solution],
    overrides: Optional[Mapping[str, Any]] = None,
) -> Genetic0Parameters:
    """Complete a client's optimization request into the parameters of a run.

    Args:
        request: The client's forecast and device parameters, used as they are.
        start_datetime: Start of the optimization run.
        warm_start: Result of the previous GENETIC0 run; not used, the request carries its own
            start solution.
        overrides: Run-time overrides of ``generations`` and ``seed``.

    Returns:
        The optimization parameters of the run.
    """
    return _complete(request, start_datetime, overrides)


def _complete(
    request: Genetic0OptimizationParameters,
    start_datetime: DateTime,
    overrides: Optional[Mapping[str, Any]],
) -> Genetic0Parameters:
    config = get_config()
    settings: dict[str, Any] = {
        "generations": config.optimization.genetic0.generations,
        "seed": config.optimization.genetic0.seed,
    }
    for key, value in (overrides or {}).items():
        if key not in OVERRIDE_KEYS:
            logger.error("GENETIC0: Unknown override '{}' ignored.", key)
        elif value is not None:
            settings[key] = value
    return Genetic0Parameters(
        request=request,
        start_hour=start_datetime.hour,
        generations=settings["generations"],
        seed=settings["seed"],
        verbose=bool(config.server.verbose),
    )
