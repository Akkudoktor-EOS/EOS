import pandas as pd
import pytest

from akkudoktoreos.server import eos as eos_server


class _FakeEms:
    async def run(self, **kwargs):
        return None


class _FakePrediction:
    def __init__(self):
        self.key_to_series_kwargs = None

    def key_to_series(self, **kwargs):
        self.key_to_series_kwargs = kwargs
        start = pd.Timestamp(kwargs["start_datetime"].isoformat())
        index = pd.date_range(start=start, periods=8, freq="15min")
        values = [1.0, 3.0, 5.0, 7.0, 10.0, 14.0, 18.0, 22.0]
        return pd.Series(values, index=index, name=kwargs["key"])


@pytest.mark.asyncio
async def test_strompreis_endpoint_averages_quarter_hour_prices(monkeypatch, config_eos):
    """Deprecated /strompreis aggregates 15-minute spot prices to hourly means."""
    prediction = _FakePrediction()

    monkeypatch.setattr(eos_server, "get_ems", lambda: _FakeEms())
    monkeypatch.setattr(eos_server, "get_prediction", lambda: prediction)

    result = await eos_server.fastapi_strompreis()

    assert result[:3] == [4.0, 16.0, 16.0]
    assert len(result) == 48
    assert prediction.key_to_series_kwargs["key"] == "elecprice_marketprice_wh"
