"""Prediction module for weather and photovoltaic forecasts.

This module provides a `Prediction` class to manage and update a sequence of
prediction providers. The `Prediction` class is a subclass of `PredictionContainer`
and is initialized with a set of forecast providers, such as `WeatherBrightSky`,
`WeatherClearOutside`, and `PVForecastAkkudoktor`.

Usage:
    Instantiate the `Prediction` class with the required providers, maintaining
    the necessary order. Then call the `update` method to refresh forecasts from
    all providers in sequence.

Example:
    # Create singleton prediction instance with prediction providers
    from akkudoktoreos.prediction.prediction import prediction

    prediction.update_data()
    print("Prediction:", prediction)

Classes:
    Prediction: Manages a list of forecast providers to fetch and update predictions.

Attributes:
    pvforecast_akkudoktor (PVForecastAkkudoktor): Forecast provider for photovoltaic data.
    weather_brightsky (WeatherBrightSky): Weather forecast provider using BrightSky.
    weather_clearoutside (WeatherClearOutside): Weather forecast provider using ClearOutside.
"""

from typing import Optional, Union

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.elecpriceakkudoktor import ElecPriceAkkudoktor
from akkudoktoreos.prediction.elecpriceenergycharts import ElecPriceEnergyCharts
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImport
from akkudoktoreos.prediction.feedintarifffixed import FeedInTariffFixed
from akkudoktoreos.prediction.feedintariffimport import FeedInTariffImport
from akkudoktoreos.prediction.loadakkudoktor import (
    LoadAkkudoktor,
    LoadAkkudoktorAdjusted,
)
from akkudoktoreos.prediction.loadimport import LoadImport
from akkudoktoreos.prediction.loadvrm import LoadVrm
from akkudoktoreos.prediction.predictionabc import PredictionContainer
from akkudoktoreos.prediction.pvforecastakkudoktor import PVForecastAkkudoktor
from akkudoktoreos.prediction.pvforecastimport import PVForecastImport
from akkudoktoreos.prediction.pvforecastvrm import PVForecastVrm
from akkudoktoreos.prediction.weatherbrightsky import WeatherBrightSky
from akkudoktoreos.prediction.weatherclearoutside import WeatherClearOutside
from akkudoktoreos.prediction.weatherimport import WeatherImport


class PredictionCommonSettings(SettingsBaseModel):
    """General Prediction Configuration."""

    hours: Optional[int] = Field(
        default=48,
        ge=0,
        json_schema_extra={"description": "Number of hours into the future for predictions"},
    )

    historic_hours: Optional[int] = Field(
        default=48,
        ge=0,
        json_schema_extra={
            "description": "Number of hours into the past for historical predictions data"
        },
    )


# Initialize forecast providers, all are singletons.
elecprice_akkudoktor = ElecPriceAkkudoktor()
elecprice_energy_charts = ElecPriceEnergyCharts()
elecprice_import = ElecPriceImport()
feedintariff_fixed = FeedInTariffFixed()
feedintariff_import = FeedInTariffImport()
loadforecast_akkudoktor = LoadAkkudoktor()
loadforecast_akkudoktor_adjusted = LoadAkkudoktorAdjusted()
loadforecast_vrm = LoadVrm()
loadforecast_import = LoadImport()
pvforecast_akkudoktor = PVForecastAkkudoktor()
pvforecast_vrm = PVForecastVrm()
pvforecast_import = PVForecastImport()
weather_brightsky = WeatherBrightSky()
weather_clearoutside = WeatherClearOutside()
weather_import = WeatherImport()


def prediction_providers() -> list[
    Union[
        ElecPriceAkkudoktor,
        ElecPriceEnergyCharts,
        ElecPriceImport,
        FeedInTariffFixed,
        FeedInTariffImport,
        LoadAkkudoktor,
        LoadAkkudoktorAdjusted,
        LoadVrm,
        LoadImport,
        PVForecastAkkudoktor,
        PVForecastVrm,
        PVForecastImport,
        WeatherBrightSky,
        WeatherClearOutside,
        WeatherImport,
    ]
]:
    """Return list of prediction providers."""
    global \
        elecprice_akkudoktor, \
        elecprice_energy_charts, \
        elecprice_import, \
        feedintariff_fixed, \
        feedintariff_import, \
        loadforecast_akkudoktor, \
        loadforecast_akkudoktor_adjusted, \
        loadforecast_vrm, \
        loadforecast_import, \
        pvforecast_akkudoktor, \
        pvforecast_vrm, \
        pvforecast_import, \
        weather_brightsky, \
        weather_clearoutside, \
        weather_import

    # Care for provider sequence as providers may rely on others to be updated before.
    return [
        elecprice_akkudoktor,
        elecprice_energy_charts,
        elecprice_import,
        feedintariff_fixed,
        feedintariff_import,
        loadforecast_akkudoktor,
        loadforecast_akkudoktor_adjusted,
        loadforecast_vrm,
        loadforecast_import,
        pvforecast_akkudoktor,
        pvforecast_vrm,
        pvforecast_import,
        weather_brightsky,
        weather_clearoutside,
        weather_import,
    ]


class Prediction(PredictionContainer):
    """Prediction container to manage multiple prediction providers."""

    providers: list[
        Union[
            ElecPriceAkkudoktor,
            ElecPriceEnergyCharts,
            ElecPriceImport,
            FeedInTariffFixed,
            FeedInTariffImport,
            LoadAkkudoktor,
            LoadAkkudoktorAdjusted,
            LoadVrm,
            LoadImport,
            PVForecastAkkudoktor,
            PVForecastVrm,
            PVForecastImport,
            WeatherBrightSky,
            WeatherClearOutside,
            WeatherImport,
        ]
    ] = Field(
        default_factory=prediction_providers,
        json_schema_extra={"description": "List of prediction providers"},
    )
