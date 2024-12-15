import pytest
from pydantic import ValidationError

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.prediction.elecpriceakkudoktor import ElecPriceAkkudoktor
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImport
from akkudoktoreos.prediction.loadakkudoktor import LoadAkkudoktor
from akkudoktoreos.prediction.loadimport import LoadImport
from akkudoktoreos.prediction.prediction import (
    Prediction,
    PredictionCommonSettings,
    get_prediction,
)
from akkudoktoreos.prediction.pvforecastakkudoktor import PVForecastAkkudoktor
from akkudoktoreos.prediction.pvforecastimport import PVForecastImport
from akkudoktoreos.prediction.weatherbrightsky import WeatherBrightSky
from akkudoktoreos.prediction.weatherclearoutside import WeatherClearOutside
from akkudoktoreos.prediction.weatherimport import WeatherImport
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime


@pytest.fixture
def sample_settings(reset_config):
    """Fixture that adds settings data to the global config."""
    settings = {
        "prediction_hours": 48,
        "prediction_historic_hours": 24,
        "latitude": 52.52,
        "longitude": 13.405,
        "pvforecast_provider": "PVForecastAkkudoktor",
        "pvforecast0_peakpower": 5.0,
        "pvforecast0_surface_azimuth": -10,
        "pvforecast0_surface_tilt": 7,
        "pvforecast0_userhorizon": [20, 27, 22, 20],
        "pvforecast0_inverter_paco": 10000,
        "pvforecast1_peakpower": 4.8,
        "pvforecast1_surface_azimuth": -90,
        "pvforecast1_surface_tilt": 7,
        "pvforecast1_userhorizon": [30, 30, 30, 50],
        "pvforecast1_inverter_paco": 10000,
        "pvforecast2_peakpower": 1.4,
        "pvforecast2_surface_azimuth": -40,
        "pvforecast2_surface_tilt": 60,
        "pvforecast2_userhorizon": [60, 30, 0, 30],
        "pvforecast2_inverter_paco": 2000,
        "pvforecast3_peakpower": 1.6,
        "pvforecast3_surface_azimuth": 5,
        "pvforecast3_surface_tilt": 45,
        "pvforecast3_userhorizon": [45, 25, 30, 60],
        "pvforecast3_inverter_paco": 1400,
        "pvforecast4_peakpower": None,
    }

    # Merge settings to config
    config = get_config()
    config.merge_settings_from_dict(settings)
    return config


@pytest.fixture
def prediction():
    """All EOS predictions."""
    return get_prediction()


@pytest.fixture
def forecast_providers():
    """Fixture for singleton forecast provider instances."""
    return [
        ElecPriceAkkudoktor(),
        ElecPriceImport(),
        LoadAkkudoktor(),
        LoadImport(),
        PVForecastAkkudoktor(),
        PVForecastImport(),
        WeatherBrightSky(),
        WeatherClearOutside(),
        WeatherImport(),
    ]


@pytest.mark.parametrize(
    "prediction_hours, prediction_historic_hours, latitude, longitude, expected_timezone",
    [
        (48, 24, 40.7128, -74.0060, "America/New_York"),  # Valid latitude/longitude
        (0, 0, None, None, None),  # No location
        (100, 50, 51.5074, -0.1278, "Europe/London"),  # Another valid location
    ],
)
def test_prediction_common_settings_valid(
    prediction_hours, prediction_historic_hours, latitude, longitude, expected_timezone
):
    """Test valid settings for PredictionCommonSettings."""
    settings = PredictionCommonSettings(
        prediction_hours=prediction_hours,
        prediction_historic_hours=prediction_historic_hours,
        latitude=latitude,
        longitude=longitude,
    )
    assert settings.prediction_hours == prediction_hours
    assert settings.prediction_historic_hours == prediction_historic_hours
    assert settings.latitude == latitude
    assert settings.longitude == longitude
    assert settings.timezone == expected_timezone


@pytest.mark.parametrize(
    "field_name, invalid_value, expected_error",
    [
        ("prediction_hours", -1, "Input should be greater than or equal to 0"),
        ("prediction_historic_hours", -5, "Input should be greater than or equal to 0"),
        ("latitude", -91.0, "Input should be greater than or equal to -90"),
        ("latitude", 91.0, "Input should be less than or equal to 90"),
        ("longitude", -181.0, "Input should be greater than or equal to -180"),
        ("longitude", 181.0, "Input should be less than or equal to 180"),
    ],
)
def test_prediction_common_settings_invalid(field_name, invalid_value, expected_error):
    """Test invalid settings for PredictionCommonSettings."""
    valid_data = {
        "prediction_hours": 48,
        "prediction_historic_hours": 24,
        "latitude": 40.7128,
        "longitude": -74.0060,
    }
    valid_data[field_name] = invalid_value

    with pytest.raises(ValidationError, match=expected_error):
        PredictionCommonSettings(**valid_data)


def test_prediction_common_settings_no_location():
    """Test that timezone is None when latitude and longitude are not provided."""
    settings = PredictionCommonSettings(
        prediction_hours=48, prediction_historic_hours=24, latitude=None, longitude=None
    )
    assert settings.timezone is None


def test_prediction_common_settings_with_location():
    """Test that timezone is correctly computed when latitude and longitude are provided."""
    settings = PredictionCommonSettings(
        prediction_hours=48, prediction_historic_hours=24, latitude=34.0522, longitude=-118.2437
    )
    assert settings.timezone == "America/Los_Angeles"


def test_prediction_common_settings_timezone_none_when_coordinates_missing():
    """Test that timezone is None when latitude or longitude is missing."""
    config_no_latitude = PredictionCommonSettings(longitude=-74.0060)
    config_no_longitude = PredictionCommonSettings(latitude=40.7128)
    config_no_coords = PredictionCommonSettings()

    assert config_no_latitude.timezone is None
    assert config_no_longitude.timezone is None
    assert config_no_coords.timezone is None


def test_initialization(prediction, forecast_providers):
    """Test that Prediction is initialized with the correct providers in sequence."""
    assert isinstance(prediction, Prediction)
    assert prediction.providers == forecast_providers


def test_provider_sequence(prediction):
    """Test the provider sequence is maintained in the Prediction instance."""
    assert isinstance(prediction.providers[0], ElecPriceAkkudoktor)
    assert isinstance(prediction.providers[1], ElecPriceImport)
    assert isinstance(prediction.providers[2], LoadAkkudoktor)
    assert isinstance(prediction.providers[3], LoadImport)
    assert isinstance(prediction.providers[4], PVForecastAkkudoktor)
    assert isinstance(prediction.providers[5], PVForecastImport)
    assert isinstance(prediction.providers[6], WeatherBrightSky)
    assert isinstance(prediction.providers[7], WeatherClearOutside)
    assert isinstance(prediction.providers[8], WeatherImport)


def test_update_calls_providers(sample_settings, prediction):
    """Test that the update method calls the update method for each provider in sequence."""
    # Mark the `update_datetime` method for each provider
    old_datetime = to_datetime("1970-01-01 00:00:00")
    for provider in prediction.providers:
        provider.update_datetime = old_datetime

    ems_eos = get_ems()
    ems_eos.set_start_datetime(to_datetime())
    prediction.update_data()

    # Verify each provider's `update` method was called
    for provider in prediction.providers:
        if provider.enabled():
            assert compare_datetimes(provider.update_datetime, old_datetime).gt


def test_provider_by_id(prediction, forecast_providers):
    """Test that provider_by_id method returns the correct provider."""
    for provider in forecast_providers:
        assert prediction.provider_by_id(provider.provider_id()) == provider


def test_prediction_repr(prediction):
    """Test that the Prediction instance's representation is correct."""
    result = repr(prediction)
    assert "Prediction([" in result
    assert "ElecPriceAkkudoktor" in result
    assert "ElecPriceImport" in result
    assert "LoadAkkudoktor" in result
    assert "LoadImport" in result
    assert "PVForecastAkkudoktor" in result
    assert "PVForecastImport" in result
    assert "WeatherBrightSky" in result
    assert "WeatherClearOutside" in result
    assert "WeatherImport" in result


def test_empty_providers(prediction, forecast_providers):
    """Test behavior when Prediction does not have providers."""
    # Clear all prediction providers from prediction
    providers_bkup = prediction.providers.copy()
    prediction.providers.clear()
    assert prediction.providers == []
    prediction.update_data()  # Should not raise an error even with no providers

    # Cleanup after Test
    prediction.providers = providers_bkup