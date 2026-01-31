import pytest
from pydantic import ValidationError

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
from akkudoktoreos.prediction.prediction import (
    Prediction,
    PredictionCommonSettings,
    get_prediction,
)
from akkudoktoreos.prediction.pvforecastakkudoktor import PVForecastAkkudoktor
from akkudoktoreos.prediction.pvforecastimport import PVForecastImport
from akkudoktoreos.prediction.pvforecastvrm import PVForecastVrm
from akkudoktoreos.prediction.weatherbrightsky import WeatherBrightSky
from akkudoktoreos.prediction.weatherclearoutside import WeatherClearOutside
from akkudoktoreos.prediction.weatherimport import WeatherImport


@pytest.fixture
def prediction():
    """All EOS predictions."""
    return get_prediction()


@pytest.fixture
def forecast_providers():
    """Fixture for singleton forecast provider instances."""
    return [
        ElecPriceAkkudoktor(),
        ElecPriceEnergyCharts(),
        ElecPriceImport(),
        FeedInTariffFixed(),
        FeedInTariffImport(),
        LoadAkkudoktor(),
        LoadAkkudoktorAdjusted(),
        LoadVrm(),
        LoadImport(),
        PVForecastAkkudoktor(),
        PVForecastVrm(),
        PVForecastImport(),
        WeatherBrightSky(),
        WeatherClearOutside(),
        WeatherImport(),
    ]


@pytest.mark.parametrize(
    "field_name, invalid_value, expected_error",
    [
        ("hours", -1, "Input should be greater than or equal to 0"),
        ("historic_hours", -5, "Input should be greater than or equal to 0"),
    ],
)
def test_prediction_common_settings_invalid(field_name, invalid_value, expected_error, config_eos):
    """Test invalid settings for PredictionCommonSettings."""
    valid_data = {
        "hours": 48,
        "historic_hours": 24,
    }
    assert PredictionCommonSettings(**valid_data) is not None
    valid_data[field_name] = invalid_value

    with pytest.raises(ValidationError, match=expected_error):
        PredictionCommonSettings(**valid_data)


def test_initialization(prediction, forecast_providers):
    """Test that Prediction is initialized with the correct providers in sequence."""
    assert isinstance(prediction, Prediction)
    assert prediction.providers == forecast_providers


def test_provider_sequence(prediction):
    """Test the provider sequence is maintained in the Prediction instance."""
    assert isinstance(prediction.providers[0], ElecPriceAkkudoktor)
    assert isinstance(prediction.providers[1], ElecPriceEnergyCharts)
    assert isinstance(prediction.providers[2], ElecPriceImport)
    assert isinstance(prediction.providers[3], FeedInTariffFixed)
    assert isinstance(prediction.providers[4], FeedInTariffImport)
    assert isinstance(prediction.providers[5], LoadAkkudoktor)
    assert isinstance(prediction.providers[6], LoadAkkudoktorAdjusted)
    assert isinstance(prediction.providers[7], LoadVrm)
    assert isinstance(prediction.providers[8], LoadImport)
    assert isinstance(prediction.providers[9], PVForecastAkkudoktor)
    assert isinstance(prediction.providers[10], PVForecastVrm)
    assert isinstance(prediction.providers[11], PVForecastImport)
    assert isinstance(prediction.providers[12], WeatherBrightSky)
    assert isinstance(prediction.providers[13], WeatherClearOutside)
    assert isinstance(prediction.providers[14], WeatherImport)


def test_provider_by_id(prediction, forecast_providers):
    """Test that provider_by_id method returns the correct provider."""
    for provider in forecast_providers:
        assert prediction.provider_by_id(provider.provider_id()) == provider


def test_prediction_repr(prediction):
    """Test that the Prediction instance's representation is correct."""
    result = repr(prediction)
    assert "Prediction([" in result
    assert "ElecPriceAkkudoktor" in result
    assert "ElecPriceEnergyCharts" in result
    assert "ElecPriceImport" in result
    assert "FeedInTariffFixed" in result
    assert "FeedInTariffImport" in result
    assert "LoadAkkudoktor" in result
    assert "LoadVrm" in result
    assert "LoadImport" in result
    assert "PVForecastAkkudoktor" in result
    assert "PVForecastVrm" in result
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
