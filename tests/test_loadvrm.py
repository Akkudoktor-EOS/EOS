import json
from unittest.mock import call, patch

import pendulum
import pytest
import requests

from akkudoktoreos.prediction.loadvrm import (
    LoadVrm,
    VrmForecastRecords,
    VrmForecastResponse,
)


@pytest.fixture
def load_vrm_instance(config_eos):
    # Settings für LoadVrm
    settings = {
        "load": {
            "provider": "LoadVrm",
            "provider_settings": {
                "LoadVrm": {
                    "load_vrm_token": "dummy-token",
                    "load_vrm_idsite": 12345,
                },
            }
        }
    }
    config_eos.merge_settings_from_dict(settings)
    # start_datetime initialize
    start_dt = pendulum.datetime(2025, 1, 1, tz='Europe/Berlin')

    # create LoadVrm-instance with config and start_datetime
    lv = LoadVrm(config=config_eos.load, start_datetime=start_dt)

    return lv


def mock_forecast_response():
    """Return a fake VrmForecastResponse with sample data."""
    return VrmForecastResponse(
        success=True,
        records=VrmForecastRecords(
            vrm_consumption_fc=[
                (pendulum.datetime(2025, 1, 1, 0, 0, tz='Europe/Berlin').int_timestamp * 1000, 100.5),
                (pendulum.datetime(2025, 1, 1, 1, 0, tz='Europe/Berlin').int_timestamp * 1000, 101.2)
            ],
            solar_yield_forecast=[]
        ),
        totals={}
    )


def test_update_data_calls_update_value(load_vrm_instance):
    with patch.object(load_vrm_instance, "_request_forecast", return_value=mock_forecast_response()), \
         patch.object(LoadVrm, "update_value") as mock_update:

        load_vrm_instance._update_data()

        assert mock_update.call_count == 2

        expected_calls = [
            call(
                pendulum.datetime(2025, 1, 1, 0, 0, 0, tz='Europe/Berlin'),
                {"load_mean": 100.5, "load_std": 0.0, "load_mean_adjusted": 100.5}
            ),
            call(
                pendulum.datetime(2025, 1, 1, 1, 0, 0, tz='Europe/Berlin'),
                {"load_mean": 101.2, "load_std": 0.0, "load_mean_adjusted": 101.2}
            ),
        ]

        mock_update.assert_has_calls(expected_calls, any_order=False)


def test_validate_data_accepts_valid_json():
    """Test that _validate_data doesn't raise with valid input."""
    response = mock_forecast_response()
    json_data = response.model_dump_json()

    validated = LoadVrm._validate_data(json_data)
    assert validated.success
    assert len(validated.records.vrm_consumption_fc) == 2


def test_validate_data_raises_on_invalid_json():
    """_validate_data should raise ValueError on schema mismatch."""
    invalid_json = json.dumps({"success": True})  # missing 'records'

    with pytest.raises(ValueError) as exc_info:
        LoadVrm._validate_data(invalid_json)

    assert "Field:" in str(exc_info.value)
    assert "records" in str(exc_info.value)


def test_request_forecast_raises_on_http_error(load_vrm_instance):
    with patch("requests.get", side_effect=requests.Timeout("Request timed out")) as mock_get:
        with pytest.raises(RuntimeError) as exc_info:
            load_vrm_instance._request_forecast(0, 1)

        assert "Failed to fetch load forecast" in str(exc_info.value)
        mock_get.assert_called_once()


def test_update_data_does_nothing_on_empty_forecast(load_vrm_instance):
    empty_response = VrmForecastResponse(
        success=True,
        records=VrmForecastRecords(vrm_consumption_fc=[], solar_yield_forecast=[]),
        totals={}
    )

    with patch.object(load_vrm_instance, "_request_forecast", return_value=empty_response), \
         patch.object(LoadVrm, "update_value") as mock_update:

        load_vrm_instance._update_data()

        mock_update.assert_not_called()
