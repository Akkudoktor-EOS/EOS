"""Datenbeschaffung und lokaler Cache für ``ElecPriceResidualLoad``.

Quellen (beide ohne Schlüssel, nur generischer User-Agent, keine Standort- oder Personendaten außer den
12 festen Wetterpunkten):

* SMARD (Bundesnetzagentur, CC BY 4.0) ``chart_data``: Day-Ahead-Preis DE-LU (4169), Netzlast (410),
  Wind an Land (4067), Wind auf See (1225), PV (4068), jeweils Stundenreihe in Wochen-Chunks.
* Open-Meteo Previous-Runs-API, Modell ``ecmwf_ifs025``: ``<variable>_previous_day{K}`` für K = 3..6.
  Dieselbe Schnittstelle liefert für Zielzeiten in der Vergangenheit die Trainingsdaten und für Zielzeiten in den
  nächsten Tagen den Wetterlauf, der K Tage vor der Zielzeit gestartet wurde. Damit entspricht die im Betrieb
  genutzte Prognose dem Vorlauf, auf den trainiert wurde (siehe README/Bericht).

Cache: komprimierte CSV-Dateien im Datenordner; nach der Erstbefüllung werden nur noch Zuwächse geholt.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from akkudoktoreos.prediction import elecpriceresidualloadcore as core

USER_AGENT = "Akkudoktor-EOS/ElecPriceResidualLoad"
SMARD_BASE = "https://www.smard.de/app/chart_data"
OPENMETEO_PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
WEATHER_MODEL = "ecmwf_ifs025"
SMARD_START = dt.date(
    2024, 1, 1
)  # Beginn der Messreihen (Trainingsfenster wächst ab hier, wie im Replay)
WEATHER_START = dt.date(2024, 3, 1)  # frühere Tage haben für ecmwf_ifs025 keine Vorlaufwerte
SERIES = {  # Spalte: (Filter, Region)
    "price_eur_mwh": (4169, "DE-LU"),
    "load_mw": (410, "DE"),
    "wind_on_mw": (4067, "DE"),
    "wind_off_mw": (1225, "DE"),
    "pv_mw": (4068, "DE"),
}
WEATHER_REFETCH_DAYS = 10  # so viele zurückliegende Tage werden bei jedem Zuwachs überschrieben (Werte werden endgültig)
WEATHER_FORWARD_DAYS = 6  # Zukunftstage (Zieltage D+2..D+5 relativ zum Ausgabetag)
WEATHER_CHUNK_DAYS = 92
WEATHER_WEIGHT_PER_DAY = (
    1.75  # gewichtete Calls je Tag für 12 Punkte x 20 Spalten (gemessen: 92 Tage = 157,7)
)
WEATHER_WEIGHT_PER_MINUTE = 300.0  # bewusst unter dem Limit von 600/min


class RateLimited(RuntimeError):
    """Open-Meteo oder SMARD meldet Überschreitung des Limits (HTTP 429); später erneut versuchen."""


Getter = Callable[[str, Optional[dict]], bytes]


def http_get(url: str, params: Optional[dict] = None) -> bytes:
    """Abruf mit begrenzten Wiederholungen; 429 wird als RateLimited gemeldet (kein Hämmern)."""
    import requests

    last: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            r = requests.get(
                url, params=params, headers={"User-Agent": USER_AGENT}, timeout=(5, 120)
            )
            if r.status_code == 429:
                raise RateLimited(f"HTTP 429 von {url.split('?')[0]}")
            r.raise_for_status()
            return r.content
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last = exc
            time.sleep(2 * attempt)
    raise last if last is not None else RuntimeError("HTTP request failed without an error")


# ------------------------------ SMARD ---------------------------------------------------------
def fetch_smard_series(get: Getter, column: str, since: pd.Timestamp) -> pd.Series:
    """Stundenreihe einer SMARD-Serie ab ``since`` (Wochen-Chunks, die ``since`` überlappen)."""
    flt, reg = SERIES[column]
    index = json.loads(get(f"{SMARD_BASE}/{flt}/{reg}/index_hour.json", None))["timestamps"]
    chunks = sorted(
        t for t in index if pd.Timestamp(t, unit="ms", tz="UTC") >= since - pd.Timedelta(days=8)
    )
    parts = []
    for t in chunks:
        raw = json.loads(get(f"{SMARD_BASE}/{flt}/{reg}/{flt}_{reg}_hour_{t}.json", None))
        df = pd.DataFrame(raw["series"], columns=["ts", column])
        df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
        parts.append(df[column])
    if not parts:
        return pd.Series(dtype=float, name=column)
    s = pd.concat(parts)
    return s[~s.index.duplicated(keep="last")].sort_index().rename(column).astype(float)


def fetch_smard(get: Getter, since: pd.Timestamp) -> pd.DataFrame:
    return pd.DataFrame({c: fetch_smard_series(get, c, since) for c in SERIES})


# ------------------------------ Open-Meteo ----------------------------------------------------
def weather_params(start: dt.date, end: dt.date) -> dict:
    cols = ",".join(f"{v}_previous_day{k}" for k in core.WEATHER_LEADS for v in core.WEATHER_VARS)
    return {
        "latitude": ",".join(str(p[1]) for p in core.WEATHER_POINTS),
        "longitude": ",".join(str(p[2]) for p in core.WEATHER_POINTS),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": cols,
        "models": WEATHER_MODEL,
        "timezone": "UTC",
    }


def parse_weather(payload: bytes) -> dict[int, pd.DataFrame]:
    """Antwort (Liste je Punkt, bei einem Punkt ein Objekt) -> K -> Zielstunde x weather_columns()."""
    data = json.loads(payload)
    if isinstance(data, dict):
        data = [data]
    if len(data) != len(core.WEATHER_POINTS):
        raise ValueError(f"Open-Meteo lieferte {len(data)} statt {len(core.WEATHER_POINTS)} Punkte")
    frames: dict[int, list[pd.DataFrame]] = {k: [] for k in core.WEATHER_LEADS}
    for (pid, _, _), entry in zip(core.WEATHER_POINTS, data):
        h = pd.DataFrame(entry["hourly"])
        h.index = pd.to_datetime(h.pop("time"), utc=True)
        for k in core.WEATHER_LEADS:
            sub = h[[f"{v}_previous_day{k}" for v in core.WEATHER_VARS]].astype(float)
            sub.columns = [f"{pid}__{v}" for v in core.WEATHER_VARS]
            frames[k].append(sub)
    return {k: pd.concat(v, axis=1)[core.weather_columns()] for k, v in frames.items()}


def fetch_weather(get: Getter, start: dt.date, end: dt.date) -> dict[int, pd.DataFrame]:
    """Wetter-Zielstunden start..end (einschließlich), in Blöcken von höchstens ~3 Monaten."""
    out: dict[int, list[pd.DataFrame]] = {k: [] for k in core.WEATHER_LEADS}
    cur = start
    while cur <= end:
        stop = min(end, cur + dt.timedelta(days=WEATHER_CHUNK_DAYS - 1))
        got = parse_weather(get(OPENMETEO_PREVIOUS_RUNS, weather_params(cur, stop)))
        for k, df in got.items():
            out[k].append(df)
        cur = stop + dt.timedelta(days=1)
    return {k: pd.concat(v).sort_index() for k, v in out.items()}


# ------------------------------ lokaler Cache ---------------------------------------------------
class DataCache:
    """Messreihen und Wetter als komprimierte CSV im Datenordner; Zuwachs wird eingemischt."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    # -- Dateien --
    def _smard_path(self) -> Path:
        return self.folder / "smard_hourly.csv.gz"

    def _weather_path(self, k: int) -> Path:
        return self.folder / f"weather_prev_day{k}.csv.gz"

    @staticmethod
    def _read(path: Path) -> Optional[pd.DataFrame]:
        if not path.exists():
            return None
        df = pd.read_csv(path, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True)
        return df

    @staticmethod
    def _write(path: Path, df: pd.DataFrame) -> None:
        tmp = path.with_suffix(".tmp")
        df.to_csv(tmp, compression={"method": "gzip", "mtime": 0})
        tmp.replace(path)

    def load_smard(self) -> Optional[pd.DataFrame]:
        return self._read(self._smard_path())

    def load_weather(self) -> Optional[dict[int, pd.DataFrame]]:
        frames = {k: self._read(self._weather_path(k)) for k in core.WEATHER_LEADS}
        if any(v is None for v in frames.values()):
            return None
        return frames  # type: ignore[return-value]

    # -- Zuwachs --
    def update_smard(self, get: Getter, now: Optional[pd.Timestamp] = None) -> pd.DataFrame:
        """Holt fehlende bzw. noch nicht endgültige Chunks (die letzten zwei) und mischt sie ein."""
        old = self.load_smard()
        since = (
            pd.Timestamp(SMARD_START, tz="UTC")
            if old is None or old.empty
            else old.index.max() - pd.Timedelta(days=14)
        )
        new = fetch_smard(get, since)
        merged = new if old is None else new.combine_first(old)
        merged = merged.sort_index()
        merged = merged[merged.index >= pd.Timestamp(SMARD_START, tz="UTC")]
        merged = merged.dropna(how="all")
        self._write(self._smard_path(), merged)
        return merged

    def update_weather(
        self, get: Getter, today: dt.date, sleep: Callable[[float], None] = time.sleep
    ) -> dict[int, pd.DataFrame]:
        """Erstbefüllung ab 2024-03-01 oder Zuwachs ab (letzter Stand - 10 Tage) bis heute + 6 Tage.

        Blockweise mit Pause zwischen den Abrufen (Open-Meteo: 600 gewichtete Calls/min; ein 92-Tage-Block kostet ~158),
        und nach jedem Block gespeichert: bei Abbruch (z. B. HTTP 429) setzt der nächste Lauf dort fort.
        """
        old = self.load_weather()
        end = today + dt.timedelta(days=WEATHER_FORWARD_DAYS)
        if old is None:
            start = WEATHER_START
        else:
            last = max(df.dropna(how="all").index.max() for df in old.values())
            # Alles ab (heute - 10 Tage) wird neu geholt: Zukunftszeilen aus früheren Abrufen werden endgültig.
            start = min(last.date(), today) - dt.timedelta(days=WEATHER_REFETCH_DAYS)
        cur = start
        out = old
        while cur <= end:
            stop = min(end, cur + dt.timedelta(days=WEATHER_CHUNK_DAYS - 1))
            got = parse_weather(get(OPENMETEO_PREVIOUS_RUNS, weather_params(cur, stop)))
            merged = {}
            for k in core.WEATHER_LEADS:
                df = got[k]
                if out is not None:
                    df = pd.concat([out[k][out[k].index < df.index.min()], df])
                merged[k] = df.sort_index()
                self._write(self._weather_path(k), merged[k])
            out = merged
            n_days = (stop - cur).days + 1
            cur = stop + dt.timedelta(days=1)
            if cur <= end:
                sleep(WEATHER_WEIGHT_PER_DAY * n_days * 60.0 / WEATHER_WEIGHT_PER_MINUTE)
        if out is None:
            raise RuntimeError("No weather data requested")
        return out


def to_inputs(
    smard: pd.DataFrame,
    weather: dict[int, pd.DataFrame],
    price_override_ct: Optional[pd.Series] = None,
) -> core.Inputs:
    """Cache -> Modelleingaben. Preis in ct/kWh (EUR/MWh / 10); ``price_override_ct`` überschreibt Stunden (bekannte Preise)."""
    price = (smard["price_eur_mwh"] / 10.0).dropna()
    if price_override_ct is not None and len(price_override_ct):
        price = price_override_ct.combine_first(price).sort_index()
    meas = smard[list(core.STAGE_A_TARGETS)]
    return core.Inputs(
        price=price,
        meas=meas,
        weather={k: weather[k][core.weather_columns()] for k in core.WEATHER_LEADS},
    )
