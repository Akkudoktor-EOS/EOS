"""Parameter preparation of GENETIC (ADR 0003, 0006).

The part of GENETIC that reads the configuration. It completes the optimization parameters once
before each run; the energy management passes the warm start and run-time overrides as arguments.

Transitional (migration step 1): the forecast and device parameters still come from the legacy
``GeneticOptimizationParameters.prepare()`` and ``GeneticRequest.resolve()``, which
read the run start from the energy management and may fail; step 3 replaces them and step 3b
makes the preparation always valid.
"""

from collections.abc import Mapping
from typing import Any, Optional

from loguru import logger

from akkudoktoreos.core.coreabc import get_config
from akkudoktoreos.optimization.genetic.geneticparameters import GeneticParameters
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticrequest import GeneticRequest
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSolution
from akkudoktoreos.optimization.optimizer import RunClock
from akkudoktoreos.utils.datetimeutil import DateTime

# Settings that a run can override.
OVERRIDE_KEYS = ("generations", "individuals", "seed")


def run_clock() -> RunClock:
    """The configured slot length; runs follow the site's local day.

    Returns:
        The clock of the next GENETIC run.
    """
    config = get_config()
    return RunClock(
        slot_seconds=config.optimization.genetic.interval_sec,
        timezone=config.general.timezone,
    )


async def prepare(
    start_datetime: DateTime,
    warm_start: Optional[GeneticSolution],
    overrides: Optional[Mapping[str, Any]] = None,
) -> GeneticParameters:
    """Prepare the parameters of a run from configuration, predictions and measurements.

    Args:
        start_datetime: Start of the optimization run.
        warm_start: Result of the previous GENETIC run, if any.
        overrides: Run-time overrides of ``generations``, ``individuals`` and ``seed``.

    Returns:
        The optimization parameters of the run.

    Raises:
        RuntimeError: Configured devices or forecasts are incomplete (until step 3b).
    """
    request = await GeneticOptimizationParameters.prepare(warm_start=warm_start)
    if request is None:
        raise RuntimeError("Could not prepare optimisation parameters.")
    return _complete(request, start_datetime, overrides)


async def prepare_from_request(
    request: GeneticRequest,
    start_datetime: DateTime,
    warm_start: Optional[GeneticSolution],
    overrides: Optional[Mapping[str, Any]] = None,
) -> GeneticParameters:
    """Complete a client's optimization request into the parameters of a run.

    Args:
        request: Runtime inputs of the client; omitted inputs come from the configured devices
            and providers.
        start_datetime: Start of the optimization run.
        warm_start: Result of the previous GENETIC run, if any.
        overrides: Run-time overrides of ``generations``, ``individuals`` and ``seed``.

    Returns:
        The optimization parameters of the run.

    Raises:
        ValueError: The request, configured devices or forecasts are incomplete (until step 3b).
    """
    parameters = await request.resolve(warm_start=warm_start)
    return _complete(parameters, start_datetime, overrides)


def _complete(
    request: GeneticOptimizationParameters,
    start_datetime: DateTime,
    overrides: Optional[Mapping[str, Any]],
) -> GeneticParameters:
    config = get_config()
    settings: dict[str, Any] = {
        "generations": config.optimization.genetic.generations,
        "individuals": None,
        "seed": config.optimization.genetic.seed,
    }
    for key, value in (overrides or {}).items():
        if key not in OVERRIDE_KEYS:
            logger.error("GENETIC: Unknown override '{}' ignored.", key)
        elif value is not None:
            settings[key] = value
    return GeneticParameters(
        request=request,
        start_hour=start_datetime.hour,
        generations=settings["generations"],
        individuals=settings["individuals"],
        seed=settings["seed"],
        verbose=bool(config.server.verbose),
    )
