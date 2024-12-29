import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.prediction.prediction import get_prediction
from akkudoktoreos.prediction.pvforecastakkudoktor import (
    AkkudoktorForecastHorizon,
    AkkudoktorForecastMeta,
    AkkudoktorForecastValue,
    PVForecastAkkudoktor,
    PVForecastAkkudoktorDataRecord,
)
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime, to_duration

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_PV_FORECAST_INPUT_1 = DIR_TESTDATA.joinpath("pv_forecast_input_1.json")
FILE_TESTDATA_PV_FORECAST_RESULT_1 = DIR_TESTDATA.joinpath("pv_forecast_result_1.txt")


config_eos = get_config()
ems_eos = get_ems()


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
    config_eos.merge_settings_from_dict(settings)
    return config_eos


@pytest.fixture
def sample_forecast_data():
    """Fixture that returns sample forecast data converted to pydantic model."""
    with open(FILE_TESTDATA_PV_FORECAST_INPUT_1, "r") as f_in:
        input_data = f_in.read()
    return PVForecastAkkudoktor._validate_data(input_data)


@pytest.fixture
def sample_forecast_data_raw():
    """Fixture that returns raw sample forecast data."""
    with open(FILE_TESTDATA_PV_FORECAST_INPUT_1, "r") as f_in:
        input_data = f_in.read()
    return input_data


@pytest.fixture
def sample_forecast_report():
    """Fixture that returns sample forecast data report."""
    with open(FILE_TESTDATA_PV_FORECAST_RESULT_1, "r") as f_res:
        input_data = f_res.read()
    return input_data


@pytest.fixture
def sample_forecast_start(sample_forecast_data):
    """Fixture that returns the start date of the sample forecast data."""
    forecast_start = to_datetime(sample_forecast_data.values[0][0].datetime)
    expected_datetime = to_datetime("2024-10-06T00:00:00.000+02:00")
    assert compare_datetimes(to_datetime(forecast_start), expected_datetime).equal

    timezone_name = sample_forecast_data.meta.timezone
    assert timezone_name == "Europe/Berlin"
    return forecast_start


@pytest.fixture
def provider():
    """Fixture that returns the PVForecastAkkudoktor instance from the prediction."""
    prediction = get_prediction()
    provider = prediction.provider_by_id("PVForecastAkkudoktor")
    assert isinstance(provider, PVForecastAkkudoktor)
    return provider


@pytest.fixture
def provider_empty_instance():
    """Fixture that returns an empty instance of PVForecast."""
    empty_instance = PVForecastAkkudoktor()
    empty_instance.clear()
    assert len(empty_instance) == 0
    return empty_instance


# Sample data for testing
sample_horizon = AkkudoktorForecastHorizon(altitude=30, azimuthFrom=90, azimuthTo=180)
sample_meta = AkkudoktorForecastMeta(
    lat=52.52,
    lon=13.405,
    power=[5000],
    azimuth=[180],
    tilt=[30],
    timezone="Europe/Berlin",
    albedo=0.25,
    past_days=5,
    inverterEfficiency=0.8,
    powerInverter=[10000],
    cellCoEff=-0.36,
    range=True,
    horizont=[[sample_horizon]],
    horizontString=["sample_horizon"],
)
sample_value = AkkudoktorForecastValue(
    datetime="2024-11-09T12:00:00",
    dcPower=500.0,
    power=480.0,
    sunTilt=30.0,
    sunAzimuth=180.0,
    temperature=15.0,
    relativehumidity_2m=50.0,
    windspeed_10m=10.0,
)
sample_config_data = {
    "prediction_hours": 48,
    "prediction_historic_hours": 24,
    "latitude": 52.52,
    "longitude":13.405,
    "pvforecast_provider": "PVForecastAkkudoktor",
    "pvforecast0_peakpower": 5.0,
    "pvforecast0_surface_azimuth": 180,
    "pvforecast0_surface_tilt": 30,
    "pvforecast0_inverter_paco": 10000,
}


# Tests for AkkudoktorForecastHorizon
def test_akkudoktor_forecast_horizon():
    horizon = AkkudoktorForecastHorizon(altitude=30, azimuthFrom=90, azimuthTo=180)
    assert horizon.altitude == 30
    assert horizon.azimuthFrom == 90
    assert horizon.azimuthTo == 180


# Tests for AkkudoktorForecastMeta
def test_akkudoktor_forecast_meta():
    meta = sample_meta
    assert meta.lat == 52.52
    assert meta.lon ==13.405
    assert meta.power == [5000]
    assert meta.tilt == [30]
    assert meta.timezone == "Europe/Berlin"


# Tests for AkkudoktorForecastValue
def test_akkudoktor_forecast_value():
    value = sample_value
    assert value.dcPower == 500.0
    assert value.power == 480.0
    assert value.temperature == 15.0
    assert value.windspeed_10m == 10.0


# Tests for PVForecastAkkudoktorDataRecord
def test_pvforecast_akkudoktor_data_record():
    record = PVForecastAkkudoktorDataRecord(
        pvforecastakkudoktor_ac_power_measured=1000.0,
        pvforecastakkudoktor_wind_speed_10m=10.0,
        pvforecastakkudoktor_temp_air=15.0,
    )
    assert record.pvforecastakkudoktor_ac_power_measured == 1000.0
    assert record.pvforecastakkudoktor_wind_speed_10m == 10.0
    assert record.pvforecastakkudoktor_temp_air == 15.0
    assert (
        record.pvforecastakkudoktor_ac_power_any == 1000.0
    )  # Assuming AC power measured is preferred


def test_pvforecast_akkudoktor_validate_data(provider_empty_instance, sample_forecast_data_raw):
    """Test validation of PV forecast data on sample data."""
    with pytest.raises(
        ValueError,
        match="Field: meta\nError: Field required\nType: missing\nField: values\nError: Field required\nType: missing\n",
    ):
        ret = provider_empty_instance._validate_data("{}")
    data = provider_empty_instance._validate_data(sample_forecast_data_raw)
    # everything worked


@patch("requests.get")
def test_pvforecast_akkudoktor_update_with_sample_forecast(
    mock_get, sample_settings, sample_forecast_data_raw, sample_forecast_start, provider
):
    """Test data processing using sample forecast data."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = sample_forecast_data_raw
    mock_get.return_value = mock_response

    # Test that update properly inserts data records
    ems_eos.set_start_datetime(sample_forecast_start)
    provider.update_data(force_enable=True, force_update=True)
    assert compare_datetimes(provider.start_datetime, sample_forecast_start).equal
    assert compare_datetimes(provider[0].date_time, to_datetime(sample_forecast_start)).equal


# Report Generation Test
def test_report_ac_power_and_measurement(provider):
    # Set the configuration
    config = get_config()
    config.merge_settings_from_dict(sample_config_data)

    record = PVForecastAkkudoktorDataRecord(
        pvforecastakkudoktor_ac_power_measured=900.0,
        pvforecast_dc_power=450.0,
        pvforecast_ac_power=400.0,
    )
    provider.append(record)

    report = provider.report_ac_power_and_measurement()
    assert "DC: 450.0" in report
    assert "AC: 400.0" in report
    assert "AC sampled: 900.0" in report


@pytest.mark.skipif(
    sys.platform.startswith("win"), reason="'other_timezone' fixture not supported on Windows"
)
@patch("requests.get")
def test_timezone_behaviour(
    mock_get,
    sample_settings,
    sample_forecast_data_raw,
    sample_forecast_start,
    provider,
    set_other_timezone,
):
    """Test PVForecast in another timezone."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = sample_forecast_data_raw
    mock_get.return_value = mock_response

    # sample forecast start in other timezone
    other_timezone = set_other_timezone()
    other_start_datetime = to_datetime(sample_forecast_start, in_timezone=other_timezone)
    assert compare_datetimes(other_start_datetime, sample_forecast_start).equal
    expected_datetime = to_datetime("2024-10-06T00:00:00+0200", in_timezone=other_timezone)
    assert compare_datetimes(other_start_datetime, expected_datetime).equal

    provider.clear()
    assert len(provider) == 0
    ems_eos.set_start_datetime(other_start_datetime)
    provider.update_data(force_update=True)
    assert compare_datetimes(provider.start_datetime, other_start_datetime).equal
    # Check wether first record starts at requested sample start time
    assert compare_datetimes(provider[0].date_time, sample_forecast_start).equal

    # Test updating AC power measurement for a specific date.
    provider.update_value(sample_forecast_start, "pvforecastakkudoktor_ac_power_measured", 1000)
    # Check wether first record was filled with ac power measurement
    assert provider[0].pvforecastakkudoktor_ac_power_measured == 1000

    # Test fetching temperature forecast for a specific date.
    other_end_datetime = other_start_datetime + to_duration("24 hours")
    expected_end_datetime = to_datetime("2024-10-07T00:00:00+0200", in_timezone=other_timezone)
    assert compare_datetimes(other_end_datetime, expected_end_datetime).equal
    forecast_temps = provider.key_to_series(
        "pvforecastakkudoktor_temp_air", other_start_datetime, other_end_datetime
    )
    assert len(forecast_temps) == 23  # 24-1, first temperature is null
    assert forecast_temps.iloc[0] == 6.5
    assert forecast_temps.iloc[1] == 6.0

    # Test fetching AC power forecast
    other_end_datetime = other_start_datetime + to_duration("48 hours")
    forecast_measured = provider.key_to_series(
        "pvforecastakkudoktor_ac_power_measured", other_start_datetime, other_end_datetime
    )
    assert len(forecast_measured) == 1
    assert forecast_measured.iloc[0] == 1000.0  # changed before
