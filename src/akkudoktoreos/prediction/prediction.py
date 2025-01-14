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

from pydantic import Field, computed_field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.elecpriceakkudoktor import ElecPriceAkkudoktor
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImport
from akkudoktoreos.prediction.loadakkudoktor import LoadAkkudoktor
from akkudoktoreos.prediction.loadimport import LoadImport
from akkudoktoreos.prediction.predictionabc import PredictionContainer
from akkudoktoreos.prediction.pvforecastakkudoktor import PVForecastAkkudoktor
from akkudoktoreos.prediction.pvforecastimport import PVForecastImport
from akkudoktoreos.prediction.weatherbrightsky import WeatherBrightSky
from akkudoktoreos.prediction.weatherclearoutside import WeatherClearOutside
from akkudoktoreos.prediction.weatherimport import WeatherImport
from akkudoktoreos.utils.datetimeutil import to_timezone


class PredictionCommonSettings(SettingsBaseModel):
    """General Prediction Configuration.

    This class provides configuration for prediction settings, allowing users to specify
    parameters such as the forecast duration (in hours) and location (latitude and longitude).
    Validators ensure each parameter is within a specified range. A computed property, `timezone`,
    determines the time zone based on latitude and longitude.

    Attributes:
        prediction_hours (Optional[int]): Number of hours into the future for predictions.
            Must be non-negative.
        prediction_historic_hours (Optional[int]): Number of hours into the past for historical data.
            Must be non-negative.
        latitude (Optional[float]): Latitude in degrees, must be between -90 and 90.
        longitude (Optional[float]): Longitude in degrees, must be between -180 and 180.

    Properties:
        timezone (Optional[str]): Computed time zone string based on the specified latitude
            and longitude.

    Validators:
        validate_prediction_hours (int): Ensures `prediction_hours` is a non-negative integer.
        validate_prediction_historic_hours (int): Ensures `prediction_historic_hours` is a non-negative integer.
        validate_latitude (float): Ensures `latitude` is within the range -90 to 90.
        validate_longitude (float): Ensures `longitude` is within the range -180 to 180.
    """

    prediction_hours: Optional[int] = Field(
        default=48, ge=0, description="Number of hours into the future for predictions"
    )
    prediction_historic_hours: Optional[int] = Field(
        default=48,
        ge=0,
        description="Number of hours into the past for historical predictions data",
    )
    latitude: Optional[float] = Field(
        default=52.52,
        ge=-90.0,
        le=90.0,
        description="Latitude in decimal degrees, between -90 and 90, north is positive (ISO 19115) (°)",
    )
    longitude: Optional[float] = Field(
        default=13.405,
        ge=-180.0,
        le=180.0,
        description="Longitude in decimal degrees, within -180 to 180 (°)",
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def timezone(self) -> Optional[str]:
        """Compute timezone based on latitude and longitude."""
        if self.latitude and self.longitude:
            return to_timezone(location=(self.latitude, self.longitude), as_string=True)
        return None


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
            ElecPriceImport,
            LoadAkkudoktor,
            LoadImport,
            PVForecastAkkudoktor,
            PVForecastImport,
            WeatherBrightSky,
            WeatherClearOutside,
            WeatherImport,
        ]
    ] = Field(default_factory=list, description="List of prediction providers")


# Initialize forecast providers, all are singletons.
elecprice_akkudoktor = ElecPriceAkkudoktor()
elecprice_import = ElecPriceImport()
load_akkudoktor = LoadAkkudoktor()
load_import = LoadImport()
pvforecast_akkudoktor = PVForecastAkkudoktor()
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
            elecprice_import,
            load_akkudoktor,
            load_import,
            pvforecast_akkudoktor,
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
