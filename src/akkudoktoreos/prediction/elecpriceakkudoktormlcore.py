"""Computation core of the electricity price provider ``ElecPriceAkkudoktorML``.

This core is pure numerics (numpy, pandas, scikit-learn) and knows neither the EOS configuration nor
the network.

Idea: the day-ahead price depends strongly on the residual load (load - wind - PV). From the ECMWF
weather forecast (12 weather points, lead time K = N+1 days) load, onshore wind, offshore wind and
PV are predicted with gradient boosting (retrained monthly). A monotonic price curve (retrained
weekly on the last 8 weeks) turns this into a price that is anchored at the known day-ahead price of
the following day.

Time model (leak rule, no look-ahead): all timestamps are UTC. Issue day D (local), issue time 13:00
Europe/Berlin. Known are prices up to the end of D+1, measurements up to the end of D-1 (+12 h
delay) and, for target hour t, the weather run ``previous_dayK`` from t - 24*K h + 12 h on. Target
days are D+2..D+5.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional, cast
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

TZ_LOCAL = ZoneInfo("Europe/Berlin")


def _dti(index: pd.Index) -> pd.DatetimeIndex:
    """Narrow a pandas index to the DatetimeIndex it is by construction (typing only)."""
    return cast(pd.DatetimeIndex, index)


ISSUE_HOUR_LOCAL = 13
TARGET_DAYS = (2, 3, 4, 5)
WEATHER_LEADS = (3, 4, 5, 6)  # K = N + 1
WEATHER_VARS = (
    "wind_speed_100m",
    "wind_speed_10m",
    "shortwave_radiation",
    "temperature_2m",
    "cloud_cover",
)
WEATHER_PUBLISH_DELAY_H = 12
MEASUREMENT_DELAY_H = 12
STAGE_A_TARGETS = ("load_mw", "wind_on_mw", "wind_off_mw", "pv_mw")

# Weather points: (id, latitude, longitude). The order is the column order of the model inputs and
# must not be changed (stored models depend on it).
WEATHER_POINTS: tuple[tuple[str, float, float], ...] = (
    ("won_husum", 54.50, 9.05),
    ("won_hannover", 52.40, 9.70),
    ("won_uckermark", 53.10, 13.80),
    ("won_boerde", 52.10, 11.60),
    ("woff_borkum", 54.00, 6.50),
    ("woff_baltic", 54.60, 12.65),
    ("pv_muenchen", 48.10, 11.60),
    ("pv_wuerzburg", 49.80, 9.95),
    ("pv_stuttgart", 48.80, 9.20),
    ("pv_cottbus", 51.75, 14.30),
    ("load_berlin", 52.50, 13.40),
    ("load_ruhr", 51.45, 7.00),
)


def weather_columns() -> list[str]:
    """Columns ``<point>__<variable>`` in the fixed order of the model inputs."""
    return [f"{p[0]}__{v}" for p in WEATHER_POINTS for v in WEATHER_VARS]


# ------------------------------ fixed settings -------------------------------------
GBM_PARAMS = dict(
    max_iter=250,
    learning_rate=0.06,
    max_leaf_nodes=31,
    min_samples_leaf=50,
    l2_regularization=1.0,
    early_stopping=False,
    random_state=0,
)
CURVE_PARAMS = dict(
    max_iter=100,
    learning_rate=0.1,
    max_leaf_nodes=8,
    min_samples_leaf=20,
    l2_regularization=1.0,
    early_stopping=False,
    random_state=0,
)
LEVEL_QUANTILE = 0.95
LEVEL_WINDOW_H = 365 * 24
LEVEL_MIN_H = 60 * 24
LEVEL_LAG_H = 48
MIN_TRAIN_ROWS = 1500
CURVE_WEEKS = 8
MIN_CURVE_ROWS = 500
DAMPING = {2: 0.8, 3: 0.6, 4: 0.5, 5: 0.4}
WINDOW_DAYS = 120  # uncertainty: error history
MIN_DAYS = 40
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
LEVEL_DAYS = 28  # price level = mean of the daily means of the last 28 days
LEVEL_MIN_DAYS = 20
LEVEL_FLOOR = 2.0  # ct/kWh
SCALE_CLIP = (0.5, 2.5)


# ------------------------------ time rules --------------------------------------------
def local_midnight_utc(day: dt.date) -> pd.Timestamp:
    return pd.Timestamp(dt.datetime.combine(day, dt.time(0), tzinfo=TZ_LOCAL)).tz_convert("UTC")


def local_day_hours(day: dt.date) -> pd.DatetimeIndex:
    start, end = local_midnight_utc(day), local_midnight_utc(day + dt.timedelta(days=1))
    return pd.date_range(start, end, freq="1h", inclusive="left")


def issue_time(day: dt.date) -> pd.Timestamp:
    return pd.Timestamp(
        dt.datetime.combine(day, dt.time(ISSUE_HOUR_LOCAL), tzinfo=TZ_LOCAL)
    ).tz_convert("UTC")


def _naive_local_midnight(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return idx.tz_convert(TZ_LOCAL).tz_localize(None).normalize()


def price_available_at(hours_utc: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Day-ahead price of an hour: known from 13:00 local time on the day before."""
    nm = (
        _naive_local_midnight(hours_utc)
        - pd.Timedelta(days=1)
        + pd.Timedelta(hours=ISSUE_HOUR_LOCAL)
    )
    return nm.tz_localize(TZ_LOCAL).tz_convert("UTC")


def measurement_available_at(hours_utc: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Measurement of an hour: end of the local day + 12 h."""
    nxt = (
        (_naive_local_midnight(hours_utc) + pd.Timedelta(days=1))
        .tz_localize(TZ_LOCAL)
        .tz_convert("UTC")
    )
    return nxt + pd.Timedelta(hours=MEASUREMENT_DELAY_H)


def weather_available_at(target_hours_utc: pd.DatetimeIndex, lead_k: int) -> pd.DatetimeIndex:
    return (
        target_hours_utc
        - pd.Timedelta(hours=24 * lead_k)
        + pd.Timedelta(hours=WEATHER_PUBLISH_DELAY_H)
    )


# ------------------------------ inputs and state of knowledge --------------------------------
@dataclass
class Inputs:
    """All raw data (UTC hourly index).

    price: ct/kWh (hourly mean day-ahead); meas: MW (columns STAGE_A_TARGETS); weather: K -> target
    hour x
    ``weather_columns()``.
    """

    price: pd.Series
    meas: pd.DataFrame
    weather: dict[int, pd.DataFrame]


@dataclass
class Knowledge:
    day: dt.date
    issue_utc: pd.Timestamp
    price: pd.Series
    meas: pd.DataFrame
    weather: dict[int, pd.DataFrame]
    price_avail: pd.DatetimeIndex
    meas_avail: pd.DatetimeIndex

    def target_hours(self, n: int) -> pd.DatetimeIndex:
        return local_day_hours(self.day + dt.timedelta(days=n))

    def all_target_hours(self) -> pd.DatetimeIndex:
        parts = [self.target_hours(n) for n in TARGET_DAYS]
        return _dti(parts[0].append(parts[1:]))


class LeakError(AssertionError):
    """An input with an availability time after the issue time was detected."""


def build_knowledge(inputs: Inputs, day: dt.date) -> Knowledge:
    """State of knowledge of day D at 13:00 Europe/Berlin; checks itself for leaks."""
    issue = issue_time(day)
    pa = price_available_at(_dti(inputs.price.index))
    ma = measurement_available_at(_dti(inputs.meas.index))
    pm, mm = np.asarray(pa <= issue), np.asarray(ma <= issue)
    weather = {}
    for k, w in inputs.weather.items():
        weather[k] = w[np.asarray(weather_available_at(_dti(w.index), k) <= issue)]
    kn = Knowledge(
        day=day,
        issue_utc=issue,
        price=inputs.price[pm],
        meas=inputs.meas[mm],
        weather=weather,
        price_avail=pa[pm],
        meas_avail=ma[mm],
    )
    if len(kn.price) and price_available_at(_dti(kn.price.index)).max() > issue:
        raise LeakError("price")
    if len(kn.meas) and measurement_available_at(_dti(kn.meas.index)).max() > issue:
        raise LeakError("meas")
    for k, w in weather.items():
        if len(w) and weather_available_at(_dti(w.index), k).max() > issue:
            raise LeakError(f"weather K={k}")
    return kn


# ------------------------------ features --------------------------------------------------
def _easter(year: int) -> dt.date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + el * 22) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


@lru_cache(maxsize=None)
def german_holidays(year: int) -> frozenset[dt.date]:
    """Nationwide public holidays (like ``holidays.Germany()`` without a federal state)."""
    e = _easter(year)
    return frozenset(
        {
            dt.date(year, 1, 1),
            e - dt.timedelta(days=2),
            e + dt.timedelta(days=1),
            dt.date(year, 5, 1),
            e + dt.timedelta(days=39),
            e + dt.timedelta(days=50),
            dt.date(year, 10, 3),
            dt.date(year, 12, 25),
            dt.date(year, 12, 26),
        }
    )


def calendar_features(idx: pd.DatetimeIndex) -> pd.DataFrame:
    loc = idx.tz_convert(TZ_LOCAL)
    doy = loc.day_of_year.to_numpy()
    dates = loc.date
    hol = {y: german_holidays(y) for y in set(d.year for d in dates)}
    return pd.DataFrame(
        {
            "hour": loc.hour.to_numpy(),
            "dow": loc.day_of_week.to_numpy(),
            "holiday": np.array([d in hol[d.year] for d in dates], dtype=float),
            "doy_sin": np.sin(2 * np.pi * doy / 365.25),
            "doy_cos": np.cos(2 * np.pi * doy / 365.25),
        },
        index=idx,
    )


def rolling_level(s: pd.Series) -> pd.Series:
    """Rolling 95 % quantile of the last 365 days, delayed by 48 h (no look-ahead)."""
    full = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="1h"))
    lv = full.rolling(LEVEL_WINDOW_H, min_periods=LEVEL_MIN_H).quantile(LEVEL_QUANTILE)
    return lv.shift(LEVEL_LAG_H)


def current_level(meas_col: pd.Series) -> float:
    tail = meas_col.dropna().iloc[-LEVEL_WINDOW_H:]
    return float(tail.quantile(LEVEL_QUANTILE)) if len(tail) >= LEVEL_MIN_H else float("nan")


def month_start(day: dt.date) -> dt.date:
    return day.replace(day=1)


def week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def preload() -> None:
    """Import scikit-learn once up front so that threads do not import it concurrently."""
    from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: F401


def _gbm() -> Any:
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(**GBM_PARAMS)


# ------------------------------ Stage A: weather -> load/wind/PV ---------------------------
class StageA:
    """One gradient-boosting model per lead time K and target, retrained monthly (growing window).

    ``models[(m0, K, target)]``; ``m0`` = first day of the month. Trained on rows that were known at
    13:00 local
    time on the first of the month according to the leak rule (measurement and weather run).
    """

    def __init__(self) -> None:
        self.models: dict[tuple[dt.date, int, str], object] = {}

    # -- training --
    def has_month(self, m0: dt.date) -> bool:
        return (m0, WEATHER_LEADS[0], STAGE_A_TARGETS[0]) in self.models

    def train_month(self, inputs: Inputs, m0: dt.date) -> None:
        cutoff = issue_time(m0)
        ma = measurement_available_at(_dti(inputs.meas.index))
        meas = inputs.meas[np.asarray(ma <= cutoff)]
        levels = pd.DataFrame({c: rolling_level(meas[c]) for c in STAGE_A_TARGETS})
        cal_all = calendar_features(_dti(meas.index))
        for K in WEATHER_LEADS:
            w = inputs.weather[K]
            w = w[np.asarray(weather_available_at(_dti(w.index), K) <= cutoff)]
            idx = meas.index.intersection(w.index)
            for tgt in STAGE_A_TARGETS:
                key = (m0, K, tgt)
                y = meas.loc[idx, tgt]
                ok = y.notna().to_numpy()
                if ok.sum() < MIN_TRAIN_ROWS:
                    self.models[key] = None
                    continue
                ids = idx[ok]
                X = pd.concat([w.loc[ids], cal_all.loc[ids]], axis=1)
                X["level"] = levels[tgt].reindex(ids).to_numpy()
                self.models[key] = _gbm().fit(X, y[ok])

    def export_month(self, m0: dt.date) -> dict:
        return {k: v for k, v in self.models.items() if k[0] == m0}

    def import_month(self, state: dict) -> None:
        self.models.update(state)

    def drop_months_before(self, m0: dt.date) -> None:
        self.models = {k: v for k, v in self.models.items() if k[0] >= m0}

    # -- forecast --
    def _predict(self, k: Knowledge, m0: dt.date, hours: pd.DatetimeIndex, K: int) -> pd.DataFrame:
        w = k.weather[K].reindex(hours)
        cal = calendar_features(hours)
        out = {}
        for tgt in STAGE_A_TARGETS:
            mdl = self.models.get((m0, K, tgt))
            if mdl is None:
                out[tgt] = np.full(len(hours), np.nan)
                continue
            X = pd.concat([w, cal], axis=1)
            X["level"] = current_level(k.meas[tgt])
            out[tgt] = mdl.predict(X)  # type: ignore[attr-defined]
        df = pd.DataFrame(out, index=hours)
        df.loc[w.isna().all(axis=1).to_numpy(), :] = np.nan  # no weather -> no forecast
        return df

    def predict(
        self,
        k: Knowledge,
        month: Optional[dt.date] = None,
    ) -> pd.DataFrame:
        """Hours of D+1 (K=3) and D+2..D+5 (K=N+1); columns: targets, ``resid_mw``, ``n``, ``K``.

        ``month``: monthly model (default: month of D; in operation the previous month as a bridge).
        """
        m0 = month or month_start(k.day)
        if not self.has_month(m0):
            raise MissingModel(f"no stage A model for {m0}")
        parts = []
        h1 = local_day_hours(k.day + dt.timedelta(days=1))
        for n, hours in [(1, h1)] + [(n, k.target_hours(n)) for n in TARGET_DAYS]:
            K = 3 if n == 1 else n + 1
            df = self._predict(k, m0, hours, K)
            df["n"], df["K"] = n, K
            parts.append(df)
        out = pd.concat(parts)
        out["resid_mw"] = out["load_mw"] - out["wind_on_mw"] - out["wind_off_mw"] - out["pv_mw"]
        return out


class MissingModel(RuntimeError):
    """The required monthly model is not (yet) trained."""


# ------------------------------ Stage B: price curve and anchor ------------------------------
def _curve_X(resid_mw: np.ndarray, idx: pd.DatetimeIndex) -> pd.DataFrame:
    cal = calendar_features(idx)
    return pd.DataFrame(
        {
            "resid_gw": np.asarray(resid_mw, dtype=float) / 1000.0,
            "hour": cal["hour"].to_numpy(),
            "weekend": ((cal["dow"] >= 5) | (cal["holiday"] > 0)).astype(float).to_numpy(),
        },
        index=idx,
    )


class PriceCurve:
    """Price = f(residual load, hour, weekend/holiday), monotonic; refitted weekly on 8 weeks."""

    def __init__(self) -> None:
        self.curves: dict[dt.date, object] = {}

    def curve(self, inputs: Inputs, day: dt.date) -> Any:
        from sklearn.ensemble import HistGradientBoostingRegressor

        w0 = week_start(day)
        if w0 in self.curves:
            return self.curves[w0]
        cutoff = issue_time(w0)
        ma = measurement_available_at(_dti(inputs.meas.index))
        meas = inputs.meas[np.asarray(ma <= cutoff)]
        meas = meas[meas.index >= local_midnight_utc(w0 - dt.timedelta(days=7 * CURVE_WEEKS))]
        resid = meas["load_mw"] - meas["wind_on_mw"] - meas["wind_off_mw"] - meas["pv_mw"]
        pa = price_available_at(_dti(inputs.price.index))
        price = inputs.price[np.asarray(pa <= cutoff)].reindex(meas.index)
        ok = (resid.notna() & price.notna()).to_numpy()
        if ok.sum() < MIN_CURVE_ROWS:
            self.curves[w0] = None
            return None
        ids = meas.index[ok]
        mdl = HistGradientBoostingRegressor(**CURVE_PARAMS, monotonic_cst=[1, 0, 0])
        mdl.fit(_curve_X(resid[ok].to_numpy(), _dti(ids)), price[ok])
        self.curves[w0] = mdl
        return mdl

    def price_from_resid(self, inputs: Inputs, day: dt.date, resid: pd.Series) -> pd.Series:
        mdl = self.curve(inputs, day)
        if mdl is None:
            return pd.Series(np.nan, index=resid.index)
        ok = resid.notna().to_numpy()
        out = np.full(len(resid), np.nan)
        if ok.any():
            out[ok] = mdl.predict(_curve_X(resid.to_numpy()[ok], _dti(resid.index[ok])))
        return pd.Series(out, index=resid.index)

    def drop_weeks_before(self, w0: dt.date) -> None:
        self.curves = {k: v for k, v in self.curves.items() if k >= w0}


@dataclass
class DayForecast:
    """Prices (ct/kWh) and residual-load forecast per target hour D+2..D+5 of an issue day."""

    day: dt.date
    point: pd.Series  # ct/kWh, index = all target hours
    resid_hat: pd.Series  # MW, index = all target hours
    offset: float  # anchor (undamped), ct/kWh
    anchor_hours: int  # number of hours of D+1 from which the anchor was formed


def anchor_forecast(
    k: Knowledge, sa: pd.DataFrame, base_all: pd.Series, d1: pd.Series
) -> DayForecast:
    """Anchor: offset between known D+1 price and curve forecast, damped per lead day."""
    base = base_all
    actual = k.price.reindex(d1.index)
    diff = (actual - d1).dropna()
    offset = float(diff.mean()) if len(diff) >= 20 else 0.0
    targets = k.all_target_hours()
    vals = base.reindex(targets).to_numpy().copy()
    ns = np.concatenate([np.full(len(k.target_hours(n)), n) for n in TARGET_DAYS])
    vals = vals + np.array([DAMPING[int(n)] for n in ns]) * offset
    resid = sa.loc[sa["n"] >= 2, "resid_mw"].reindex(targets)
    return DayForecast(
        day=k.day,
        point=pd.Series(vals, index=targets),
        resid_hat=resid,
        offset=offset,
        anchor_hours=int(len(diff)),
    )


def forecast_day(
    inputs: Inputs,
    k: Knowledge,
    stage_a: StageA,
    curve: PriceCurve,
    month: Optional[dt.date] = None,
) -> DayForecast:
    """Forecast for issue day D: curve(residual load) + damped offset to the known D+1 price."""
    sa = stage_a.predict(k, month=month)
    p = curve.price_from_resid(inputs, k.day, sa["resid_mw"])
    return anchor_forecast(k, sa, p[sa["n"] >= 2], p[sa["n"] == 1])


# ------------------------------ Uncertainty ------------------------------------------------
def hour_keys(t: pd.DatetimeIndex) -> pd.DataFrame:
    """(Local hour, k-th occurrence of that hour on the local day) per UTC hour (25-h days)."""
    loc = t.tz_convert(TZ_LOCAL)
    df = pd.DataFrame({"date": loc.date, "hour": loc.hour}, index=range(len(t)))
    df["occ"] = df.groupby(["date", "hour"]).cumcount()
    return df[["hour", "occ"]]


def target_keys(k: Knowledge) -> list[tuple[int, int, int]]:
    keys: list[tuple[int, int, int]] = []
    for n in TARGET_DAYS:
        hk = hour_keys(k.target_hours(n))
        keys += [(n, int(h), int(o)) for h, o in zip(hk["hour"], hk["occ"])]
    return keys


def error_record(inputs: Inputs, curve: PriceCurve, fc: DayForecast) -> Optional[dict]:
    """Error profile of an earlier issue day D' (only if all target days are completely known).

    e_r = actual residual load - forecast; e_p = actual price - curve(actual residual load) - anchor
    remainder (price remainder).
    """
    idx = fc.point.index
    meas = inputs.meas.reindex(idx)
    act_resid = meas["load_mw"] - meas["wind_on_mw"] - meas["wind_off_mw"] - meas["pv_mw"]
    act_price = inputs.price.reindex(idx)
    f_act = curve.price_from_resid(inputs, fc.day, act_resid)
    f_hat = curve.price_from_resid(inputs, fc.day, fc.resid_hat)
    delta = fc.point - f_hat
    e_r = act_resid - fc.resid_hat
    e_p = act_price - f_act - delta
    rec: dict = {"day": fc.day.isoformat(), "n": {}}
    for n in TARGET_DAYS:
        hrs = local_day_hours(fc.day + dt.timedelta(days=n))
        er, ep = e_r.reindex(hrs), e_p.reindex(hrs)
        ok = er.notna() & ep.notna()
        if len(hrs) < 23 or not bool(ok.all()):
            return None
        hk = hour_keys(hrs)
        rec["n"][str(n)] = {
            "hour": [int(x) for x in hk["hour"]],
            "occ": [int(x) for x in hk["occ"]],
            "e_r": [float(x) for x in er],
            "e_p": [float(x) for x in ep],
        }
    return rec


def _align(rec: dict, keys: list[tuple[int, int, int]], field: str) -> np.ndarray:
    table = {}
    for n, d in rec["n"].items():
        for h, o, v in zip(d["hour"], d["occ"], d[field]):
            table[(int(n), h, o)] = v
    out = np.zeros(len(keys))
    for i, (n, h, o) in enumerate(keys):
        v = table.get((n, h, o))
        if v is None:
            v = table.get((n, h, 0), 0.0)  # 25-h day: second occurrence -> first; otherwise 0
        out[i] = v
    return out


def usable_records(records: dict[str, dict], day: dt.date) -> list[dict]:
    """Earlier issue days D' usable at D: targets measured (D'+5 <= D-1), D' >= D-120."""
    out = []
    for d_s, rec in records.items():
        d = dt.date.fromisoformat(d_s)
        if d >= day - dt.timedelta(days=WINDOW_DAYS) and d + dt.timedelta(
            days=max(TARGET_DAYS)
        ) <= day - dt.timedelta(days=1):
            out.append(rec)
    return sorted(out, key=lambda r: r["day"])


def daily_means(price: pd.Series) -> pd.Series:
    """Daily mean per local calendar day (only days with >= 23 hours), index = date."""
    loc = price.copy()
    loc.index = _dti(loc.index).tz_convert(TZ_LOCAL)
    g = loc.groupby(_dti(loc.index).date)
    m = g.mean()[g.count() >= 23]
    m.index = pd.Index(m.index)
    return m


def level_asof(dm: pd.Series, last_day: dt.date) -> float:
    """Mean of the daily means of the 28 local days up to ``last_day``; NaN for < 20 days."""
    days = [last_day - dt.timedelta(days=i) for i in range(LEVEL_DAYS)]
    v = dm.reindex(days).dropna()
    return float(max(v.mean(), LEVEL_FLOOR)) if len(v) >= LEVEL_MIN_DAYS else float("nan")


def residual_scenarios(
    inputs: Inputs,
    k: Knowledge,
    curve: PriceCurve,
    fc: DayForecast,
    records: dict[str, dict],
) -> np.ndarray:
    """Price scenarios S x H (ct/kWh) from the error profiles of earlier days.

    Residual-load errors of earlier days are passed through the current curve and the price
    remainder is added. The price remainder of an earlier day D' is scaled with clip(L_D / L_D',
    0.5, 2.5) (L = mean of the daily means of the 28 days up to and including D+1 or D'+1, both
    known at D).
    """
    recs = usable_records(records, k.day)
    if len(recs) < MIN_DAYS:
        raise ValueError(f"only {len(recs)} usable error days (need {MIN_DAYS})")
    keys = target_keys(k)
    idx = fc.point.index
    r_hat = fc.resid_hat
    f_now = curve.price_from_resid(inputs, k.day, r_hat)
    delta = (fc.point - f_now).to_numpy()
    e_r = np.vstack([_align(r, keys, "e_r") for r in recs])
    e_p = np.vstack([_align(r, keys, "e_p") for r in recs])
    dm = daily_means(k.price)
    l_now = level_asof(dm, k.day + dt.timedelta(days=1))
    g = np.array(
        [level_asof(dm, dt.date.fromisoformat(r["day"]) + dt.timedelta(days=1)) for r in recs]
    )
    g = np.where(np.isfinite(g) & np.isfinite(l_now), l_now / g, 1.0)
    e_p = e_p * np.clip(g, *SCALE_CLIP)[:, None]
    S, H = e_r.shape
    idx_rep = idx.append([idx] * (S - 1)) if S > 1 else idx
    resid_s = pd.Series((r_hat.to_numpy()[None, :] + e_r).ravel(), index=idx_rep)
    price_s = curve.price_from_resid(inputs, k.day, resid_s).to_numpy().reshape(S, H)
    return price_s + delta[None, :] + e_p


def scenario_quantiles(scen: np.ndarray) -> np.ndarray:
    """Q x H quantiles (10/25/50/75/90 %) of the scenarios."""
    return np.quantile(scen, QUANTILES, axis=0)
