"""Tests for snow coverage in the native (pvlib) PV forecast provider.

The scenario tests replay real winter data (February 2026, see
``testdata/pvforecast_snow_feb2026.json``): hourly Open-Meteo weather and the plant's
measured production. Snow covered the modules on 02-19 and 02-20. The weather after
the simulated run start is the analysed weather, so the tests isolate the snow logic
from weather-forecast error.
"""

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import pendulum
import pvlib
import pytest
import pytest_asyncio

from akkudoktoreos.core.coreabc import get_ems, get_measurement
from akkudoktoreos.prediction.pvforecastakkudoktor import PVForecastAkkudoktor
from akkudoktoreos.prediction.pvforecastakkudoktorlocal import (
    PVForecastAkkudoktorLocal,
    PVForecastAkkudoktorLocalCommonSettings,
)

FIXTURE = Path(__file__).parent / "testdata" / "pvforecast_snow_feb2026.json"
TIMEZONE = "Europe/Berlin"

# Whole-degree grid point near the plant; the real site coordinates are not published.
LATITUDE = 48.0
LONGITUDE = 11.0

# Two 5.7 kWp roof halves plus two 1 kWp planes, as on the real plant. The two large
# planes share one inverter and each repeats its rating, as real configurations do.
PLANES = [
    {"surface_tilt": 28.0, "surface_azimuth": 17.0, "peakpower": 5.7, "inverter_paco": 10000},
    {"surface_tilt": 28.0, "surface_azimuth": 197.0, "peakpower": 5.7, "inverter_paco": 10000},
    {"surface_tilt": 21.0, "surface_azimuth": 28.0, "peakpower": 1.0, "inverter_paco": 1600},
    {"surface_tilt": 21.0, "surface_azimuth": 208.0, "peakpower": 1.0, "inverter_paco": 1600},
]

BASE_SETTINGS: dict[str, Any] = {"backend": "local", "resolution_minutes": 60, "snow_enabled": True}


def load_fixture() -> dict:
    with FIXTURE.open(encoding="utf-8") as handle:
        return json.load(handle)


def local(text: str) -> pendulum.DateTime:
    parsed = pendulum.parse(text, tz=TIMEZONE)
    assert isinstance(parsed, pendulum.DateTime)
    return parsed


def window_kwh(frame: pd.DataFrame, start: str, end: str) -> float:
    """Forecast energy between two local times (hourly rows, interval-start labels)."""
    index = pd.DatetimeIndex(frame.index).tz_convert(TIMEZONE)
    mask = (index >= pd.Timestamp(start, tz=TIMEZONE)) & (index < pd.Timestamp(end, tz=TIMEZONE))
    return float(frame["ac_power"].to_numpy()[mask].sum()) / 1000.0


def measured_kwh(fixture: dict, start: str, end: str) -> float:
    readings = {pd.Timestamp(t): v for t, v in fixture["pv_meter_kwh"]}
    return (
        readings[pd.Timestamp(end, tz=TIMEZONE).tz_convert("UTC")]
        - readings[pd.Timestamp(start, tz=TIMEZONE).tz_convert("UTC")]
    )


async def feed_meter(
    key: str, fixture: dict, until: pendulum.DateTime, override: dict | None = None
) -> None:
    """Store the cumulative meter readings up to the run start.

    ``override`` maps local hour starts to a replacement hourly yield in kWh, to
    simulate an outage or a meter jump on real data. Each test uses its own key:
    measurements are database-backed and would otherwise leak between tests.
    """
    measurement = get_measurement()
    replaced_hours = {pd.Timestamp(k, tz=TIMEZONE): v for k, v in (override or {}).items()}
    readings = [(pd.Timestamp(t), float(v)) for t, v in fixture["pv_meter_kwh"]]
    limit = pd.Timestamp(until.in_timezone("UTC").isoformat())
    cumulative = readings[0][1]
    previous = None
    for timestamp, value in readings:
        if timestamp > limit:
            break
        if previous is not None:
            hour_start, hour_value = previous
            replaced = replaced_hours.get(hour_start.tz_convert(TIMEZONE))
            cumulative += (value - hour_value) if replaced is None else replaced
        await measurement.update_value(
            pendulum.instance(timestamp.to_pydatetime()), key, round(cumulative, 6)
        )
        previous = (timestamp, value)


@pytest_asyncio.fixture
async def snow_instance(config_eos):
    config_eos.merge_settings_from_dict(
        {
            "general": {"latitude": LATITUDE, "longitude": LONGITUDE, "timezone": TIMEZONE},
            "prediction": {"hours": 48, "historic_hours": 48},
            "pvforecast": {
                "provider": "PVForecastAkkudoktor",
                "planes": PLANES,
                "akkudoktor": BASE_SETTINGS,
            },
        }
    )
    provider = PVForecastAkkudoktor()
    await provider.delete_by_datetime()
    await get_measurement().delete_by_datetime()
    yield provider
    await provider.delete_by_datetime()
    await get_measurement().delete_by_datetime()


async def run_at(
    instance: PVForecastAkkudoktorLocal,
    now: str,
    key: str | None,
    fixture: dict,
    override: dict | None = None,
    weather: dict | None = None,
    **settings,
) -> pd.DataFrame:
    """Compute the forecast frame as a run starting at local time ``now`` would.

    ``key`` None runs without PV measurements (snow model only).
    """
    start = local(now)
    get_ems().set_start_datetime(start)
    instance.config.pvforecast.akkudoktor = PVForecastAkkudoktorLocalCommonSettings(
        **{**BASE_SETTINGS, **settings}
    )
    if key is not None:
        instance.config.measurement.pv_production_emr_keys = [key]
        await feed_meter(key, fixture, start, override)
    else:
        instance.config.measurement.pv_production_emr_keys = None
    return await instance._forecast_frame(weather or fixture["openmeteo"])


def with_injected_snowfall(fixture: dict, start: str, hours: int, depth_until: str) -> dict:
    """Return weather that shows heavy snowfall the plant never had."""
    data = copy.deepcopy(fixture["openmeteo"])
    hourly = data["hourly"]
    first = pd.Timestamp(start, tz=TIMEZONE).tz_convert("UTC")
    last = pd.Timestamp(depth_until, tz=TIMEZONE).tz_convert("UTC")
    for position, stamp in enumerate(hourly["time"]):
        moment = pd.Timestamp(stamp, tz="UTC")
        if first < moment <= first + pd.Timedelta(hours=hours):
            hourly["snowfall"][position] = 2.0
        if first < moment <= last:
            hourly["snow_depth"][position] = max(hourly["snow_depth"][position] or 0.0, 0.08)
    return data


# ---------------------------------------------------------------- settings / request


def test_snow_is_off_by_default():
    settings = PVForecastAkkudoktorLocalCommonSettings()
    assert settings.snow_enabled is False
    assert settings.snow_feedback_enabled is True


@pytest.mark.parametrize("resolution_minutes", [15, 60])
def test_request_asks_for_hourly_snow_and_enough_history(snow_instance, resolution_minutes):
    snow_instance.config.pvforecast.akkudoktor = PVForecastAkkudoktorLocalCommonSettings(
        **{**BASE_SETTINGS, "resolution_minutes": resolution_minutes, "snow_lookback_days": 9}
    )
    response = Mock()
    response.url = "https://api.open-meteo.com/v1/forecast"
    response.raise_for_status.return_value = None
    block = "minutely_15" if resolution_minutes == 15 else "hourly"
    response.json.return_value = {block: {"time": []}}
    with patch(
        "akkudoktoreos.prediction.pvforecastakkudoktorlocal.requests.get", return_value=response
    ) as request:
        snow_instance._request_local_forecast(force_update=True)

    params = request.call_args.kwargs["params"]
    hourly = params["hourly"].split(",")
    assert "snowfall" in hourly and "snow_depth" in hourly
    if resolution_minutes == 60:
        assert "shortwave_radiation" in hourly
    else:
        assert "shortwave_radiation" in params["minutely_15"]
    assert params["past_days"] >= 9


def test_hourly_snowfall_is_spread_over_quarter_hours(snow_instance):
    snow_instance.config.pvforecast.akkudoktor = PVForecastAkkudoktorLocalCommonSettings(
        **{**BASE_SETTINGS, "resolution_minutes": 15}
    )
    data = {
        "hourly": {
            "time": ["2026-02-20T10:00", "2026-02-20T11:00", "2026-02-20T12:00"],
            "snowfall": [0.0, 2.0, 0.0],
            "snow_depth": [0.10, 0.12, 0.12],
        }
    }
    index = pd.date_range("2026-02-20 10:15", "2026-02-20 12:00", freq="15min", tz="UTC")
    snowfall, depth = snow_instance._snow_weather(data, index)

    # Open-Meteo's 11:00 value is the snowfall of 10:00-11:00.
    assert snowfall.loc["2026-02-20 10:15":"2026-02-20 11:00"].tolist() == [0.5] * 4
    assert snowfall.sum() == pytest.approx(2.0)
    assert depth.iloc[0] == pytest.approx(10.5)  # metres -> cm, interpolated
    assert depth.iloc[-1] == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_missing_snow_variables_fall_back_to_clear_modules(snow_instance, caplog):
    fixture = load_fixture()
    weather = copy.deepcopy(fixture["openmeteo"])
    del weather["hourly"]["snow_depth"]

    frame = await run_at(snow_instance, "2026-02-20 10:00", None, fixture, weather=weather)

    assert frame["snow_coverage"].max() == 0.0
    assert "missing snowfall or snow_depth" in caplog.text


# ---------------------------------------------------------------- snow model


def test_step_model_matches_pvlib_coverage_nrel():
    """Without measurements the stepwise model is pvlib's NREL model."""
    index = pd.date_range("2026-02-18 00:00", periods=96, freq="1h", tz="UTC")
    hours = np.arange(len(index))
    snowfall = np.where((hours >= 10) & (hours < 13), 1.5, 0.0)
    snowfall[60] = 0.7  # below the threshold: no new event
    temp_air = 2.0 * np.sin(hours / 24 * 2 * np.pi) - 0.5
    poa = np.clip(600.0 * np.sin((hours % 24 - 6) / 12 * np.pi), 0.0, None)
    depth = np.where(hours < 80, 12.0, 0.4)
    tilt = 28.0

    expected = pvlib.snow.coverage_nrel(
        snowfall=pd.Series(snowfall, index=index),
        poa_irradiance=pd.Series(poa, index=index),
        temp_air=pd.Series(temp_air, index=index),
        surface_tilt=tilt,
        snow_depth=pd.Series(depth, index=index),
        threshold_snowfall=1.0,
        threshold_depth=1.0,
    )
    coverage, corrections = PVForecastAkkudoktorLocal._simulate_snow_coverage(
        snowfall_cm=snowfall,
        snow_depth_cm=depth,
        temp_air=temp_air,
        poa_global=poa[np.newaxis, :],
        surface_tilt=np.full((1, len(index)), tilt),
        interval_hours=1.0,
        threshold_snowfall=1.0,
        threshold_depth=1.0,
    )

    assert corrections == 0
    # The series exercises full cover, partial sliding and the ground-clear reset.
    assert expected.max() == 1.0
    assert ((expected > 0.05) & (expected < 0.95)).any()
    assert expected.iloc[-1] == 0.0
    np.testing.assert_allclose(coverage[0], expected.to_numpy(), atol=1e-12)


@pytest.mark.asyncio
async def test_weather_alone_lowers_the_forecast_after_heavy_snowfall(snow_instance):
    """Run at midnight before the covered day: no measurement evidence yet."""
    fixture = load_fixture()
    clear = await run_at(snow_instance, "2026-02-20 00:00", None, fixture, snow_enabled=False)
    model = await run_at(snow_instance, "2026-02-20 00:00", None, fixture)

    day = ("2026-02-20 00:00", "2026-02-21 00:00")
    assert window_kwh(model, *day) < 0.6 * window_kwh(clear, *day)


# ---------------------------------------------------------------- measurement feedback


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "now",
    [
        # Snow came at noon; the model starts to cover the modules but lets it slide.
        "2026-02-19 12:00",
        # Covered all day at around 0 degC; the NREL model slides it off by noon.
        "2026-02-20 10:00",
        "2026-02-20 13:00",
        # Light snowfall the weather model never turns into coverage; 0.9 kWh made.
        "2026-02-15 12:00",
    ],
)
async def test_feedback_catches_snow_the_model_underestimates(snow_instance, now):
    fixture = load_fixture()
    end = f"{pd.Timestamp(now):%Y-%m-%d} 23:00"
    key = f"under_{now[5:10]}_{now[11:13]}"
    model = await run_at(snow_instance, now, None, fixture)
    feedback = await run_at(snow_instance, now, key, fixture)

    actual = measured_kwh(fixture, now, end)
    model_error = abs(window_kwh(model, now, end) - actual)
    feedback_error = abs(window_kwh(feedback, now, end) - actual)
    assert window_kwh(feedback, now, end) < window_kwh(model, now, end)
    assert feedback_error < 0.5 * model_error


@pytest.mark.asyncio
async def test_feedback_clears_snow_the_plant_does_not_have(snow_instance):
    """Weather says heavy snowfall until 09:00; the real plant produced 23 kWh that day."""
    fixture = load_fixture()
    weather = with_injected_snowfall(fixture, "2026-02-22 06:00", 3, "2026-02-23 00:00")
    now, end = "2026-02-22 11:00", "2026-02-22 23:00"

    clear = await run_at(snow_instance, now, None, fixture, weather=weather, snow_enabled=False)
    model = await run_at(snow_instance, now, None, fixture, weather=weather)
    feedback = await run_at(snow_instance, now, "phantom_snow", fixture, weather=weather)

    assert window_kwh(model, now, end) < 0.7 * window_kwh(clear, now, end)
    assert window_kwh(feedback, now, end) > 0.95 * window_kwh(clear, now, end)


@pytest.mark.asyncio
async def test_low_production_without_snow_is_not_learned_as_snow(snow_instance):
    """A morning outage on a warm, snow-free day (inverter fault, curtailment)."""
    fixture = load_fixture()
    outage = {f"2026-02-23 {hour:02d}:00": 0.0 for hour in range(7, 12)}
    now, end = "2026-02-23 12:00", "2026-02-23 23:00"
    model = await run_at(snow_instance, now, None, fixture)
    feedback = await run_at(snow_instance, now, "outage", fixture, override=outage)

    assert window_kwh(feedback, now, end) == pytest.approx(window_kwh(model, now, end), rel=1e-6)


@pytest.mark.asyncio
async def test_light_snowfall_on_a_productive_day_is_not_overcorrected(snow_instance):
    fixture = load_fixture()
    now, end = "2026-02-16 11:00", "2026-02-16 23:00"
    clear = await run_at(snow_instance, now, None, fixture, snow_enabled=False)
    feedback = await run_at(snow_instance, now, "light_snow", fixture)

    assert window_kwh(feedback, now, end) > 0.95 * window_kwh(clear, now, end)


@pytest.mark.asyncio
async def test_learned_slow_slide_fades_over_a_warm_night(snow_instance):
    """After the covered 02-20, a night at +3..5 degC clears the modules for 02-21."""
    fixture = load_fixture()
    now, end = "2026-02-21 06:00", "2026-02-21 23:00"
    clear = await run_at(snow_instance, now, None, fixture, snow_enabled=False)
    feedback = await run_at(snow_instance, now, "warm_night", fixture)

    assert window_kwh(feedback, now, end) > 0.95 * window_kwh(clear, now, end)


@pytest.mark.asyncio
async def test_meter_jump_is_ignored(snow_instance):
    """A sub-meter dropping out of a sum makes the meter jump by its lifetime count."""
    fixture = load_fixture()
    now, end = "2026-02-20 13:00", "2026-02-20 23:00"
    normal = await run_at(snow_instance, now, "jump_reference", fixture)
    jumped = await run_at(snow_instance, now, "jump", fixture, override={"2026-02-20 11:00": 940.0})

    assert window_kwh(jumped, now, end) == pytest.approx(window_kwh(normal, now, end), abs=0.3)


@pytest.mark.asyncio
async def test_feedback_can_be_disabled(snow_instance):
    fixture = load_fixture()
    now, end = "2026-02-20 13:00", "2026-02-20 23:00"
    model = await run_at(snow_instance, now, None, fixture)
    disabled = await run_at(
        snow_instance, now, "feedback_off", fixture, snow_feedback_enabled=False
    )

    assert window_kwh(disabled, now, end) == pytest.approx(window_kwh(model, now, end))


@pytest.mark.asyncio
async def test_stale_meter_readings_are_reported(snow_instance, caplog):
    fixture = load_fixture()
    snow_instance.config.measurement.pv_production_emr_keys = ["stale"]
    await feed_meter("stale", fixture, local("2026-02-20 09:00"))
    get_ems().set_start_datetime(local("2026-02-20 15:00"))

    await snow_instance._forecast_frame(fixture["openmeteo"])

    assert "older than 3 h" in caplog.text


# ---------------------------------------------------------------- calibration


@pytest.mark.asyncio
async def test_calibration_skips_intervals_with_possible_snow(snow_instance, caplog):
    fixture = load_fixture()
    get_ems().set_start_datetime(local("2026-02-23 23:00"))
    snow_instance.config.measurement.pv_production_emr_keys = ["calibration_snow"]
    await feed_meter("calibration_snow", fixture, local("2026-02-23 23:00"))

    factors = {}
    for snow_enabled in (False, True):
        snow_instance.config.pvforecast.akkudoktor = PVForecastAkkudoktorLocalCommonSettings(
            **{
                **BASE_SETTINGS,
                "snow_enabled": snow_enabled,
                "calibration_enabled": True,
                "calibration_days": 10,
                "calibration_outage_filter_enabled": False,
                "calibration_azimuth_bin_degrees": 0,
            }
        )
        raw = await snow_instance._forecast_frame(fixture["openmeteo"], calibrate=False)
        calibration = await snow_instance._fit_calibration(raw)
        assert calibration is not None
        factors[snow_enabled] = calibration[0]

    assert "possible snow on the modules" in caplog.text
    # Snow days only pull the plant level down.
    assert factors[True] > factors[False]
