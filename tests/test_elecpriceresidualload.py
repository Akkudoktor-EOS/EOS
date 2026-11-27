# ruff: noqa: S101

"""Tests of the residual-load electricity price provider without network access."""

import datetime as dt
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction import elecpriceresidualloadcore as core
from akkudoktoreos.prediction import elecpriceresidualloaddata as data
from akkudoktoreos.prediction.elecprice import ElecPriceCommonSettings, elecprice_provider_ids
from akkudoktoreos.prediction.elecpriceenergycharts import EnergyChartsElecPrice
from akkudoktoreos.prediction.elecpriceresidualload import ElecPriceResidualLoad
from akkudoktoreos.prediction.elecpriceresidualloadengine import ForecastOutcome
from akkudoktoreos.utils.datetimeutil import to_datetime

DIR_TESTDATA = Path(__file__).absolute().parent.joinpath("testdata")


@pytest.fixture
def smard_recording() -> dict[str, Any]:
    """Recorded SMARD index and weekly chunk (day-ahead price DE-LU, first 72 hours)."""
    return json.loads(
        DIR_TESTDATA.joinpath("elecpriceresidualload_smard.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def provider(config_eos):
    """Configure and return the residual-load provider singleton."""
    config_eos.elecprice = ElecPriceCommonSettings(provider="ElecPriceResidualLoad")
    get_ems().set_start_datetime(to_datetime("2026-11-23 00:00:00", in_timezone="Europe/Berlin"))
    provider = ElecPriceResidualLoad()
    provider.highest_orig_datetime = None
    assert provider.enabled()
    provider._db_reset_state()
    return provider


def test_provider_id_is_registered() -> None:
    """The provider id is valid for the configuration without a prediction instance."""
    assert ElecPriceResidualLoad.provider_id() == "ElecPriceResidualLoad"
    assert "ElecPriceResidualLoad" in elecprice_provider_ids()


def test_production_config_value_is_accepted(config_eos) -> None:
    """The production configuration (provider only, no own settings) validates."""
    settings = ElecPriceCommonSettings.model_validate(
        {"provider": "ElecPriceResidualLoad", "smard": {"region": "DE-LU"}}
    )
    assert settings.provider == "ElecPriceResidualLoad"


def test_price_is_known_from_13_local_the_day_before() -> None:
    """Day-ahead prices of a local day are known at 13:00 local time of the previous day."""
    hours = pd.DatetimeIndex(["2026-11-24T00:00Z", "2026-11-24T22:00Z"])  # 01:00 and 23:00 CET
    available = core.price_available_at(hours)
    assert list(available) == [pd.Timestamp("2026-11-23T12:00Z")] * 2


@pytest.mark.parametrize(
    ("day", "expected_hours"),
    [
        (dt.date(2026, 3, 29), 23),
        (dt.date(2026, 10, 25), 25),
        (dt.date(2026, 11, 23), 24),
    ],
)
def test_local_day_hours_follow_daylight_saving(day: dt.date, expected_hours: int) -> None:
    """A local day has 23, 24 or 25 hours."""
    assert len(core.local_day_hours(day)) == expected_hours


def test_german_holidays_include_moveable_feasts() -> None:
    """Easter-dependent holidays are derived."""
    holidays = core.german_holidays(2026)
    assert dt.date(2026, 4, 3) in holidays  # Good Friday
    assert dt.date(2026, 12, 25) in holidays


def test_known_hourly_price_keeps_only_complete_hours(provider) -> None:
    """Quarter-hour prices become hourly means in ct/kWh, an incomplete last hour is dropped."""
    index = pd.date_range("2026-11-23T10:00Z", periods=6, freq="15min")  # 10:00 .. 11:15
    raw_wh = pd.Series([0.0001] * 4 + [0.0003, 0.0003], index=index)  # EUR/Wh
    hourly = provider._known_hourly_ct(raw_wh)
    assert list(hourly.index) == [pd.Timestamp("2026-11-23T10:00Z")]
    assert hourly.iloc[0] == pytest.approx(10.0)


def test_last_complete_local_day(provider) -> None:
    """The latest local day with all hours known is found, a partial day is skipped."""
    complete = core.local_day_hours(dt.date(2026, 11, 22))
    partial = core.local_day_hours(dt.date(2026, 11, 23))[:10]
    hourly = pd.Series(8.0, index=complete.append(partial))
    assert provider._last_complete_local_day(hourly) == dt.date(2026, 11, 22)
    assert provider._last_complete_local_day(hourly.iloc[0:0]) is None


def test_fetch_smard_series_from_recorded_response(smard_recording) -> None:
    """The weekly SMARD chunks are combined into an hourly float series."""
    calls: list[str] = []

    def getter(url: str, params: dict | None = None) -> bytes:
        calls.append(url)
        if url.endswith("index_hour.json"):
            return json.dumps(smard_recording["index"]).encode()
        return json.dumps(smard_recording["chunk"]).encode()

    since = pd.Timestamp(smard_recording["chunk"]["series"][0][0], unit="ms", tz="UTC")
    series = data.fetch_smard_series(getter, "price_eur_mwh", since)

    assert len(calls) == 2
    assert series.index.is_monotonic_increasing
    assert isinstance(series.index, pd.DatetimeIndex)
    assert series.index.tz is not None
    assert series.dtype == float
    assert series.iloc[0] == pytest.approx(smard_recording["chunk"]["series"][0][1])


def test_parse_weather_returns_all_lead_frames() -> None:
    """An Open-Meteo previous-runs response is split into one frame per lead day."""
    times = pd.date_range("2026-11-20", periods=3, freq="1h", tz="UTC").strftime("%Y-%m-%dT%H:%M")
    entries = []
    for _ in core.WEATHER_POINTS:
        hourly: dict[str, Any] = {"time": list(times)}
        for lead in core.WEATHER_LEADS:
            for variable in core.WEATHER_VARS:
                hourly[f"{variable}_previous_day{lead}"] = [1.0, 2.0, 3.0]
        entries.append({"hourly": hourly})
    frames = data.parse_weather(json.dumps(entries).encode())
    assert set(frames) == set(core.WEATHER_LEADS)
    for frame in frames.values():
        assert list(frame.columns) == core.weather_columns()
        assert len(frame) == 3

    with pytest.raises(ValueError, match="Punkte"):
        data.parse_weather(json.dumps(entries[:1]).encode())


def test_fetch_error_falls_back_to_smard(provider) -> None:
    """Without usable Energy-Charts data the SMARD result is returned unchanged."""
    smard = EnergyChartsElecPrice(
        unix_seconds=[1_795_000_000],
        price=[80.0],
        license_info="SMARD",
        unit="EUR/MWh",
        deprecated=False,
    )
    with (
        patch(
            "akkudoktoreos.prediction.elecpriceresidualload.ElecPriceSMARD._request_forecast",
            return_value=smard,
        ),
        patch(
            "akkudoktoreos.prediction.elecpriceresidualload.ElecPriceEnergyCharts._request_forecast",
            side_effect=ValueError("down"),
        ),
    ):
        assert provider._request_forecast(start_date="2026-11-23") is smard


@pytest.mark.asyncio
async def test_model_prices_replace_extrapolation_and_uncertainty_is_written(
    provider, tmp_path
) -> None:
    """Model prices are stored for the missing days and the quantile file is written."""
    hours = core.local_day_hours(dt.date(2026, 11, 24))
    point = pd.Series(7.5, index=hours)
    quantiles = np.tile(np.array([[6.0], [7.0], [7.5], [8.0], [9.0]]), (1, len(hours)))
    outcome = ForecastOutcome(
        forecast=core.DayForecast(
            day=dt.date(2026, 11, 22),
            point=point,
            resid_hat=pd.Series(0.0, index=hours),
            offset=0.5,
            anchor_hours=24,
        ),
        quantiles=quantiles,
        uncertainty_reason=None,
        model_month=dt.date(2026, 11, 1),
        calibration=None,
        revision=None,
    )

    class StubEngine:
        def forecast(self, day, known, today):
            return outcome, "ok"

    provider.config.general.data_folder_path = tmp_path
    known = pd.Series(
        0.00008, index=pd.date_range("2026-11-22T23:00Z", "2026-11-23T22:45Z", freq="15min")
    )
    await provider.key_from_series("elecprice_marketprice_raw_wh", known)
    provider.highest_orig_datetime = to_datetime("2026-11-24 00:00:00", in_timezone="Europe/Berlin")

    with patch(
        "akkudoktoreos.prediction.elecpriceresidualload.get_engine", return_value=StubEngine()
    ):
        await provider._apply_model()

    stored = await provider.key_to_raw_series(
        key="elecprice_marketprice_raw_wh",
        start_datetime=to_datetime("2026-11-24 00:00:00", in_timezone="Europe/Berlin"),
        end_datetime=to_datetime("2026-11-24 23:59:00", in_timezone="Europe/Berlin"),
    )
    assert len(stored) >= len(hours)
    np.testing.assert_allclose(stored.to_numpy(), 7.5e-5)  # 7.5 ct/kWh in EUR/Wh
    written = json.loads((tmp_path / "elecprice_residualload" / "uncertainty.json").read_text())
    assert written["provider"] == "ElecPriceResidualLoad"
    assert written["unit"] == "ct/kWh"
    assert len(written["hours_utc"]) == len(hours)
    assert set(written["quantiles"]) == {"0.1", "0.25", "0.5", "0.75", "0.9"}
