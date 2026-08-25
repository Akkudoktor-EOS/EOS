from unittest.mock import call, patch

import pendulum
import pytest
import requests

from akkudoktoreos.prediction.pvforecasthomeassistant import PVForecastHomeAssistant

# Trimmed excerpt of a real Home Assistant response, captured live from a
# Helios Forecast "Power now" sensor (sensor.pv1_power_now).
REAL_HA_RESPONSE = {
    "entity_id": "sensor.pv1_power_now",
    "state": "0.0",
    "attributes": {
        "state_class": "measurement",
        "forecast": [
            {"datetime": "2026-08-18T06:00:00+02:00", "watts": 0},
            {"datetime": "2026-08-18T06:15:00+02:00", "watts": 0},
            {"datetime": "2026-08-18T06:30:00+02:00", "watts": 17.19},
            {"datetime": "2026-08-18T06:45:00+02:00", "watts": 43.24},
            {"datetime": "2026-08-18T07:00:00+02:00", "watts": 75.68},
        ],
        "unit_of_measurement": "W",
        "device_class": "power",
        "friendly_name": "PV1 Power now",
    },
}


@pytest.fixture
def pvforecast_instance(config_eos):
    settings = {
        "pvforecast": {
            "homeassistant": {
                "entity_id": "sensor.pv1_power_now",
                "base_url": "http://homeassistant.local:8123",
                "token": "dummy-token",
            },
        }
    }
    config_eos.merge_settings_from_dict(settings)
    start_dt = pendulum.datetime(2026, 8, 18, tz="Europe/Berlin")
    return PVForecastHomeAssistant(config=config_eos.load, start_datetime=start_dt)


def mock_response(json_data, status_code=200):
    mock = requests.Response()
    mock.status_code = status_code
    mock.json = lambda: json_data
    return mock


@pytest.mark.asyncio
async def test_update_data_updates_ac_power(pvforecast_instance):
    with (
        patch("requests.get", return_value=mock_response(REAL_HA_RESPONSE)),
        patch.object(PVForecastHomeAssistant, "update_value") as mock_update,
    ):
        await pvforecast_instance._update_data()

        assert mock_update.call_count == 5
        expected_calls = [
            call(pendulum.parse("2026-08-18T06:00:00+02:00"), {"pvforecast_ac_power": 0.0}),
            call(pendulum.parse("2026-08-18T06:15:00+02:00"), {"pvforecast_ac_power": 0.0}),
            call(pendulum.parse("2026-08-18T06:30:00+02:00"), {"pvforecast_ac_power": 17.19}),
            call(pendulum.parse("2026-08-18T06:45:00+02:00"), {"pvforecast_ac_power": 43.24}),
            call(pendulum.parse("2026-08-18T07:00:00+02:00"), {"pvforecast_ac_power": 75.68}),
        ]
        mock_update.assert_has_calls(expected_calls, any_order=False)


@pytest.mark.asyncio
async def test_update_data_converts_kw_to_w(pvforecast_instance):
    pvforecast_instance.config.pvforecast.homeassistant.value_unit = "kW"
    response = {
        "state": "0.0",
        "attributes": {"forecast": [{"datetime": "2026-08-18T12:00:00+02:00", "watts": 2.5}]},
    }
    with (
        patch("requests.get", return_value=mock_response(response)),
        patch.object(PVForecastHomeAssistant, "update_value") as mock_update,
    ):
        await pvforecast_instance._update_data()

        mock_update.assert_called_once_with(
            pendulum.parse("2026-08-18T12:00:00+02:00"), {"pvforecast_ac_power": 2500.0}
        )


@pytest.mark.asyncio
async def test_update_data_skips_missing_attribute(pvforecast_instance):
    response = {"state": "0.0", "attributes": {}}
    with (
        patch("requests.get", return_value=mock_response(response)),
        patch.object(PVForecastHomeAssistant, "update_value") as mock_update,
    ):
        await pvforecast_instance._update_data()
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_update_data_skips_empty_forecast(pvforecast_instance):
    response = {"state": "0.0", "attributes": {"forecast": []}}
    with (
        patch("requests.get", return_value=mock_response(response)),
        patch.object(PVForecastHomeAssistant, "update_value") as mock_update,
    ):
        await pvforecast_instance._update_data()
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_update_data_skips_malformed_entries(pvforecast_instance):
    response = {
        "state": "0.0",
        "attributes": {
            "forecast": [
                {"datetime": "2026-08-18T06:00:00+02:00", "watts": 12.0},
                {"datetime": "2026-08-18T06:15:00+02:00"},  # missing "watts"
                {"watts": "not-a-number", "datetime": "2026-08-18T06:30:00+02:00"},
                {"datetime": "2026-08-18T06:45:00+02:00", "watts": 34.0},
            ]
        },
    }
    with (
        patch("requests.get", return_value=mock_response(response)),
        patch.object(PVForecastHomeAssistant, "update_value") as mock_update,
    ):
        await pvforecast_instance._update_data()

        assert mock_update.call_count == 2
        expected_calls = [
            call(pendulum.parse("2026-08-18T06:00:00+02:00"), {"pvforecast_ac_power": 12.0}),
            call(pendulum.parse("2026-08-18T06:45:00+02:00"), {"pvforecast_ac_power": 34.0}),
        ]
        mock_update.assert_has_calls(expected_calls, any_order=False)


def test_request_entity_state_raises_on_http_error(pvforecast_instance):
    with patch("requests.get", side_effect=requests.Timeout("Request timed out")) as mock_get:
        with pytest.raises(RuntimeError) as exc_info:
            pvforecast_instance._request_entity_state()

        assert "Failed to fetch pvforecast entity" in str(exc_info.value)
        mock_get.assert_called_once()


def test_request_entity_state_raises_without_token(pvforecast_instance):
    pvforecast_instance.config.pvforecast.homeassistant.token = None
    with pytest.raises(RuntimeError) as exc_info:
        pvforecast_instance._request_entity_state()
    assert "No Home Assistant access token available" in str(exc_info.value)
