import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pvlib
import pytest
from bs4 import BeautifulSoup

from akkudoktoreos.core.ems import get_ems
from akkudoktoreos.prediction.weatherclearoutside import WeatherClearOutside
from akkudoktoreos.utils.cacheutil import CacheFileStore
from akkudoktoreos.utils.datetimeutil import compare_datetimes, to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_WEATHERCLEAROUTSIDE_1_HTML = DIR_TESTDATA.joinpath("weatherforecast_clearout_1.html")
FILE_TESTDATA_WEATHERCLEAROUTSIDE_1_DATA = DIR_TESTDATA.joinpath("weatherforecast_clearout_1.json")


@pytest.fixture
def weather_provider(config_eos):
    """Fixture to create a WeatherProvider instance."""
    settings = {
        "weather": {
            "weather_provider": "ClearOutside",
        },
        "prediction": {
            "latitude": 50.0,
            "longitude": 10.0,
        },
    }
    config_eos.merge_settings_from_dict(settings)
    return WeatherClearOutside()


@pytest.fixture
def sample_clearout_1_html():
    """Fixture that returns sample forecast data report."""
    with open(FILE_TESTDATA_WEATHERCLEAROUTSIDE_1_HTML, "r") as f_res:
        input_data = f_res.read()
    return input_data


@pytest.fixture
def sample_clearout_1_data():
    """Fixture that returns sample forecast data."""
    with open(FILE_TESTDATA_WEATHERCLEAROUTSIDE_1_DATA, "r", encoding="utf8") as f_in:
        json_str = f_in.read()
        data = WeatherClearOutside.from_json(json_str)
    return data


@pytest.fixture
def cache_store():
    """A pytest fixture that creates a new CacheFileStore instance for testing."""
    return CacheFileStore()


# ------------------------------------------------
# General WeatherProvider
# ------------------------------------------------


def test_singleton_instance(weather_provider):
    """Test that WeatherForecast behaves as a singleton."""
    another_instance = WeatherClearOutside()
    assert weather_provider is another_instance


def test_invalid_provider(weather_provider, config_eos):
    """Test requesting an unsupported weather_provider."""
    settings = {
        "weather": {
            "weather_provider": "<invalid>",
        }
    }
    config_eos.merge_settings_from_dict(settings)
    assert not weather_provider.enabled()


def test_invalid_coordinates(weather_provider, config_eos):
    """Test invalid coordinates raise ValueError."""
    settings = {
        "weather": {
            "weather_provider": "ClearOutside",
        },
        "prediction": {
            "latitude": 1000.0,
            "longitude": 1000.0,
        },
    }
    with pytest.raises(
        ValueError,  # match="Latitude '1000' and/ or longitude `1000` out of valid range."
    ):
        config_eos.merge_settings_from_dict(settings)


# ------------------------------------------------
# Irradiance caclulation
# ------------------------------------------------


def test_irridiance_estimate_from_cloud_cover(weather_provider):
    """Test cloud cover to irradiance estimation."""
    cloud_cover_data = pd.Series(
        data=[20, 50, 80], index=pd.date_range("2023-10-22", periods=3, freq="h")
    )

    ghi, dni, dhi = weather_provider.estimate_irradiance_from_cloud_cover(
        50.0, 10.0, cloud_cover_data
    )

    assert ghi == [0, 0, 0]
    assert dhi == [0, 0, 0]
    assert dni == [0, 0, 0]


# ------------------------------------------------
# ClearOutside
# ------------------------------------------------


@patch("requests.get")
def test_request_forecast(mock_get, weather_provider, sample_clearout_1_html, config_eos):
    """Test fetching forecast from ClearOutside."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = sample_clearout_1_html
    mock_get.return_value = mock_response

    # Preset, as this is usually done by update()
    config_eos.update()

    # Test function
    response = weather_provider._request_forecast()

    assert response.status_code == 200
    assert response.content == sample_clearout_1_html


@patch("requests.get")
def test_update_data(mock_get, weather_provider, sample_clearout_1_html, sample_clearout_1_data):
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = sample_clearout_1_html
    mock_get.return_value = mock_response

    expected_start = to_datetime("2024-10-26 00:00:00", in_timezone="Europe/Berlin")
    expected_end = to_datetime("2024-10-28 00:00:00", in_timezone="Europe/Berlin")
    expected_keep = to_datetime("2024-10-24 00:00:00", in_timezone="Europe/Berlin")

    # Call the method
    ems_eos = get_ems()
    ems_eos.set_start_datetime(expected_start)
    weather_provider.update_data()

    # Check for correct prediction time window
    assert weather_provider.config.prediction.prediction_hours == 48
    assert weather_provider.config.prediction.prediction_historic_hours == 48
    assert compare_datetimes(weather_provider.start_datetime, expected_start).equal
    assert compare_datetimes(weather_provider.end_datetime, expected_end).equal
    assert compare_datetimes(weather_provider.keep_datetime, expected_keep).equal

    # Verify the data
    assert len(weather_provider) == 165  # 6 days, 24 hours per day - 7th day 21 hours

    # Check that specific values match the expected output
    # for i, record in enumerate(weather_data.records):
    #    # Compare datetime and specific values
    #    assert record.datetime == sample_clearout_1_data.records[i].datetime
    #    assert record.data['total_clouds'] == sample_clearout_1_data.records[i].data['total_clouds']
    #    # Check additional weather attributes as necessary


@pytest.mark.skip(reason="Test fixture to be improved")
@patch("requests.get")
def test_cache_forecast(mock_get, weather_provider, sample_clearout_1_html, cache_store):
    """Test that ClearOutside forecast data is cached with TTL.

    This can not be tested with mock_get. Mock objects are not pickable and therefor can not be
    cached to a file. Keep it for documentation.
    """
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = sample_clearout_1_html
    mock_get.return_value = mock_response

    cache_store.clear(clear_all=True)

    weather_provider.update_data()
    mock_get.assert_called_once()
    forecast_data_first = weather_provider.to_json()

    weather_provider.update_data()
    forecast_data_second = weather_provider.to_json()
    # Verify that cache returns the same object without calling the method again
    assert forecast_data_first == forecast_data_second
    # A mock object is not pickable and therefor can not be chached to file
    assert mock_get.call_count == 2


# ------------------------------------------------
# Development ClearOutside
# ------------------------------------------------


@pytest.mark.skip(reason="For development only")
@patch("requests.get")
def test_development_forecast_data(mock_get, weather_provider, sample_clearout_1_html):
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = sample_clearout_1_html
    mock_get.return_value = mock_response

    # Fill the instance
    weather_provider.update_data(force_enable=True)

    with open(FILE_TESTDATA_WEATHERCLEAROUTSIDE_1_DATA, "w", encoding="utf8") as f_out:
        f_out.write(weather_provider.to_json())


@pytest.mark.skip(reason="For development only")
def test_clearoutsides_development_scraper(weather_provider, sample_clearout_1_html):
    """Test scraping from ClearOutside."""
    soup = BeautifulSoup(sample_clearout_1_html, "html.parser")

    # Sample was created for the loacation
    lat = 50.0
    lon = 10.0

    # Find generation data
    p_generated = soup.find("h2", string=lambda text: text and text.startswith("Generated:"))
    assert p_generated is not None

    # Extract forecast start and end dates
    forecast_pattern = r"Forecast: (\d{2}/\d{2}/\d{2}) to (\d{2}/\d{2}/\d{2})"
    forecast_match = re.search(forecast_pattern, p_generated.get_text())
    if forecast_match:
        forecast_start_date = forecast_match.group(1)
        forecast_end_date = forecast_match.group(2)
    else:
        assert False
    assert forecast_start_date == "26/10/24"
    assert forecast_end_date == "01/11/24"

    # Extract timezone offset
    timezone_pattern = r"Timezone: UTC([+-]\d+)\.(\d+)"
    timezone_match = re.search(timezone_pattern, p_generated.get_text())
    if timezone_match:
        hours = int(timezone_match.group(1))
        assert hours == 2
        # Convert the decimal part to minutes (e.g., .50 -> 30 minutes)
        minutes = int(timezone_match.group(2)) * 6  # Multiply by 6 to convert to minutes
        assert minutes == 0

        # Create the timezone object using timedelta for the offset
        forecast_timezone = timezone(timedelta(hours=hours, minutes=minutes))
    else:
        assert False

    forecast_start_datetime = to_datetime(
        forecast_start_date, in_timezone=forecast_timezone, to_naiv=False, to_maxtime=False
    )
    assert forecast_start_datetime == datetime(2024, 10, 26, 0, 0)

    # Find all paragraphs with id 'day_<x>'. There should be seven.
    p_days = soup.find_all(id=re.compile(r"day_[0-9]"))
    assert len(p_days) == 7
    p_day = p_days[0]

    # Within day_x paragraph find the details labels
    p_detail_labels = p_day.find_all(class_="fc_detail_label")
    detail_names = [p.get_text() for p in p_detail_labels]

    assert detail_names == [
        "Total Clouds (% Sky Obscured)",
        "Low Clouds (% Sky Obscured)",
        "Medium Clouds (% Sky Obscured)",
        "High Clouds (% Sky Obscured)",
        "ISS Passover",
        "Visibility (miles)",
        "Fog (%)",
        "Precipitation Type",
        "Precipitation Probability (%)",
        "Precipitation Amount (mm)",
        "Wind Speed/Direction (mph)",
        "Chance of Frost",
        "Temperature (°C)",
        "Feels Like (°C)",
        "Dew Point (°C)",
        "Relative Humidity (%)",
        "Pressure (mb)",
        "Ozone (du)",
    ]

    # Find all the paragraphs that are associated to the details.
    # Beware there is one ul paragraph before that is not associated to a detail
    p_detail_tables = p_day.find_all("ul")
    assert len(p_detail_tables) == len(detail_names) + 1
    p_detail_tables.pop(0)

    # Create clearout data
    clearout_data = {}
    # Add data values
    for i, detail_name in enumerate(detail_names):
        p_detail_values = p_detail_tables[i].find_all("li")
        detail_data = []
        for p_detail_value in p_detail_values:
            if (
                detail_name in ("Precipitation Type", "Chance of Frost")
                and hasattr(p_detail_value, "title")
                and p_detail_value.title
            ):
                value_str = p_detail_value.title.string
            else:
                value_str = p_detail_value.get_text()
            try:
                value = float(value_str)
            except ValueError:
                value = value_str
            detail_data.append(value)
        assert len(detail_data) == 24
        clearout_data[detail_name] = detail_data

    assert clearout_data["Temperature (°C)"] == [
        14.0,
        14.0,
        13.0,
        12.0,
        11.0,
        11.0,
        10.0,
        10.0,
        9.0,
        9.0,
        9.0,
        9.0,
        9.0,
        10.0,
        9.0,
        9.0,
        10.0,
        11.0,
        13.0,
        14.0,
        15.0,
        16.0,
        16.0,
        16.0,
    ]
    assert clearout_data["Relative Humidity (%)"] == [
        59.0,
        68.0,
        75.0,
        81.0,
        84.0,
        85.0,
        85.0,
        91.0,
        91.0,
        93.0,
        93.0,
        93.0,
        93.0,
        93.0,
        95.0,
        95.0,
        93.0,
        87.0,
        81.0,
        76.0,
        70.0,
        66.0,
        66.0,
        69.0,
    ]
    assert clearout_data["Wind Speed/Direction (mph)"] == [
        7.0,
        6.0,
        4.0,
        4.0,
        4.0,
        4.0,
        4.0,
        4.0,
        3.0,
        3.0,
        3.0,
        2.0,
        1.0,
        1.0,
        1.0,
        2.0,
        2.0,
        2.0,
        4.0,
        5.0,
        6.0,
        6.0,
        5.0,
        5.0,
    ]

    # Add datetimes of the scrapped data
    clearout_data["DateTime"] = [forecast_start_datetime + timedelta(hours=i) for i in range(24)]
    detail_names.append("DateTime")

    assert len(clearout_data["DateTime"]) == 24
    assert clearout_data["DateTime"][0] == to_datetime(
        "2024-10-26 00:00:00", in_timezone=forecast_timezone
    )
    assert clearout_data["DateTime"][23] == to_datetime(
        "2024-10-26 23:00:00", in_timezone=forecast_timezone
    )

    # Converting the cloud cover into Global Horizontal Irradiance (GHI) with a PVLib method
    offset = 35  # The default
    offset_fraction = offset / 100.0  # Adjust percentage to scaling factor
    cloud_cover = pd.Series(clearout_data["Total Clouds (% Sky Obscured)"])

    # Convert datetime list to a pandas DatetimeIndex
    cloud_cover_times = pd.DatetimeIndex(clearout_data["DateTime"])

    # Create a location object
    location = pvlib.location.Location(latitude=lat, longitude=lon)

    # Get solar position and clear-sky GHI using the Ineichen model
    solpos = location.get_solarposition(cloud_cover_times)
    clear_sky = location.get_clearsky(cloud_cover_times, model="ineichen")

    # Convert cloud cover percentage to a scaling factor
    cloud_cover_fraction = np.array(cloud_cover) / 100.0

    # Calculate adjusted GHI with proportional offset adjustment
    adjusted_ghi = clear_sky["ghi"] * (
        offset_fraction + (1 - offset_fraction) * (1 - cloud_cover_fraction)
    )
    adjusted_ghi.fillna(0.0, inplace=True)

    # Apply DISC model to estimate Direct Normal Irradiance (DNI) from adjusted GHI
    disc_output = pvlib.irradiance.disc(adjusted_ghi, solpos["zenith"], cloud_cover_times)
    adjusted_dni = disc_output["dni"]
    adjusted_dni.fillna(0.0, inplace=True)

    # Calculate Diffuse Horizontal Irradiance (DHI) as DHI = GHI - DNI * cos(zenith)
    zenith_rad = np.radians(solpos["zenith"])
    adjusted_dhi = adjusted_ghi - adjusted_dni * np.cos(zenith_rad)
    adjusted_dhi.fillna(0.0, inplace=True)

    # Add GHI, DNI, DHI to clearout data
    clearout_data["Global Horizontal Irradiance (W/m2)"] = adjusted_ghi.to_list()
    detail_names.append("Global Horizontal Irradiance (W/m2)")
    clearout_data["Direct Normal Irradiance (W/m2)"] = adjusted_dni.to_list()
    detail_names.append("Direct Normal Irradiance (W/m2)")
    clearout_data["Diffuse Horizontal Irradiance (W/m2)"] = adjusted_dhi.to_list()
    detail_names.append("Diffuse Horizontal Irradiance (W/m2)")

    assert clearout_data["Global Horizontal Irradiance (W/m2)"] == [
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        24.291000436601216,
        85.88494154645998,
        136.09269403109946,
        139.26925350542064,
        146.7174434892616,
        149.0167479382964,
        138.97458866666065,
        103.47132353697396,
        46.81279774519421,
        0.12972168074047014,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    ]
    assert clearout_data["Direct Normal Irradiance (W/m2)"] == [
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        10.19687368654253,
        0.0,
        0.0,
        2.9434862632289804,
        9.621272744657047,
        9.384995789935898,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    ]
    assert clearout_data["Diffuse Horizontal Irradiance (W/m2)"] == [
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        24.291000436601216,
        85.88494154645998,
        132.32210426501337,
        139.26925350542064,
        146.7174434892616,
        147.721968406295,
        135.32240392326145,
        100.82522311704261,
        46.81279774519421,
        0.12972168074047014,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    ]

    # Preciptable Water (PWAT) with a PVLib method
    clearout_data["Preciptable Water (cm)"] = pvlib.atmosphere.gueymard94_pw(
        pd.Series(data=clearout_data["Temperature (°C)"]),
        pd.Series(data=clearout_data["Relative Humidity (%)"]),
    ).to_list()
    detail_names.append("Preciptable Water (cm)")

    assert clearout_data["Preciptable Water (cm)"] == [
        1.5345406562673334,
        1.7686231292572652,
        1.8354895631381385,
        1.8651290310892348,
        1.8197998755611786,
        1.8414641597940502,
        1.7325709431177607,
        1.8548700685143087,
        1.7453005409540279,
        1.783658794601369,
        1.783658794601369,
        1.783658794601369,
        1.783658794601369,
        1.8956364436464912,
        1.8220170482487101,
        1.8220170482487101,
        1.8956364436464912,
        1.8847927282597918,
        1.9823287281891897,
        1.9766964385816497,
        1.9346943880237457,
        1.9381315133101413,
        1.9381315133101413,
        2.026228400278784,
    ]
