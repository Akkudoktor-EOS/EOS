"""Test Module for PV Power Forecasting Module.

This test module is designed to verify the functionality of the `PVForecast` class
and its methods in the `prediction.pv_forecast` module. The tests include validation for
forecast data processing, updating AC power measurements, retrieving forecast data,
and caching behavior.

Fixtures:
    sample_forecast_data: Provides sample forecast data in JSON format for testing.
    pv_forecast_instance: Provides an instance of `PVForecast` class with sample data loaded.

Test Cases:
    - test_generate_cache_filename: Verifies correct cache filename generation based on URL and date.
    - test_update_ac_power_measurement: Tests updating AC power measurement for a matching date.
    - test_update_ac_power_measurement_no_match: Ensures no updates occur when there is no matching date.
    - test_get_temperature_forecast_for_date: Tests retrieving the temperature forecast for a specific date.
    - test_get_pv_forecast_for_date_range: Verifies retrieval of AC power forecast for a specified date range.
    - test_get_forecast_dataframe: Ensures forecast data can be correctly converted into a Pandas DataFrame.
    - test_cache_loading: Tests loading forecast data from a cached file to ensure caching works as expected.

Usage:
    This test module uses `pytest` and requires the `akkudoktoreos.prediction.pv_forecast.py` module to be present.
    Run the tests using the command: `pytest test_pv_forecast.py`.

"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from akkudoktoreos.prediction.pv_forecast import PVForecast, validate_pv_forecast_data
from akkudoktoreos.utils.datetimeutil import to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_PV_FORECAST_INPUT_1 = DIR_TESTDATA.joinpath("pv_forecast_input_1.json")
FILE_TESTDATA_PV_FORECAST_RESULT_1 = DIR_TESTDATA.joinpath("pv_forecast_result_1.txt")


@pytest.fixture
def sample_forecast_data():
    """Fixture that returns sample forecast data."""
    with open(FILE_TESTDATA_PV_FORECAST_INPUT_1, "r") as f_in:
        input_data = json.load(f_in)
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
    forecast_start_str = sample_forecast_data["values"][0][0]["datetime"]
    assert forecast_start_str == "2024-10-06T00:00:00.000+02:00"

    timezone_name = sample_forecast_data["meta"]["timezone"]
    assert timezone_name == "Europe/Berlin"

    forecast_start = to_datetime(forecast_start_str, to_timezone=timezone_name, to_naiv=True)
    assert forecast_start == datetime(2024, 10, 6)

    return forecast_start


@pytest.fixture
def pv_forecast_empty_instance():
    """Fixture that returns an empty instance of PVForecast."""
    empty_instance = PVForecast()
    assert empty_instance.get_forecast_start() is None

    return empty_instance


@pytest.fixture
def pv_forecast_instance(sample_forecast_data, sample_forecast_start):
    """Fixture that returns an instance of PVForecast with sample data loaded."""
    pv_forecast = PVForecast(
        data=sample_forecast_data,
        forecast_start=sample_forecast_start,
        prediction_hours=48,
    )
    return pv_forecast


def test_validate_pv_forecast_data(sample_forecast_data):
    """Test validation of PV forecast data on sample data."""
    ret = validate_pv_forecast_data({})
    assert ret is None

    ret = validate_pv_forecast_data(sample_forecast_data)
    assert ret == "Akkudoktor"


def test_process_data(sample_forecast_data, sample_forecast_start):
    """Test data processing using sample data."""
    pv_forecast_instance = PVForecast(forecast_start=sample_forecast_start)

    # Assure the start date is correctly set by init funtion
    forecast_start = pv_forecast_instance.get_forecast_start()
    expected_start = sample_forecast_start
    assert forecast_start == expected_start

    # Assure the prediction hours are unset
    assert pv_forecast_instance.prediction_hours is None

    # Load forecast with sample data - throws exceptions on error
    pv_forecast_instance.process_data(data=sample_forecast_data)


def test_update_ac_power_measurement(pv_forecast_instance, sample_forecast_start):
    """Test updating AC power measurement for a specific date."""
    forecast_start = pv_forecast_instance.get_forecast_start()
    assert forecast_start == sample_forecast_start

    updated = pv_forecast_instance.update_ac_power_measurement(forecast_start, 1000)
    assert updated is True
    forecast_data = pv_forecast_instance.get_forecast_data()
    assert forecast_data[0].ac_power_measurement == 1000


def test_update_ac_power_measurement_no_match(pv_forecast_instance):
    """Test updating AC power measurement where no date matches."""
    date_time = datetime(2023, 10, 2, 1, 0, 0)
    updated = pv_forecast_instance.update_ac_power_measurement(date_time, 1000)
    assert not updated


def test_get_temperature_forecast_for_date(pv_forecast_instance, sample_forecast_start):
    """Test fetching temperature forecast for a specific date."""
    forecast_temps = pv_forecast_instance.get_temperature_forecast_for_date(sample_forecast_start)
    assert len(forecast_temps) == 24
    assert forecast_temps[0] == 7.0
    assert forecast_temps[1] == 6.5
    assert forecast_temps[2] == 6.0

    # Assure function bails out if there is no timezone name available for the system.
    tz_name = pv_forecast_instance._tz_name
    pv_forecast_instance._tz_name = None
    with pytest.raises(Exception) as exc_info:
        forecast_temps = pv_forecast_instance.get_temperature_forecast_for_date(
            sample_forecast_start
        )
    pv_forecast_instance._tz_name = tz_name
    assert (
        exc_info.value.args[0] == "Processing without PV system timezone info ist not implemented!"
    )


def test_get_temperature_for_date_range(pv_forecast_instance, sample_forecast_start):
    """Test fetching temperature forecast for a specific date range."""
    end_date = sample_forecast_start + timedelta(hours=24)
    forecast_temps = pv_forecast_instance.get_temperature_for_date_range(
        sample_forecast_start, end_date
    )
    assert len(forecast_temps) == 48
    assert forecast_temps[0] == 7.0
    assert forecast_temps[1] == 6.5
    assert forecast_temps[2] == 6.0

    # Assure function bails out if there is no timezone name available for the system.
    tz_name = pv_forecast_instance._tz_name
    pv_forecast_instance._tz_name = None
    with pytest.raises(Exception) as exc_info:
        forecast_temps = pv_forecast_instance.get_temperature_for_date_range(
            sample_forecast_start, end_date
        )
    pv_forecast_instance._tz_name = tz_name
    assert (
        exc_info.value.args[0] == "Processing without PV system timezone info ist not implemented!"
    )


def test_get_forecast_for_date_range(pv_forecast_instance, sample_forecast_start):
    """Test fetching AC power forecast for a specific date range."""
    end_date = sample_forecast_start + timedelta(hours=24)
    forecast = pv_forecast_instance.get_pv_forecast_for_date_range(sample_forecast_start, end_date)
    assert len(forecast) == 48
    assert forecast[0] == 0.0
    assert forecast[1] == 0.0
    assert forecast[2] == 0.0

    # Assure function bails out if there is no timezone name available for the system.
    tz_name = pv_forecast_instance._tz_name
    pv_forecast_instance._tz_name = None
    with pytest.raises(Exception) as exc_info:
        forecast = pv_forecast_instance.get_pv_forecast_for_date_range(
            sample_forecast_start, end_date
        )
    pv_forecast_instance._tz_name = tz_name
    assert (
        exc_info.value.args[0] == "Processing without PV system timezone info ist not implemented!"
    )


def test_get_forecast_dataframe(pv_forecast_instance):
    """Test converting forecast data to a DataFrame."""
    df = pv_forecast_instance.get_forecast_dataframe()
    assert len(df) == 288
    assert list(df.columns) == ["date_time", "dc_power", "ac_power", "windspeed_10m", "temperature"]
    assert df.iloc[0]["dc_power"] == 0.0
    assert df.iloc[1]["ac_power"] == 0.0
    assert df.iloc[2]["temperature"] == 6.0


def test_load_data_from_file(server, pv_forecast_empty_instance):
    """Test loading data from file."""
    # load from valid address file path
    filepath = FILE_TESTDATA_PV_FORECAST_INPUT_1
    data = pv_forecast_empty_instance.load_data_from_file(filepath)
    assert len(data) > 0


def test_load_data_from_url(server, pv_forecast_empty_instance):
    """Test loading data from url."""
    # load from valid address of our server
    url = f"{server}/gesamtlast_simple?year_energy=2000&"
    data = pv_forecast_empty_instance.load_data_from_url(url)
    assert len(data) > 0

    # load from invalid address of our server
    url = f"{server}/invalid?"
    data = pv_forecast_empty_instance.load_data_from_url(url)
    assert data == f"Failed to load data from `{url}`. Status Code: 404"


def test_load_data_from_url_with_caching(
    server, pv_forecast_empty_instance, sample_forecast_data, sample_forecast_start
):
    """Test loading data from url with cache."""
    # load from valid address of our server
    url = f"{server}/gesamtlast_simple?year_energy=2000&"
    data = pv_forecast_empty_instance.load_data_from_url_with_caching(url)
    assert len(data) > 0

    # load from invalid address of our server
    url = f"{server}/invalid?"
    data = pv_forecast_empty_instance.load_data_from_url_with_caching(url)
    assert data == f"Failed to load data from `{url}`. Status Code: 404"


def test_report_ac_power_and_measurement(pv_forecast_instance, sample_forecast_report):
    """Test reporting."""
    report = pv_forecast_instance.report_ac_power_and_measurement()
    assert report == sample_forecast_report


@pytest.mark.skipif(
    sys.platform.startswith("win"), reason="'other_timezone' fixture not supported on Windows"
)
def test_timezone_behaviour(
    pv_forecast_instance, sample_forecast_report, sample_forecast_start, other_timezone
):
    """Test PVForecast in another timezone."""
    current_time = datetime.now()

    # Test updating AC power measurement for a specific date.
    date_time = pv_forecast_instance.get_forecast_start()
    assert date_time == sample_forecast_start
    updated = pv_forecast_instance.update_ac_power_measurement(date_time, 1000)
    assert updated is True
    forecast_data = pv_forecast_instance.get_forecast_data()
    assert forecast_data[0].ac_power_measurement == 1000

    # Test fetching temperature forecast for a specific date.
    forecast_temps = pv_forecast_instance.get_temperature_forecast_for_date(sample_forecast_start)
    assert len(forecast_temps) == 24
    assert forecast_temps[0] == 7.0
    assert forecast_temps[1] == 6.5
    assert forecast_temps[2] == 6.0

    # Test fetching AC power forecast
    end_date = sample_forecast_start + timedelta(hours=24)
    forecast = pv_forecast_instance.get_pv_forecast_for_date_range(sample_forecast_start, end_date)
    assert len(forecast) == 48
    assert forecast[0] == 1000.0  # changed before
    assert forecast[1] == 0.0
    assert forecast[2] == 0.0
