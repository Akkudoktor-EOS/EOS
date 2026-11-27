"""Hourly ct/kWh correction. Apply only to the forecast portion D+2 through D+5."""

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

ZONE = "Europe/Berlin"
DAMP = {2: 0.8, 3: 0.6, 4: 0.5, 5: 0.4}
COMPONENTS = ["point", "rl_gw", "load_gw", "wind_on_gw", "wind_off_gw", "pv_gw"]
RAMPS = [
    f"{key}_{direction}{step}"
    for key in ["rhat_gw", "renewable_gw", "pv_gw", "wind_on_gw", "point"]
    for step in (1, 3)
    for direction in ("lag", "lead")
] + ["price_curve_ramp_up", "price_curve_ramp_down"]
PROFILE = [
    "d1_error_shape",
    "d1_price_shape",
    "known_shape_adjacent",
    "is_weekend_diff",
    "shape_regime",
]
CHANGES = [
    "point_revision",
    "rhat_gw_revision",
    "load_gw_revision",
    "wind_on_gw_revision",
    "wind_off_gw_revision",
    "pv_gw_revision",
    "anchor_revision",
    "curve_revision",
    "point_revision_daily_mean",
    "rhat_gw_revision_daily_mean",
]
FEATURES = {"half": RAMPS + PROFILE, "revision": RAMPS + PROFILE + CHANGES}


def hours(day: Any) -> Any:
    left = pd.Timestamp(day, tz=ZONE)
    return pd.date_range(left, left + pd.DateOffset(days=1), inclusive="left", freq="h").tz_convert(
        "UTC"
    )


def feature_frame(frame: Any, d1: Any, previous: Any, issue_date: Any) -> Any:
    day = pd.Timestamp(issue_date).date()
    tomorrow = day + dt.timedelta(days=1)
    expected = hours(tomorrow)
    targets = hours(day + dt.timedelta(days=2)).append(
        [hours(day + dt.timedelta(days=h)) for h in (3, 4, 5)]
    )
    if not frame.index.equals(targets) or not d1.index.equals(expected):
        return None
    if not np.isfinite(d1[["actual", "curve"]].to_numpy()).all():
        return None
    f = frame.copy()
    f["rhat_gw"] = f.rl_gw
    f["renewable_gw"] = f.wind_on_gw + f.wind_off_gw + f.pv_gw
    local = targets.tz_convert(ZONE)
    if not np.array_equal(f.horizon, [(v - day).days for v in local.date]):
        return None
    h = local.hour
    err = d1.actual - d1.curve
    prof = (
        pd.DataFrame({"err": err - err.mean(), "price": d1.actual - d1.actual.mean()})
        .groupby(expected.tz_convert(ZONE).hour)
        .mean()
    )

    def profile(col: Any, hour: Any) -> Any:
        return prof[col].reindex(hour).to_numpy()

    f["d1_error_shape"] = profile("err", h)
    f["d1_price_shape"] = profile("price", h)
    f["known_shape_adjacent"] = profile("err", (h + 1) % 24) - profile("err", (h - 1) % 24)
    f["is_weekend_diff"] = (local.weekday >= 5) != (tomorrow.weekday() >= 5)
    f["shape_regime"] = f.d1_error_shape * f.is_weekend_diff
    for key in ["rhat_gw", "renewable_gw", "pv_gw", "wind_on_gw", "point"]:
        for step in (1, 3):
            f[f"{key}_lag{step}"] = f[key].diff(step).fillna(0) / step
            f[f"{key}_lead{step}"] = (f[key].shift(-step) - f[key]).fillna(0) / step
    f["price_curve_ramp_up"] = f.rhat_gw_lead1 * f.curve_up10
    f["price_curve_ramp_down"] = f.rhat_gw_lag1 * f.curve_down10
    for col in CHANGES:
        f[col] = np.nan
    if previous is not None and previous.index.is_unique:
        prev = previous.reindex(targets)
        match = prev.horizon.eq(f.horizon + 1)
        for col in COMPONENTS:
            f[("rhat_gw" if col == "rl_gw" else col) + "_revision"] = (f[col] - prev[col]).where(
                match
            )
        f["anchor_revision"] = (
            f.horizon.map(DAMP) * f.anchor - prev.horizon.map(DAMP) * prev.anchor
        ).where(match)
        f["curve_revision"] = f.point_revision - f.anchor_revision
        for key in ("point_revision", "rhat_gw_revision"):
            f[key + "_daily_mean"] = f[key].groupby(local.date).transform("mean")
    return f


def apply_revision(frame: Any, d1: Any, previous: Any, weights: Any, issue_date: Any) -> Any:
    day = pd.Timestamp(issue_date).date()
    monday = day - dt.timedelta(days=day.weekday())
    output = frame.point.copy()
    if weights.get("week") != monday.isoformat():
        return output
    f = feature_frame(frame, d1, previous, day)
    if f is None:
        return output
    for h in DAMP:
        for name, cols in FEATURES.items():
            fit = weights["models"].get(str(h), {}).get(name)
            if fit is None:
                continue
            x = f.loc[f.horizon.eq(h), cols].astype(float)
            x = x.loc[np.isfinite(x).all(axis=1)]
            output.loc[x.index] = frame.point.loc[x.index] + 0.5 * (
                float(fit["intercept"]) + x.to_numpy() @ np.array(fit["coef"])
            )
    return output
