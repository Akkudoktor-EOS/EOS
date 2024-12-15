import json
from pathlib import Path

import pytest

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.prediction.weatherimport import WeatherImport
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_WEATHERIMPORT_1_JSON = DIR_TESTDATA.joinpath("import_input_1.json")

config_eos = get_config()
ems_eos = get_ems()


@pytest.fixture
def weather_provider(reset_config, sample_import_1_json):
    """Fixture to create a WeatherProvider instance."""
    settings = {
        "weather_provider": "WeatherImport",
        "weatherimport_file_path": str(FILE_TESTDATA_WEATHERIMPORT_1_JSON),
        "weatherimport_json": json.dumps(sample_import_1_json),
    }
    config_eos.merge_settings_from_dict(settings)
    provider = WeatherImport()
    assert provider.enabled() == True
    return provider


@pytest.fixture
def sample_import_1_json():
    """Fixture that returns sample forecast data report."""
    with open(FILE_TESTDATA_WEATHERIMPORT_1_JSON, "r") as f_res:
        input_data = json.load(f_res)
    return input_data


# ------------------------------------------------
# General forecast
# ------------------------------------------------


def test_singleton_instance(weather_provider):
    """Test that WeatherForecast behaves as a singleton."""
    another_instance = WeatherImport()
    assert weather_provider is another_instance


def test_invalid_provider(weather_provider, monkeypatch):
    """Test requesting an unsupported weather_provider."""
    settings = {
        "weather_provider": "<invalid>",
        "weatherimport_file_path": str(FILE_TESTDATA_WEATHERIMPORT_1_JSON),
    }
    config_eos.merge_settings_from_dict(settings)
    assert weather_provider.enabled() == False


# ------------------------------------------------
# Import
# ------------------------------------------------


@pytest.mark.parametrize(
    "start_datetime, from_file",
    [
        ("2024-11-10 00:00:00", True),  # No DST in Germany
        ("2024-08-10 00:00:00", True),  # DST in Germany
        ("2024-03-31 00:00:00", True),  # DST change in Germany (23 hours/ day)
        ("2024-10-27 00:00:00", True),  # DST change in Germany (25 hours/ day)
        ("2024-11-10 00:00:00", False),  # No DST in Germany
        ("2024-08-10 00:00:00", False),  # DST in Germany
        ("2024-03-31 00:00:00", False),  # DST change in Germany (23 hours/ day)
        ("2024-10-27 00:00:00", False),  # DST change in Germany (25 hours/ day)
    ],
)
def test_import(weather_provider, sample_import_1_json, start_datetime, from_file):
    """Test fetching forecast from Import."""
    ems_eos.set_start_datetime(to_datetime(start_datetime, in_timezone="Europe/Berlin"))
    if from_file:
        config_eos.weatherimport_json = None
        assert config_eos.weatherimport_json is None
    else:
        config_eos.weatherimport_file_path = None
        assert config_eos.weatherimport_file_path is None
    weather_provider.clear()

    # Call the method
    weather_provider.update_data()

    # Assert: Verify the result is as expected
    assert weather_provider.start_datetime is not None
    assert weather_provider.total_hours is not None
    assert compare_datetimes(weather_provider.start_datetime, ems_eos.start_datetime).equal
    values = sample_import_1_json["weather_temp_air"]
    value_datetime_mapping = weather_provider.import_datetimes(len(values))
    for i, mapping in enumerate(value_datetime_mapping):
        assert i < len(weather_provider.records)
        expected_datetime, expected_value_index = mapping
        expected_value = values[expected_value_index]
        result_datetime = weather_provider.records[i].date_time
        result_value = weather_provider.records[i]["weather_temp_air"]

        # print(f"{i}: Expected: {expected_datetime}:{expected_value}")
        # print(f"{i}:   Result: {result_datetime}:{result_value}")
        assert compare_datetimes(result_datetime, expected_datetime).equal
        assert result_value == expected_value
