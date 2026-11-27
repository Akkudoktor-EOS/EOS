"""Rechenkern des Strompreis-Providers ``ElecPriceResidualLoad``.

Dieser Kern ist reine Numerik (numpy, pandas, scikit-learn) und kennt weder EOS-Konfiguration noch Netzwerk.
Er ist eine verlustfreie Übernahme der im Replay (``scripts/elecprice_replay``) festgelegten und im
Blindzeitraum geprüften Bauform ``a_12punkte`` + ``kurve_anker`` + Unsicherheit U2 (``residual``).

Idee: Der Day-Ahead-Preis hängt stark an der Residuallast (Last - Wind - PV). Aus dem Wetter der
ECMWF-Prognose (12 Wetterpunkte, Vorlauf K = N+1 Tage) werden Last, Wind an Land, Wind auf See und PV
mit Gradient-Boosting vorhergesagt (monatlich neu trainiert). Eine monotone Preiskurve (wöchentlich neu auf den
letzten 8 Wochen) macht daraus einen Preis, der am bekannten Day-Ahead-Preis des Folgetags verankert wird.

Zeitmodell (Leckregel, identisch zum Replay): Alle Zeitstempel sind UTC. Ausgabetag D (lokal), Ausgabezeit
13:00 Europe/Berlin. Bekannt sind Preise bis Ende D+1, Messwerte bis Ende D-1 (+12 h Verzögerung), Wetter
``previous_dayK`` für Zielstunde t ab t - 24*K h + 12 h. Zieltage sind D+2..D+5.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

TZ_LOCAL = ZoneInfo("Europe/Berlin")
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

# Wetterpunkte: (id, Breite, Länge). Die Reihenfolge ist die Spaltenreihenfolge der Modelleingänge
# (identisch zur Reihenfolge im Replay-Archiv) und darf nicht geändert werden.
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
    ("last_berlin", 52.50, 13.40),
    ("last_ruhr", 51.45, 7.00),
)


def weather_columns() -> list[str]:
    """Spalten ``<punkt>__<variable>`` in der festen Reihenfolge der Modelleingänge."""
    return [f"{p[0]}__{v}" for p in WEATHER_POINTS for v in WEATHER_VARS]


# ------------------------------ feste Einstellungen -------------------------------------
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
WINDOW_DAYS = 120  # Unsicherheit: Fehlerhistorie
MIN_DAYS = 40
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
LEVEL_DAYS = 28  # U3: Preisniveau = Mittel der Tagesmittel der letzten 28 Tage
LEVEL_MIN_DAYS = 20
LEVEL_FLOOR = 2.0  # ct/kWh
SCALE_CLIP = (0.5, 2.5)


# ------------------------------ Zeitregeln --------------------------------------------
def local_midnight_utc(day: dt.date) -> pd.Timestamp:
    return pd.Timestamp(dt.datetime.combine(day, dt.time(0), tzinfo=TZ_LOCAL)).tz_convert("UTC")


def local_day_hours(day: dt.date) -> pd.DatetimeIndex:
    start, end = local_midnight_utc(day), local_midnight_utc(day + dt.timedelta(days=1))
    return pd.date_range(start, end, freq="1h", inclusive="left")


def issue_time(day: dt.date) -> pd.Timestamp:
    return pd.Timestamp(
        dt.datetime.combine(day, dt.time(ISSUE_HOUR_LOCAL), tzinfo=TZ_LOCAL)
    ).tz_convert("UTC")


def _naive_local_midnight(idx: pd.Index) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(idx).tz_convert(TZ_LOCAL).tz_localize(None).normalize()


def price_available_at(hours_utc: pd.Index) -> pd.DatetimeIndex:
    """Day-Ahead-Preis einer Stunde: ab 13:00 lokal am Vortag des lokalen Liefertags."""
    nm = (
        _naive_local_midnight(hours_utc)
        - pd.Timedelta(days=1)
        + pd.Timedelta(hours=ISSUE_HOUR_LOCAL)
    )
    return nm.tz_localize(TZ_LOCAL).tz_convert("UTC")


def measurement_available_at(hours_utc: pd.Index) -> pd.DatetimeIndex:
    """Messwert einer Stunde: Ende des lokalen Tages + 12 h."""
    nxt = (
        (_naive_local_midnight(hours_utc) + pd.Timedelta(days=1))
        .tz_localize(TZ_LOCAL)
        .tz_convert("UTC")
    )
    return nxt + pd.Timedelta(hours=MEASUREMENT_DELAY_H)


def weather_available_at(target_hours_utc: pd.Index, lead_k: int) -> pd.DatetimeIndex:
    return (
        pd.DatetimeIndex(target_hours_utc)
        - pd.Timedelta(hours=24 * lead_k)
        + pd.Timedelta(hours=WEATHER_PUBLISH_DELAY_H)
    )


# ------------------------------ Eingaben und Wissensstand --------------------------------
@dataclass
class Inputs:
    """Alle Rohdaten (UTC-Stundenindex).

    price: ct/kWh (Stundenmittel Day-Ahead); meas: MW (Spalten STAGE_A_TARGETS); weather: K -> Zielstunde x
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
        return pd.DatetimeIndex(parts[0].append(parts[1:]))


class LeakError(AssertionError):
    """Ein Eingang mit Verfügbarkeitszeitpunkt nach der Ausgabezeit wurde entdeckt."""


def build_knowledge(inputs: Inputs, day: dt.date) -> Knowledge:
    """Wissensstand von Tag D um 13:00 Europe/Berlin; prüft sich selbst auf Leckfreiheit."""
    issue = issue_time(day)
    pa = price_available_at(inputs.price.index)
    ma = measurement_available_at(inputs.meas.index)
    pm, mm = np.asarray(pa <= issue), np.asarray(ma <= issue)
    weather = {}
    for k, w in inputs.weather.items():
        weather[k] = w[np.asarray(weather_available_at(w.index, k) <= issue)]
    kn = Knowledge(
        day=day,
        issue_utc=issue,
        price=inputs.price[pm],
        meas=inputs.meas[mm],
        weather=weather,
        price_avail=pa[pm],
        meas_avail=ma[mm],
    )
    if len(kn.price) and price_available_at(kn.price.index).max() > issue:
        raise LeakError("price")
    if len(kn.meas) and measurement_available_at(kn.meas.index).max() > issue:
        raise LeakError("meas")
    for k, w in weather.items():
        if len(w) and weather_available_at(w.index, k).max() > issue:
            raise LeakError(f"weather K={k}")
    return kn


# ------------------------------ Merkmale --------------------------------------------------
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
    """Bundesweite Feiertage (wie ``holidays.Germany()`` ohne Bundesland)."""
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


def calendar_features(idx: pd.Index) -> pd.DataFrame:
    loc = pd.DatetimeIndex(idx).tz_convert(TZ_LOCAL)
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
    """Gleitendes 95-%-Quantil der letzten 365 Tage, um 48 h verzögert (Zubau-Maß ohne Zukunft)."""
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
    """scikit-learn im aufrufenden (Ereignisschleifen-)Thread importieren, bevor Hintergrund-Threads es lazy laden."""
    from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: F401


def _gbm() -> Any:
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(**GBM_PARAMS)


# ------------------------------ Stufe A: Wetter -> Last/Wind/PV ---------------------------
class StageA:
    """Je Vorlauf K und Ziel ein Gradient-Boosting-Modell, monatlich neu (wachsendes Fenster).

    ``models[(m0, K, ziel)]``; ``m0`` = Monatserster. Training mit Zeilen, die um 13:00 lokal am Monatsersten
    nach Leckregel bekannt waren (Messwert und Wetterlauf).
    """

    def __init__(self) -> None:
        self.models: dict[tuple[dt.date, int, str], object] = {}
        self.audit: list[dict] = []

    # -- Training --
    def has_month(self, m0: dt.date) -> bool:
        return (m0, WEATHER_LEADS[0], STAGE_A_TARGETS[0]) in self.models

    def train_month(self, inputs: Inputs, m0: dt.date) -> None:
        cutoff = issue_time(m0)
        ma = measurement_available_at(inputs.meas.index)
        meas = inputs.meas[np.asarray(ma <= cutoff)]
        levels = pd.DataFrame({c: rolling_level(meas[c]) for c in STAGE_A_TARGETS})
        cal_all = calendar_features(meas.index)
        for K in WEATHER_LEADS:
            w = inputs.weather[K]
            w = w[np.asarray(weather_available_at(w.index, K) <= cutoff)]
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
                self.audit.append(
                    dict(
                        month=m0,
                        K=K,
                        target=tgt,
                        cutoff=cutoff,
                        rows=len(ids),
                        max_meas_avail=measurement_available_at(ids).max(),
                        max_weather_avail=weather_available_at(ids, K).max(),
                    )
                )

    def export_month(self, m0: dt.date) -> dict:
        return {k: v for k, v in self.models.items() if k[0] == m0}

    def import_month(self, state: dict) -> None:
        self.models.update(state)

    def drop_months_before(self, m0: dt.date) -> None:
        self.models = {k: v for k, v in self.models.items() if k[0] >= m0}

    # -- Prognose --
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
        # Solar generation cannot be negative. Apply the same physical bound to
        # the D+1 anchor and every forecast/error-history reconstruction.
        # NaN remains NaN; the saved monthly GBMs and their training are unchanged.
        df["pv_mw"] = df["pv_mw"].clip(lower=0.0)
        df.loc[w.isna().all(axis=1).to_numpy(), :] = np.nan  # kein Wetter -> keine Prognose
        return df

    def predict(
        self,
        k: Knowledge,
        inputs: Optional[Inputs] = None,
        auto_train: bool = False,
        month: Optional[dt.date] = None,
    ) -> pd.DataFrame:
        """Index = Stunden D+1 (K=3, n=1) und D+2..D+5 (K=N+1); Spalten Ziele, ``resid_mw``, ``n``, ``K``.

        ``month``: Monatsmodell (Standard: Monat von D; im Betrieb zur Überbrückung auch der Vormonat).
        """
        m0 = month or month_start(k.day)
        if not self.has_month(m0):
            if not auto_train or inputs is None:
                raise MissingModel(f"kein Stufe-A-Modell für {m0}")
            self.train_month(inputs, m0)
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
    """Das benötigte Monatsmodell ist (noch) nicht trainiert."""


# ------------------------------ Stufe B: Preiskurve und Anker ------------------------------
def _curve_X(resid_mw: np.ndarray, idx: pd.Index) -> pd.DataFrame:
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
    """Preis = f(Residuallast, Stunde, Wochenende/Feiertag); montags neu auf den letzten 8 Wochen (monoton in der Residuallast)."""

    def __init__(self) -> None:
        self.curves: dict[dt.date, object] = {}

    def curve(self, inputs: Inputs, day: dt.date) -> Any:
        from sklearn.ensemble import HistGradientBoostingRegressor

        w0 = week_start(day)
        if w0 in self.curves:
            return self.curves[w0]
        cutoff = issue_time(w0)
        ma = measurement_available_at(inputs.meas.index)
        meas = inputs.meas[np.asarray(ma <= cutoff)]
        meas = meas[meas.index >= local_midnight_utc(w0 - dt.timedelta(days=7 * CURVE_WEEKS))]
        resid = meas["load_mw"] - meas["wind_on_mw"] - meas["wind_off_mw"] - meas["pv_mw"]
        pa = price_available_at(inputs.price.index)
        price = inputs.price[np.asarray(pa <= cutoff)].reindex(meas.index)
        ok = (resid.notna() & price.notna()).to_numpy()
        if ok.sum() < MIN_CURVE_ROWS:
            self.curves[w0] = None
            return None
        ids = meas.index[ok]
        mdl = HistGradientBoostingRegressor(**CURVE_PARAMS, monotonic_cst=[1, 0, 0])
        mdl.fit(_curve_X(resid[ok].to_numpy(), ids), price[ok])
        self.curves[w0] = mdl
        return mdl

    def price_from_resid(self, inputs: Inputs, day: dt.date, resid: pd.Series) -> pd.Series:
        mdl = self.curve(inputs, day)
        if mdl is None:
            return pd.Series(np.nan, index=resid.index)
        ok = resid.notna().to_numpy()
        out = np.full(len(resid), np.nan)
        if ok.any():
            out[ok] = mdl.predict(_curve_X(resid.to_numpy()[ok], resid.index[ok]))
        return pd.Series(out, index=resid.index)

    def drop_weeks_before(self, w0: dt.date) -> None:
        self.curves = {k: v for k, v in self.curves.items() if k >= w0}


@dataclass
class DayForecast:
    """Ergebnis für einen Ausgabetag: Preise (ct/kWh) und Residuallast-Prognose je Zielstunde D+2..D+5."""

    day: dt.date
    point: pd.Series  # ct/kWh, Index = alle Zielstunden
    resid_hat: pd.Series  # MW, Index = alle Zielstunden
    offset: float  # Anker (ungedämpft), ct/kWh
    anchor_hours: int  # Zahl der Stunden von D+1, aus denen der Anker gebildet wurde


def anchor_forecast(
    k: Knowledge, sa: pd.DataFrame, base_all: pd.Series, d1: pd.Series
) -> DayForecast:
    """Anker: Niveauversatz zwischen bekanntem Preis von D+1 und der Kurvenprognose für D+1, gedämpft je Vorlauftag."""
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
    auto_train: bool = False,
    month: Optional[dt.date] = None,
) -> DayForecast:
    """``kurve_anker`` für Ausgabetag D: Kurve(Residuallast) + gedämpfter Niveauversatz aus dem bekannten Preis von D+1."""
    sa = stage_a.predict(k, inputs, auto_train=auto_train, month=month)
    p = curve.price_from_resid(inputs, k.day, sa["resid_mw"])
    return anchor_forecast(k, sa, p[sa["n"] >= 2], p[sa["n"] == 1])


# ------------------------------ Unsicherheit U2 ---------------------------------------------
def hour_keys(t: pd.DatetimeIndex) -> pd.DataFrame:
    """(Ortsstunde, k-tes Vorkommen dieser Ortsstunde am lokalen Tag) je UTC-Stunde (25-h-Tage)."""
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
    """Fehlerverlauf eines früheren Ausgabetags D' (nur wenn alle Zieltage vollständig bekannt).

    e_r = Residuallast ist - Prognose; e_p = Preis ist - Kurve(Residuallast ist) - Anker-Rest (Preisrest).
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
            v = table.get((n, h, 0), 0.0)  # 25-h-Tag: zweites Vorkommen -> erstes; sonst 0
        out[i] = v
    return out


def usable_records(records: dict[str, dict], day: dt.date) -> list[dict]:
    """Frühere Ausgabetage D', die an D als Ganzes nutzbar sind: alle Zieltage gemessen (D'+5 <= D-1) und D' >= D-120."""
    out = []
    for d_s, rec in records.items():
        d = dt.date.fromisoformat(d_s)
        if d >= day - dt.timedelta(days=WINDOW_DAYS) and d + dt.timedelta(
            days=max(TARGET_DAYS)
        ) <= day - dt.timedelta(days=1):
            out.append(rec)
    return sorted(out, key=lambda r: r["day"])


def daily_means(price: pd.Series) -> pd.Series:
    """Tagesmittel je lokalem Kalendertag (nur Tage mit >= 23 Stunden), Index = Datum."""
    loc = price.copy()
    local_index = pd.DatetimeIndex(loc.index).tz_convert(TZ_LOCAL)
    loc.index = local_index
    g = loc.groupby(local_index.date)
    m = g.mean()[g.count() >= 23]
    m.index = pd.Index(m.index)
    return m


def level_asof(dm: pd.Series, last_day: dt.date) -> float:
    """Mittel der Tagesmittel der 28 lokalen Tage bis einschließlich ``last_day`` (nur bekannte Tage); NaN bei < 20 Tagen."""
    days = [last_day - dt.timedelta(days=i) for i in range(LEVEL_DAYS)]
    v = dm.reindex(days).dropna()
    return float(max(v.mean(), LEVEL_FLOOR)) if len(v) >= LEVEL_MIN_DAYS else float("nan")


def residual_scenarios(
    inputs: Inputs,
    k: Knowledge,
    curve: PriceCurve,
    fc: DayForecast,
    records: dict[str, dict],
    level_scale: bool = True,
) -> np.ndarray:
    """U3 (``level_scale=True``) bzw. U2: Szenarien S x H (ct/kWh).

    Residuallastfehler früherer Tage durch die aktuelle Kurve,
    dazu Preisrest; bei U3 wird der Preisrest eines früheren Tags D' mit clip(L_D / L_D', 0,5, 2,5) skaliert
    (L = Mittel der Tagesmittel der 28 Tage bis einschließlich D+1 bzw. D'+1, beide bei D bekannt).
    """
    recs = usable_records(records, k.day)
    if len(recs) < MIN_DAYS:
        raise ValueError(f"nur {len(recs)} nutzbare Fehlertage (nötig {MIN_DAYS})")
    keys = target_keys(k)
    idx = fc.point.index
    r_hat = fc.resid_hat
    f_now = curve.price_from_resid(inputs, k.day, r_hat)
    delta = (fc.point - f_now).to_numpy()
    e_r = np.vstack([_align(r, keys, "e_r") for r in recs])
    e_p = np.vstack([_align(r, keys, "e_p") for r in recs])
    if level_scale:
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
    """Q x H Quantile (10/25/50/75/90 %) der Szenarien."""
    return np.quantile(scen, QUANTILES, axis=0)
