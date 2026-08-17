import pytest
from pydantic import ValidationError

from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.prediction.elecfeefixed import ElecFeeFixed
from akkudoktoreos.prediction.elecfeeimport import ElecFeeImport
from akkudoktoreos.prediction.elecpriceakkudoktor import ElecPriceAkkudoktor
from akkudoktoreos.prediction.elecpriceenergycharts import ElecPriceEnergyCharts
from akkudoktoreos.prediction.elecpricefixed import ElecPriceFixed
from akkudoktoreos.prediction.elecpriceimport import ElecPriceImport
from akkudoktoreos.prediction.elecpricetibber import ElecPriceTibber
from akkudoktoreos.prediction.feedintariffakkudoktor import FeedInTariffAkkudoktor
from akkudoktoreos.prediction.feedintariffdvhubonline import FeedInTariffDvhubOnline
from akkudoktoreos.prediction.feedintariffenergycharts import FeedInTariffEnergyCharts
from akkudoktoreos.prediction.feedintarifffixed import FeedInTariffFixed
from akkudoktoreos.prediction.feedintariffimport import FeedInTariffImport
from akkudoktoreos.prediction.feedintarifftibber import FeedInTariffTibber
from akkudoktoreos.prediction.loadakkudoktor import (
    LoadAkkudoktor,
    LoadAkkudoktorAdjusted,
)
from akkudoktoreos.prediction.loadimport import LoadImport
from akkudoktoreos.prediction.loadvrm import LoadVrm
from akkudoktoreos.prediction.prediction import (
    Prediction,
    PredictionCommonSettings,
)
from akkudoktoreos.prediction.pvforecastakkudoktor import PVForecastAkkudoktor
from akkudoktoreos.prediction.pvforecastforecastsolar import PVForecastForecastSolar
from akkudoktoreos.prediction.pvforecastimport import PVForecastImport
from akkudoktoreos.prediction.pvforecastpvlib import PVForecastPVLib
from akkudoktoreos.prediction.pvforecastpvnode import PVForecastPVNode
from akkudoktoreos.prediction.pvforecastsolcast import PVForecastSolcast
from akkudoktoreos.prediction.pvforecastvrm import PVForecastVrm
from akkudoktoreos.prediction.weatherbrightsky import WeatherBrightSky
from akkudoktoreos.prediction.weatherclearoutside import WeatherClearOutside
from akkudoktoreos.prediction.weatherimport import WeatherImport
from akkudoktoreos.prediction.weatheropenmeteo import WeatherOpenMeteo


@pytest.fixture
def prediction():
    """All EOS predictions."""
    return get_prediction()


@pytest.fixture
def forecast_providers():
    """Fixture for singleton forecast provider instances."""
    return [
        WeatherBrightSky(),
        WeatherClearOutside(),
        WeatherImport(),
        WeatherOpenMeteo(),
        ElecFeeFixed(),
        ElecFeeImport(),
        ElecPriceAkkudoktor(),
        ElecPriceEnergyCharts(),
        ElecPriceFixed(),
        ElecPriceImport(),
        ElecPriceTibber(),
        FeedInTariffAkkudoktor(),
        FeedInTariffDvhubOnline(),
        FeedInTariffEnergyCharts(),
        FeedInTariffFixed(),
        FeedInTariffImport(),
        FeedInTariffTibber(),
        LoadAkkudoktor(),
        LoadAkkudoktorAdjusted(),
        LoadImport(),
        LoadVrm(),
        PVForecastAkkudoktor(),
        PVForecastForecastSolar(),
        PVForecastImport(),
        PVForecastPVLib(),
        PVForecastPVNode(),
        PVForecastSolcast(),
        PVForecastVrm(),
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
    for idx, provider in enumerate(prediction.providers):
        assert provider.provider_id() == forecast_providers[idx].provider_id()


def test_provider_sequence(prediction):
    """Test the provider sequence is maintained in the Prediction instance."""
    assert isinstance(prediction.providers[0], WeatherBrightSky)
    assert isinstance(prediction.providers[1], WeatherClearOutside)
    assert isinstance(prediction.providers[2], WeatherImport)
    assert isinstance(prediction.providers[3], WeatherOpenMeteo)
    assert isinstance(prediction.providers[4], ElecFeeFixed)
    assert isinstance(prediction.providers[5], ElecFeeImport)
    assert isinstance(prediction.providers[6], ElecPriceAkkudoktor)
    assert isinstance(prediction.providers[7], ElecPriceEnergyCharts)
    assert isinstance(prediction.providers[8], ElecPriceFixed)
    assert isinstance(prediction.providers[9], ElecPriceImport)
    assert isinstance(prediction.providers[10], ElecPriceTibber)
    assert isinstance(prediction.providers[11], FeedInTariffAkkudoktor)
    assert isinstance(prediction.providers[12], FeedInTariffDvhubOnline)
    assert isinstance(prediction.providers[13], FeedInTariffEnergyCharts)
    assert isinstance(prediction.providers[14], FeedInTariffFixed)
    assert isinstance(prediction.providers[15], FeedInTariffImport)
    assert isinstance(prediction.providers[16], FeedInTariffTibber)
    assert isinstance(prediction.providers[17], LoadAkkudoktor)
    assert isinstance(prediction.providers[18], LoadAkkudoktorAdjusted)
    assert isinstance(prediction.providers[19], LoadImport)
    assert isinstance(prediction.providers[20], LoadVrm)
    assert isinstance(prediction.providers[21], PVForecastAkkudoktor)
    assert isinstance(prediction.providers[22], PVForecastForecastSolar)
    assert isinstance(prediction.providers[23], PVForecastImport)
    assert isinstance(prediction.providers[24], PVForecastPVLib)
    assert isinstance(prediction.providers[25], PVForecastPVNode)
    assert isinstance(prediction.providers[26], PVForecastSolcast)
    assert isinstance(prediction.providers[27], PVForecastVrm)


def test_provider_by_id(prediction, forecast_providers):
    """Test that provider_by_id method returns the correct provider."""
    for provider in forecast_providers:
        assert prediction.provider_by_id(provider.provider_id()).provider_id() == provider.provider_id()


def test_prediction_repr(prediction):
    """Test that the Prediction instance's representation is correct."""
    result = repr(prediction)
    assert "Prediction([" in result
    assert "ElecFeeFixed" in result
    assert "ElecFeeImport" in result
    assert "ElecPriceAkkudoktor" in result
    assert "ElecPriceEnergyCharts" in result
    assert "ElecPriceFixed" in result
    assert "ElecPriceImport" in result
    assert "ElecPriceTibber" in result
    assert "FeedInTariffAkkudoktor" in result
    assert "FeedInTariffDvhubOnline" in result
    assert "FeedInTariffEnergyCharts" in result
    assert "FeedInTariffFixed" in result
    assert "FeedInTariffImport" in result
    assert "FeedInTariffTibber" in result
    assert "LoadAkkudoktor" in result
    assert "LoadAkkudoktorAdjusted" in result
    assert "LoadImport" in result
    assert "LoadVrm" in result
    assert "PVForecastAkkudoktor" in result
    assert "PVForecastForecastSolar" in result
    assert "PVForecastImport" in result
    assert "PVForecastPVLib" in result
    assert "PVForecastPVNode" in result
    assert "PVForecastSolcast" in result
    assert "PVForecastVrm" in result
    assert "WeatherBrightSky" in result
    assert "WeatherClearOutside" in result
    assert "WeatherImport" in result
    assert "WeatherOpenMeteo" in result


@pytest.mark.asyncio
async def test_empty_providers(prediction, forecast_providers):
    """Test behavior when Prediction does not have providers."""
    # Clear all prediction providers from prediction
    providers_bkup = prediction.providers.copy()
    prediction.providers.clear()
    assert prediction.providers == []
    await prediction.update_data()  # Should not raise an error even with no providers

    # Cleanup after Test
    prediction.providers = providers_bkup
