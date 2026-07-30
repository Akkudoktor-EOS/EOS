from unittest.mock import call, patch

import pendulum
import pytest
import requests

from akkudoktoreos.prediction.pvforecastpvnode import PVForecastPVNode


@pytest.fixture
def pvforecast_instance(config_eos):
    settings = {
        "general": {"latitude": 52.5, "longitude": 13.4},
        "pvforecast": {
            "provider": "PVForecastPVNode",
            "pvnode": {
                "api_key": "dummy-key",
                "site_id": "test-site-123",
                "forecast_days": 2,
            },
        },
    }
    config_eos.merge_settings_from_dict(settings)
    start_dt = pendulum.datetime(2025, 1, 1, tz="Europe/Berlin")
    return PVForecastPVNode(config=config_eos.load, start_datetime=start_dt)


def mock_v2_body():
    """A canonical pvnode V2 response: site-local wall-clock + IANA timezone.

    The second slot carries a null ``pv_power`` (night) which must become 0 W.
    """
    return {
        "timezone": "Europe/Berlin",
        "values": [
            {"timestamp": "2025-01-01T12:00:00", "pv_power": 1200.0},
            {"timestamp": "2025-01-01T12:15:00", "pv_power": None},
        ],
    }


def test_provider_id(pvforecast_instance):
    assert PVForecastPVNode.provider_id() == "PVForecastPVNode"
    assert pvforecast_instance.enabled() is True


def test_extract_values_resolves_local_wall_clock_to_instant(pvforecast_instance):
    """V2 timestamps are local wall-clock; 12:00 Europe/Berlin (CET) == 11:00 UTC."""
    rows = pvforecast_instance._extract_values(mock_v2_body())

    assert len(rows) == 2
    # Same instant, regardless of representation.
    assert rows[0][0] == pendulum.datetime(2025, 1, 1, 12, 0, tz="Europe/Berlin")
    assert rows[0][0].in_timezone("UTC").hour == 11
    assert rows[0][1] == 1200.0
    # Null pv_power -> 0 W (not a gap, to avoid phantom night interpolation).
    assert rows[1][1] == 0.0


def test_extract_values_trusts_explicit_offset(pvforecast_instance):
    """A timestamp already carrying an offset is trusted as-is (no double shift)."""
    body = {
        "timezone": "Europe/Berlin",
        "values": [{"timestamp": "2025-01-01T12:00:00+00:00", "pv_power": 500.0}],
    }
    rows = pvforecast_instance._extract_values(body)
    assert rows[0][0].in_timezone("UTC").hour == 12


@pytest.mark.asyncio
async def test_update_data_sets_ac_and_dc_power(pvforecast_instance):
    with patch.object(pvforecast_instance, "_request_forecast", return_value=mock_v2_body()), \
         patch.object(PVForecastPVNode, "update_value") as mock_update:
        await pvforecast_instance._update_data()

        assert mock_update.call_count == 2
        expected = [
            call(
                pendulum.datetime(2025, 1, 1, 12, 0, tz="Europe/Berlin"),
                {"pvforecast_ac_power": 1200.0, "pvforecast_dc_power": 1200.0},
            ),
            call(
                pendulum.datetime(2025, 1, 1, 12, 15, tz="Europe/Berlin"),
                {"pvforecast_ac_power": 0.0, "pvforecast_dc_power": 0.0},
            ),
        ]
        mock_update.assert_has_calls(expected, any_order=False)


@pytest.mark.asyncio
async def test_update_data_skips_when_disabled(pvforecast_instance, config_eos):
    config_eos.merge_settings_from_dict({"pvforecast": {"provider": "PVForecastAkkudoktor"}})
    with patch.object(pvforecast_instance, "_request_forecast") as mock_req, \
         patch.object(PVForecastPVNode, "update_value") as mock_update:
        await pvforecast_instance._update_data()
        mock_req.assert_not_called()
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_update_data_skips_on_empty_forecast(pvforecast_instance):
    with patch.object(pvforecast_instance, "_request_forecast", return_value={"values": []}), \
         patch.object(PVForecastPVNode, "update_value") as mock_update:
        await pvforecast_instance._update_data()
        mock_update.assert_not_called()


def test_request_forecast_uses_saved_site_get(pvforecast_instance):
    """site_id set -> GET /v2/forecast/{site_id} with Bearer auth."""
    fake = type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: {"values": []}})()
    with patch("requests.get", return_value=fake) as mock_get:
        pvforecast_instance._request_forecast(force_update=True)
        url = mock_get.call_args[0][0]
        assert url.endswith("/v2/forecast/test-site-123")
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer dummy-key"


def test_request_forecast_inline_post_when_no_site(config_eos):
    """No site_id -> POST /v2/forecast/inline with planes geometry."""
    config_eos.merge_settings_from_dict(
        {
            "general": {"latitude": 52.5, "longitude": 13.4},
            "pvforecast": {
                "provider": "PVForecastPVNode",
                "pvnode": {"api_key": "k", "site_id": None},
                "planes": [{"surface_tilt": 30.0, "surface_azimuth": 180.0, "peakpower": 5.0}],
            },
        }
    )
    pv = PVForecastPVNode(config=config_eos.load, start_datetime=pendulum.datetime(2025, 1, 1, tz="UTC"))
    fake = type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: {"values": []}})()
    with patch("requests.post", return_value=fake) as mock_post:
        # force_update is consumed by the cache_in_file decorator at runtime
        # (same call convention as pvforecastakkudoktor.py).
        pv._request_forecast(force_update=True)  # type: ignore
        url = mock_post.call_args[0][0]
        assert url.endswith("/v2/forecast/inline")
        body = mock_post.call_args.kwargs["json"]
        assert body["strings"][0] == {"slope": 30.0, "orientation": 180.0, "power_kw": 5.0}


def test_request_forecast_raises_on_http_error(pvforecast_instance):
    with patch("requests.get", side_effect=requests.Timeout("timed out")):
        with pytest.raises(RuntimeError) as exc_info:
            pvforecast_instance._request_forecast(force_update=True)
        assert "Failed to fetch pvforecast from pvnode" in str(exc_info.value)
