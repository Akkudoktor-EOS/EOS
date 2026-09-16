"""Tests for the native (pvlib) PV forecast provider."""

from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pandas as pd
import pendulum
import pvlib
import pytest
import pytest_asyncio
import requests

from akkudoktoreos.core.coreabc import get_ems, get_measurement, get_prediction
from akkudoktoreos.prediction.pvforecastakkudoktor import PVForecastAkkudoktor
from akkudoktoreos.prediction.pvforecastakkudoktorlocal import (
    PVForecastAkkudoktorLocal,
    PVForecastAkkudoktorLocalCommonSettings,
)

LATITUDE = 52.52
LONGITUDE = 13.405

# A window that starts well before `START` so the calibration fit has past data.
WINDOW_START = pendulum.datetime(2025, 6, 1, 0, 0, tz="UTC")
WINDOW_END = pendulum.datetime(2025, 6, 20, 0, 0, tz="UTC")
START = pendulum.datetime(2025, 6, 15, 0, 0, tz="UTC")


def synthetic_openmeteo(resolution_minutes: int = 15, models: list[str] | None = None) -> dict:
    """Build an Open-Meteo-shaped response from a pvlib clear-sky series.

    Open-Meteo stamps an interval mean with the interval END, and that is what the
    provider expects, so the values are generated at those stamps directly.
    """
    freq = f"{resolution_minutes}min"
    index = pd.date_range(
        start=WINDOW_START.format("YYYY-MM-DD HH:mm"),
        end=WINDOW_END.format("YYYY-MM-DD HH:mm"),
        freq=freq,
        tz="UTC",
    )
    location = pvlib.location.Location(LATITUDE, LONGITUDE, tz="UTC", altitude=37.0)
    clearsky = location.get_clearsky(index, model="ineichen")

    block = "minutely_15" if resolution_minutes == 15 else "hourly"
    values = {
        "shortwave_radiation": clearsky["ghi"].round(1).tolist(),
        "diffuse_radiation": clearsky["dhi"].round(1).tolist(),
        "direct_normal_irradiance": clearsky["dni"].round(1).tolist(),
        "temperature_2m": [20.0] * len(index),
        "relative_humidity_2m": [50.0] * len(index),
        "wind_speed_10m": [2.0] * len(index),
    }

    data: dict = {"elevation": 37.0}
    payload = {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in index]}
    if models:
        # Multi-model requests come back with one suffixed series per member.
        for name in models:
            for key, series in values.items():
                payload[f"{key}_{name}"] = series
    else:
        payload.update(values)
    data[block] = payload
    return data


@pytest_asyncio.fixture
async def pvforecast_instance(config_eos):
    config_eos.merge_settings_from_dict(
        {
            "general": {"latitude": LATITUDE, "longitude": LONGITUDE, "timezone": "UTC"},
            "prediction": {"hours": 96, "historic_hours": 48},
            "pvforecast": {
                "provider": "PVForecastAkkudoktor",
                "planes": [
                    {
                        "surface_tilt": 30.0,
                        "surface_azimuth": 180.0,
                        "peakpower": 10.0,
                        "inverter_paco": 10000,
                        "loss": 14.0,
                    }
                ],
                "akkudoktor": {"backend": "local", "resolution_minutes": 15},
            },
        }
    )
    provider = PVForecastAkkudoktor()
    get_ems().set_start_datetime(START)
    await provider.delete_by_datetime()
    await get_measurement().delete_by_datetime()
    yield provider
    await provider.delete_by_datetime()
    await get_measurement().delete_by_datetime()


def test_provider_id(pvforecast_instance):
    assert PVForecastAkkudoktorLocal.provider_id() == "PVForecastAkkudoktor"
    assert pvforecast_instance.enabled() is True


@pytest.mark.parametrize("value", [0, 5, 30, 61])
def test_resolution_must_be_15_or_60(value):
    with pytest.raises(ValueError, match="resolution_minutes must be 15 or 60"):
        PVForecastAkkudoktorLocalCommonSettings(resolution_minutes=value)


def test_invalid_transposition_model():
    with pytest.raises(ValueError, match="Invalid transposition_model"):
        PVForecastAkkudoktorLocalCommonSettings(transposition_model="nonsense")


@pytest.mark.asyncio
async def test_forecast_frame_is_quarter_hourly_and_plausible(pvforecast_instance):
    frame = await pvforecast_instance._forecast_frame(synthetic_openmeteo())

    assert not frame.empty
    deltas = frame.index.to_series().diff().dropna().unique()
    assert list(deltas) == [pd.Timedelta(minutes=15)]

    # A 10 kWp south-facing roof under clear June skies: below the inverter cap,
    # but a substantial fraction of it.
    peak = frame["ac_power"].max()
    assert 5000.0 < peak <= 10000.0
    assert (frame["ac_power"] >= 0.0).all()
    assert (frame["ac_power"] <= frame["dc_power"] + 1e-6).all()

    # Nights are dark.
    midnight = frame.between_time("00:00", "01:00")["ac_power"]
    assert midnight.max() == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_records_are_shifted_to_interval_start(pvforecast_instance):
    """Open-Meteo labels an interval by its end; EOS labels it by its start."""
    shifted = await pvforecast_instance._forecast_frame(synthetic_openmeteo())

    pvforecast_instance.config.pvforecast.akkudoktor = PVForecastAkkudoktorLocalCommonSettings(
        resolution_minutes=15, shift_to_interval_start=False
    )
    raw = await pvforecast_instance._forecast_frame(synthetic_openmeteo())

    assert raw.index[0] - shifted.index[0] == pd.Timedelta(minutes=15)
    assert raw["ac_power"].to_numpy() == pytest.approx(shifted["ac_power"].to_numpy())


@pytest.mark.asyncio
async def test_ensemble_members_are_averaged(pvforecast_instance):
    """Several models in one request must be averaged, not dropped or duplicated."""
    single = await pvforecast_instance._forecast_frame(synthetic_openmeteo())
    ensemble = await pvforecast_instance._forecast_frame(
        synthetic_openmeteo(models=["icon_seamless", "gfs_seamless", "ecmwf_ifs025"])
    )

    # The synthetic members are identical, so the mean must reproduce the single run.
    assert ensemble["ac_power"].to_numpy() == pytest.approx(single["ac_power"].to_numpy())


def test_horizon_elevation_wraps_around_north():
    horizon = PVForecastAkkudoktorLocal._horizon_elevation(
        [0.0, 10.0, 20.0, 30.0], np.array([0.0, 90.0, 180.0, 270.0, 359.999])
    )
    assert horizon[:4] == pytest.approx([0.0, 10.0, 20.0, 30.0])
    # Wrapping back to due north interpolates from 30 deg towards 0 deg.
    assert horizon[4] == pytest.approx(0.0, abs=0.01)


@pytest.mark.asyncio
async def test_horizon_shading_reduces_yield(pvforecast_instance):
    baseline = await pvforecast_instance._forecast_frame(synthetic_openmeteo())

    # A 40 deg wall all around blocks the beam for most of the day.
    pvforecast_instance.config.pvforecast.planes[0].userhorizon = [40.0] * 12
    shaded = await pvforecast_instance._forecast_frame(synthetic_openmeteo())

    assert shaded["ac_power"].sum() < baseline["ac_power"].sum() * 0.9
    assert shaded["ac_power"].min() >= 0.0

    # The low morning sun (below 40 deg elevation until ~07:00 UTC in June) is behind
    # the wall, so its beam is gone entirely and only diffuse is left.
    assert (
        shaded["ac_power"].between_time("05:00", "07:00").sum()
        < baseline["ac_power"].between_time("05:00", "07:00").sum() * 0.5
    )


@pytest.mark.asyncio
async def test_update_data_writes_records(pvforecast_instance):
    with patch.object(
        PVForecastAkkudoktorLocal, "_request_local_forecast", return_value=synthetic_openmeteo()
    ):
        await pvforecast_instance._update_data(force_update=True)

    dates, values = await pvforecast_instance.key_to_lists("pvforecast_ac_power")
    assert len(dates) > 0
    assert all(value is not None for value in values)
    _, dc_values = await pvforecast_instance.key_to_lists("pvforecast_dc_power")
    assert all(value is not None for value in dc_values)


async def _feed_measurements(
    instance: PVForecastAkkudoktorLocal, frame: pd.DataFrame, bias: float, key: str
) -> None:
    """Write cumulative PV meter readings that are `bias` times the modelled power.

    Each test passes its own `key`. `Measurement` is database-backed, so clearing the
    in-memory record list would not remove readings another test already stored.
    """
    instance.config.measurement.pv_production_emr_keys = [key]
    measurement = get_measurement()

    hourly = frame["ac_power"].resample("1h").mean()
    hourly = hourly.loc[hourly.index < START]

    cumulative = 0.0
    for timestamp, power_w in hourly.items():
        timestamp = pd.Timestamp(str(timestamp))
        await measurement.update_value(
            pendulum.instance(timestamp.to_pydatetime()), key, round(cumulative, 6)
        )
        cumulative += float(power_w) * bias / 1000.0
    # Closing reading so the last interval has a difference to work with.
    await measurement.update_value(
        pendulum.instance(hourly.index[-1].to_pydatetime()).add(hours=1),
        key,
        round(cumulative, 6),
    )


async def _feed_measurements_with_recent_outage(
    instance: PVForecastAkkudoktorLocal,
    frame: pd.DataFrame,
    healthy_bias: float,
    outage_bias: float,
    outage_days: int,
    key: str,
) -> None:
    """Write a healthy meter history followed by demand-limited PV production."""
    instance.config.measurement.pv_production_emr_keys = [key]
    measurement = get_measurement()

    hourly = frame["ac_power"].resample("1h").mean()
    hourly = hourly.loc[hourly.index < START]
    outage_start = START.subtract(days=outage_days)
    healthy_looking_gap = outage_start.add(days=2).date()

    cumulative = 0.0
    for timestamp, power_w in hourly.items():
        timestamp = pd.Timestamp(str(timestamp))
        await measurement.update_value(
            pendulum.instance(timestamp.to_pydatetime()), key, round(cumulative, 6)
        )
        in_outage = timestamp >= outage_start and timestamp.date() != healthy_looking_gap
        bias = outage_bias if in_outage else healthy_bias
        cumulative += float(power_w) * bias / 1000.0
    await measurement.update_value(
        pendulum.instance(hourly.index[-1].to_pydatetime()).add(hours=1),
        key,
        round(cumulative, 6),
    )


async def _feed_native_quarter_hour_measurements(
    instance: PVForecastAkkudoktorLocal, frame: pd.DataFrame, bias: float, key: str
) -> None:
    """Write cumulative PV readings at the provider's native 15-minute cadence."""
    instance.config.measurement.pv_production_emr_keys = [key]
    measurement = get_measurement()
    slots = frame.loc[frame.index < START, "ac_power"]

    cumulative = 0.0
    for timestamp, power_w in slots.items():
        timestamp = pd.Timestamp(str(timestamp))
        await measurement.update_value(
            pendulum.instance(timestamp.to_pydatetime()), key, round(cumulative, 6)
        )
        cumulative += float(power_w) * 0.25 * bias / 1000.0
    await measurement.update_value(
        pendulum.instance(slots.index[-1].to_pydatetime()).add(minutes=15),
        key,
        round(cumulative, 6),
    )


@pytest.mark.asyncio
async def test_calibration_is_off_by_default(pvforecast_instance):
    frame = await pvforecast_instance._forecast_frame(synthetic_openmeteo(), calibrate=False)
    assert await pvforecast_instance._fit_calibration(frame) is None


@pytest.mark.asyncio
async def test_calibration_skips_without_measurement_keys(pvforecast_instance):
    pvforecast_instance.config.pvforecast.akkudoktor = PVForecastAkkudoktorLocalCommonSettings(
        calibration_enabled=True
    )
    frame = await pvforecast_instance._forecast_frame(synthetic_openmeteo(), calibrate=False)
    assert await pvforecast_instance._fit_calibration(frame) is None


@pytest.mark.asyncio
async def test_calibration_recovers_a_systematic_bias(pvforecast_instance):
    """A plant that consistently delivers 80% of the model must be corrected to 0.8."""
    pvforecast_instance.config.pvforecast.akkudoktor = PVForecastAkkudoktorLocalCommonSettings(
        calibration_enabled=True,
        calibration_days=14,
        calibration_azimuth_bin_degrees=0,
    )

    frame = await pvforecast_instance._forecast_frame(synthetic_openmeteo(), calibrate=False)
    await _feed_measurements(pvforecast_instance, frame, bias=0.8, key="pv_bias_emr")

    calibration = await pvforecast_instance._fit_calibration(frame)
    assert calibration is not None
    global_factor, factors = calibration
    assert global_factor == pytest.approx(0.8, abs=0.03)
    assert factors == pytest.approx([global_factor])

    corrected = pvforecast_instance._apply_calibration(frame, factors, 10000.0)
    assert corrected["ac_power"].sum() == pytest.approx(frame["ac_power"].sum() * global_factor)


@pytest.mark.asyncio
async def test_calibration_excludes_recent_demand_limited_outage(pvforecast_instance, caplog):
    """A battery outage must not teach demand-limited PV as available generation."""
    pvforecast_instance.config.pvforecast.akkudoktor = PVForecastAkkudoktorLocalCommonSettings(
        calibration_enabled=True,
        calibration_days=5,
        calibration_reference_days=14,
        calibration_azimuth_bin_degrees=0,
        calibration_min_factor=0.2,
    )

    frame = await pvforecast_instance._forecast_frame(synthetic_openmeteo(), calibrate=False)
    await _feed_measurements_with_recent_outage(
        pvforecast_instance,
        frame,
        healthy_bias=0.8,
        outage_bias=0.2,
        outage_days=5,
        key="pv_demand_limited_emr",
    )

    with caplog.at_level("INFO"):
        calibration = await pvforecast_instance._fit_calibration(frame)

    assert calibration is not None
    global_factor, factors = calibration
    assert global_factor == pytest.approx(0.8, abs=0.03)
    assert factors == pytest.approx([global_factor])
    assert "excluded probable outage/curtailment days" in caplog.text
    # A single statistically healthy-looking day inside the outage is bridged.
    assert "2025-06-12" in caplog.text

    # Filtering only selects training data. Applying a global factor preserves the
    # native quarter-hour shape instead of replacing it with hourly bucket values.
    corrected = pvforecast_instance._apply_calibration(frame, factors, 10000.0)
    producing = frame["ac_power"] > 0.0
    assert corrected.index.to_series().diff().dropna().unique().tolist() == [
        pd.Timedelta(minutes=15)
    ]
    assert (
        corrected.loc[producing, "ac_power"] / frame.loc[producing, "ac_power"]
    ).to_numpy() == pytest.approx(np.full(producing.sum(), global_factor))


@pytest.mark.asyncio
async def test_calibration_uses_real_quarter_hour_measurements(pvforecast_instance, caplog):
    """Native meter slots permit shape calibration without inventing intrahour data."""
    pvforecast_instance.config.pvforecast.akkudoktor = PVForecastAkkudoktorLocalCommonSettings(
        calibration_enabled=True,
        calibration_days=14,
        calibration_reference_days=14,
        calibration_azimuth_bin_degrees=0,
    )
    frame = await pvforecast_instance._forecast_frame(synthetic_openmeteo(), calibrate=False)
    await _feed_native_quarter_hour_measurements(
        pvforecast_instance, frame, bias=0.8, key="pv_quarter_hour_emr"
    )

    assert (
        await pvforecast_instance._calibration_interval_minutes(START.subtract(days=14), START)
        == 15
    )
    with caplog.at_level("INFO"):
        calibration = await pvforecast_instance._fit_calibration(frame)

    assert calibration is not None
    global_factor, _ = calibration
    assert global_factor == pytest.approx(0.8, abs=0.03)
    assert "15-minute measurement resolution" in caplog.text or "15-minute intervals" in caplog.text


@pytest.mark.asyncio
async def test_calibration_factor_is_clamped(pvforecast_instance):
    """A wildly wrong meter must not be allowed to swing the forecast."""
    pvforecast_instance.config.pvforecast.akkudoktor = PVForecastAkkudoktorLocalCommonSettings(
        calibration_enabled=True,
        calibration_days=14,
        calibration_azimuth_bin_degrees=0,
        calibration_min_factor=0.9,
        calibration_max_factor=1.1,
    )

    frame = await pvforecast_instance._forecast_frame(synthetic_openmeteo(), calibrate=False)
    await _feed_measurements(pvforecast_instance, frame, bias=0.2, key="pv_clamp_emr")

    calibration = await pvforecast_instance._fit_calibration(frame)
    assert calibration is not None
    global_factor, _ = calibration
    assert global_factor == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_calibration_respects_the_inverter_cap(pvforecast_instance):
    frame = await pvforecast_instance._forecast_frame(synthetic_openmeteo(), calibrate=False)
    corrected = PVForecastAkkudoktorLocal._apply_calibration(frame, np.array([1.5]), 10000.0)
    assert corrected["ac_power"].max() <= 10000.0 + 1e-6


def test_azimuth_calibration_is_interpolated_smoothly():
    """Azimuth correction must not introduce steps into the quarter-hour plan."""
    frame = pd.DataFrame(
        {
            "solar_azimuth": [44.9, 45.0, 45.1, 134.9, 135.0, 135.1],
            "dc_power": [1000.0] * 6,
            "ac_power": [1000.0] * 6,
        }
    )
    corrected = PVForecastAkkudoktorLocal._apply_calibration(
        frame, np.array([0.5, 1.0, 1.5, 1.0]), ac_cap_w=2000.0
    )

    assert corrected.loc[1, "ac_power"] == pytest.approx(500.0)
    assert corrected.loc[4, "ac_power"] == pytest.approx(1000.0)
    values = corrected["ac_power"].to_numpy(dtype=float)
    assert abs(values[2] - values[0]) < 2.0
    assert abs(values[5] - values[3]) < 2.0


def test_azimuth_shape_preserves_each_days_global_energy():
    """The EMS gets a changed shape without a changed daily energy budget."""
    index = pd.date_range("2025-06-01", periods=8, freq="12h", tz="UTC")
    frame = pd.DataFrame(
        {
            "solar_azimuth": [45.0, 225.0, 45.0, 225.0, 45.0, 225.0, 45.0, 225.0],
            "dc_power": [100.0, 300.0, 200.0, 200.0, 300.0, 100.0, 150.0, 250.0],
            "ac_power": [100.0, 300.0, 200.0, 200.0, 300.0, 100.0, 150.0, 250.0],
        },
        index=index,
    )
    global_factor = 0.8
    corrected = PVForecastAkkudoktorLocal._apply_calibration(
        frame,
        np.array([0.5, 1.0, 1.5, 1.0]),
        ac_cap_w=10_000.0,
        global_factor=global_factor,
        timezone="UTC",
    )

    raw_daily = frame["ac_power"].resample("1D").sum()
    corrected_daily = corrected["ac_power"].resample("1D").sum()
    assert corrected_daily.to_numpy() == pytest.approx(raw_daily.to_numpy() * global_factor)
    assert np.std(corrected["ac_power"] / frame["ac_power"]) > 0.01


def test_azimuth_shape_fit_preserves_global_energy(pvforecast_instance):
    """Intraday correction must not undo the independently fitted daily kWh."""
    centers = np.arange(22.5, 360.0, 45.0)
    azimuth = np.repeat(centers, 20)
    modelled_kwh = np.ones(len(azimuth))
    expected_shape = np.repeat([0.8, 0.9, 1.0, 1.1, 1.2, 1.1, 1.0, 0.9], 20)
    global_factor = 0.8
    measured_kwh = modelled_kwh * global_factor * expected_shape

    factors = pvforecast_instance._fit_azimuth_factors(
        modelled_kwh, measured_kwh, azimuth, global_factor
    )
    fitted_scale = pvforecast_instance._interpolate_azimuth_factors(azimuth, factors)

    assert np.std(factors) > 0.01
    assert np.dot(modelled_kwh, fitted_scale) == pytest.approx(
        global_factor * modelled_kwh.sum(), rel=1e-6
    )


@pytest.mark.asyncio
async def test_forecast_frame_applies_the_calibration(pvforecast_instance):
    """The correction must reach every caller of the chain, not just `_update_data`."""
    pvforecast_instance.config.pvforecast.akkudoktor = PVForecastAkkudoktorLocalCommonSettings(
        calibration_enabled=True,
        calibration_days=14,
        calibration_azimuth_bin_degrees=0,
    )

    raw = await pvforecast_instance._forecast_frame(synthetic_openmeteo(), calibrate=False)
    await _feed_measurements(pvforecast_instance, raw, bias=0.8, key="pv_chain_emr")

    calibrated = await pvforecast_instance._forecast_frame(synthetic_openmeteo())
    ratio = calibrated["ac_power"].sum() / raw["ac_power"].sum()
    assert ratio == pytest.approx(0.8, abs=0.03)


def test_transient_weather_outage_is_retried(pvforecast_instance):
    """Open-Meteo answers 503 while rotating model runs; that clears in seconds."""
    error = requests.exceptions.HTTPError("503 Server Error")
    error.response = Mock(status_code=503)
    good = Mock()
    good.url = "https://api.open-meteo.com/v1/forecast"
    good.raise_for_status.return_value = None
    good.json.return_value = synthetic_openmeteo()

    failing = Mock()
    failing.url = good.url
    failing.raise_for_status.side_effect = error

    with (
        patch(
            "akkudoktoreos.prediction.pvforecastakkudoktorlocal.requests.get",
            side_effect=[failing, good],
        ) as request,
        patch("akkudoktoreos.prediction.pvforecastakkudoktorlocal.time.sleep"),
    ):
        data = pvforecast_instance._request_local_forecast(force_update=True)

    assert request.call_count == 2
    assert "minutely_15" in data


@pytest.mark.asyncio
async def test_weather_outage_keeps_the_previous_forecast(pvforecast_instance):
    """One dead weather API must not take the whole prediction update down.

    `PredictionContainer.update_data` re-raises whatever an enabled provider
    raises, so every provider after this one would be skipped and the endpoint
    would answer 400. This fixture retains future samples from the previous run.
    """
    # The stored forecast has to reach past the run start for the fallback to be
    # worth anything, so put the run inside the synthetic window.
    get_ems().set_start_datetime(WINDOW_START.add(days=1))
    with patch.object(
        pvforecast_instance,
        "_request_local_forecast",
        return_value=synthetic_openmeteo(),
    ):
        await pvforecast_instance._update_data(force_update=True)
    stored = await pvforecast_instance.max_datetime()
    assert stored is not None
    records_before = len(pvforecast_instance)

    with patch.object(
        pvforecast_instance,
        "_request_local_forecast",
        side_effect=RuntimeError("Failed to fetch weather from Open-Meteo API"),
    ):
        await pvforecast_instance._update_data(force_update=True)

    assert await pvforecast_instance.max_datetime() == stored
    assert len(pvforecast_instance) == records_before


@pytest.mark.asyncio
async def test_weather_outage_without_any_forecast_still_fails(pvforecast_instance):
    """A cold start has nothing to fall back to, so the caller must hear about it.

    The stored forecast lives in the backing store, not in `records`, so an empty
    store is what a cold start actually looks like.
    """
    await pvforecast_instance.delete_by_datetime()
    with patch.object(
        pvforecast_instance,
        "_request_local_forecast",
        side_effect=RuntimeError("Failed to fetch weather from Open-Meteo API"),
    ):
        with pytest.raises(RuntimeError, match="Failed to fetch weather"):
            await pvforecast_instance._update_data(force_update=True)


def test_remote_backend_is_the_compatible_default():
    assert PVForecastAkkudoktorLocalCommonSettings().backend == "remote"


def test_calibration_rejects_inverted_bounds():
    with pytest.raises(ValueError, match="must not exceed"):
        PVForecastAkkudoktorLocalCommonSettings(
            calibration_min_factor=1.5, calibration_max_factor=0.5
        )


def test_existing_provider_id_is_registered_once(pvforecast_instance):
    providers = get_prediction().providers
    selected = [p for p in providers if p.provider_id() == "PVForecastAkkudoktor"]
    assert selected == [pvforecast_instance]
    assert all(p.provider_id() != "PVForecastAkkudoktorLocal" for p in providers)


@pytest.mark.asyncio
async def test_local_power_contract_for_hourly_and_quarter_hour_consumers(pvforecast_instance):
    """Both optimizer cadences receive W, ready for their W-to-Wh conversion."""
    start = START.add(hours=12)
    frame = pd.DataFrame(
        {
            "ac_power": [1000.0, 2000.0, 3000.0, 4000.0],
            "dc_power": [1100.0, 2200.0, 3300.0, 4400.0],
        },
        index=pd.date_range(start=start, periods=4, freq="15min"),
    )
    with (
        patch.object(pvforecast_instance, "_request_local_forecast", return_value={}),
        patch.object(pvforecast_instance, "_forecast_frame", new=AsyncMock(return_value=frame)),
        patch.object(pvforecast_instance, "_request_forecast") as remote,
    ):
        await pvforecast_instance._update_data(force_update=True)
    remote.assert_not_called()
    quarter_power = await get_prediction().key_to_array(
        "pvforecast_ac_power",
        start_datetime=start,
        end_datetime=start.add(hours=1),
        interval=pendulum.duration(minutes=15),
    )
    hourly_power = await get_prediction().key_to_array(
        "pvforecast_ac_power",
        start_datetime=start,
        end_datetime=start.add(hours=1),
        interval=pendulum.duration(hours=1),
    )
    assert quarter_power == pytest.approx([1000.0, 2000.0, 3000.0, 4000.0])
    assert hourly_power == pytest.approx([2500.0])
    assert float(sum(quarter_power * 0.25)) == pytest.approx(float(sum(hourly_power)))


@pytest.mark.asyncio
@pytest.mark.parametrize("record", ["expired_power", "future_measurement_only"])
async def test_outage_does_not_accept_unusable_stored_data(pvforecast_instance, record):
    if record == "expired_power":
        await pvforecast_instance.update_value(
            START.subtract(hours=1), "pvforecast_ac_power", 1000.0
        )
    else:
        await pvforecast_instance.update_value(
            START.add(hours=1), "pvforecastakkudoktor_ac_power_measured", 1000.0
        )
    with patch.object(
        pvforecast_instance, "_request_local_forecast", side_effect=RuntimeError("outage")
    ):
        with pytest.raises(RuntimeError, match="outage"):
            await pvforecast_instance._update_data(force_update=True)


@pytest.mark.parametrize("through_file_migration", [False, True])
def test_feature_settings_migrate_without_loss_or_input_mutation(
    config_eos, through_file_migration
):
    import copy
    from akkudoktoreos.config.configmigrate import migrate_config_data
    from akkudoktoreos.prediction.pvforecast import PVForecastCommonSettings

    source = {
        "provider": "PVForecastAkkudoktorLocal",
        "provider_settings": {
            "PVForecastAkkudoktorLocal": {
                "calibration_enabled": True,
                "calibration_days": 21,
                "calibration_min_factor": 1.7,
                "calibration_max_factor": 2.1,
            }
        },
    }
    before = copy.deepcopy(source)
    if through_file_migration:
        migrated = migrate_config_data({"pvforecast": source}).pvforecast
    else:
        migrated = PVForecastCommonSettings.model_validate(source)
    assert source == before
    assert migrated.provider == "PVForecastAkkudoktor"
    assert migrated.akkudoktor.backend == "local"
    assert migrated.akkudoktor.calibration_enabled
    assert migrated.akkudoktor.calibration_days == 21
    assert migrated.akkudoktor.calibration_min_factor == 1.7
    assert migrated.akkudoktor.calibration_max_factor == 2.1


def test_current_pv_settings_take_precedence_during_migration(config_eos):
    from akkudoktoreos.config.configmigrate import migrate_config_data

    migrated = migrate_config_data(
        {
            "pvforecast": {
                "provider": "PVForecastAkkudoktorLocal",
                "provider_settings": {
                    "PVForecastAkkudoktorLocal": {
                        "calibration_enabled": True,
                        "calibration_days": 21,
                    }
                },
                "akkudoktor": {"backend": "remote", "calibration_days": 7},
            }
        }
    ).pvforecast
    assert migrated.provider == "PVForecastAkkudoktor"
    assert migrated.akkudoktor.backend == "remote"
    assert migrated.akkudoktor.calibration_days == 7
    assert migrated.akkudoktor.calibration_enabled


@pytest.mark.parametrize(
    "invalid",
    [
        {"resolution_minutes": 5},
        {"calibration_min_factor": 2.0, "calibration_max_factor": 1.0},
        "invalid",
    ],
)
def test_file_migration_rejects_invalid_local_settings(config_eos, invalid):
    from akkudoktoreos.config.configmigrate import migrate_config_data

    with pytest.raises(ValueError):
        migrate_config_data(
            {
                "pvforecast": {
                    "provider": "PVForecastAkkudoktorLocal",
                    "provider_settings": {"PVForecastAkkudoktorLocal": invalid},
                }
            }
        )
