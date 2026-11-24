"""Data retrieval and local cache for ``ElecPriceAkkudoktorML``.

Sources (both without key, generic user agent only, no location or personal data except the 12 fixed
weather points):

* SMARD (Bundesnetzagentur, CC BY 4.0) ``chart_data``: day-ahead price DE-LU (4169), grid load
  (410), onshore wind (4067), offshore wind (1225), PV (4068), each as an hourly series in weekly
  chunks.
* Open-Meteo Previous-Runs API, model ``ecmwf_ifs025``: ``<variable>_previous_day{K}`` for K = 3..6.
  The same interface delivers the training data for target times in the past and, for target times
  in the coming days, the weather run that was started K days before the target time. Thus the
  forecast used in operation matches the lead time the models were trained on.

Cache: compressed CSV files in the data folder; after the initial fill only increments are fetched.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Callable, Optional, cast

import pandas as pd

from akkudoktoreos.prediction import elecpriceakkudoktormlcore as core
from akkudoktoreos.prediction.elecpricesmard import (
    SMARD_BASE_URL,
    SmardChunk,
    SmardIndex,
)

USER_AGENT = "Akkudoktor-EOS/ElecPriceAkkudoktorML"
OPENMETEO_PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
WEATHER_MODEL = "ecmwf_ifs025"
SMARD_START = dt.date(
    2024, 1, 1
)  # start of the measurement series (the training window grows from here)
WEATHER_START = dt.date(2024, 3, 1)  # earlier days have no lead-time values for ecmwf_ifs025
SERIES = {  # column: (filter, region)
    "price_eur_mwh": (4169, "DE"),  # the DE-LU day-ahead price, same series as ElecPriceSMARD uses
    "load_mw": (410, "DE"),
    "wind_on_mw": (4067, "DE"),
    "wind_off_mw": (1225, "DE"),
    "pv_mw": (4068, "DE"),
}
WEATHER_REFETCH_DAYS = (
    10  # this many past days are overwritten on every increment (values become final)
)
WEATHER_FORWARD_DAYS = 6  # future days (target days D+2..D+5 relative to the issue day)
WEATHER_CHUNK_DAYS = 92
LEGACY_POINT_IDS = {
    "last_berlin": "load_berlin",
    "last_ruhr": "load_ruhr",
}  # ids of earlier cache files
WEATHER_WEIGHT_PER_DAY = (
    1.75  # weighted calls per day for 12 points x 20 columns (measured: 92 days = 157.7)
)
WEATHER_WEIGHT_PER_MINUTE = 300.0  # deliberately below the limit of 600/min


class RateLimited(RuntimeError):
    """Open-Meteo or SMARD reports that the rate limit was exceeded (HTTP 429); try again later."""


Getter = Callable[[str, Optional[dict]], bytes]


def http_get(url: str, params: Optional[dict] = None) -> bytes:
    """Fetch with a limited number of retries; 429 is reported as RateLimited (no hammering)."""
    import requests

    last: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            r = requests.get(
                url, params=params, headers={"User-Agent": USER_AGENT}, timeout=(5, 120)
            )
            if r.status_code == 429:
                raise RateLimited(f"HTTP 429 from {url.split('?')[0]}")
            r.raise_for_status()
            return r.content
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last = exc
            time.sleep(2 * attempt)
    raise last if last is not None else RuntimeError(f"request to {url.split('?')[0]} failed")


# ------------------------------ SMARD ---------------------------------------------------------
def fetch_smard_series(get: Getter, column: str, since: pd.Timestamp) -> pd.Series:
    """Hourly series of a SMARD series from ``since`` (weekly chunks overlapping ``since``)."""
    flt, reg = SERIES[column]
    index = SmardIndex.model_validate_json(
        get(f"{SMARD_BASE_URL}/{flt}/{reg}/index_hour.json", None)
    )
    chunks = sorted(
        t
        for t in index.timestamps
        if pd.Timestamp(t, unit="ms", tz="UTC") >= since - pd.Timedelta(days=8)
    )
    parts = []
    for t in chunks:
        chunk = SmardChunk.model_validate_json(
            get(f"{SMARD_BASE_URL}/{flt}/{reg}/{flt}_{reg}_hour_{t}.json", None)
        )
        df = pd.DataFrame(chunk.series, columns=["ts", column])
        df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
        parts.append(df[column])
    if not parts:
        return pd.Series(dtype=float, name=column)
    s = pd.concat(parts)
    return s[~s.index.duplicated(keep="last")].sort_index().rename(column).astype(float)


def fetch_smard(get: Getter, since: pd.Timestamp) -> pd.DataFrame:
    return pd.DataFrame({c: fetch_smard_series(get, c, since) for c in SERIES})


def _current_point_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Rename weather columns of cache files that were written with earlier point ids."""
    renamed = {}
    for column in df.columns:
        point, _, variable = str(column).partition("__")
        if point in LEGACY_POINT_IDS:
            renamed[column] = f"{LEGACY_POINT_IDS[point]}__{variable}"
    return df.rename(columns=renamed) if renamed else df


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
    """Open-Meteo response (one entry per point) -> K -> target hour x weather columns."""
    data = json.loads(payload)
    if isinstance(data, dict):
        data = [data]
    if len(data) != len(core.WEATHER_POINTS):
        raise ValueError(
            f"Open-Meteo returned {len(data)} points instead of {len(core.WEATHER_POINTS)}"
        )
    frames: dict[int, list[pd.DataFrame]] = {k: [] for k in core.WEATHER_LEADS}
    for (pid, _, _), entry in zip(core.WEATHER_POINTS, data):
        h = pd.DataFrame(entry["hourly"])
        h.index = pd.to_datetime(h.pop("time"), utc=True)
        for k in core.WEATHER_LEADS:
            sub = h[[f"{v}_previous_day{k}" for v in core.WEATHER_VARS]].astype(float)
            sub.columns = [f"{pid}__{v}" for v in core.WEATHER_VARS]
            frames[k].append(sub)
    return {k: pd.concat(v, axis=1)[core.weather_columns()] for k, v in frames.items()}


# ------------------------------ local cache ---------------------------------------------------
class DataCache:
    """Measurement series and weather as compressed CSV files; increments are merged in."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    # -- files --
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
        return {k: _current_point_ids(cast(pd.DataFrame, v)) for k, v in frames.items()}

    # -- increments --
    def update_smard(self, get: Getter, now: Optional[pd.Timestamp] = None) -> pd.DataFrame:
        """Fetch missing or not yet final chunks (the last two) and merge them in."""
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
        """Initial fill from 2024-03-01 or increment from (last state - 10 days) to today + 6 days.

        Block by block with a pause between the requests (Open-Meteo: 600 weighted calls/min; a
        92-day block costs ~158), and saved after every block: after an abort (e.g. HTTP 429) the
        next run continues there.
        """
        old = self.load_weather()
        end = today + dt.timedelta(days=WEATHER_FORWARD_DAYS)
        if old is None:
            start = WEATHER_START
        else:
            last = max(df.dropna(how="all").index.max() for df in old.values())
            # Everything from (today - 10 days) is fetched again: future rows from earlier requests
            # become final.
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
            raise ValueError("no weather data requested")
        return out


def to_inputs(
    smard: pd.DataFrame,
    weather: dict[int, pd.DataFrame],
    price_override_ct: Optional[pd.Series] = None,
) -> core.Inputs:
    """Cache -> model inputs (price in ct/kWh); ``price_override_ct`` replaces known hours."""
    price = (smard["price_eur_mwh"] / 10.0).dropna()
    if price_override_ct is not None and len(price_override_ct):
        price = price_override_ct.combine_first(price).sort_index()
    meas = smard[list(core.STAGE_A_TARGETS)]
    return core.Inputs(
        price=price,
        meas=meas,
        weather={k: weather[k][core.weather_columns()] for k in core.WEATHER_LEADS},
    )
