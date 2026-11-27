"""Versioned, causal calibration of raw U3 q10/q90; standard library only.

No file/network access, model fitting or EOS imports. The owner persists the raw
archive atomically. known_prices must be actual exchange prices in ct/kWh, never
the EOS series containing predictions. Returned quantiles are a new 5xH list.
"""

from __future__ import annotations

import datetime as dt
import math
from numbers import Real
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
UTC = dt.timezone.utc
LEVELS = ("0.1", "0.25", "0.5", "0.75", "0.9")
CALIBRATED_HORIZONS = (4, 5)
MIN_DAYS = 40
WIDTH_FLOOR_CT = 2.0
SCHEMA_VERSION = 1


def _number(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError("non-finite calibration value")
    return float(value)


def _day(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        raise ValueError("model day must be a date")
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(value)


def _time(value: Any) -> Any:
    result = (
        value
        if isinstance(value, dt.datetime)
        else dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if result.tzinfo is None:
        raise ValueError("calibration hours require a timezone")
    return result.astimezone(UTC)


def _hours(day: Any) -> Any:
    start = dt.datetime.combine(day + dt.timedelta(days=2), dt.time(), TZ).astimezone(UTC)
    end = dt.datetime.combine(day + dt.timedelta(days=6), dt.time(), TZ).astimezone(UTC)
    return [
        start + dt.timedelta(hours=i) for i in range(int((end - start).total_seconds() // 3600))
    ]


def _validate_hours(hours: Any, day: Any) -> Any:
    parsed = [_time(t) for t in hours]
    if parsed != _hours(day):
        raise ValueError("calibration horizon is not the complete D+2..D+5 UTC series")
    return parsed


def _quantile(values: Any, probability: Any, upper: Any) -> Any:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty calibration sample")
    rank = (
        math.ceil((len(ordered) + 1) * probability)
        if upper
        else math.floor((len(ordered) + 1) * probability)
    )
    return ordered[max(0, min(len(ordered) - 1, rank - 1))]


def new_archive(basis_version: Any) -> Any:
    if not isinstance(basis_version, str) or not basis_version:
        raise ValueError("basis version is required")
    return {"schema_version": SCHEMA_VERSION, "basis_version": basis_version, "entries": {}}


def archive_raw(
    archive: Any,
    raw_q: Any,
    forecast_hours: Any,
    model_day: Any,
    *,
    basis_version: Any,
    model_month: Any,
    issued_at: Any,
    origin: Any = "live",
) -> Any:
    """Store the first complete raw output for a day; never replace it.

    Returns True for a new entry, False when that day's first output is retained.
    The caller supplies raw, never calibrated, quantiles. Only memory is mutated.
    """
    _check_archive(archive, basis_version)
    day = _day(model_day)
    hours = _validate_hours(forecast_hours, day)
    q = _validate_raw(raw_q, len(hours))
    if origin not in ("live", "replay"):
        raise ValueError("unknown archive origin")
    if day.isoformat() in archive["entries"]:
        return False
    archive["entries"][day.isoformat()] = {
        "model_day": day.isoformat(),
        "model_month": model_month,
        "issued_at": _time(issued_at).isoformat(),
        "origin": origin,
        "hours_utc": [t.isoformat() for t in hours],
        "raw_quantiles": {key: q[i] for i, key in enumerate(LEVELS)},
    }
    return True


def _check_archive(archive: Any, basis_version: Any) -> Any:
    if (
        not isinstance(archive, dict)
        or archive.get("schema_version") != SCHEMA_VERSION
        or archive.get("basis_version") != basis_version
        or not isinstance(archive.get("entries"), dict)
    ):
        raise ValueError("calibration archive schema or model basis differs")


def _validate_raw(raw_q: Any, count: Any) -> Any:
    if len(raw_q) != 5 or any(len(row) != count for row in raw_q):
        raise ValueError("raw quantiles must have shape 5 x hours")
    q = [[_number(v) for v in row] for row in raw_q]
    if any(any(q[j][i] > q[j + 1][i] for j in range(4)) for i in range(count)):
        raise ValueError("raw quantiles are not ordered")
    return q


def observed_entries(
    archive: Any,
    known_prices: Any,
    *,
    basis_version: Any,
    model_day: Any = None,
    window_days: Any = None,
) -> Any:
    """Validate fully observed raw issues once, useful for a rolling backtest.

    Actual prices are external to the immutable raw forecast archive. Entries
    missing any actual hour are excluded wholesale, never pooled partially.
    """
    _check_archive(archive, basis_version)
    prices = {_time(t): v for t, v in known_prices.items()}
    records: dict[Any, Any] = {}
    for key, entry in archive["entries"].items():
        day = _day(key)
        if model_day is not None:
            current = _day(model_day)
            if day > current - dt.timedelta(days=6) or (
                window_days is not None and day < current - dt.timedelta(days=window_days)
            ):
                continue
        if entry.get("model_day") != key:
            raise ValueError("archive entry day differs from its key")
        hours = _validate_hours(entry["hours_utc"], day)
        if any(
            t not in prices
            or isinstance(prices[t], bool)
            or not isinstance(prices[t], Real)
            or not math.isfinite(float(prices[t]))
            for t in hours
        ):
            continue
        q = entry["raw_quantiles"]
        keys = ("0.1", "0.25", "0.75", "0.9")
        if any(len(q[k]) != len(hours) for k in keys):
            raise ValueError("incomplete historical raw quantiles")
        records[day] = {n: [] for n in CALIBRATED_HORIZONS}
        for i, t in enumerate(hours):
            vals = [_number(q[k][i]) for k in keys]
            if vals != sorted(vals):
                raise ValueError("historical quantiles are not ordered")
            n = (t.astimezone(TZ).date() - day).days
            if n in CALIBRATED_HORIZONS:
                y = _number(prices[t])
                scale = max(vals[3] - vals[0], WIDTH_FLOOR_CT)
                records[day][n].append((y - vals[0], y - vals[3], scale))
    return records


def fit_parameters(
    records: Any, model_day: Any, *, window_days: Any, mode: Any, outward_only: Any = False
) -> Any:
    """Only complete issues in [D-window, D-6] enter the empirical correction."""
    if window_days not in (60, 120) or mode not in ("additive", "normalized"):
        raise ValueError("unregistered calibration policy")
    day = _day(model_day)
    eligible = sorted(
        d
        for d in records
        if day - dt.timedelta(days=window_days) <= d <= day - dt.timedelta(days=6)
    )
    diagnostics = {
        "method": mode,
        "window_days": window_days,
        "outward_only": outward_only,
        "sample_days": len(eligible),
        "first_issue": eligible[0].isoformat() if eligible else None,
        "last_issue": eligible[-1].isoformat() if eligible else None,
        "status": "ready" if len(eligible) >= MIN_DAYS else "insufficient_history",
    }
    if len(eligible) < MIN_DAYS:
        return None, diagnostics
    parameters = {}
    for n in CALIBRATED_HORIZONS:
        rows = [row for d in eligible for row in records[d][n]]
        lower = [a / s if mode == "normalized" else a for a, b, s in rows]
        upper = [b / s if mode == "normalized" else b for a, b, s in rows]
        low, high = _quantile(lower, 0.1, False), _quantile(upper, 0.9, True)
        parameters[n] = (min(low, 0.0), max(high, 0.0)) if outward_only else (low, high)
    diagnostics["corrections"] = {
        str(n): {"lower": a, "upper": b} for n, (a, b) in parameters.items()
    }
    return parameters, diagnostics


def apply_parameters(
    raw_q: Any, forecast_hours: Any, model_day: Any, parameters: Any, *, mode: Any
) -> Any:
    day = _day(model_day)
    hours = _validate_hours(forecast_hours, day)
    result = _validate_raw(raw_q, len(hours))
    if parameters is None:
        return result
    for i, t in enumerate(hours):
        n = (t.astimezone(TZ).date() - day).days
        if n not in CALIBRATED_HORIZONS:
            continue
        result[0][i], result[4][i] = adjust_bounds(
            result[0][i], result[4][i], result[1][i], result[3][i], parameters[n], mode=mode
        )
    return result


def adjust_bounds(
    lower: Any, upper: Any, inner_lower: Any, inner_upper: Any, parameters: Any, *, mode: Any
) -> Any:
    """Shared one-hour operation, also used by the offline evaluation."""
    lo, hi = parameters
    scale = max(upper - lower, WIDTH_FLOOR_CT) if mode == "normalized" else 1.0
    return (
        min(inner_lower, _number(lower + lo * scale)),
        max(inner_upper, _number(upper + hi * scale)),
    )


def apply(
    raw_q: Any,
    forecast_hours: Any,
    model_day: Any,
    archive: Any,
    known_prices: Any,
    *,
    basis_version: Any,
    window_days: Any,
    mode: Any,
    outward_only: Any = False,
) -> Any:
    """Engine adapter: raw 5xH quantiles -> new quantiles and public diagnostics.

    No defaults for policy: only the selected, independently checked policy may
    be configured. The owner catches malformed-history exceptions and retains
    raw U3. Insufficient valid history returns raw U3 with a visible status.
    """
    records = observed_entries(
        archive,
        known_prices,
        basis_version=basis_version,
        model_day=model_day,
        window_days=window_days,
    )
    parameters, diagnostics = fit_parameters(
        records, model_day, window_days=window_days, mode=mode, outward_only=outward_only
    )
    result = apply_parameters(raw_q, forecast_hours, model_day, parameters, mode=mode)
    return result, diagnostics
