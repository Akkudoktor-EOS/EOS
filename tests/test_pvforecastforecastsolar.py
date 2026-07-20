from unittest.mock import call, patch

import pendulum
import pytest
import requests

from akkudoktoreos.prediction.pvforecastforecastsolar import PVForecastForecastSolar


def _config(config_eos, planes=None, api_key=None):
    settings = {
        "general": {"latitude": 52.5, "longitude": 13.4},
        "pvforecast": {
            "provider": "PVForecastForecastSolar",
            "forecastsolar": {"api_key": api_key},
            "planes": planes
            if planes is not None
            else [{"surface_tilt": 30.0, "surface_azimuth": 180.0, "peakpower": 5.0}],
        },
    }
    config_eos.merge_settings_from_dict(settings)
    return config_eos


@pytest.fixture
def pvforecast_instance(config_eos):
    _config(config_eos)
    start_dt = pendulum.datetime(2025, 1, 1, tz="Europe/Berlin")
    return PVForecastForecastSolar(config=config_eos.load, start_datetime=start_dt)


def _http(watts, timezone="Europe/Berlin"):
    return type(
        "R",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "result": {"watts": watts},
                "message": {"info": {"timezone": timezone}},
            },
        },
    )()


def test_provider_id(pvforecast_instance):
    assert PVForecastForecastSolar.provider_id() == "PVForecastForecastSolar"
    assert pvforecast_instance.enabled() is True


@pytest.mark.asyncio
async def test_update_data_resolves_tz_and_sets_power(pvforecast_instance):
    body = {
        "timezone": "Europe/Berlin",
        "watts": {"2025-01-01 12:00:00": 1200.0, "2025-01-01 13:00:00": 1500.0},
    }
    with patch.object(pvforecast_instance, "_request_forecast", return_value=body), \
         patch.object(PVForecastForecastSolar, "update_value") as mock_update:
        await pvforecast_instance._update_data()

        assert mock_update.call_count == 2
        expected = [
            call(
                pendulum.datetime(2025, 1, 1, 12, 0, tz="Europe/Berlin"),
                {"pvforecast_ac_power": 1200.0, "pvforecast_dc_power": 1200.0},
            ),
            call(
                pendulum.datetime(2025, 1, 1, 13, 0, tz="Europe/Berlin"),
                {"pvforecast_ac_power": 1500.0, "pvforecast_dc_power": 1500.0},
            ),
        ]
        mock_update.assert_has_calls(expected, any_order=False)
        # 12:00 Europe/Berlin (CET) is 11:00 UTC.
        assert mock_update.call_args_list[0][0][0].in_timezone("UTC").hour == 11


def test_plane_url_converts_azimuth(config_eos):
    """EOS azimuth 270 (west) -> Forecast.Solar 90; the key + plane geometry land in the URL."""
    _config(
        config_eos,
        planes=[{"surface_tilt": 25.0, "surface_azimuth": 270.0, "peakpower": 7.5}],
        api_key="secret",
    )
    pv = PVForecastForecastSolar(
        config=config_eos.load, start_datetime=pendulum.datetime(2025, 1, 1, tz="UTC")
    )
    with patch("requests.get", return_value=_http({})) as mock_get:
        # force_update is consumed by the cache_in_file decorator at runtime
        # (same call convention as pvforecastakkudoktor.py).
        pv._request_forecast(force_update=True)  # type: ignore
        url = mock_get.call_args[0][0]
        assert url == "https://api.forecast.solar/secret/estimate/52.5/13.4/25.0/90.0/7.5"


def test_request_forecast_sums_planes(config_eos):
    """Two planes -> two requests; instantaneous powers are summed per timestamp."""
    _config(
        config_eos,
        planes=[
            {"surface_tilt": 30.0, "surface_azimuth": 90.0, "peakpower": 3.0},
            {"surface_tilt": 30.0, "surface_azimuth": 270.0, "peakpower": 3.0},
        ],
    )
    pv = PVForecastForecastSolar(
        config=config_eos.load, start_datetime=pendulum.datetime(2025, 1, 1, tz="UTC")
    )
    responses = [
        _http({"2025-01-01 12:00:00": 1000.0}),
        _http({"2025-01-01 12:00:00": 800.0}),
    ]
    with patch("requests.get", side_effect=responses) as mock_get:
        body = pv._request_forecast(force_update=True)  # type: ignore
        assert mock_get.call_count == 2
        assert body["watts"]["2025-01-01 12:00:00"] == 1800.0


def test_update_data_skips_when_disabled(pvforecast_instance, config_eos):
    config_eos.merge_settings_from_dict({"pvforecast": {"provider": "PVForecastAkkudoktor"}})
    with patch.object(pvforecast_instance, "_request_forecast") as mock_req, \
         patch.object(PVForecastForecastSolar, "update_value") as mock_update:
        pvforecast_instance._update_data()
        mock_req.assert_not_called()
        mock_update.assert_not_called()


def test_request_forecast_raises_on_http_error(pvforecast_instance):
    with patch("requests.get", side_effect=requests.Timeout("timed out")):
        with pytest.raises(RuntimeError) as exc_info:
            pvforecast_instance._request_forecast(force_update=True)
        assert "Failed to fetch pvforecast from Forecast.Solar" in str(exc_info.value)
