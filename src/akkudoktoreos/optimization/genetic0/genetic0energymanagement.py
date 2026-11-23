"""Hourly forecasts and prices of a GENETIC0 run, as the simulation reads them.

A module of its own so that the request can derive them without importing the simulation.
"""

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True, slots=True)
class Genetic0EnergyManagementParameters:
    """Hourly forecasts and prices of the simulation, from midnight of the start day.

    Attributes:
        pv_forecast_wh: PV energy per hour in watt-hours.
        electricity_price_per_wh: Electricity price per watt-hour.
        feed_in_tariff_per_wh: Feed-in tariff per watt-hour, per hour or one constant value.
        price_per_wh_battery: Cost of battery energy per watt-hour.
        total_load: Load energy per hour in watt-hours.
    """

    pv_forecast_wh: tuple[float, ...]
    electricity_price_per_wh: tuple[float, ...]
    feed_in_tariff_per_wh: Union[tuple[float, ...], float]
    price_per_wh_battery: float
    total_load: tuple[float, ...]
