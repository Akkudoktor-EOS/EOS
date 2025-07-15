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

from typing import List, Optional, Union

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.elecpriceakkudoktor import ElecPriceAkkudoktor
from akkudoktoreos.prediction.elecpriceenergycharts import ElecPriceEnergyCharts
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImport
from akkudoktoreos.prediction.loadakkudoktor import LoadAkkudoktor
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
    """General Prediction Configuration.

    This class provides configuration for prediction settings, allowing users to specify
    parameters such as the forecast duration (in hours).
    Validators ensure each parameter is within a specified range.

    Attributes:
        hours (Optional[int]): Number of hours into the future for predictions.
            Must be non-negative.
        historic_hours (Optional[int]): Number of hours into the past for historical data.
            Must be non-negative.

    Validators:
        validate_hours (int): Ensures `hours` is a non-negative integer.
        validate_historic_hours (int): Ensures `historic_hours` is a non-negative integer.
    """

    hours: Optional[int] = Field(
        default=48, ge=0, description="Number of hours into the future for predictions"
    )
    historic_hours: Optional[int] = Field(
        default=48,
        ge=0,
        description="Number of hours into the past for historical predictions data",
    )


class Prediction(PredictionContainer):
    """Prediction container to manage multiple prediction providers.

    Attributes:
        providers (List[Union[PVForecastAkkudoktor, WeatherBrightSky, WeatherClearOutside]]):
            List of forecast provider instances, in the order they should be updated.
            Providers may depend on updates from others.
    """

    providers: List[
        Union[
            ElecPriceAkkudoktor,
            ElecPriceEnergyCharts,
            ElecPriceImport,
            LoadAkkudoktor,
            LoadVrm,
            LoadImport,
            PVForecastAkkudoktor,
            PVForecastVrm,
            PVForecastImport,
            WeatherBrightSky,
            WeatherClearOutside,
            WeatherImport,
        ]
    ] = Field(default_factory=list, description="List of prediction providers")


# Initialize forecast providers, all are singletons.
elecprice_akkudoktor = ElecPriceAkkudoktor()
elecprice_energy_charts = ElecPriceEnergyCharts()
elecprice_import = ElecPriceImport()
load_akkudoktor = LoadAkkudoktor()
load_vrm = LoadVrm()
load_import = LoadImport()
pvforecast_akkudoktor = PVForecastAkkudoktor()
pvforecast_vrm = PVForecastVrm()
pvforecast_import = PVForecastImport()
weather_brightsky = WeatherBrightSky()
weather_clearoutside = WeatherClearOutside()
weather_import = WeatherImport()


def get_prediction() -> Prediction:
    """Gets the EOS prediction data."""
    # Initialize Prediction instance with providers in the required order
    # Care for provider sequence as providers may rely on others to be updated before.
    prediction = Prediction(
        providers=[
            elecprice_akkudoktor,
            elecprice_energy_charts,
            elecprice_import,
            load_akkudoktor,
            load_vrm,
            load_import,
            pvforecast_akkudoktor,
            pvforecast_vrm,
            pvforecast_import,
            weather_brightsky,
            weather_clearoutside,
            weather_import,
        ]
    )
    return prediction


def main() -> None:
    """Main function to update and display predictions.

    This function initializes and updates the forecast providers in sequence
    according to the `Prediction` instance, then prints the updated prediction data.
    """
    prediction = get_prediction()
    prediction.update_data()
    print(f"Prediction: {prediction}")


if __name__ == "__main__":
    main()
