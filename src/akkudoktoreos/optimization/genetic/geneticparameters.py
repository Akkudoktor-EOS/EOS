"""Optimization parameters of GENETIC: the complete input of one run (ADR 0003, 0006, 0009).

A run reads nothing but these parameters: the run settings and the forecast and device
parameters. They are prepared once before the run by ``geneticprepare``.

The simulation reads the forecast and device dataclasses, which the request
(``GeneticOptimizationParameters``) derives. The request itself is kept for the conversion, the
visualization and the public responses.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional, Union

from akkudoktoreos.optimization.genetic.geneticrequest import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsettings import TerminalValueMode
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime
from akkudoktoreos.utils.gridinterpolator import GridInterpolatorBackend


@dataclass(frozen=True, slots=True)
class GeneticRunSettings:
    """Algorithm settings and run clock of one run, resolved from configuration and overrides.

    Attributes:
        interval_sec: Length of one optimization slot in seconds (900 or 3600).
        horizon_hours: Control horizon in hours.
        tail_horizon_hours: Requested forecast tail beyond the control horizon in hours.
        prediction_hours: Forecast budget in hours.
        individuals: Population size; ``None`` uses the algorithm's default.
        generations: Number of generations; ``None`` uses the algorithm's default.
        seed: Random seed; ``None`` gives a random seed.
        penalties: Penalty parameters by name (e.g. ``ev_soc_miss``).
        workers: Number of evaluation processes; ``None`` chooses automatically.
        pin_workers: Pin the evaluation processes to fixed CPU cores.
        fitness_cache_max_entries: Size limit of the fitness cache; ``None`` is unbounded.
        terminal_value_mode: How energy left at the end of the horizon is valued.
        terminal_value_window_hours: Price window of the terminal value.
        direct_marketing_enabled: Optimize DC charging and battery export for direct marketing.
        battery_charge_rates: Charge rates of the first configured battery, used when the
            request has none.
        battery_grid_export_rates: Grid export rates of the first configured battery, used when
            the request has none.
        ev_charge_rates: Charge rates of the first configured electric vehicle, used when the
            request has none.
        start_datetime: Start of the run's first slot.
        observation_datetime: Unrounded moment of the run that measurements refer to.
        timezone: Site time zone of the appliance start times; ``None`` is the local time zone.
        self_consumption_interpolator: How the inverter's self-consumption table is evaluated.
        verbose: Log the progress of the run.
    """

    interval_sec: int
    horizon_hours: int
    tail_horizon_hours: int
    prediction_hours: int
    individuals: Optional[int]
    generations: Optional[int]
    seed: Optional[int]
    penalties: Mapping[str, Union[float, int, str]]
    workers: Optional[int]
    pin_workers: bool
    fitness_cache_max_entries: Optional[int]
    terminal_value_mode: TerminalValueMode
    terminal_value_window_hours: int
    direct_marketing_enabled: bool
    battery_charge_rates: Optional[tuple[float, ...]]
    battery_grid_export_rates: Optional[tuple[float, ...]]
    ev_charge_rates: Optional[tuple[float, ...]]
    start_datetime: DateTime
    observation_datetime: DateTime
    timezone: Optional[str]
    self_consumption_interpolator: GridInterpolatorBackend
    verbose: bool


@dataclass(frozen=True, slots=True)
class GeneticParameters:
    """Everything a GENETIC run needs.

    Attributes:
        request: Forecast and device parameters of the run, including the warm start.
        start_hour: Hour of the day the run starts at.
        settings: Algorithm settings and run clock of the run.
    """

    request: GeneticOptimizationParameters
    start_hour: int
    settings: GeneticRunSettings


def parameters_to_dict(parameters: GeneticParameters) -> dict[str, Any]:
    """Serialize the parameters into plain JSON values, e.g. for a replay file.

    NaN in the forecast series (a gap the preparation keeps) becomes ``None``; the series
    carry no other ``None``.

    Args:
        parameters: The parameters to serialize.

    Returns:
        A dictionary that ``parameters_from_dict()`` turns back into equal parameters.
    """
    settings = parameters.settings
    request = parameters.request.model_dump(mode="json")
    for key, values in request["ems"].items():
        if isinstance(values, list):
            request["ems"][key] = [None if math.isnan(value) else value for value in values]
    return {
        "request": request,
        "start_hour": parameters.start_hour,
        "settings": {
            "interval_sec": settings.interval_sec,
            "horizon_hours": settings.horizon_hours,
            "tail_horizon_hours": settings.tail_horizon_hours,
            "prediction_hours": settings.prediction_hours,
            "individuals": settings.individuals,
            "generations": settings.generations,
            "seed": settings.seed,
            "penalties": dict(settings.penalties),
            "workers": settings.workers,
            "pin_workers": settings.pin_workers,
            "fitness_cache_max_entries": settings.fitness_cache_max_entries,
            "terminal_value_mode": settings.terminal_value_mode.value,
            "terminal_value_window_hours": settings.terminal_value_window_hours,
            "direct_marketing_enabled": settings.direct_marketing_enabled,
            "battery_charge_rates": _list_or_none(settings.battery_charge_rates),
            "battery_grid_export_rates": _list_or_none(settings.battery_grid_export_rates),
            "ev_charge_rates": _list_or_none(settings.ev_charge_rates),
            "start_datetime": _datetime_to_dict(settings.start_datetime),
            "observation_datetime": _datetime_to_dict(settings.observation_datetime),
            "timezone": settings.timezone,
            "self_consumption_interpolator": settings.self_consumption_interpolator.value,
            "verbose": settings.verbose,
        },
    }


def parameters_from_dict(data: Mapping[str, Any]) -> GeneticParameters:
    """Deserialize parameters written by ``parameters_to_dict()``.

    Args:
        data: The serialized parameters.

    Returns:
        The parameters.
    """
    settings = data["settings"]
    request = dict(data["request"])
    request["ems"] = dict(request["ems"])
    for key, values in request["ems"].items():
        if isinstance(values, list):
            request["ems"][key] = [math.nan if value is None else value for value in values]
    return GeneticParameters(
        request=GeneticOptimizationParameters.model_validate(request),
        start_hour=data["start_hour"],
        settings=GeneticRunSettings(
            interval_sec=settings["interval_sec"],
            horizon_hours=settings["horizon_hours"],
            tail_horizon_hours=settings["tail_horizon_hours"],
            prediction_hours=settings["prediction_hours"],
            individuals=settings["individuals"],
            generations=settings["generations"],
            seed=settings["seed"],
            penalties=dict(settings["penalties"]),
            workers=settings["workers"],
            pin_workers=settings["pin_workers"],
            fitness_cache_max_entries=settings["fitness_cache_max_entries"],
            terminal_value_mode=TerminalValueMode(settings["terminal_value_mode"]),
            terminal_value_window_hours=settings["terminal_value_window_hours"],
            direct_marketing_enabled=settings["direct_marketing_enabled"],
            battery_charge_rates=_tuple_or_none(settings["battery_charge_rates"]),
            battery_grid_export_rates=_tuple_or_none(settings["battery_grid_export_rates"]),
            ev_charge_rates=_tuple_or_none(settings["ev_charge_rates"]),
            start_datetime=_datetime_from_dict(settings["start_datetime"]),
            observation_datetime=_datetime_from_dict(settings["observation_datetime"]),
            timezone=settings["timezone"],
            self_consumption_interpolator=GridInterpolatorBackend(
                settings["self_consumption_interpolator"]
            ),
            verbose=settings["verbose"],
        ),
    )


def _datetime_to_dict(value: DateTime) -> dict[str, Optional[str]]:
    # The offset alone would lose daylight saving time; keep the named zone as well.
    return {"datetime": value.isoformat(), "timezone": value.timezone_name}


def _datetime_from_dict(data: Mapping[str, Optional[str]]) -> DateTime:
    return to_datetime(data["datetime"], in_timezone=data["timezone"])


def _list_or_none(values: Optional[tuple[float, ...]]) -> Optional[list[float]]:
    return None if values is None else list(values)


def _tuple_or_none(values: Optional[list[float]]) -> Optional[tuple[float, ...]]:
    return None if values is None else tuple(values)
