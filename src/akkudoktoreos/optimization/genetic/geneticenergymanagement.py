"""Forecasts and prices of the GENETIC simulation."""

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True, slots=True)
class GeneticEnergyManagementParameters:
    """Forecasts and prices per slot of a run, derived from the request.

    Attributes:
        pv_forecast_wh: PV energy per slot [Wh].
        electricity_price_per_wh: Electricity price per slot [currency/Wh].
        feed_in_tariff_per_wh: Feed-in tariff per slot, or one value for all slots
            [currency/Wh].
        price_per_wh_battery: Value of energy left in the battery [currency/Wh].
        total_load: Load energy per slot [Wh].
    """

    pv_forecast_wh: tuple[float, ...]
    electricity_price_per_wh: tuple[float, ...]
    feed_in_tariff_per_wh: Union[tuple[float, ...], float]
    price_per_wh_battery: float
    total_load: tuple[float, ...]
