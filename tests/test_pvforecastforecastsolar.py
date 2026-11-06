import math
from unittest.mock import call, patch

import numpy as np
import pendulum
import pytest
import requests

from akkudoktoreos.optimization.genetic.forecast import bounded_forecast_array
from akkudoktoreos.prediction.pvforecastforecastsolar import PVForecastForecastSolar
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


def _config(config_eos, planes=None, api_key=None):
    settings = {
        "general": {"latitude": 52.5, "longitude": 13.4},
        "pvforecast": {
            "provider": "PVForecastForecastSolar",
            "forecastsolar": {"api_key": api_key},
            "planes": planes
            if planes is not None
            else [{"surface_tilt": 30.0, "surface_azimuth": 180.0, "peakpower": 5.0}],
        },
    }
    config_eos.merge_settings_from_dict(settings)
    return config_eos


@pytest.fixture
def pvforecast_instance(config_eos):
    _config(config_eos)
    start_dt = pendulum.datetime(2025, 1, 1, tz="Europe/Berlin")
    return PVForecastForecastSolar(config=config_eos.load, start_datetime=start_dt)


def _http(watts, timezone="Europe/Berlin"):
    return type(
        "R",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "result": {"watts": watts},
                "message": {"info": {"timezone": timezone}},
            },
        },
    )()


def test_provider_id(pvforecast_instance):
    assert PVForecastForecastSolar.provider_id() == "PVForecastForecastSolar"
    assert pvforecast_instance.enabled() is True


@pytest.mark.asyncio
async def test_update_data_resolves_tz_and_sets_power(pvforecast_instance):
    body = {
        "timezone": "Europe/Berlin",
        "watts": {"2025-01-01 12:00:00": 1200.0, "2025-01-01 13:00:00": 1500.0},
    }
    with patch.object(pvforecast_instance, "_request_forecast", return_value=body), \
         patch.object(PVForecastForecastSolar, "update_value") as mock_update, \
         patch.object(PVForecastForecastSolar, "delete_by_datetime") as mock_delete:
        await pvforecast_instance._update_data()

        assert mock_update.call_count == 2
        mock_delete.assert_awaited_once_with(
            start_datetime=to_datetime("2025-01-01T12:00:00+01:00")
        )
        expected = [
            call(
                pendulum.datetime(2025, 1, 1, 12, 0, tz="Europe/Berlin"),
                {"pvforecast_ac_power": 1200.0, "pvforecast_dc_power": 1200.0},
            ),
            call(
                pendulum.datetime(2025, 1, 1, 13, 0, tz="Europe/Berlin"),
                {"pvforecast_ac_power": 1500.0, "pvforecast_dc_power": 1500.0},
            ),
        ]
        mock_update.assert_has_calls(expected, any_order=False)
        # 12:00 Europe/Berlin (CET) is 11:00 UTC.
        assert mock_update.call_args_list[0][0][0].in_timezone("UTC").hour == 11


def test_plane_url_converts_azimuth(config_eos):
    """EOS azimuth 270 (west) -> Forecast.Solar 90; the key + plane geometry land in the URL."""
    _config(
        config_eos,
        planes=[{"surface_tilt": 25.0, "surface_azimuth": 270.0, "peakpower": 7.5}],
        api_key="secret",
    )
    pv = PVForecastForecastSolar(
        config=config_eos.load, start_datetime=pendulum.datetime(2025, 1, 1, tz="UTC")
    )
    with patch("requests.get", return_value=_http({})) as mock_get:
        # force_update is consumed by the cache_in_file decorator at runtime
        # (same call convention as pvforecastakkudoktor.py).
        pv._request_forecast(force_update=True)  # type: ignore
        url = mock_get.call_args[0][0]
        assert url == "https://api.forecast.solar/secret/estimate/52.5/13.4/25.0/90.0/7.5"


def test_request_forecast_sums_planes(config_eos):
    """Two planes -> two requests; instantaneous powers are summed per timestamp."""
    _config(
        config_eos,
        planes=[
            {"surface_tilt": 30.0, "surface_azimuth": 90.0, "peakpower": 3.0},
            {"surface_tilt": 30.0, "surface_azimuth": 270.0, "peakpower": 3.0},
        ],
    )
    pv = PVForecastForecastSolar(
        config=config_eos.load, start_datetime=pendulum.datetime(2025, 1, 1, tz="UTC")
    )
    responses = [
        _http({"2025-01-01 12:00:00": 1000.0, "2025-01-01 13:00:00": 1000.0}),
        _http({"2025-01-01 12:00:00": 800.0, "2025-01-01 13:00:00": 800.0}),
    ]
    with patch("requests.get", side_effect=responses) as mock_get:
        body = pv._request_forecast(force_update=True)  # type: ignore
        assert mock_get.call_count == 2
        assert body["watts"]["2025-01-01T11:00:00+00:00"] == 1800.0


@pytest.mark.asyncio
async def test_update_data_skips_when_disabled(pvforecast_instance, config_eos):
    config_eos.merge_settings_from_dict({"pvforecast": {"provider": "PVForecastAkkudoktor"}})
    with patch.object(pvforecast_instance, "_request_forecast") as mock_req, \
         patch.object(PVForecastForecastSolar, "update_value") as mock_update:
        await pvforecast_instance._update_data()
        mock_req.assert_not_called()
        mock_update.assert_not_called()


def test_request_forecast_raises_on_http_error(pvforecast_instance):
    with patch("requests.get", side_effect=requests.Timeout("timed out")):
        with pytest.raises(RuntimeError) as exc_info:
            pvforecast_instance._request_forecast(force_update=True)
        assert "Failed to fetch pvforecast from Forecast.Solar" in str(exc_info.value)


def _day_watts(day="2026-11-02"):
    """The irregular sunrise/sunset samples reported in #1485."""
    return {
        f"{day} {time}": power
        for time, power in {
            "07:16:18": 0.0,
            "08:00:00": 84.0,
            "09:00:00": 292.0,
            "10:00:00": 709.0,
            "11:00:00": 1225.0,
            "12:00:00": 1296.0,
            "13:00:00": 1289.0,
            "14:00:00": 1894.0,
            "15:00:00": 2509.0,
            "16:00:00": 1777.0,
            "16:55:54": 0.0,
        }.items()
    }


def test_instantaneous_power_is_integrated_and_nights_are_zero(pvforecast_instance):
    averages = pvforecast_instance._interval_power(_day_watts(), "Europe/Berlin")
    assert len(averages) == 96
    assert all(math.isfinite(value) for value in averages.values())
    at = lambda time: int(to_datetime(f"2026-11-02T{time}+01:00").timestamp())
    # A linear ramp 84 -> 292 W over an hour has 110 W average in its first quarter.
    assert averages[at("08:00:00")] == pytest.approx(110.0)
    # Integrating into Wh must conserve the entire daylight curve's energy.
    points = [
        (int(to_datetime(ts, in_timezone="Europe/Berlin").timestamp()), p)
        for ts, p in _day_watts().items()
    ]
    expected_wh = sum((b - a) * (pa + pb) / 7200 for (a, pa), (b, pb) in zip(points, points[1:]))
    assert sum(averages.values()) / 4 == pytest.approx(expected_wh)
    assert all(
        value == 0
        for stamp, value in averages.items()
        if stamp < at("07:00:00") or stamp >= at("17:00:00")
    )


@pytest.mark.parametrize("change", ["missing", "invalid", "negative", "infinite"])
def test_bad_daylight_coverage_remains_missing(pvforecast_instance, change):
    watts = _day_watts()
    key = "2026-11-02 12:00:00"
    if change == "missing":
        del watts[key]
    else:
        watts[key] = {"invalid": "bad", "negative": -1, "infinite": math.inf}[change]
    averages = pvforecast_instance._interval_power(watts, "Europe/Berlin")
    at = int(to_datetime("2026-11-02T11:00:00+01:00").timestamp())
    assert all(math.isnan(averages[at + i * 900]) for i in range(8))
    assert averages[at + 8 * 900] == pytest.approx((1289 * 7 + 1894) / 8)


def test_partial_days_and_missing_days_are_not_invented(pvforecast_instance):
    watts = {"2026-11-02 12:00:00": 1000, "2026-11-02 13:00:00": 1000, "2026-11-04 12:00:00": 1000}
    averages = pvforecast_instance._interval_power(watts, "Europe/Berlin")
    assert len(averages) == 192
    assert sum(math.isfinite(value) for value in averages.values()) == 4
    assert max(averages) < to_datetime("2026-11-05T00:00:00+01:00").timestamp()


@pytest.mark.parametrize("day,hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_dst_days_have_elapsed_time_slots(pvforecast_instance, day, hours):
    averages = pvforecast_instance._interval_power(_day_watts(day), "Europe/Berlin")
    assert len(averages) == hours * 4
    assert all(math.isfinite(value) for value in averages.values())
    assert np.diff(sorted(averages)).tolist() == [900] * (hours * 4 - 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [900, 3600])
async def test_provider_to_genetic_and_prediction_list(pvforecast_instance, interval):
    """Exercise real persistence/resampling, including cleanup of pre-fix samples."""
    pv = pvforecast_instance
    start = to_datetime("2026-11-02T00:00:00+01:00", in_timezone="Europe/Berlin")
    # Upgrade/refresh must remove the irregular sunset and an old forecast tail.
    await pv.update_value(
        start.subtract(days=1).add(hours=16, minutes=55, seconds=54), "pvforecast_ac_power", 0.0
    )
    await pv.update_value(start.add(hours=16, minutes=55, seconds=54), "pvforecast_ac_power", 0.0)
    await pv.update_value(start.add(days=3), "pvforecast_ac_power", 123.0)
    watts = _day_watts() | _day_watts("2026-11-03")
    with patch("requests.get", return_value=_http(watts)):
        await pv._update_data(force_update=True)
    raw = await pv.key_to_raw_series("pvforecast_ac_power", dropna=False)
    assert len(raw) == 192
    assert np.diff(raw.index.as_unit("ns").asi8 / 1e9).tolist() == [900] * 191
    values = await bounded_forecast_array(
        pv,
        key="pvforecast_ac_power",
        start_datetime=start,
        end_datetime=start.add(days=2),
        interval=to_duration(interval),
    )
    assert np.isfinite(values).all()
    listed = await pv.key_to_array(
        "pvforecast_ac_power",
        start_datetime=start,
        end_datetime=start.add(days=2),
        interval=to_duration(interval),
    )
    assert listed == pytest.approx(values)
    assert values[17 * 3600 // interval : 24 * 3600 // interval].tolist() == [0.0] * (
        7 * 3600 // interval
    )
    # Preserve the missing tail instead of extrapolating zeros into a third day.
    tail = await bounded_forecast_array(
        pv,
        key="pvforecast_ac_power",
        start_datetime=start.add(days=2),
        end_datetime=start.add(days=2, hours=1),
        interval=to_duration(interval),
    )
    assert np.isnan(tail).all()
    # Once migrated, a normal refresh retains already normalized history.
    with patch("requests.get", return_value=_http(_day_watts("2026-11-03"))):
        await pv._update_data(force_update=True)
    raw = await pv.key_to_raw_series("pvforecast_ac_power", dropna=False)
    assert len(raw) == 192
    await pv.delete_by_datetime()


def test_planes_are_integrated_before_summing(config_eos):
    _config(
        config_eos,
        planes=[
            {"surface_tilt": 30, "surface_azimuth": 90, "peakpower": 3},
            {"surface_tilt": 30, "surface_azimuth": 270, "peakpower": 3},
        ],
    )
    pv = PVForecastForecastSolar(config=config_eos.load)
    first = {
        "2026-11-02 07:10:00": 0,
        "2026-11-02 08:00:00": 1000,
        "2026-11-02 09:00:00": 1000,
        "2026-11-02 09:45:00": 0,
    }
    second = {
        "2026-11-02 07:25:00": 0,
        "2026-11-02 08:00:00": 500,
        "2026-11-02 09:00:00": 500,
        "2026-11-02 09:55:00": 0,
    }
    expected = pv._interval_power(first, "Europe/Berlin")
    other = pv._interval_power(second, "Europe/Berlin")
    with patch("requests.get", side_effect=[_http(first), _http(second)]):
        body = pv._request_forecast(force_update=True)  # type: ignore[call-arg]
    for ts, value in body["watts"].items():
        stamp = int(to_datetime(ts).timestamp())
        assert value == pytest.approx(expected[stamp] + other[stamp])
    # An absent plane may not silently produce the other plane's forecast.
    with patch("requests.get", side_effect=[_http(first), _http({})]):
        body = pv._request_forecast(force_update=True)  # type: ignore[call-arg]
    assert all(math.isnan(value) for value in body["watts"].values())
