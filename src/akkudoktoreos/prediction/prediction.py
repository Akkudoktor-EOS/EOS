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

    await prediction.update_data()
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
from akkudoktoreos.prediction.elecfeefixed import ElecFeeFixed
from akkudoktoreos.prediction.elecfeeimport import ElecFeeImport
from akkudoktoreos.prediction.elecpriceakkudoktor import ElecPriceAkkudoktor
from akkudoktoreos.prediction.elecpriceenergycharts import ElecPriceEnergyCharts
from akkudoktoreos.prediction.elecpricefixed import ElecPriceFixed
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImport
from akkudoktoreos.prediction.elecpricesmard import ElecPriceSMARD
from akkudoktoreos.prediction.elecpricetibber import ElecPriceTibber
from akkudoktoreos.prediction.feedintariffakkudoktor import FeedInTariffAkkudoktor
from akkudoktoreos.prediction.feedintariffdvhubonline import FeedInTariffDvhubOnline
from akkudoktoreos.prediction.feedintariffenergycharts import FeedInTariffEnergyCharts
from akkudoktoreos.prediction.feedintarifffixed import FeedInTariffFixed
from akkudoktoreos.prediction.feedintariffimport import FeedInTariffImport
from akkudoktoreos.prediction.feedintariffsmard import FeedInTariffSMARD
from akkudoktoreos.prediction.feedintarifftibber import FeedInTariffTibber
from akkudoktoreos.prediction.loadakkudoktor import (
    LoadAkkudoktor,
    LoadAkkudoktorAdjusted,
)
from akkudoktoreos.prediction.loadimport import LoadImport
from akkudoktoreos.prediction.loadvrm import LoadVrm
from akkudoktoreos.prediction.predictionabc import PredictionContainer
from akkudoktoreos.prediction.pvforecastakkudoktor import PVForecastAkkudoktor
from akkudoktoreos.prediction.pvforecastforecastsolar import PVForecastForecastSolar
from akkudoktoreos.prediction.pvforecasthomeassistant import PVForecastHomeAssistant
from akkudoktoreos.prediction.pvforecastimport import PVForecastImport
from akkudoktoreos.prediction.pvforecastpvlib import PVForecastPVLib
from akkudoktoreos.prediction.pvforecastpvnode import PVForecastPVNode
from akkudoktoreos.prediction.pvforecastsolcast import PVForecastSolcast
from akkudoktoreos.prediction.pvforecastvrm import PVForecastVrm
from akkudoktoreos.prediction.weatherbrightsky import WeatherBrightSky
from akkudoktoreos.prediction.weatherclearoutside import WeatherClearOutside
from akkudoktoreos.prediction.weatherimport import WeatherImport
from akkudoktoreos.prediction.weatheropenmeteo import WeatherOpenMeteo


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
elecfee_fixed = ElecFeeFixed()
elecfee_import = ElecFeeImport()
elecprice_akkudoktor = ElecPriceAkkudoktor()
elecprice_energy_charts = ElecPriceEnergyCharts()
elecprice_fixed = ElecPriceFixed()
elecprice_import = ElecPriceImport()
elecprice_smard = ElecPriceSMARD()
elecprice_tibber = ElecPriceTibber()
feedintariff_akkudoktor = FeedInTariffAkkudoktor()
feedintariff_dvhubonline = FeedInTariffDvhubOnline()
feedintariff_energy_charts = FeedInTariffEnergyCharts()
feedintariff_fixed = FeedInTariffFixed()
feedintariff_import = FeedInTariffImport()
feedintariff_smard = FeedInTariffSMARD()
feedintariff_tibber = FeedInTariffTibber()
loadforecast_akkudoktor = LoadAkkudoktor()
loadforecast_akkudoktor_adjusted = LoadAkkudoktorAdjusted()
loadforecast_vrm = LoadVrm()
loadforecast_import = LoadImport()
pvforecast_akkudoktor = PVForecastAkkudoktor()
pvforecast_vrm = PVForecastVrm()
pvforecast_homeassistant = PVForecastHomeAssistant()
pvforecast_pvlib = PVForecastPVLib()
pvforecast_pvnode = PVForecastPVNode()
pvforecast_forecastsolar = PVForecastForecastSolar()
pvforecast_solcast = PVForecastSolcast()
pvforecast_import = PVForecastImport()
weather_brightsky = WeatherBrightSky()
weather_clearoutside = WeatherClearOutside()
weather_openmeteo = WeatherOpenMeteo()
weather_import = WeatherImport()


def prediction_providers() -> list[
    Union[
        ElecFeeFixed,
        ElecFeeImport,
        ElecPriceAkkudoktor,
        ElecPriceEnergyCharts,
        ElecPriceFixed,
        ElecPriceImport,
        ElecPriceSMARD,
        ElecPriceTibber,
        FeedInTariffAkkudoktor,
        FeedInTariffDvhubOnline,
        FeedInTariffEnergyCharts,
        FeedInTariffFixed,
        FeedInTariffImport,
        FeedInTariffSMARD,
        FeedInTariffTibber,
        LoadAkkudoktor,
        LoadAkkudoktorAdjusted,
        LoadImport,
        LoadVrm,
        PVForecastAkkudoktor,
        PVForecastForecastSolar,
        PVForecastImport,
        PVForecastPVLib,
        PVForecastPVNode,
        PVForecastSolcast,
        PVForecastVrm,
        WeatherBrightSky,
        WeatherClearOutside,
        WeatherImport,
        WeatherOpenMeteo,
    ]
]:
    """Return list of prediction providers.

    Factory for prediction container.
    """
    global \
        elecfee_fixed, \
        elecfee_import, \
        elecprice_akkudoktor, \
        elecprice_energy_charts, \
        elecprice_fixed, \
        elecprice_import, \
        elecprice_smard, \
        elecprice_tibber, \
        feedintariff_akkudoktor, \
        feedintariff_dvhubonline, \
        feedintariff_energy_charts, \
        feedintariff_fixed, \
        feedintariff_import, \
        feedintariff_smard, \
        feedintariff_tibber, \
        loadforecast_akkudoktor, \
        loadforecast_akkudoktor_adjusted, \
        loadforecast_vrm, \
        loadforecast_import, \
        pvforecast_akkudoktor, \
        pvforecast_vrm, \
        pvforecast_homeassistant, \
        pvforecast_pvlib, \
        pvforecast_pvnode, \
        pvforecast_forecastsolar, \
        pvforecast_solcast, \
        pvforecast_import, \
        weather_brightsky, \
        weather_clearoutside, \
        weather_openmeteo, \
        weather_import

    # Care for provider sequence as providers may rely on others to be updated before.
    #
    # Inter provider dependencies:
    # - pvforecast_pvlib depends on weather
    return [
        weather_brightsky,  # weather maybe needed by the pvforcast, keep before
        weather_clearoutside,
        weather_import,
        weather_openmeteo,
        elecfee_fixed,  # elecfee maybe needed by elecprice and feedintariff, keep before
        elecfee_import,
        elecprice_akkudoktor,
        elecprice_energy_charts,
        elecprice_fixed,
        elecprice_import,
        elecprice_smard,
        elecprice_tibber,
        feedintariff_akkudoktor,
        feedintariff_dvhubonline,
        feedintariff_energy_charts,
        feedintariff_fixed,
        feedintariff_import,
        feedintariff_smard,
        feedintariff_tibber,
        loadforecast_akkudoktor,
        loadforecast_akkudoktor_adjusted,
        loadforecast_import,
        loadforecast_vrm,
        pvforecast_akkudoktor,
        pvforecast_forecastsolar,
        pvforecast_homeassistant,
        pvforecast_import,
        pvforecast_pvlib,
        pvforecast_pvnode,
        pvforecast_solcast,
        pvforecast_vrm,
    ]


class Prediction(PredictionContainer):
    """Prediction container to manage multiple prediction providers."""

    providers: list[
        Union[
            ElecFeeFixed,
            ElecFeeImport,
            ElecPriceAkkudoktor,
            ElecPriceEnergyCharts,
            ElecPriceFixed,
            ElecPriceImport,
            ElecPriceSMARD,
            ElecPriceTibber,
            FeedInTariffAkkudoktor,
            FeedInTariffDvhubOnline,
            FeedInTariffEnergyCharts,
            FeedInTariffFixed,
            FeedInTariffImport,
            FeedInTariffSMARD,
            FeedInTariffTibber,
            LoadAkkudoktor,
            LoadAkkudoktorAdjusted,
            LoadImport,
            LoadVrm,
            PVForecastAkkudoktor,
            PVForecastForecastSolar,
            PVForecastHomeAssistant,
            PVForecastImport,
            PVForecastPVLib,
            PVForecastPVNode,
            PVForecastSolcast,
            PVForecastVrm,
            WeatherBrightSky,
            WeatherClearOutside,
            WeatherImport,
            WeatherOpenMeteo,
        ]
    ] = Field(
        default_factory=prediction_providers,
        json_schema_extra={"description": "List of prediction providers"},
    )
