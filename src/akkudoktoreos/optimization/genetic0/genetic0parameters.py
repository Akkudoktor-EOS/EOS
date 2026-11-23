"""Optimization parameters of GENETIC0: the complete input of one run (ADR 0003, 0006, 0009).

A run reads nothing but these parameters: the algorithm settings, the forecast and device
parameters, and the data the solution conversion presents (time zone, device IDs, prediction
series). They are prepared once before the run by ``genetic0prepare``.

The simulation reads the forecast and device dataclasses, which are derived from the request
(``Genetic0Request``). The request itself is kept because the result echoes it (until step 5); a
replay file stores the request and the dataclasses are derived again when it is read.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional, Union

from akkudoktoreos.optimization.genetic0.devices.genetic0battery import (
    Genetic0BatteryParameters,
)
from akkudoktoreos.optimization.genetic0.devices.genetic0homeappliance import (
    Genetic0HomeApplianceParameters,
)
from akkudoktoreos.optimization.genetic0.devices.genetic0inverter import (
    Genetic0InverterParameters,
)
from akkudoktoreos.optimization.genetic0.genetic0energymanagement import (
    Genetic0EnergyManagementParameters,
)
from akkudoktoreos.optimization.genetic0.genetic0request import Genetic0Request
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime
from akkudoktoreos.utils.gridinterpolator import GridInterpolatorBackend


@dataclass(frozen=True, slots=True)
class Genetic0RunSettings:
    """Algorithm settings of one run, resolved from the configuration and overrides.

    Attributes:
        prediction_hours: Length of the hourly arrays, from midnight of the start day.
        horizon_hours: Optimization horizon in hours.
        individuals: Population size; ``None`` uses the algorithm's default.
        generations: Number of generations; ``None`` uses the algorithm's default.
        seed: Random seed; ``None`` gives a random seed.
        penalties: Penalty parameters by name (e.g. ``ev_soc_miss``).
        ev_charge_rates: Charge rates of the first configured electric vehicle, used when the
            request has none.
        battery_charge_rates: Charge rates of the first configured battery, used when the
            request has none.
        self_consumption_interpolator: How the inverter's self-consumption table is evaluated.
        verbose: Log the progress of the run.
    """

    prediction_hours: int
    horizon_hours: int
    individuals: Optional[int]
    generations: Optional[int]
    seed: Optional[int]
    penalties: Mapping[str, Union[float, int, str]]
    ev_charge_rates: Optional[tuple[float, ...]]
    battery_charge_rates: Optional[tuple[float, ...]]
    self_consumption_interpolator: GridInterpolatorBackend
    verbose: bool


@dataclass(frozen=True, slots=True)
class Genetic0Presentation:
    """Data the solution conversion presents; not used by the run itself.

    Attributes:
        start_datetime: Start of the run.
        timezone: Time zone the hours of the day refer to; ``None`` is the local time zone.
        battery_device_id: Device ID of the battery in the solution.
        ev_device_id: Device ID of the electric vehicle in the solution.
        home_appliance_device_id: Device ID of the home appliance in the solution.
        home_appliance_enabled: Whether the solution contains the home appliance.
        prediction_series: Hourly prediction series from the start of the run, by prediction key;
            a key without data is missing. Values may be NaN.
    """

    start_datetime: DateTime
    timezone: Optional[str]
    battery_device_id: str
    ev_device_id: str
    home_appliance_device_id: str
    home_appliance_enabled: bool
    prediction_series: Mapping[str, tuple[float, ...]]


@dataclass(frozen=True, slots=True)
class Genetic0Parameters:
    """Everything a GENETIC0 run and its solution conversion need.

    Attributes:
        request: Forecast and device parameters of the run, as requested; echoed in the result.
        start_hour: Hour of the day the run starts at; indexes the hourly arrays.
        settings: Algorithm settings of the run.
        presentation: Data the solution conversion presents.
        ems: Hourly forecasts and prices of the simulation.
        pv_battery: The stationary battery, if any.
        ev: The electric vehicle, if any.
        inverter: The inverter, if any.
        dishwasher: The home appliance, if any.
        start_solution: Start solution of the previous run (warm start), if any.
    """

    request: Genetic0Request
    start_hour: int
    settings: Genetic0RunSettings
    presentation: Genetic0Presentation
    ems: Genetic0EnergyManagementParameters
    pv_battery: Optional[Genetic0BatteryParameters]
    ev: Optional[Genetic0BatteryParameters]
    inverter: Optional[Genetic0InverterParameters]
    dishwasher: Optional[Genetic0HomeApplianceParameters]
    start_solution: Optional[tuple[float, ...]]

    @classmethod
    def from_request(
        cls,
        request: Genetic0Request,
        start_hour: int,
        settings: Genetic0RunSettings,
        presentation: Genetic0Presentation,
    ) -> "Genetic0Parameters":
        """Derive the simulation's dataclasses from the request.

        Args:
            request: Forecast and device parameters of the run.
            start_hour: Hour of the day the run starts at.
            settings: Algorithm settings of the run.
            presentation: Data the solution conversion presents.

        Returns:
            The parameters of the run.
        """
        return cls(
            request=request,
            start_hour=start_hour,
            settings=settings,
            presentation=presentation,
            ems=request.ems.to_parameters(),
            pv_battery=None if request.pv_battery is None else request.pv_battery.to_parameters(),
            ev=None if request.ev is None else request.ev.to_parameters(),
            inverter=None if request.inverter is None else request.inverter.to_parameters(),
            dishwasher=(
                None
                if request.dishwasher is None
                else request.dishwasher.to_parameters(settings.prediction_hours)
            ),
            start_solution=(
                None if request.start_solution is None else tuple(request.start_solution)
            ),
        )


def parameters_to_dict(parameters: Genetic0Parameters) -> dict[str, Any]:
    """Serialize the parameters into plain JSON values, e.g. for a replay file.

    NaN in the prediction series becomes ``None``.

    Args:
        parameters: The parameters to serialize.

    Returns:
        A dictionary that ``parameters_from_dict()`` turns back into equal parameters.
    """
    settings = parameters.settings
    presentation = parameters.presentation
    return {
        "request": parameters.request.model_dump(mode="json"),
        "start_hour": parameters.start_hour,
        "settings": {
            "prediction_hours": settings.prediction_hours,
            "horizon_hours": settings.horizon_hours,
            "individuals": settings.individuals,
            "generations": settings.generations,
            "seed": settings.seed,
            "penalties": dict(settings.penalties),
            "ev_charge_rates": _list_or_none(settings.ev_charge_rates),
            "battery_charge_rates": _list_or_none(settings.battery_charge_rates),
            "self_consumption_interpolator": settings.self_consumption_interpolator.value,
            "verbose": settings.verbose,
        },
        "presentation": {
            "start_datetime": presentation.start_datetime.isoformat(),
            "timezone": presentation.timezone,
            "battery_device_id": presentation.battery_device_id,
            "ev_device_id": presentation.ev_device_id,
            "home_appliance_device_id": presentation.home_appliance_device_id,
            "home_appliance_enabled": presentation.home_appliance_enabled,
            "prediction_series": {
                key: [None if math.isnan(value) else value for value in series]
                for key, series in presentation.prediction_series.items()
            },
        },
    }


def parameters_from_dict(data: Mapping[str, Any]) -> Genetic0Parameters:
    """Deserialize parameters written by ``parameters_to_dict()``.

    Args:
        data: The serialized parameters.

    Returns:
        The parameters.
    """
    settings = data["settings"]
    presentation = data["presentation"]
    return Genetic0Parameters.from_request(
        request=Genetic0Request.model_validate(data["request"]),
        start_hour=data["start_hour"],
        settings=Genetic0RunSettings(
            prediction_hours=settings["prediction_hours"],
            horizon_hours=settings["horizon_hours"],
            individuals=settings["individuals"],
            generations=settings["generations"],
            seed=settings["seed"],
            penalties=dict(settings["penalties"]),
            ev_charge_rates=_tuple_or_none(settings["ev_charge_rates"]),
            battery_charge_rates=_tuple_or_none(settings["battery_charge_rates"]),
            self_consumption_interpolator=GridInterpolatorBackend(
                settings["self_consumption_interpolator"]
            ),
            verbose=settings["verbose"],
        ),
        presentation=Genetic0Presentation(
            # The offset alone would lose daylight saving time; restore the named zone.
            start_datetime=to_datetime(
                presentation["start_datetime"], in_timezone=presentation["timezone"]
            ),
            timezone=presentation["timezone"],
            battery_device_id=presentation["battery_device_id"],
            ev_device_id=presentation["ev_device_id"],
            home_appliance_device_id=presentation["home_appliance_device_id"],
            home_appliance_enabled=presentation["home_appliance_enabled"],
            prediction_series={
                key: tuple(math.nan if value is None else float(value) for value in series)
                for key, series in presentation["prediction_series"].items()
            },
        ),
    )


def _list_or_none(values: Optional[tuple[float, ...]]) -> Optional[list[float]]:
    return None if values is None else list(values)


def _tuple_or_none(values: Optional[list[float]]) -> Optional[tuple[float, ...]]:
    return None if values is None else tuple(values)
