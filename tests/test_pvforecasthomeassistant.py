from unittest.mock import MagicMock, call, patch

import pendulum
import pytest
import requests

from akkudoktoreos.prediction.pvforecasthomeassistant import PVForecastHomeAssistant

# Fixed "current" EMS time so tests can assert on the [start_of_day, +prediction.hours)
# window that _update_data clears before writing, independent of wall-clock time.
FIXED_EMS_START = pendulum.datetime(2026, 8, 18, 5, 0, tz="Europe/Berlin")

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
    mock = MagicMock(spec=requests.Response)
    mock.status_code = status_code
    mock.json.return_value = json_data
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
async def test_update_data_raises_on_missing_attribute(pvforecast_instance):
    response = {"state": "0.0", "attributes": {}}
    with (
        patch("requests.get", return_value=mock_response(response)),
        patch.object(PVForecastHomeAssistant, "update_value") as mock_update,
    ):
        with pytest.raises(ValueError, match="no 'forecast' attribute"):
            await pvforecast_instance._update_data()
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_update_data_raises_on_empty_forecast(pvforecast_instance):
    response = {"state": "0.0", "attributes": {"forecast": []}}
    with (
        patch("requests.get", return_value=mock_response(response)),
        patch.object(PVForecastHomeAssistant, "update_value") as mock_update,
    ):
        with pytest.raises(ValueError, match="no 'forecast' attribute"):
            await pvforecast_instance._update_data()
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_update_data_raises_when_all_entries_malformed(pvforecast_instance):
    """A non-empty but entirely unusable forecast must fail explicitly, not succeed as a no-op."""
    response = {
        "state": "0.0",
        "attributes": {
            "forecast": [
                {"datetime": "2026-08-18T06:00:00+02:00"},  # missing "watts"
                {"watts": "not-a-number", "datetime": "2026-08-18T06:15:00+02:00"},
            ]
        },
    }
    with (
        patch("requests.get", return_value=mock_response(response)),
        patch.object(PVForecastHomeAssistant, "update_value") as mock_update,
    ):
        with pytest.raises(ValueError, match="no usable forecast entries"):
            await pvforecast_instance._update_data()
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_update_data_clears_stale_entries_missing_from_shorter_forecast(
    pvforecast_instance,
):
    """A shorter refresh must not leave stale values behind at now-omitted timestamps.

    Regression test for a bug where an early return on empty/short responses left
    previously-written pvforecast_ac_power values in place, letting EOS optimize against
    a mixture of current and stale forecast data.
    """
    with patch("akkudoktoreos.core.coreabc.get_ems") as mock_get_ems:
        mock_get_ems.return_value.start_datetime = FIXED_EMS_START

        # Seed a value as if a previous, longer forecast had covered this timestamp.
        stale_dt = pendulum.datetime(2026, 8, 18, 10, 0, tz="Europe/Berlin")
        await pvforecast_instance.update_value(stale_dt, {"pvforecast_ac_power": 999.0})

        shorter_response = {
            "state": "0.0",
            "attributes": {
                "forecast": [{"datetime": "2026-08-18T06:00:00+02:00", "watts": 12.0}]
            },
        }
        with patch("requests.get", return_value=mock_response(shorter_response)):
            await pvforecast_instance._update_data()

        values = {
            pendulum.parse(dt): value
            for dt, value in (
                await pvforecast_instance.key_to_dict("pvforecast_ac_power", dropna=False)
            ).items()
        }
        assert values[stale_dt] is None
        assert values[pendulum.parse("2026-08-18T06:00:00+02:00")] == 12.0


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
