from unittest.mock import Mock

import pandas as pd
import pytest

from akkudoktoreos.server.dash import prediction


@pytest.mark.parametrize("dark", [False, True])
def test_prediction_preserves_series_and_local_times_without_pandas_deprecations(monkeypatch, dark):
    # Across the autumn DST transition, two distinct instants share a local wall time.
    timestamps = ["2026-10-25T00:30:00Z", "2026-10-25T01:30:00Z", "2026-10-25T02:30:00Z"]
    values = [1.0, 2.0, 3.0]
    config = {"test": "configuration"}
    keys = []

    def get(url, **kwargs):
        if url.endswith("/v1/config"):
            return Mock(json=lambda: config)
        assert url.endswith("/v1/prediction/series")
        keys.append(kwargs["params"]["key"])
        return Mock(json=lambda: {
            "data": dict(zip(timestamps, values)),
            "dtype": "float64",
            "tz": "Europe/Berlin",
        })

    monkeypatch.setattr(prediction.requests, "get", get)
    plots = []
    for name in (
        "PVForecast",
        "ElectricityPriceForecast",
        "WeatherTempAirHumidityForecast",
        "WeatherIrradianceForecast",
        "LoadForecast",
    ):
        plot = Mock(return_value=name)
        monkeypatch.setattr(prediction, name, plot)
        plots.append(plot)
    grid = Mock(return_value="prediction grid")
    monkeypatch.setattr(prediction, "Grid", grid)

    assert prediction.Prediction("localhost", 8503, {"dark": str(dark).lower()}) == "prediction grid"

    expected = pd.DataFrame({
        "date_time": pd.to_datetime(timestamps).tz_convert("Europe/Berlin").tz_localize(None),
        **{key: values for key in keys},
    })
    assert len(keys) == 10
    for plot in plots:
        plot.assert_called_once()
        actual, actual_config, timezone, actual_dark = plot.call_args.args
        pd.testing.assert_frame_equal(actual, expected)
        assert actual_config == config
        assert str(timezone) == "Europe/Berlin"
        assert actual_dark is dark
    grid.assert_called_once_with(*(plot.return_value for plot in plots), cols_max=2)
