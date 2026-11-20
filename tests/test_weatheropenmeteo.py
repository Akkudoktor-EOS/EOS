import copy
import json
from pathlib import Path
from typing import Any, Callable
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pytest
from loguru import logger

from akkudoktoreos.core.cache import CacheFileStore
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.weatheropenmeteo import WeatherOpenMeteo
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")

FILE_TESTDATA_WEATHEROPENMETEO_1_JSON = DIR_TESTDATA.joinpath("weatherforecast_openmeteo_1.json")
FILE_TESTDATA_WEATHEROPENMETEO_2_JSON = DIR_TESTDATA.joinpath("weatherforecast_openmeteo_2.json")


@pytest.fixture
def provider(monkeypatch):
    """Fixture to create a WeatherProvider instance."""
    monkeypatch.setenv("EOS_WEATHER__WEATHER_PROVIDER", "OpenMeteo")
    monkeypatch.setenv("EOS_GENERAL__LATITUDE", "50.0")
    monkeypatch.setenv("EOS_GENERAL__LONGITUDE", "10.0")
    return WeatherOpenMeteo()


@pytest.fixture
def sample_openmeteo_1_json():
    """Fixture that returns sample forecast data report from Open-Meteo."""
    with FILE_TESTDATA_WEATHEROPENMETEO_1_JSON.open("r", encoding="utf-8", newline=None) as f_res:
        input_data = json.load(f_res)
    return input_data


@pytest.fixture
def sample_openmeteo_2_json():
    """Fixture that returns sample processed forecast data from Open-Meteo."""
    with FILE_TESTDATA_WEATHEROPENMETEO_2_JSON.open("r", encoding="utf-8", newline=None) as f_res:
        input_data = json.load(f_res)
    return input_data


def clear_sky_openmeteo_json(openmeteo_json: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of an Open-Meteo response with clear-sky irradiance.

    The radiation variables follow the Open-Meteo API: the plain variables are means
    over the preceding hour, the ``*_instant`` variables the irradiance at the indicated
    time. ``direct_radiation`` is the beam on the horizontal plane,
    ``direct_normal_irradiance`` the beam on the plane normal to the sun. The values
    are pvlib clear-sky irradiance at one-minute resolution.

    Args:
        openmeteo_json: An Open-Meteo forecast response.

    Returns:
        A copy of the response whose radiation variables are clear-sky irradiance.
    """
    import pvlib

    data = copy.deepcopy(openmeteo_json)
    hourly = data["hourly"]
    stamps = pd.DatetimeIndex(hourly["time"], tz=data["timezone"])
    location = pvlib.location.Location(data["latitude"], data["longitude"])
    minutes = pd.date_range(stamps[0] - to_duration("1 hour"), stamps[-1], freq="1min")
    irradiance = location.get_clearsky(minutes, model="ineichen")
    zenith = location.get_solarposition(minutes)["apparent_zenith"]
    irradiance["bhi"] = irradiance["dni"] * np.cos(np.radians(zenith)).clip(lower=0.0)
    preceding_hour_mean = irradiance.rolling("1h", closed="left").mean().reindex(stamps)
    instant = irradiance.reindex(stamps)
    for openmeteo_key, column in (
        ("shortwave_radiation", "ghi"),
        ("direct_radiation", "bhi"),
        ("diffuse_radiation", "dhi"),
        ("direct_normal_irradiance", "dni"),
    ):
        hourly[openmeteo_key] = preceding_hour_mean[column].round(1).tolist()
        hourly[f"{openmeteo_key}_instant"] = instant[column].round(1).tolist()
    return data


@pytest.fixture
def sample_openmeteo_clear_sky_json(sample_openmeteo_1_json: dict[str, Any]) -> dict[str, Any]:
    """Fixture that returns the sample forecast with clear-sky irradiance."""
    return clear_sky_openmeteo_json(sample_openmeteo_1_json)


@pytest.fixture
def cache_store():
    """A pytest fixture that creates a new CacheFileStore instance for testing."""
    return CacheFileStore()


# ------------------------------------------------
# General forecast
# ------------------------------------------------


def test_singleton_instance(provider):
    """Test that WeatherForecast behaves as a singleton."""
    another_instance = WeatherOpenMeteo()
    assert provider is another_instance


def test_invalid_provider(provider, monkeypatch):
    """Test requesting an unsupported provider."""
    monkeypatch.setenv("EOS_WEATHER__WEATHER_PROVIDER", "<invalid>")
    provider.config.reset_settings()
    assert not provider.enabled()


def test_invalid_coordinates(provider, monkeypatch):
    """Test invalid coordinates raise ValueError."""
    monkeypatch.setenv("EOS_GENERAL__LATITUDE", "1000")
    monkeypatch.setenv("EOS_GENERAL__LONGITUDE", "1000")
    with pytest.raises(
        ValueError,  # match="Latitude '1000' and/ or longitude `1000` out of valid range."
    ):
        provider.config.reset_settings()


# ------------------------------------------------
# Irradiance calculation
# ------------------------------------------------


def test_irridiance_estimate_from_cloud_cover(provider):
    """Test cloud cover to irradiance estimation (fallback method)."""
    cloud_cover_data = pd.Series(
        data=[20, 50, 80], index=pd.date_range("2023-10-22", periods=3, freq="h")
    )

    ghi, dni, dhi = provider.estimate_irradiance_from_cloud_cover(50.0, 10.0, cloud_cover_data)

    # This is just the fallback method - Open-Meteo normally provides direct values
    assert len(ghi) == 3
    assert len(dni) == 3
    assert len(dhi) == 3
    # Values should be floats (actual values depend on the algorithm)
    assert all(isinstance(x, float) for x in ghi)
    assert all(isinstance(x, float) for x in dni)
    assert all(isinstance(x, float) for x in dhi)


# ------------------------------------------------
# Open-Meteo
# ------------------------------------------------


@patch("requests.get")
def test_request_forecast(mock_get, provider, sample_openmeteo_1_json):
    """Test requesting forecast from Open-Meteo."""
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = sample_openmeteo_1_json
    mock_response.content = json.dumps(sample_openmeteo_1_json)
    mock_get.return_value = mock_response

    # Test function
    openmeteo_data = provider._request_forecast()

    # Verify API was called with correct parameters
    mock_get.assert_called_once()
    call_args = mock_get.call_args
    assert call_args[0][0] == "https://api.open-meteo.com/v1/forecast"
    assert "latitude" in call_args[1]["params"]
    assert "longitude" in call_args[1]["params"]
    assert "hourly" in call_args[1]["params"]

    # Verify returned data structure
    assert isinstance(openmeteo_data, dict)
    assert "hourly" in openmeteo_data
    assert "time" in openmeteo_data["hourly"]
    assert "temperature_2m" in openmeteo_data["hourly"]
    assert "shortwave_radiation" in openmeteo_data["hourly"]
    assert "direct_radiation" in openmeteo_data["hourly"]
    assert "diffuse_radiation" in openmeteo_data["hourly"]


@pytest.mark.asyncio
@pytest.mark.parametrize("host_timezone", ["UTC", "Europe/Berlin"])
@patch("requests.get")
async def test_update_data(
    mock_get: Mock,
    provider: WeatherOpenMeteo,
    sample_openmeteo_clear_sky_json: dict[str, Any],
    cache_store: CacheFileStore,
    set_other_timezone: Callable[[str], str],
    host_timezone: str,
) -> None:
    """Map each forecast hour in the provider timezone, regardless of the host timezone."""
    set_other_timezone(host_timezone)
    # Mock response object
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = sample_openmeteo_clear_sky_json
    mock_response.content = json.dumps(sample_openmeteo_clear_sky_json)
    mock_get.return_value = mock_response

    cache_store.clear(clear_all=True)

    # Call the method
    ems_eos = get_ems()
    start_datetime = to_datetime("2026-03-02 09:00:00+01:00", in_timezone="Europe/Berlin")
    ems_eos.set_start_datetime(start_datetime)
    await provider.update_data(force_enable=True, force_update=True)

    # Assert: Verify the result is as expected
    mock_get.assert_called_once()
    assert len(provider) > 0

    # Open-Meteo returns local wall times without offsets or seconds. Build the
    # expected instants independently of to_datetime, including midnight boundaries.
    hourly = sample_openmeteo_clear_sky_json["hourly"]
    expected_datetimes = pd.DatetimeIndex(hourly["time"], tz="Europe/Berlin").to_pydatetime()
    for source_key, record_key in (
        ("shortwave_radiation_instant", "weather_ghi"),
        ("direct_normal_irradiance_instant", "weather_dni"),
        ("diffuse_radiation_instant", "weather_dhi"),
    ):
        datetimes, values = await provider.key_to_lists(record_key)
        assert list(datetimes) == list(expected_datetimes)
        assert values == hourly[source_key]


# ------------------------------------------------
# Test specific Open-Meteo features
# ------------------------------------------------


def test_openmeteo_radiation_mapping(provider):
    """Test that radiation values are correctly mapped from Open-Meteo keys."""
    # Verify mapping contains the radiation fields
    from akkudoktoreos.prediction.weatheropenmeteo import WeatherDataOpenMeteoMapping

    descriptions = {key: desc for key, desc, _ in WeatherDataOpenMeteoMapping}

    # Irradiance at the indicated time; DNI is the beam on the plane normal to the sun.
    assert descriptions["shortwave_radiation_instant"] == "Global Horizontal Irradiance (W/m2)"
    assert descriptions["direct_normal_irradiance_instant"] == "Direct Normal Irradiance (W/m2)"
    assert descriptions["diffuse_radiation_instant"] == "Diffuse Horizontal Irradiance (W/m2)"

    # Preceding-hour means and the beam on the horizontal plane are not mapped.
    for key in (
        "shortwave_radiation",
        "direct_radiation",
        "diffuse_radiation",
        "direct_normal_irradiance",
        "direct_radiation_instant",
    ):
        assert descriptions[key] is None


class TestOpenMeteoIrradiance:
    """Irradiance describes the record time, DNI the beam on the plane normal to the sun."""

    @staticmethod
    def _response(openmeteo_json: dict[str, Any]) -> Mock:
        """Return a mocked HTTP response carrying an Open-Meteo response."""
        response = Mock()
        response.status_code = 200
        response.json.return_value = openmeteo_json
        response.content = json.dumps(openmeteo_json)
        return response

    @classmethod
    async def _update(cls, provider: WeatherOpenMeteo, openmeteo_json: dict[str, Any]) -> None:
        """Update the provider from an Open-Meteo response, without network access."""
        CacheFileStore().clear(clear_all=True)
        get_ems().set_start_datetime(
            to_datetime("2026-03-02 09:00:00+01:00", in_timezone="Europe/Berlin")
        )
        with patch("requests.get", return_value=cls._response(openmeteo_json)):
            await provider.update_data(force_enable=True, force_update=True)

    @staticmethod
    async def _stored(provider: WeatherOpenMeteo, key: str) -> pd.Series:
        """Return the stored values of a key, indexed by UTC timestamps."""
        series = await provider.key_to_raw_series(key)
        index = pd.DatetimeIndex([pd.Timestamp(stamp.isoformat()) for stamp in series.index])
        return pd.Series(series.to_numpy(dtype=float), index=index.tz_convert("UTC"))

    def test_requests_instantaneous_irradiance(
        self, provider: WeatherOpenMeteo, sample_openmeteo_1_json: dict[str, Any]
    ) -> None:
        """Request GHI, DNI and DHI at the indicated time, DNI on the normal plane."""
        response = self._response(sample_openmeteo_1_json)
        with patch("requests.get", return_value=response) as mock_get:
            provider._request_forecast(force_update=True)  # type: ignore[call-arg]

        requested = mock_get.call_args[1]["params"]["hourly"]
        for openmeteo_key in (
            "shortwave_radiation_instant",
            "direct_normal_irradiance_instant",
            "diffuse_radiation_instant",
        ):
            assert openmeteo_key in requested

    @pytest.mark.asyncio
    async def test_dni_is_beam_on_plane_normal_to_sun(
        self,
        provider: WeatherOpenMeteo,
        sample_openmeteo_clear_sky_json: dict[str, Any],
    ) -> None:
        """Stored GHI, DNI and DHI close as GHI = DHI + DNI * cos(zenith)."""
        import pvlib

        await self._update(provider, sample_openmeteo_clear_sky_json)
        ghi = await self._stored(provider, "weather_ghi")
        dni = await self._stored(provider, "weather_dni")
        dhi = await self._stored(provider, "weather_dhi")

        location = pvlib.location.Location(
            sample_openmeteo_clear_sky_json["latitude"],
            sample_openmeteo_clear_sky_json["longitude"],
        )
        zenith = location.get_solarposition(ghi.index)["apparent_zenith"]
        daylight = zenith < 70.0
        assert daylight.sum() >= 6
        closure = dhi + dni * np.cos(np.radians(zenith))
        assert closure[daylight].to_list() == pytest.approx(ghi[daylight].to_list(), abs=2.0)

    @pytest.mark.asyncio
    async def test_irradiance_is_value_at_record_time(
        self,
        provider: WeatherOpenMeteo,
        sample_openmeteo_clear_sky_json: dict[str, Any],
    ) -> None:
        """A record holds the irradiance at its time, not the mean of the preceding hour."""
        import pvlib

        await self._update(provider, sample_openmeteo_clear_sky_json)
        ghi = await self._stored(provider, "weather_ghi")

        location = pvlib.location.Location(
            sample_openmeteo_clear_sky_json["latitude"],
            sample_openmeteo_clear_sky_json["longitude"],
        )
        clear_sky_ghi = location.get_clearsky(ghi.index, model="ineichen")["ghi"]

        # Late morning and noon: the value at the stamp, not the lower preceding-hour mean.
        for stamp in ("2026-03-02 11:00:00+01:00", "2026-03-02 12:00:00+01:00"):
            moment = to_datetime(stamp)
            assert ghi[moment] == pytest.approx(clear_sky_ghi[moment], rel=0.01)

        # After sunset there is no irradiance, although the preceding hour had sun.
        stamps = pd.DatetimeIndex(ghi.index)
        elevation = location.get_solarposition(stamps)["apparent_elevation"].to_numpy()
        elevation_hour_before = location.get_solarposition(stamps - to_duration("1 hour"))[
            "apparent_elevation"
        ].to_numpy()
        after_sunset = (elevation < 0.0) & (elevation_hour_before > 0.0)
        assert after_sunset.sum() > 0
        assert ghi[after_sunset].max() == pytest.approx(0.0, abs=0.5)


def test_openmeteo_unit_conversions(provider):
    """Test that unit conversions are correctly applied."""
    from akkudoktoreos.prediction.weatheropenmeteo import WeatherDataOpenMeteoMapping

    # Check wind speed conversion (m/s to km/h)
    wind_speed_mapping = next(item for item in WeatherDataOpenMeteoMapping
                             if item[0] == 'wind_speed_10m')
    assert wind_speed_mapping[2] == 3.6  # Conversion factor

    # Check pressure conversion (Pa to hPa)
    pressure_mapping = next(item for item in WeatherDataOpenMeteoMapping
                           if item[0] == 'pressure_msl')
    assert pressure_mapping[2] == 0.01  # Conversion factor


@pytest.mark.parametrize(
    "start_offset_days, expected_keys, forbidden_keys",
    [
        # Future → forecast mode
        (1, ["forecast_days"], ["start_date", "end_date", "models"]),
        # Today (edge case, should still be forecast)
        (0, ["forecast_days"], ["start_date", "end_date", "models"]),
        # Past → historical mode
        (-2, ["start_date", "end_date", "models"], ["forecast_days"]),
    ],
)
@patch("requests.get")
def test_openmeteo_request_mode_selection(
    mock_get,
    provider,
    sample_openmeteo_1_json,
    start_offset_days,
    expected_keys,
    forbidden_keys,
):
    """Test that Open-Meteo request switches correctly between forecast and historical modes."""

    # Mock response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = sample_openmeteo_1_json
    mock_response.content = str(sample_openmeteo_1_json)
    mock_get.return_value = mock_response

    # Fixed current time to avoid timing issues
    fixed_now = to_datetime("2026-03-26 12:00:00+01:00", in_timezone="Europe/Berlin")

    # Patch to_datetime to return fixed_now when called without a datetime argument
    with patch("akkudoktoreos.prediction.weatheropenmeteo.to_datetime") as mock_to_datetime:
        def to_datetime_side_effect(dt=None, in_timezone=None):
            if dt is None:
                return fixed_now
            # Otherwise fall back to the real function
            from akkudoktoreos.utils.datetimeutil import to_datetime as real_to_datetime
            return real_to_datetime(dt, in_timezone=in_timezone)

        mock_to_datetime.side_effect = to_datetime_side_effect

        # Set deterministic start time based on fixed_now
        start = fixed_now + pd.Timedelta(days=start_offset_days)

        ems_eos = get_ems()
        ems_eos.set_start_datetime(start)

        # Execute; force_update bypasses the file cache, the request must be made.
        provider._request_forecast(force_update=True)

    # Inspect request params
    params = mock_get.call_args[1]["params"]

    # Assertions
    for key in expected_keys:
        assert key in params, f"Expected '{key}' in params but got {params}"

    for key in forbidden_keys:
        assert key not in params, f"Did not expect '{key}' in params but got {params}"

    if "forecast_days" in params:
        assert params["forecast_days"] >= 1
        assert params["forecast_days"] <= 16


# ------------------------------------------------
# Development Open-Meteo
# ------------------------------------------------


@pytest.mark.asyncio
async def test_openmeteo_development_forecast_data(provider, config_eos, is_system_test):
    """Fetch data from real Open-Meteo server for development purposes."""
    if not is_system_test:
        return

    # Us actual date for forecast (not historic data)
    now = to_datetime(in_timezone="Europe/Berlin")
    start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date + pd.Timedelta(days=3)  # 3 Tage Vorhersage

    ems_eos = get_ems()
    ems_eos.set_start_datetime(start_date)

    config_eos.general.latitude = 50.0
    config_eos.general.longitude = 10.0

    # Fetch raw data from Open-Meteo
    try:
        openmeteo_data = provider._request_forecast()

        # Save raw API response
        with FILE_TESTDATA_WEATHEROPENMETEO_1_JSON.open("w", encoding="utf-8", newline="\n") as f_out:
            json.dump(openmeteo_data, f_out, indent=4)

        # Update and process data
        await provider.update_data(force_enable=True, force_update=True)

        # Save processed data
        with FILE_TESTDATA_WEATHEROPENMETEO_2_JSON.open("w", encoding="utf-8", newline="\n") as f_out:
            f_out.write(provider.model_dump_json(indent=4))

        # Verify radiation values
        if len(provider) > 0:
            records = list(provider.records)

            # Check fo radiation values available
            has_ghi = any(hasattr(r, 'ghi') and r.ghi is not None for r in records)
            has_dni = any(hasattr(r, 'dni') and r.dni is not None for r in records)
            has_dhi = any(hasattr(r, 'dhi') and r.dhi is not None for r in records)

            logger.info(f"Open-Meteo data verification: GHI={has_ghi}, DNI={has_dni}, DHI={has_dhi}")

            # Optional: Check for positive values (at day time)
            daytime_values = [getattr(r, 'ghi', 0) for r in records[:24]
                            if hasattr(r, 'ghi') and r.ghi is not None and r.ghi > 10]
            if daytime_values:
                logger.info(f"Found {len(daytime_values)} positive GHI values")

    except Exception as e:
        logger.error(f"Error fetching Open-Meteo data: {e}")
        # Debug-Ausgabe
        logger.error(f"Request would have been: https://api.open-meteo.com/v1/forecast?latitude=50.0&longitude=10.0&hourly=temperature_2m,relative_humidity_2m,shortwave_radiation&timezone=Europe/Berlin&forecast_days=3")
        raise
