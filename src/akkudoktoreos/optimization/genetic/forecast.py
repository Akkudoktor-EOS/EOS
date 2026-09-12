"""Resample actual forecast intervals without extrapolating missing provider data."""

from typing import Any

import numpy as np
import pandas as pd


def bounded_forecast_array(
    prediction: Any,
    *,
    key: str,
    start_datetime: Any,
    end_datetime: Any,
    interval: Any,
    **kwargs: Any,
) -> np.ndarray:
    """Hold interval averages only within their source interval.

    EOS forecasts carry interval starts. Infer source cadence from timestamps,
    conservatively bounded to one hour; never extend the last value indefinitely.
    Explicit NaNs and holes remain missing. Downsampling requires full coverage.
    """
    target_seconds = int(interval.total_seconds())
    try:
        series = prediction.key_to_series(key, dropna=False)
    except KeyError:
        series = pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    series = pd.to_numeric(series, errors="coerce").sort_index()
    series = series[~series.index.duplicated(keep="last")]
    cadence = 3600
    if len(series) > 1:
        gaps = np.diff(series.index.as_unit("ns").asi8) / 1e9
        cadence = int(min(3600, np.min(gaps[gaps > 0])))
    step = min(cadence, target_seconds)
    index = pd.date_range(start=start_datetime, end=end_datetime, freq=f"{step}s", inclusive="left")
    if series.empty:
        sampled = pd.Series(np.nan, index=index)
    else:
        sampled = series.reindex(index, method="ffill", tolerance=pd.Timedelta(seconds=cadence - 1))
    groups = sampled.resample(f"{target_seconds}s", origin=start_datetime)
    result = groups.mean()
    result[groups.count().to_numpy() < target_seconds / step] = np.nan
    return result.to_numpy(dtype=float)
