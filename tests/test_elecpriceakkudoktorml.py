# ruff: noqa: S101
"""Tests for the residual-load electricity price provider (no network, synthetic fixtures)."""

import datetime as dt
import importlib.metadata
import json
import threading
import time
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.prediction import elecpriceakkudoktormlcore as core
from akkudoktoreos.prediction import elecpriceakkudoktormldata as rdata
from akkudoktoreos.prediction import elecpriceakkudoktormlengine as engine_mod
from akkudoktoreos.prediction.elecpriceakkudoktorml import ElecPriceAkkudoktorML
from akkudoktoreos.prediction.elecpriceakkudoktormlengine import (
    AkkudoktorMLEngine,
    ForecastOutcome,
)
from akkudoktoreos.prediction.elecpriceenergycharts import EnergyChartsElecPrice
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

LOCAL_D = dt.date(2026, 3, 25)  # Wednesday: Monday 23.3. is the curve week start


# ------------------------------------------------------------------ synthetic data
def make_inputs(
    first: str = "2025-10-01", last: str = "2026-04-05", weather_extra_days: int = 7
) -> core.Inputs:
    idx = pd.date_range(pd.Timestamp(first, tz="UTC"), pd.Timestamp(last, tz="UTC"), freq="1h")
    widx = pd.date_range(
        pd.Timestamp(first, tz="UTC"),
        pd.Timestamp(last, tz="UTC") + pd.Timedelta(days=weather_extra_days),
        freq="1h",
    )
    rng = np.random.default_rng(1)
    hours = widx.hour.to_numpy()
    doy = widx.day_of_year.to_numpy()
    base = {
        "wind_speed_100m": 8
        + 4 * np.sin(2 * np.pi * np.arange(len(widx)) / 90)
        + rng.normal(0, 1.0, len(widx)),
        "wind_speed_10m": 5 + rng.normal(0, 0.8, len(widx)),
        "shortwave_radiation": np.clip(600 * np.sin(np.pi * (hours - 5) / 14), 0, None)
        * (0.6 + 0.4 * np.sin(doy / 58.0)),
        "temperature_2m": 8
        + 10 * np.sin(2 * np.pi * (doy - 110) / 365)
        + rng.normal(0, 1, len(widx)),
        "cloud_cover": np.clip(50 + rng.normal(0, 25, len(widx)), 0, 100),
    }
    cols = core.weather_columns()
    weather = {}
    for K in core.WEATHER_LEADS:
        data = {
            c: np.clip(
                base[c.split("__")[1]] * (1 + 0.02 * i) + rng.normal(0, 0.2, len(widx)), 0, None
            )
            for i, c in enumerate(cols)
        }
        weather[K] = pd.DataFrame(data, index=widx)[cols]
    w = weather[3].reindex(idx)
    wind = w[[c for c in cols if c.endswith("wind_speed_100m")]].mean(axis=1).to_numpy()
    sun = w[[c for c in cols if c.endswith("shortwave_radiation")]].mean(axis=1).to_numpy()
    load = (
        50000 + 9000 * np.sin(np.pi * (idx.hour.to_numpy() - 6) / 12) + rng.normal(0, 500, len(idx))
    )
    meas = pd.DataFrame(
        {"load_mw": load, "wind_on_mw": 2500 * wind, "wind_off_mw": 800 * wind, "pv_mw": 60 * sun},
        index=idx,
    )
    resid = meas["load_mw"] - meas["wind_on_mw"] - meas["wind_off_mw"] - meas["pv_mw"]
    price = pd.Series(60 + 0.0025 * (resid - resid.mean()) + rng.normal(0, 2, len(idx)), index=idx)
    return core.Inputs(price=price, meas=meas, weather=weather)


@pytest.fixture(scope="module", autouse=True)
def small_model():
    """Tiny models, three weather points, short windows: the tests check logic, not quality."""
    with pytest.MonkeyPatch.context() as mp:
        for params in (core.GBM_PARAMS, core.CURVE_PARAMS):
            mp.setitem(params, "max_iter", 3)
            mp.setitem(params, "max_leaf_nodes", 4)
            mp.setitem(params, "min_samples_leaf", 20)
        mp.setattr(core, "WEATHER_POINTS", core.WEATHER_POINTS[:3])
        mp.setattr(core, "MIN_TRAIN_ROWS", 300)
        mp.setattr(core, "MIN_CURVE_ROWS", 200)
        mp.setattr(core, "MIN_DAYS", 15)
        mp.setattr(core, "WINDOW_DAYS", 30)
        yield


@pytest.fixture(scope="module")
def inputs(small_model) -> core.Inputs:
    return make_inputs()


# ------------------------------------------------------------------ time rules
def test_availability_rules_including_dst():
    h = pd.DatetimeIndex(
        ["2026-03-29 00:00", "2026-03-29 22:00", "2026-10-25 00:00"], tz="UTC"
    )  # 29.3./25.10. = switch days
    # price of local day X: 13:00 local on X-1
    pa = core.price_available_at(h)
    assert pa[0] == pd.Timestamp("2026-03-28 12:00", tz="UTC")  # CET, 13:00 local
    assert pa[2] == pd.Timestamp("2026-10-24 11:00", tz="UTC")  # CEST, 13:00 local
    # measurement: end of local day + 12 h; 29.3. 00:00 UTC is local 01:00 CET, local midnight end =
    # 2026-03-29 22:00 UTC
    ma = core.measurement_available_at(h)
    assert ma[0] == pd.Timestamp("2026-03-29 22:00", tz="UTC") + pd.Timedelta(hours=12)
    wa = core.weather_available_at(pd.DatetimeIndex(["2026-03-10 12:00"], tz="UTC"), 3)
    assert wa[0] == pd.Timestamp("2026-03-08 00:00", tz="UTC")


def test_local_day_hours_dst():
    assert len(core.local_day_hours(dt.date(2026, 3, 29))) == 23
    assert len(core.local_day_hours(dt.date(2026, 10, 25))) == 25
    assert len(core.local_day_hours(dt.date(2026, 6, 1))) == 24


def test_german_holidays():
    assert core.german_holidays(2026) == {
        dt.date(2026, 1, 1),
        dt.date(2026, 4, 3),
        dt.date(2026, 4, 6),
        dt.date(2026, 5, 1),
        dt.date(2026, 5, 14),
        dt.date(2026, 5, 25),
        dt.date(2026, 10, 3),
        dt.date(2026, 12, 25),
        dt.date(2026, 12, 26),
    }


def test_knowledge_has_no_future_data(inputs):
    k = core.build_knowledge(inputs, LOCAL_D)
    issue = core.issue_time(LOCAL_D)
    assert core.price_available_at(pd.DatetimeIndex(k.price.index)).max() <= issue
    assert core.measurement_available_at(pd.DatetimeIndex(k.meas.index)).max() <= issue
    for K, w in k.weather.items():
        assert core.weather_available_at(pd.DatetimeIndex(w.index), K).max() <= issue
    # prices known to the end of D+1, measurements to the end of D-1
    assert k.price.index.max() == core.local_day_hours(LOCAL_D + dt.timedelta(days=1))[-1]
    assert k.meas.index.max() == core.local_day_hours(LOCAL_D - dt.timedelta(days=1))[-1]


# ------------------------------------------------------------------ model core
def test_forecast_is_finite_and_anchor_is_damped(inputs):
    sa, pc = core.StageA(), core.PriceCurve()
    k = core.build_knowledge(inputs, LOCAL_D)
    fc = _forecast(inputs, k, sa, pc)
    assert len(fc.point) == sum(len(k.target_hours(n)) for n in core.TARGET_DAYS)
    assert fc.point.notna().all() and fc.resid_hat.notna().all()
    assert fc.anchor_hours >= 20
    # raise the known prices of D+1 by 5 ct: the point forecast moves by DAMPING[n] * 5 (curve week
    # starts before D)
    shifted = core.Inputs(price=inputs.price.copy(), meas=inputs.meas, weather=inputs.weather)
    d1 = core.local_day_hours(LOCAL_D + dt.timedelta(days=1))
    shifted.price.loc[d1] += 5.0
    k2 = core.build_knowledge(shifted, LOCAL_D)
    fc2 = core.forecast_day(shifted, k2, sa, pc)
    for n in core.TARGET_DAYS:
        hrs = k.target_hours(n)
        assert np.allclose((fc2.point - fc.point).reindex(hrs), core.DAMPING[n] * 5.0)


def test_missing_weather_gives_nan_not_fallback(inputs):
    sa, pc = core.StageA(), core.PriceCurve()
    cut = core.Inputs(
        price=inputs.price,
        meas=inputs.meas,
        weather={K: w.copy() for K, w in inputs.weather.items()},
    )
    k0 = core.build_knowledge(cut, LOCAL_D)
    hrs = k0.target_hours(3)
    cut.weather[4].loc[hrs, :] = np.nan  # K=4 serves D+3
    k = core.build_knowledge(cut, LOCAL_D)
    fc = _forecast(cut, k, sa, pc)
    assert fc.point.reindex(hrs).isna().all()
    assert fc.point.reindex(k.target_hours(2)).notna().all()


def _forecast(inputs, k, sa, pc):
    """Forecast with the monthly model of the issue day (trained on demand, test helper)."""
    m0 = core.month_start(k.day)
    if not sa.has_month(m0):
        sa.train_month(inputs, m0)
    return core.forecast_day(inputs, k, sa, pc)


def test_missing_stage_a_model_is_reported(inputs):
    k = core.build_knowledge(inputs, LOCAL_D)
    with pytest.raises(core.MissingModel):
        core.forecast_day(inputs, k, core.StageA(), core.PriceCurve())


def test_stage_a_training_rows_known_at_month_start(inputs):
    sa = core.StageA()
    sa.train_month(inputs, dt.date(2026, 3, 1))
    assert sa.has_month(dt.date(2026, 3, 1))
    # poison everything after the cutoff: models must not change
    poisoned = core.Inputs(price=inputs.price, meas=inputs.meas.copy(), weather=inputs.weather)
    poisoned.meas.loc[poisoned.meas.index >= pd.Timestamp("2026-03-01", tz="UTC")] = 1e9
    sb = core.StageA()
    sb.train_month(poisoned, dt.date(2026, 3, 1))
    k = core.build_knowledge(inputs, LOCAL_D)
    a = sa.predict(k, month=dt.date(2026, 3, 1))
    b = sb.predict(k, month=dt.date(2026, 3, 1))
    assert np.allclose(a["resid_mw"].fillna(0), b["resid_mw"].fillna(0))


# ------------------------------------------------------------------ uncertainty
def _records(inputs, n_days=18, last=dt.date(2026, 3, 19)):
    sa, pc = core.StageA(), core.PriceCurve()
    recs = {}
    for i in range(n_days):
        d = last - dt.timedelta(days=i)
        k = core.build_knowledge(inputs, d)
        fc = _forecast(inputs, k, sa, pc)
        rec = core.error_record(inputs, pc, fc)
        if rec is not None:
            recs[d.isoformat()] = rec
    return recs, sa, pc


@pytest.fixture(scope="module")
def history(inputs):
    """Error history of 18 issue days, built once for all tests that need it."""
    return _records(inputs)


def test_error_history_window_and_scenarios(inputs, history):
    recs, sa, pc = history
    assert len(recs) >= 15
    # usable at D only if D'+5 <= D-1 and D' >= D-WINDOW_DAYS
    usable = core.usable_records(recs, LOCAL_D)
    assert all(
        dt.date.fromisoformat(r["day"]) + dt.timedelta(days=5) <= LOCAL_D - dt.timedelta(days=1)
        for r in usable
    )
    assert max(r["day"] for r in usable) == (LOCAL_D - dt.timedelta(days=6)).isoformat()
    k = core.build_knowledge(inputs, LOCAL_D)
    fc = core.forecast_day(inputs, k, sa, pc)
    scen = core.residual_scenarios(inputs, k, pc, fc, recs)
    assert scen.shape == (len(usable), len(fc.point))
    q = core.scenario_quantiles(scen)
    assert q.shape == (5, len(fc.point))
    assert (np.diff(q, axis=0) >= -1e-9).all()  # quantiles are ordered
    with pytest.raises(ValueError):
        core.residual_scenarios(inputs, k, pc, fc, dict(list(recs.items())[:10]))


def _hourly(start, days, f):
    idx = pd.date_range(core.local_midnight_utc(start), periods=24 * days, freq="1h")
    return pd.Series([f(i) for i in range(len(idx))], index=idx)


def test_level_asof_only_known_days_and_floor():
    p = _hourly(dt.date(2025, 1, 1), 60, lambda i: 10.0 if i < 24 * 40 else 1000.0)
    dm = core.daily_means(p)
    assert core.level_asof(dm, dt.date(2025, 2, 9)) == pytest.approx(
        10.0
    )  # nothing from later days
    assert core.level_asof(dm, dt.date(2025, 2, 10)) > 10.0
    short = core.daily_means(_hourly(dt.date(2025, 1, 1), 10, lambda i: 5.0))
    assert np.isnan(core.level_asof(short, dt.date(2025, 1, 10)))  # < 20 days
    neg = core.daily_means(_hourly(dt.date(2025, 1, 1), 40, lambda i: -3.0))
    assert core.level_asof(neg, dt.date(2025, 2, 9)) == core.LEVEL_FLOOR


def test_level_scaling_of_price_remainder(inputs, history, monkeypatch):
    recs, sa, pc = history
    k = core.build_knowledge(inputs, LOCAL_D)
    fc = core.forecast_day(inputs, k, sa, pc)
    real_level = core.level_asof
    monkeypatch.setattr(core, "level_asof", lambda dm, day: 1.0)  # no scaling: all factors are 1
    u2 = core.residual_scenarios(inputs, k, pc, fc, recs)
    monkeypatch.setattr(core, "level_asof", real_level)
    u3 = core.residual_scenarios(inputs, k, pc, fc, recs)
    assert u2.shape == u3.shape
    # expected factor per usable record, scenario difference = (g - 1) * e_p
    usable = core.usable_records(recs, LOCAL_D)
    keys = core.target_keys(k)
    dm = core.daily_means(k.price)
    l_now = core.level_asof(dm, LOCAL_D + dt.timedelta(days=1))
    g = np.array(
        [
            core.level_asof(dm, dt.date.fromisoformat(r["day"]) + dt.timedelta(days=1))
            for r in usable
        ]
    )
    g = np.clip(l_now / g, *core.SCALE_CLIP)
    e_p = np.vstack([core._align(r, keys, "e_p") for r in usable])
    assert np.allclose(u3 - u2, (g[:, None] - 1.0) * e_p, atol=1e-9)
    assert ((g >= 0.5) & (g <= 2.5)).all()


def test_scenarios_use_no_price_after_known_day(inputs, history):
    """Prices published after the issue time (beyond D+1) must not change the scenarios."""
    recs, sa, pc = history
    k = core.build_knowledge(inputs, LOCAL_D)
    fc = core.forecast_day(inputs, k, sa, pc)
    a = core.residual_scenarios(inputs, k, pc, fc, recs)
    cut = core.local_midnight_utc(LOCAL_D + dt.timedelta(days=2))
    poisoned_price = inputs.price.copy()
    poisoned_price[poisoned_price.index >= cut] = 1e6
    kp = core.build_knowledge(
        core.Inputs(price=poisoned_price, meas=inputs.meas, weather=inputs.weather), LOCAL_D
    )
    b = core.residual_scenarios(inputs, kp, pc, fc, recs)
    assert np.allclose(a, b)


def test_error_record_needs_complete_measurements(inputs):
    sa, pc = core.StageA(), core.PriceCurve()
    d = dt.date(2026, 3, 30)  # D+5 = 4.4.: price/meas exist until 5.4.; D=31.3. needs data beyond
    k = core.build_knowledge(inputs, d)
    fc = _forecast(inputs, k, sa, pc)
    assert core.error_record(inputs, pc, fc) is not None
    short = core.Inputs(price=inputs.price[:-48], meas=inputs.meas[:-48], weather=inputs.weather)
    assert core.error_record(short, pc, fc) is None


# ------------------------------------------------------------------ data module
def smard_getter(frame: pd.DataFrame, calls: list):
    inv = {v[0]: c for c, v in rdata.SERIES.items()}

    def get(url, params):
        calls.append(url)
        parts = url.split("/")
        flt = int(parts[-3])
        col = inv[flt]
        if parts[-1].startswith("index_"):
            starts = pd.date_range(
                frame.index.min().normalize() - pd.Timedelta(days=frame.index.min().weekday()),
                frame.index.max(),
                freq="7D",
                tz="UTC",
            )
            return json.dumps({"timestamps": [int(t.timestamp() * 1000) for t in starts]}).encode()
        t0 = pd.Timestamp(int(parts[-1].split("_")[-1].split(".")[0]), unit="ms", tz="UTC")
        sub = frame.loc[(frame.index >= t0) & (frame.index < t0 + pd.Timedelta(days=7)), col]
        series = [
            [int(pd.Timestamp(str(i)).timestamp() * 1000), None if np.isnan(v) else float(v)]
            for i, v in sub.items()
        ]
        return json.dumps({"meta_data": {"version": 1, "created": 1}, "series": series}).encode()

    return get


def weather_getter(frames: dict, calls: list, bump: float = 0.0):
    def get(url, params):
        calls.append(dict(params))
        start, end = (
            pd.Timestamp(params["start_date"], tz="UTC"),
            pd.Timestamp(params["end_date"], tz="UTC") + pd.Timedelta(hours=23),
        )
        out = []
        for pid, _, _ in core.WEATHER_POINTS:
            hourly: dict[str, list] = {
                "time": [t.strftime("%Y-%m-%dT%H:%M") for t in pd.date_range(start, end, freq="1h")]
            }
            for K in core.WEATHER_LEADS:
                for v in core.WEATHER_VARS:
                    col = frames[K][f"{pid}__{v}"].reindex(pd.date_range(start, end, freq="1h"))
                    hourly[f"{v}_previous_day{K}"] = [
                        None if np.isnan(x) else float(x) + bump for x in col
                    ]
            out.append({"hourly": hourly})
        return json.dumps(out).encode()

    return get


def test_smard_cache_first_fill_and_increment(tmp_path, inputs):
    full = pd.concat([inputs.meas, inputs.price.rename("price_eur_mwh") * 10], axis=1)
    cache = rdata.DataCache(tmp_path)
    old = full[full.index < "2026-02-01"]
    calls: list = []
    with patch.object(rdata, "SMARD_START", dt.date(2025, 2, 1)):
        m1 = cache.update_smard(smard_getter(old, calls))
        n_first = len(calls)
        assert m1.index.max() == old.index.max() and set(m1.columns) == set(rdata.SERIES)
        calls.clear()
        m2 = cache.update_smard(smard_getter(full, calls))
    assert len(calls) < n_first  # only the tail, not the whole history
    assert m2.index.max() == full.index.max()
    assert np.allclose(m2.dropna()["load_mw"], full.loc[m2.dropna().index, "load_mw"])
    loaded = cache.load_smard()
    assert loaded is not None and loaded.shape == m2.shape


def test_weather_cache_with_earlier_point_ids_is_renamed(tmp_path):
    cache = rdata.DataCache(tmp_path)
    idx = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    old = pd.DataFrame(
        1.0,
        index=idx,
        columns=["won_husum__cloud_cover", "last_berlin__cloud_cover", "last_ruhr__temperature_2m"],
    )
    for k in core.WEATHER_LEADS:
        cache._write(cache._weather_path(k), old)
    loaded = cache.load_weather()
    assert loaded is not None
    assert all(
        list(w.columns)
        == ["won_husum__cloud_cover", "load_berlin__cloud_cover", "load_ruhr__temperature_2m"]
        for w in loaded.values()
    )


def test_weather_cache_overwrites_recent_days(tmp_path, inputs):
    cache = rdata.DataCache(tmp_path)
    calls: list = []
    sleeps: list = []
    with patch.object(rdata, "WEATHER_START", dt.date(2026, 3, 1)):
        w1 = cache.update_weather(
            weather_getter(inputs.weather, calls), dt.date(2026, 3, 20), sleep=sleeps.append
        )
        assert set(w1) == set(core.WEATHER_LEADS) and list(w1[3].columns) == core.weather_columns()
        assert w1[3].index.max() == pd.Timestamp("2026-03-26 23:00", tz="UTC")  # today + 6 days
        calls.clear()
        # next day: refetch from last-10d, values of overlapping days are replaced, older rows kept
        w2 = cache.update_weather(
            weather_getter(inputs.weather, calls, bump=1000.0),
            dt.date(2026, 3, 21),
            sleep=sleeps.append,
        )
    assert len(calls) == 1 and pd.Timestamp(calls[0]["start_date"]) >= pd.Timestamp("2026-03-10")
    assert w2[3].index.max() == pd.Timestamp("2026-03-27 23:00", tz="UTC")
    c = core.weather_columns()[0]
    assert w2[3].loc["2026-03-05 12:00", c] == pytest.approx(w1[3].loc["2026-03-05 12:00", c])
    assert w2[3].loc["2026-03-22 12:00", c] == pytest.approx(
        cast(float, w1[3].loc["2026-03-22 12:00", c]) + 1000.0
    )


def test_parse_weather_rejects_wrong_point_count():
    with pytest.raises(ValueError):
        rdata.parse_weather(json.dumps([{"hourly": {"time": []}}]).encode())


def test_http_get_reports_rate_limit():
    class R:
        status_code = 429

        def raise_for_status(self):
            raise AssertionError

    with patch("requests.get", return_value=R()):
        with pytest.raises(rdata.RateLimited):
            rdata.http_get("https://example.invalid/x", None)


def test_requests_carry_no_personal_data():
    seen = {}

    class R:
        status_code = 200
        content = b"{}"

        def raise_for_status(self):
            pass

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(headers or {})
        return R()

    with patch("requests.get", side_effect=fake_get):
        rdata.http_get("https://example.invalid/x", None)
    assert "@" not in seen["User-Agent"] and "EOS" in seen["User-Agent"]
    params = rdata.weather_params(dt.date(2026, 1, 1), dt.date(2026, 1, 2))
    assert set(params) == {
        "latitude",
        "longitude",
        "start_date",
        "end_date",
        "hourly",
        "models",
        "timezone",
    }


# ------------------------------------------------------------------ engine
def prepared_engine(tmp_path, inputs, monkeypatch):
    eng = AkkudoktorMLEngine(
        tmp_path, get=lambda *a: (_ for _ in ()).throw(AssertionError("no network"))
    )
    smard = pd.concat([inputs.meas, inputs.price.rename("price_eur_mwh") * 10], axis=1)
    eng._smard, eng._weather, eng._loaded = smard, inputs.weather, True
    eng._smard_at = eng._weather_at = time.time()
    monkeypatch.setattr(eng, "kick", lambda *_, **__: None)
    return eng


def known_hourly(inputs, day):
    return inputs.price[inputs.price.index <= core.local_day_hours(day + dt.timedelta(days=1))[-1]]


def test_engine_not_ready_without_cache_or_model(tmp_path, inputs, monkeypatch):
    eng = AkkudoktorMLEngine(tmp_path)
    kicked = []
    monkeypatch.setattr(eng, "kick", lambda d, m=None: kicked.append((d, m)))
    out, why = eng.forecast(LOCAL_D, known_hourly(inputs, LOCAL_D), LOCAL_D)
    assert out is None and "data cache" in why and kicked
    eng2 = prepared_engine(tmp_path / "b", inputs, monkeypatch)
    out, why = eng2.forecast(LOCAL_D, known_hourly(inputs, LOCAL_D), LOCAL_D)
    assert out is None and "being trained" in why


def test_engine_forecast_persists_models_and_builds_uncertainty(tmp_path, inputs, monkeypatch):
    eng = prepared_engine(tmp_path, inputs, monkeypatch)
    m0 = core.month_start(LOCAL_D)
    full_inputs = eng.inputs()
    eng._ensure_month(full_inputs, m0)
    assert (tmp_path / f"stage_a_{m0:%Y-%m}_{eng._model_tag()}.joblib").exists()
    out, why = eng.forecast(LOCAL_D, known_hourly(inputs, LOCAL_D), LOCAL_D)
    assert (
        why == "ok"
        and out is not None
        and out.quantiles is None
        and "usable error days" in out.uncertainty_reason
    )
    assert out.forecast.point.notna().all()
    added = eng.extend_records(full_inputs, LOCAL_D)
    assert added > core.MIN_DAYS
    assert (tmp_path / "error_records.json").exists()
    # memory: only the model of the current month stays loaded (older ones remain on disk)
    assert {k[0] for k in eng.stage_a.models} == {m0}
    assert len(list(tmp_path.glob("stage_a_*.joblib"))) > 1
    out2, _ = eng.forecast(LOCAL_D, known_hourly(inputs, LOCAL_D), LOCAL_D)
    assert out2.quantiles is not None and out2.quantiles.shape == (5, len(out2.forecast.point))
    assert np.allclose(
        out2.forecast.point, out.forecast.point
    )  # the point forecast does not depend on the error history
    # a second engine instance loads models and history from disk
    eng3 = prepared_engine(tmp_path, inputs, monkeypatch)
    eng3._loaded = False
    eng3._load_disk()
    eng3._smard, eng3._weather = eng._smard, eng._weather
    eng3._smard_at = eng3._weather_at = time.time()
    assert len(eng3.records) == len(eng.records)
    out3, why3 = eng3.forecast(LOCAL_D, known_hourly(inputs, LOCAL_D), LOCAL_D)
    assert why3 == "ok" and np.allclose(out3.forecast.point, out.forecast.point)


def test_engine_forecast_is_cached_until_inputs_change(tmp_path, inputs, monkeypatch):
    """update_data runs several times per planning cycle: identical inputs, no recomputation."""
    eng = prepared_engine(tmp_path, inputs, monkeypatch)
    full_inputs = eng.inputs()
    eng._ensure_month(full_inputs, core.month_start(LOCAL_D))
    eng.extend_records(full_inputs, LOCAL_D)
    calls = []
    real = eng._day_forecast

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(eng, "_day_forecast", counting)
    hourly = known_hourly(inputs, LOCAL_D)
    a, _ = eng.forecast(LOCAL_D, hourly, LOCAL_D)
    b, _ = eng.forecast(LOCAL_D, hourly, LOCAL_D)
    assert len(calls) == 1 and b is a
    changed = hourly.copy()
    changed.iloc[-1] += 1.0  # a newly published price changes the key
    eng.forecast(LOCAL_D, changed, LOCAL_D)
    assert len(calls) == 2


def test_engine_uses_previous_month_model_while_new_one_trains(tmp_path, inputs, monkeypatch):
    eng = prepared_engine(tmp_path, inputs, monkeypatch)
    prev = dt.date(2026, 2, 1)
    eng._ensure_month(eng.inputs(), prev)
    day = dt.date(2026, 3, 3)
    out, why = eng.forecast(day, known_hourly(inputs, day), day)
    assert why == "ok" and out.model_month == prev


def test_background_work_secures_the_month_of_the_model_day(tmp_path, inputs, monkeypatch):
    """On the 1st the forecast needs the new month's model; the background work trains it."""
    eng = prepared_engine(tmp_path, inputs, monkeypatch)
    day = dt.date(2026, 3, 1)  # old logic secured the month of day - 1 (February)
    eng._work(day, day)
    assert eng.status == "ready" and eng.last_error is None
    assert eng.stage_a.has_month(dt.date(2026, 3, 1))
    assert (tmp_path / f"stage_a_2026-03_{eng._model_tag()}.joblib").exists()


def test_background_work_does_not_disturb_a_concurrent_forecast(tmp_path, inputs, monkeypatch):
    """The forecast keeps working (and keeps its bridge model) while the history is built."""
    eng = prepared_engine(tmp_path, inputs, monkeypatch)
    prev = dt.date(2026, 2, 1)
    eng._ensure_month(eng.inputs(), prev)
    day = dt.date(2026, 3, 3)
    hourly = known_hourly(inputs, day)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            eng.extend_records(eng.inputs(), day)
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=work)
    t.start()
    results = []
    while t.is_alive():
        results.append(eng.forecast(day, hourly, day))
    t.join()
    results.append(eng.forecast(day, hourly, day))
    assert not errors
    assert results and all(why == "ok" and out is not None for out, why in results)
    assert eng.stage_a.has_month(prev)  # the bridge month survives until the new month is available
    assert eng.records


def test_engine_reports_missing_weather(tmp_path, inputs, monkeypatch):
    eng = prepared_engine(tmp_path, inputs, monkeypatch)
    eng._ensure_month(eng.inputs(), core.month_start(LOCAL_D))
    eng._weather = {
        K: w[w.index < pd.Timestamp("2026-03-27", tz="UTC")] for K, w in inputs.weather.items()
    }
    out, why = eng.forecast(LOCAL_D, known_hourly(inputs, LOCAL_D), LOCAL_D)
    assert out is None and "weather forecast" in why


def test_engine_background_failure_is_contained_and_backs_off(tmp_path, monkeypatch):
    def boom(url, params=None):
        raise rdata.RateLimited("HTTP 429")

    eng = AkkudoktorMLEngine(tmp_path, get=boom)
    eng.kick(LOCAL_D)
    assert eng._thread is not None
    eng._thread.join(30)
    assert eng.status.startswith("waiting") and "429" in (eng.last_error or "")
    # back-off: no new attempt immediately
    before = eng._thread
    eng.kick(LOCAL_D)
    assert eng._thread is before


# ------------------------------------------------------------------ provider
@pytest.fixture
def provider(config_eos):
    config_eos.merge_settings_from_dict({"elecprice": {"provider": "ElecPriceAkkudoktorML"}})
    prov = ElecPriceAkkudoktorML()
    prov.highest_orig_datetime = None
    assert prov.enabled()
    prov._db_reset_state()
    return prov


class StubEngine:
    def __init__(self, outcome=None, reason="stub", exc=None):
        self.outcome, self.reason, self.exc, self.calls = outcome, reason, exc, []

    def forecast(self, day, known, today):
        self.calls.append((day, known))
        if self.exc:
            raise self.exc
        return self.outcome, self.reason


HISTORY_DAYS = 16  # enough history for the base class (the ETS fit is replaced in the tests)


def _source(start, days_known=2):
    slots = 96 * (days_known + HISTORY_DAYS)
    t0 = start.subtract(days=HISTORY_DAYS)
    return EnergyChartsElecPrice(
        license_info="",
        unix_seconds=[int(t0.add(minutes=15 * i).timestamp()) for i in range(slots)],
        price=[50.0 + float(i % 96) / 4 for i in range(slots)],
        unit="EUR/MWh",
        deprecated=False,
    )


async def _run_provider(provider, engine, start):
    get_ems().set_start_datetime(start)
    provider.config.prediction.hours = 96

    def fake_ets(history, seasonal_periods, hours):
        return np.full(hours, 0.0003)

    with (
        patch.object(provider, "_request_forecast", return_value=_source(start)),
        patch.object(type(provider), "_predict_ets", side_effect=fake_ets),
        patch.object(type(provider), "_get_engine", return_value=engine),
    ):
        await provider.update_data(force_enable=True, force_update=True)
    return await provider.key_to_series(
        key="elecprice_marketprice_raw_wh",
        start_datetime=start,
        end_datetime=start.add(hours=96),
        interval=to_duration("15 minutes"),
    )


def _outcome(day, level_ct=20.0):
    idx = pd.DatetimeIndex(
        [h for n in core.TARGET_DAYS for h in core.local_day_hours(day + dt.timedelta(days=n))]
    )
    point = pd.Series(level_ct, index=idx)
    q = np.vstack([point.to_numpy() + d for d in (-3, -1, 0, 1, 3)])
    return ForecastOutcome(
        core.DayForecast(
            day=day, point=point, resid_hat=pd.Series(0.0, index=idx), offset=1.5, anchor_hours=24
        ),
        dt.date(2026, 7, 1),
        q,
        None,
    )


@pytest.mark.asyncio
async def test_provider_replaces_ets_after_known_prices(provider, tmp_path, monkeypatch):
    monkeypatch.setattr(type(provider), "_engine_folder", lambda self: tmp_path)
    start = to_datetime("2026-07-10 00:00:00", in_timezone="Europe/Berlin")
    day_d = dt.date(
        2026, 7, 10
    )  # known prices cover 2 days from start (until the end of 11.7.) => D = 10.7.
    eng = StubEngine(_outcome(day_d))
    series = await _run_provider(provider, eng, start)
    last_known = to_datetime(provider.highest_orig_datetime)
    known = series[series.index <= last_known]
    after = series[series.index > last_known]
    assert eng.calls and eng.calls[0][0] == day_d
    t0 = pd.Timestamp(start.subtract(days=HISTORY_DAYS).in_timezone("UTC").isoformat())
    steps = (
        (pd.DatetimeIndex(known.index).tz_convert("UTC") - t0) / pd.Timedelta(minutes=15)
    ).astype(int)
    assert np.allclose(
        known.to_numpy(), (50.0 + (np.asarray(steps) % 96) / 4) / 1e6
    )  # known prices untouched
    # the model days cover the whole rest of the horizon: 20 ct/kWh = 0.0002 EUR/Wh in every slot
    assert np.allclose(after.to_numpy(), 20.0 / 1e5)
    doc = json.loads((tmp_path / "uncertainty.json").read_text())
    assert doc["unit"] == "ct/kWh" and set(doc["quantiles"]) == {
        "0.1",
        "0.25",
        "0.5",
        "0.75",
        "0.9",
    }
    assert len(doc["hours_utc"]) == len(doc["point"])


@pytest.mark.asyncio
async def test_hours_without_a_model_value_keep_the_ets_price(provider, tmp_path, monkeypatch):
    monkeypatch.setattr(type(provider), "_engine_folder", lambda self: tmp_path)
    start = to_datetime("2026-07-10 00:00:00", in_timezone="Europe/Berlin")
    day_d = dt.date(2026, 7, 10)
    outcome = _outcome(day_d)
    missing = pd.DatetimeIndex(core.local_day_hours(day_d + dt.timedelta(days=3)))
    outcome.forecast.point.loc[missing] = np.nan  # no model value for D+3
    series = await _run_provider(provider, StubEngine(outcome), start)
    last_known = to_datetime(provider.highest_orig_datetime)
    after = series[series.index > last_known]
    local_day = pd.DatetimeIndex(after.index).tz_convert("Europe/Berlin").date
    assert np.allclose(after[local_day == dt.date(2026, 7, 12)].to_numpy(), 20.0 / 1e5)
    assert np.allclose(after[local_day == dt.date(2026, 7, 13)].to_numpy(), 0.0003)  # ETS


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["not_ready", "exception"])
async def test_provider_falls_back_to_ets_and_keeps_prices(
    provider, tmp_path, monkeypatch, variant
):
    monkeypatch.setattr(type(provider), "_engine_folder", lambda self: tmp_path)
    start = to_datetime("2026-07-12 00:00:00", in_timezone="Europe/Berlin")
    eng = (
        StubEngine(None, "stage A model is being trained")
        if variant == "not_ready"
        else StubEngine(exc=RuntimeError("broken"))
    )
    series = await _run_provider(provider, eng, start)
    last_known = to_datetime(provider.highest_orig_datetime)
    after = series[series.index > last_known]
    assert len(after) > 0 and np.allclose(after.to_numpy(), 0.0003)  # pure ETS
    assert not (tmp_path / "uncertainty.json").exists()


def test_known_hourly_and_complete_day_helpers():
    idx = pd.date_range("2026-07-10 00:00", periods=96, freq="15min", tz="Europe/Berlin")
    raw = pd.Series(np.arange(96) * 1e-6, index=idx)
    hourly = ElecPriceAkkudoktorML._known_hourly_ct(raw)
    assert len(hourly) == 24 and hourly.iloc[0] == pytest.approx(np.mean(np.arange(4)) * 1e-6 * 1e5)
    assert ElecPriceAkkudoktorML._last_complete_local_day(hourly) == dt.date(2026, 7, 10)
    assert ElecPriceAkkudoktorML._last_complete_local_day(hourly.iloc[:-1]) is None


def _prices(end: str, step: int = 900):
    """Stub of a published price series ending (exclusive) at ``end`` (local time)."""
    stop = int(pd.Timestamp(end, tz="Europe/Berlin").timestamp())
    return SimpleNamespace(unix_seconds=[stop - 2 * step, stop - step])


class _Sources:
    """Replaces the request methods of both sources and records calls (source, force_update)."""

    def __init__(self, energycharts, smard):
        self.calls: list[tuple[str, bool]] = []
        self.answers = {"EC": energycharts, "SMARD": smard}

    def stub(self, name):
        def request(provider, start_date=None, force_update=False):
            self.calls.append((name, bool(force_update)))
            answer = self.answers[name]
            if isinstance(answer, Exception):
                raise answer
            return answer

        return request


@pytest.fixture
def sources(provider, monkeypatch):
    from akkudoktoreos.prediction.elecpriceenergycharts import ElecPriceEnergyCharts
    from akkudoktoreos.prediction.elecpricesmard import ElecPriceSMARD

    state = {}

    def install(energycharts, smard, now="2026-07-10 15:00"):
        src = _Sources(energycharts, smard)
        state["src"] = src
        monkeypatch.setattr(ElecPriceEnergyCharts, "_request_forecast", src.stub("EC"))
        monkeypatch.setattr(ElecPriceSMARD, "_request_forecast", src.stub("SMARD"))
        monkeypatch.setattr(
            type(provider), "_now", lambda self: pd.Timestamp(now, tz="Europe/Berlin")
        )
        provider._last_published_end = None
        provider._last_forced_at = 0.0
        return src

    return install


def test_published_prices_come_from_energycharts_first(provider, sources):
    src = sources(_prices("2026-07-12 00:00"), _prices("2026-07-12 00:00"))
    assert provider._request_forecast("2026-07-01").unix_seconds == src.answers["EC"].unix_seconds
    assert src.calls == [("EC", False)]


def test_published_prices_use_the_source_that_has_the_next_day(provider, sources):
    # after 14:00 the next day (up to 12.7. 00:00) is expected; Energy-Charts has not got it yet,
    # SMARD has
    src = sources(_prices("2026-07-11 00:00"), _prices("2026-07-12 00:00"))
    assert provider._request_forecast() is src.answers["SMARD"]
    assert src.calls == [("EC", False), ("SMARD", False)]


def test_published_prices_before_publication_hour_need_only_the_current_day(provider, sources):
    src = sources(_prices("2026-07-11 00:00"), _prices("2026-07-11 00:00"), now="2026-07-10 10:00")
    assert provider._request_forecast() is src.answers["EC"]
    assert src.calls == [("EC", False)]


def test_incomplete_answers_are_not_reused_for_an_hour(provider, sources, monkeypatch):
    src = sources(_prices("2026-07-11 00:00"), _prices("2026-07-11 00:00"))
    provider._request_forecast()  # nothing has the next day yet: both asked, normal caching
    assert src.calls == [("EC", False), ("SMARD", False)]
    provider._request_forecast()  # the answer was incomplete: a fresh request is forced once
    assert [force for _, force in src.calls[2:]] == [True, True]
    provider._request_forecast()  # within 5 minutes: normal (cached) requests, no flooding
    assert [force for _, force in src.calls[4:]] == [False, False]
    provider._last_forced_at = time.time() - 301
    provider._request_forecast()  # five minutes later: forced again, not the cached answer
    assert [force for _, force in src.calls[6:]] == [True, True]


def test_published_prices_fall_back_when_a_source_fails(provider, sources):
    src = sources(RuntimeError("down"), _prices("2026-07-12 00:00"))
    assert provider._request_forecast() is src.answers["SMARD"]
    assert [name for name, _ in src.calls] == ["EC", "SMARD"]


def test_published_prices_smard_as_primary_source(provider, config_eos, sources):
    config_eos.merge_settings_from_dict({"elecprice": {"akkudoktorml": {"price_source": "smard"}}})
    src = sources(_prices("2026-07-12 00:00"), _prices("2026-07-12 00:00"))
    provider._request_forecast()
    assert src.calls == [("SMARD", False)]


def test_smard_source_is_skipped_for_a_non_default_region(provider, config_eos, sources):
    """The model is valid for the DE-LU price: SMARD with another region/filter must not be used."""
    config_eos.merge_settings_from_dict(
        {"elecprice": {"akkudoktorml": {"price_source": "smard"}, "smard": {"region": "AT"}}}
    )
    src = sources(_prices("2026-07-12 00:00"), _prices("2026-07-12 00:00"))
    assert provider._request_forecast() is src.answers["EC"]
    assert [name for name, _ in src.calls] == ["EC"]


def test_published_prices_raise_when_both_sources_fail(provider, sources):
    sources(RuntimeError("down"), RuntimeError("down too"))
    with pytest.raises(RuntimeError):
        provider._request_forecast()


def test_model_uses_zone_de_lu_regardless_of_user_zone(provider, config_eos):
    config_eos.merge_settings_from_dict({"elecprice": {"energycharts": {"bidding_zone": "AT"}}})
    assert provider._bidding_zone() == "DE-LU"
    assert provider.exchange_price_zone() == "DE-LU"


def test_stored_models_are_bound_to_library_version_and_schema(tmp_path, inputs, monkeypatch):
    """Model files of another scikit-learn version or schema are never loaded, then pruned."""
    eng = prepared_engine(tmp_path, inputs, monkeypatch)
    m0 = core.month_start(LOCAL_D)
    foreign = tmp_path / f"stage_a_{m0:%Y-%m}_sk0.0.1_s1.joblib"
    foreign.write_bytes(b"not a model of this version")
    legacy = tmp_path / f"stage_a_{m0:%Y-%m}.joblib"
    legacy.write_bytes(b"legacy file name without version")
    assert eng._load_models(m0) is False  # nothing stored for the current version yet
    assert foreign.exists() and legacy.exists()
    eng._ensure_month(eng.inputs(), m0)
    own = tmp_path / f"stage_a_{m0:%Y-%m}_{eng._model_tag()}.joblib"
    assert own.exists()
    assert not foreign.exists() and not legacy.exists()  # pruned after the new model was stored
    assert str(importlib.metadata.version("scikit-learn")) in own.name
    assert f"_s{engine_mod.MODEL_SCHEMA}" in own.name


def test_old_monthly_models_are_pruned(tmp_path, inputs, monkeypatch):
    eng = prepared_engine(tmp_path, inputs, monkeypatch)
    tag = eng._model_tag()
    m0 = core.month_start(LOCAL_D)  # 2026-03
    old = [tmp_path / f"stage_a_{m}_{tag}.joblib" for m in ("2025-12", "2026-01", "2026-02")]
    for p in old:
        p.write_bytes(b"model")
    eng._ensure_month(eng.inputs(), m0)
    assert not old[0].exists()  # older than the current month minus 2 months
    assert old[1].exists() and old[2].exists()  # kept (fallback while a new month is trained)
    assert eng._models_path(m0).exists()


def test_corrupt_model_file_is_deleted_and_retrained(tmp_path, inputs, monkeypatch):
    eng = prepared_engine(tmp_path, inputs, monkeypatch)
    m0 = core.month_start(LOCAL_D)
    bad = eng._models_path(m0)
    bad.write_bytes(b"corrupt")
    assert eng._load_models(m0) is False
    assert not bad.exists()  # removed, so the next start does not try again
    eng._ensure_month(eng.inputs(), m0)  # retrained and stored again
    assert bad.exists() and bad.stat().st_size > 1000
    eng2 = prepared_engine(tmp_path, inputs, monkeypatch)
    assert eng2._load_models(m0) is True


def test_state_messages_are_logged_once_per_state(provider, monkeypatch):
    from loguru import logger

    provider._state = ""
    provider._state_logged_at = 0.0
    messages: list[tuple[str, str]] = []
    handler = logger.add(
        lambda m: messages.append((m.record["level"].name, m.record["message"])), level="INFO"
    )
    try:
        for _ in range(5):  # several updates per planning cycle
            provider._log_fallback("rate limit")
        provider._note_state("model", "info", "model prices active")
        for _ in range(5):
            provider._note_state("model", "info", "model prices active")
        provider._log_fallback("rate limit")  # a new state change is reported again
    finally:
        logger.remove(handler)
    assert [level for level, _ in messages] == ["WARNING", "INFO", "WARNING"]
    assert "rate limit" in messages[0][1]


def test_weather_first_fill_is_paced_and_resumable(tmp_path, inputs):
    cache = rdata.DataCache(tmp_path)
    calls: list = []
    sleeps: list = []
    real = weather_getter(inputs.weather, calls)

    def flaky(url, params):
        if len(calls) >= 2:
            raise rdata.RateLimited("HTTP 429")
        return real(url, params)

    with (
        patch.object(rdata, "WEATHER_START", dt.date(2025, 10, 1)),
        patch.object(rdata, "WEATHER_CHUNK_DAYS", 30),
    ):
        with pytest.raises(rdata.RateLimited):
            cache.update_weather(flaky, dt.date(2026, 3, 20), sleep=sleeps.append)
        # two blocks were stored before the limit hit; pauses keep the call weight below the
        # per-minute limit
        partial = cache.load_weather()
        assert partial is not None and partial[3].index.max() == pd.Timestamp(
            "2025-11-29 23:00", tz="UTC"
        )
        assert len(sleeps) == 2 and all(
            s >= 30 * rdata.WEATHER_WEIGHT_PER_DAY * 60 / rdata.WEATHER_WEIGHT_PER_MINUTE - 1e-9
            for s in sleeps
        )
        calls.clear()
        full = cache.update_weather(real, dt.date(2026, 3, 20), sleep=lambda s: None)
    assert pd.Timestamp(calls[0]["start_date"]) == pd.Timestamp(
        "2025-11-19"
    )  # resumes (last stored day - 10 days)
    assert (
        full[3].index.max() == pd.Timestamp("2026-03-26 23:00", tz="UTC")
        and full[3].index.is_monotonic_increasing
    )
