from unittest.mock import call, patch

import pendulum
import pytest
import requests

from akkudoktoreos.prediction.pvforecastsolcast import PVForecastSolcast


@pytest.fixture
def pvforecast_instance(config_eos):
    settings = {
        "pvforecast": {
            "provider": "PVForecastSolcast",
            "solcast": {"api_key": "dummy-key", "site_id": "site-abc"},
        },
    }
    config_eos.merge_settings_from_dict(settings)
    start_dt = pendulum.datetime(2025, 1, 1, tz="Europe/Berlin")
    return PVForecastSolcast(config=config_eos.load, start_datetime=start_dt)


def _http(forecasts):
    return type(
        "R",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"forecasts": forecasts},
        },
    )()


def test_provider_id(pvforecast_instance):
    assert PVForecastSolcast.provider_id() == "PVForecastSolcast"
    assert pvforecast_instance.enabled() is True


@pytest.mark.parametrize(
    "period,minutes",
    [("PT30M", 30), ("PT15M", 15), ("PT5M", 5), ("PT1H", 60), ("PT1H30M", 90), ("", 0), (None, 0)],
)
def test_period_minutes(period, minutes):
    assert PVForecastSolcast._period_minutes(period) == minutes


@pytest.mark.asyncio
async def test_update_data_normalises_to_period_start_and_converts_kw(pvforecast_instance):
    """pv_estimate (kW) -> W; timestamp = period_end - period; period_end is UTC."""
    body = {
        "forecasts": [
            {"pv_estimate": 1.2, "period_end": "2025-01-01T12:30:00.0000000Z", "period": "PT30M"},
            {"pv_estimate": 0.0, "period_end": "2025-01-01T13:00:00.0000000Z", "period": "PT30M"},
        ]
    }
    with patch.object(pvforecast_instance, "_request_forecast", return_value=body), \
         patch.object(PVForecastSolcast, "update_value") as mock_update:
        await pvforecast_instance._update_data()

        assert mock_update.call_count == 2
        expected = [
            call(
                pendulum.datetime(2025, 1, 1, 12, 0, tz="UTC"),
                {"pvforecast_ac_power": 1200.0, "pvforecast_dc_power": 1200.0},
            ),
            call(
                pendulum.datetime(2025, 1, 1, 12, 30, tz="UTC"),
                {"pvforecast_ac_power": 0.0, "pvforecast_dc_power": 0.0},
            ),
        ]
        mock_update.assert_has_calls(expected, any_order=False)


def test_request_forecast_uses_site_and_bearer(pvforecast_instance):
    with patch("requests.get", return_value=_http([])) as mock_get:
        pvforecast_instance._request_forecast(force_update=True)
        url = mock_get.call_args[0][0]
        assert url.endswith("/rooftop_sites/site-abc/forecasts")
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer dummy-key"
        assert mock_get.call_args.kwargs["params"]["format"] == "json"


@pytest.mark.asyncio
async def test_update_data_skips_when_disabled(pvforecast_instance, config_eos):
    config_eos.merge_settings_from_dict({"pvforecast": {"provider": "PVForecastAkkudoktor"}})
    with patch.object(pvforecast_instance, "_request_forecast") as mock_req, \
         patch.object(PVForecastSolcast, "update_value") as mock_update:
        await pvforecast_instance._update_data()
        mock_req.assert_not_called()
        mock_update.assert_not_called()


def test_request_forecast_raises_on_http_error(pvforecast_instance):
    with patch("requests.get", side_effect=requests.Timeout("timed out")):
        with pytest.raises(RuntimeError) as exc_info:
            pvforecast_instance._request_forecast(force_update=True)
        assert "Failed to fetch pvforecast from Solcast" in str(exc_info.value)
