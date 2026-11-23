"""Read forecast interval averages without filling gaps or extrapolating the tail."""

from typing import Any

import numpy as np
import pandas as pd

from akkudoktoreos.utils.datetimeutil import DateTime, Duration


async def bounded_forecast_array(
    prediction: Any,
    *,
    key: str,
    start_datetime: DateTime,
    end_datetime: DateTime,
    interval: Duration,
) -> np.ndarray:
    """Integrate stored interval averages only across completely covered target slots.

    Source timestamps are interval starts. Infer their smallest positive cadence,
    capped at one hour. A missing source interval, explicit NaN or the end of the
    available forecast remains missing. UTC elapsed time handles DST transitions.
    The returned values retain the input units; callers convert W to slot Wh once.
    """
    seconds = int(interval.total_seconds())
    start = start_datetime.timestamp()
    end = end_datetime.timestamp()
    if seconds <= 0 or end <= start or (end - start) % seconds:
        raise ValueError("Forecast bounds must contain a positive whole number of slots.")
    result = np.full(int((end - start) / seconds), np.nan)
    try:
        series = await prediction.key_to_raw_series(key=key, dropna=False)
    except KeyError:
        return result
    if series.empty:
        return result
    series = pd.to_numeric(series, errors="coerce").sort_index()
    series = series[~series.index.duplicated(keep="last")]
    stamps = pd.to_datetime(series.index, utc=True).as_unit("ns").asi8 / 1e9
    values = series.to_numpy(dtype=float)
    differences = np.diff(stamps)
    cadence = min(3600.0, float(np.min(differences))) if len(differences) else 3600.0
    for slot in range(len(result)):
        left = start + slot * seconds
        right = left + seconds
        position = max(0, int(np.searchsorted(stamps, left, side="right")) - 1)
        covered = 0.0
        weighted = 0.0
        while position < len(stamps) and stamps[position] < right:
            source_end = stamps[position] + cadence
            if position + 1 < len(stamps):
                source_end = min(source_end, stamps[position + 1])
            overlap = max(0.0, min(right, source_end) - max(left, stamps[position]))
            if overlap and np.isfinite(values[position]):
                covered += overlap
                weighted += (overlap / seconds) * values[position]
            position += 1
        if abs(covered - seconds) < 1e-6:
            result[slot] = weighted
    return result
