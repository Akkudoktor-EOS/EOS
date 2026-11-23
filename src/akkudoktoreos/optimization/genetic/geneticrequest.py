"""Configuration-owned GENETIC requests with fresh runtime observations.

``geneticprepare`` completes a request into the parameters of a run.
"""

from typing import Annotated, Any, Optional

from pydantic import AliasChoices, Field, field_validator

from akkudoktoreos.optimization.genetic.geneticabc import GeneticParametersBaseModel
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.utils.datetimeutil import DateTime


class GeneticForecastsRequest(GeneticParametersBaseModel):
    """External series from local midnight, in slot Wh and currency per Wh.

    The interval is configured in optimization.genetic.interval_sec. Omitted
    series are read from the configured providers, preserving their raw coverage.
    """

    pv_forecast_wh: Optional[list[float]] = Field(
        default=None, validation_alias=AliasChoices("pv_forecast_wh", "pv_prognose_wh")
    )
    total_load: Optional[list[float]] = Field(
        default=None, validation_alias=AliasChoices("total_load", "gesamtlast")
    )
    electricity_price_per_wh: Optional[list[float]] = Field(
        default=None,
        validation_alias=AliasChoices("electricity_price_per_wh", "strompreis_euro_pro_wh"),
    )
    feed_in_tariff_per_wh: Optional[list[float]] = Field(
        default=None,
        validation_alias=AliasChoices("feed_in_tariff_per_wh", "einspeiseverguetung_euro_pro_wh"),
    )
    temperature_forecast: Optional[list[Optional[float]]] = None


class GeneticRequest(GeneticParametersBaseModel):
    """Supply observations while hardware, tariffs and scheduling remain in config.

    Explicit SoC values take precedence over fresh measurements. Without a usable
    measurement, assume 0% initial SoC for the affected device and continue.
    """

    soc: dict[str, Annotated[int, Field(ge=0, le=100)]] = Field(default_factory=dict)
    forecasts: GeneticForecastsRequest = Field(default_factory=GeneticForecastsRequest)
    start_solution: Optional[list[float]] = None
    start_solution_datetime: Optional[DateTime] = None

    @field_validator("start_solution_datetime", mode="before")
    @classmethod
    def validate_start_solution_datetime(cls, value: Any) -> Optional[DateTime]:
        """Parse a previous-plan timestamp while retaining its explicit timezone."""
        return GeneticOptimizationParameters.transform_start_solution_datetime(value)
