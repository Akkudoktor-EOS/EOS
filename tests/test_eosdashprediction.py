from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from akkudoktoreos.server.dash import prediction

PREDICTION_KEYS = [
    "pvforecast_ac_power",
    "elecprice_marketprice_kwh",
    "weather_relative_humidity",
    "weather_temp_air",
    "weather_ghi",
    "weather_dni",
    "weather_dhi",
    "loadforecast_power_w",
    "loadakkudoktor_std_power_w",
    "loadakkudoktor_mean_power_w",
]


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
        if url.endswith("/v1/prediction/keys"):
            return Mock(json=lambda: PREDICTION_KEYS)
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


class TestAvailablePredictionSeries:
    @pytest.fixture(autouse=True)
    def dashboard(self, monkeypatch):
        self.config: dict = {
            "load": {
                "provider": "LoadImport",
                "loadakkudoktor": {"loadakkudoktor_year_energy_kwh": 4000},
            },
            "pvforecast": {"provider": "PVForecastImport"},
            "elecprice": {"provider": "ElecPriceImport"},
            "weather": {"provider": "WeatherImport"},
        }
        self.available = PREDICTION_KEYS[:-2]
        self.requested: list[str] = []
        self.failures: dict[str, int] = {}
        self.empty_keys: set[str] = set()
        self.null_keys: set[str] = set()
        self.keys_status = 200

        def response(payload, status=200):
            result = Mock(status_code=status, json=lambda: payload)
            if status >= 400:
                result.raise_for_status.side_effect = requests.HTTPError(
                    str(status), response=result
                )
            return result

        def get(url, **kwargs):
            if url.endswith("/v1/config"):
                return response(self.config)
            if url.endswith("/v1/prediction/keys"):
                if self.keys_status != 200:
                    return response({"detail": "Cannot list keys"}, self.keys_status)
                return response(self.available)
            assert url.endswith("/v1/prediction/series")
            key = kwargs["params"]["key"]
            self.requested.append(key)
            assert key in self.available
            if key in self.failures:
                return response({"detail": f"Cannot retrieve {key}"}, self.failures[key])
            values = {} if key in self.empty_keys else {
                "2026-10-30T00:00:00Z": None if key in self.null_keys else 100.0,
                "2026-10-30T00:15:00Z": None if key in self.null_keys else 200.0,
            }
            return response({"data": values, "dtype": "float64", "tz": "Europe/Berlin"})

        monkeypatch.setattr(prediction.requests, "get", get)
        # Keep real Bokeh plotting: missing columns must not be hidden by plot mocks.
        monkeypatch.setattr(prediction, "Bokeh", lambda plot: plot)
        self.grid = Mock(return_value="prediction grid")
        self.error = Mock(return_value="prediction error")
        self.message = Mock(return_value="prediction message")
        monkeypatch.setattr(prediction, "Grid", self.grid)
        monkeypatch.setattr(prediction, "Error", self.error)
        monkeypatch.setattr(prediction, "P", self.message)

    def test_loadimport_preserves_all_five_charts_without_optional_statistics(self):
        assert prediction.Prediction("localhost", 8503) == "prediction grid"
        assert self.requested == PREDICTION_KEYS[:-2]
        plots = self.grid.call_args.args
        assert len(plots) == 5
        load = plots[-1]
        assert load.title.text == "Load Prediction (LoadImport)"
        assert [renderer.glyph.y for renderer in load.renderers] == ["loadforecast_power_w"]
        assert "stddev" not in load.extra_y_ranges
        self.error.assert_not_called()

    @pytest.mark.parametrize("provider", ["LoadAkkudoktor", "LoadAkkudoktorAdjusted"])
    def test_akkudoktor_statistics_are_preserved(self, provider):
        self.config["load"]["provider"] = provider
        self.available = PREDICTION_KEYS
        assert prediction.Prediction("localhost", 8503) == "prediction grid"
        load = self.grid.call_args.args[-1]
        assert [renderer.glyph.y for renderer in load.renderers] == [
            "loadforecast_power_w",
            "loadakkudoktor_mean_power_w",
            "loadakkudoktor_std_power_w",
        ]
        assert "stddev" in load.extra_y_ranges
        if provider == "LoadAkkudoktorAdjusted":
            assert "4000 kWh" in load.title.text

    @pytest.mark.parametrize(
        "optional_key",
        ["loadakkudoktor_mean_power_w", "loadakkudoktor_std_power_w"],
    )
    def test_optional_statistics_can_be_available_independently(self, optional_key):
        self.available = [*PREDICTION_KEYS[:-2], optional_key]
        assert prediction.Prediction("localhost", 8503) == "prediction grid"
        load = self.grid.call_args.args[-1]
        assert [renderer.glyph.y for renderer in load.renderers] == [
            "loadforecast_power_w", optional_key
        ]
        assert ("stddev" in load.extra_y_ranges) == optional_key.endswith("std_power_w")

    def test_series_disappearing_after_key_discovery_does_not_abort_other_charts(self):
        self.available = PREDICTION_KEYS
        self.failures = {
            "elecprice_marketprice_kwh": 404,
            "loadakkudoktor_std_power_w": 404,
        }
        assert prediction.Prediction("localhost", 8503) == "prediction grid"
        assert self.requested == PREDICTION_KEYS
        plots = self.grid.call_args.args
        assert len(plots) == 4
        assert "stddev" not in plots[-1].extra_y_ranges
        self.error.assert_not_called()

    @pytest.mark.parametrize("status", [400, 500])
    def test_other_http_errors_remain_visible(self, status):
        self.failures = {"pvforecast_ac_power": status}
        assert prediction.Prediction("localhost", 8503) == "prediction error"
        assert "Cannot retrieve pvforecast_ac_power" in self.error.call_args.args[0]
        self.grid.assert_not_called()

    def test_key_discovery_errors_remain_visible(self):
        self.keys_status = 500
        assert prediction.Prediction("localhost", 8503) == "prediction error"
        assert "Cannot list keys" in self.error.call_args.args[0]
        assert self.requested == []

    def test_unavailable_charts_are_skipped(self):
        self.available = ["loadforecast_power_w"]
        assert prediction.Prediction("localhost", 8503) == "prediction grid"
        assert len(self.grid.call_args.args) == 1
        assert self.grid.call_args.args[0].title.text == "Load Prediction (LoadImport)"

    @pytest.mark.parametrize("scenario", ["no_keys", "empty", "null", "disappeared"])
    def test_no_usable_series_shows_a_message(self, scenario):
        self.available = ["loadforecast_power_w"]
        if scenario == "no_keys":
            self.available = []
        elif scenario == "empty":
            self.empty_keys = {"loadforecast_power_w"}
        elif scenario == "null":
            self.null_keys = {"loadforecast_power_w"}
        else:
            self.failures = {"loadforecast_power_w": 404}
        assert prediction.Prediction("localhost", 8503) == "prediction message"
        self.message.assert_called_once_with(
            "No prediction data available.", cls="text-center"
        )
        self.grid.assert_not_called()
        self.error.assert_not_called()

    def test_empty_statistics_do_not_create_an_invalid_axis(self):
        self.available = PREDICTION_KEYS
        self.null_keys = set(PREDICTION_KEYS[-2:])
        assert prediction.Prediction("localhost", 8503) == "prediction grid"
        load = self.grid.call_args.args[-1]
        assert [renderer.glyph.y for renderer in load.renderers] == ["loadforecast_power_w"]
        assert "stddev" not in load.extra_y_ranges
