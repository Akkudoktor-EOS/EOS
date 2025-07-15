import json
from unittest.mock import call, patch

import pendulum
import pytest
import requests

from akkudoktoreos.prediction.pvforecastvrm import (
    PVForecastVrm,
    VrmForecastRecords,
    VrmForecastResponse,
)


@pytest.fixture
def pvforecast_instance(config_eos):
    # Settings for PVForecastVrm
    settings = {
        "pvforecast": {
            "provider": "PVForecastVrm",
            "provider_settings": {
                "pvforecast_vrm_token": "dummy-token",
                "pvforecast_vrm_idsite": 12345
            }
        }
    }
    config_eos.merge_settings_from_dict(settings)
    # start_datetime initialize
    start_dt = pendulum.datetime(2025, 1, 1, tz='Europe/Berlin')

    # create PVForecastVrm-instance with config and start_datetime
    pv = PVForecastVrm(config=config_eos.load, start_datetime=start_dt)

    return pv


def mock_forecast_response():
    """Return a fake VrmForecastResponse with sample data."""
    return VrmForecastResponse(
        success=True,
        records=VrmForecastRecords(
            vrm_consumption_fc=[],
            solar_yield_forecast=[
                (pendulum.datetime(2025, 1, 1, 0, 0, tz='Europe/Berlin').int_timestamp * 1000, 120.0),
                (pendulum.datetime(2025, 1, 1, 1, 0, tz='Europe/Berlin').int_timestamp * 1000, 130.0)
            ]
        ),
        totals={}
    )


def test_update_data_updates_dc_and_ac_power(pvforecast_instance):
    with patch.object(pvforecast_instance, "_request_forecast", return_value=mock_forecast_response()), \
         patch.object(PVForecastVrm, "update_value") as mock_update:

        pvforecast_instance._update_data()

        # Check that update_value was called correctly
        assert mock_update.call_count == 2

        expected_calls = [
            call(
                pendulum.datetime(2025, 1, 1, 0, 0, tz='Europe/Berlin'),
                {"pvforecast_dc_power": 120.0, "pvforecast_ac_power": 115.2}
            ),
            call(
                pendulum.datetime(2025, 1, 1, 1, 0, tz='Europe/Berlin'),
                {"pvforecast_dc_power": 130.0, "pvforecast_ac_power": 124.8}
            ),
        ]

        mock_update.assert_has_calls(expected_calls, any_order=False)


def test_validate_data_accepts_valid_json():
    """Test that _validate_data doesn't raise with valid input."""
    response = mock_forecast_response()
    json_data = response.model_dump_json()

    validated = PVForecastVrm._validate_data(json_data)
    assert validated.success
    assert len(validated.records.solar_yield_forecast) == 2


def test_validate_data_invalid_json_raises():
    """Test that _validate_data raises with invalid input."""
    invalid_json = json.dumps({"success": True})  # missing 'records'
    with pytest.raises(ValueError) as exc_info:
        PVForecastVrm._validate_data(invalid_json)
    assert "Field:" in str(exc_info.value)
    assert "records" in str(exc_info.value)


def test_request_forecast_raises_on_http_error(pvforecast_instance):
    """Ensure _request_forecast raises RuntimeError on HTTP failure."""
    with patch("requests.get", side_effect=requests.Timeout("Request timed out")) as mock_get:
        with pytest.raises(RuntimeError) as exc_info:
            pvforecast_instance._request_forecast(0, 1)

        assert "Failed to fetch pvforecast" in str(exc_info.value)
        mock_get.assert_called_once()


def test_update_data_skips_on_empty_forecast(pvforecast_instance):
    """Ensure no update_value calls are made if no forecast data is present."""
    empty_response = VrmForecastResponse(
        success=True,
        records=VrmForecastRecords(vrm_consumption_fc=[], solar_yield_forecast=[]),
        totals={}
    )

    with patch.object(pvforecast_instance, "_request_forecast", return_value=empty_response), \
         patch.object(PVForecastVrm, "update_value") as mock_update:

        pvforecast_instance._update_data()
        mock_update.assert_not_called()
