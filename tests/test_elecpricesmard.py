# ruff: noqa: S101

import json
from unittest.mock import Mock, patch

import pytest

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction.elecprice import ElecPriceCommonSettings
from akkudoktoreos.prediction.elecpricesmard import ElecPriceSMARD
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest.fixture
def provider(config_eos):
    """Configure and return the direct SMARD singleton provider."""
    config_eos.elecprice = ElecPriceCommonSettings(provider="ElecPriceSMARD")
    provider = ElecPriceSMARD()
    provider.highest_orig_datetime = None
    get_ems().set_start_datetime(
        to_datetime("2026-07-27 00:00:00", in_timezone="Europe/Berlin")
    )
    return provider


def _response(payload):
    response = Mock()
    response.content = json.dumps(payload).encode()
    response.raise_for_status.return_value = None
    return response


@patch("akkudoktoreos.prediction.elecpricesmard.requests.get")
def test_request_forecast_fetches_index_and_overlapping_chunks(mock_get, provider):
    """SMARD index and weekly chunks are combined, sorted, and stripped of null values."""
    chunk_start = 1785103200000
    mock_get.side_effect = [
        _response({"timestamps": [chunk_start]}),
        _response(
            {
                "meta_data": {"version": 1, "created": 1785500527370},
                "series": [
                    [1785103200000, 86.04],
                    [1785106800000, None],
                    [1785110400000, -1.25],
                ],
            }
        ),
    ]

    result = provider._request_forecast(
        start_date="2026-07-27", force_update=True
    )

    assert result.unix_seconds == [1785103200, 1785110400]
    assert result.price == [86.04, -1.25]
    assert result.license_info == "CC BY 4.0 Bundesnetzagentur | SMARD.de"
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0].args[0].endswith("/4169/DE/index_quarterhour.json")
    assert mock_get.call_args_list[1].args[0].endswith(
        "/4169/DE/4169_DE_quarterhour_1785103200000.json"
    )


def test_chunk_selection_includes_preceding_overlapping_chunk(provider):
    """A range beginning mid-week includes the chunk that started before it."""
    index = provider._validate_index(
        json.dumps({"timestamps": [1000, 2000, 3000]}).encode()
    )
    start = to_datetime(2.5, in_timezone="UTC")
    end = to_datetime(3.5, in_timezone="UTC")

    assert provider._chunk_timestamps(index, start, end) == [2000, 3000]


def test_smard_provider_is_enabled(provider):
    assert provider.enabled()


@patch("akkudoktoreos.prediction.elecpricesmard.requests.get")
def test_unpublished_period_names_the_latest_published_value(mock_get, provider):
    """A day-ahead source that lags is not a broken response.

    SMARD publishes the next day around midday. Until then a run whose horizon
    already reaches into that day asks for a window SMARD cannot serve yet. The
    error has to say that, because the caller keeps its history and extrapolates
    in that case rather than treating the API as broken.
    """
    chunk_start = 1785103200000
    # The chunk holds prices, but all of them end before the requested window.
    mock_get.side_effect = [
        _response({"timestamps": [chunk_start]}),
        _response(
            {
                "meta_data": {"version": 1, "created": 1785500527370},
                "series": [[chunk_start, 86.04], [chunk_start + 900000, 84.5]],
            }
        ),
    ]

    with pytest.raises(ValueError, match="has not published day-ahead prices"):
        provider._request_forecast(start_date="2026-07-28", force_update=True)


@patch("akkudoktoreos.prediction.elecpricesmard.requests.get")
def test_empty_series_still_reports_an_unusable_response(mock_get, provider):
    """A chunk without a single price is a different problem and says so."""
    chunk_start = 1785103200000
    mock_get.side_effect = [
        _response({"timestamps": [chunk_start]}),
        _response(
            {
                "meta_data": {"version": 1, "created": 1785500527370},
                "series": [[chunk_start, None]],
            }
        ),
    ]

    with pytest.raises(ValueError, match="no usable day-ahead prices"):
        provider._request_forecast(start_date="2026-07-27", force_update=True)
