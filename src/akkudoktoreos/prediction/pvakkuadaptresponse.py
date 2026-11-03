"""Regularized angular response and issued-weather calibration with causal guards."""

from typing import Any, cast

import numpy as np
import pandas as pd

LEADS = ("2-6h", "6-24h", "24-48h")
METHODS = ("calibrated", "solar", "weather")


def _sum_powers(values: list[Any]) -> float:
    if any(value is None for value in values):
        return float("nan")
    return float(np.sum(values))


def lead_bucket(minutes: float) -> str | None:
    """Separate intraday, next-day and second-day weather skill."""
    if 120 <= minutes < 360:
        return LEADS[0]
    if 360 <= minutes < 1440:
        return LEADS[1]
    if 1440 <= minutes <= 2880:
        return LEADS[2]
    return None


def solar_basis(index: pd.DatetimeIndex, latitude: float, longitude: float) -> np.ndarray:
    """Smooth local angular basis at interval centers, independent of civil time."""
    import pvlib

    solar = pvlib.solarposition.get_solarposition(
        index + pd.Timedelta(minutes=7.5), latitude, longitude
    )
    azimuth = solar.azimuth.to_numpy()
    elevation = solar.apparent_elevation.to_numpy()
    az = np.exp(-0.5 * ((azimuth[:, None] - np.arange(60, 301, 30)) / 22) ** 2)
    el = np.exp(-0.5 * ((elevation[:, None] - np.arange(0, 81, 20)) / 14) ** 2)
    basis = (az[:, :, None] * el[:, None, :]).reshape(len(index), 45)
    return basis / np.maximum(basis.sum(axis=1, keepdims=True), 1e-9)


def fit_solar_response(
    history: pd.DataFrame,
    cutoff: pd.Timestamp,
    timezone: str,
    latitude: float,
    longitude: float,
    minimum_days: int,
) -> dict[str, Any]:
    """Learn differential array response after removing common reference weather.

    This estimates repeatable angular residuals, not a measured geometric horizon.
    Training requires eligible observations and a separate independent reference.
    Unseen angular regions retain the prior response of one.
    """
    result: dict[str, Any] = dict(coefficients=[0.0] * 45, days=0, samples=0, fitted=False)
    if history.empty:
        return result
    today = cutoff.tz_convert(timezone).normalize()
    valid = (
        (history.index < today)
        & (history.available_at <= cutoff)
        & np.isfinite(history[["base", "actual", "common"]]).all(axis=1)
        & (history.base > 80)
        & (history.actual >= 0)
        & history.common.between(0.35, 2.5)
    )
    pairs = history.loc[valid].copy()
    days = pd.DatetimeIndex(pairs.index).tz_convert(timezone).normalize().nunique()
    result.update(days=int(days), samples=len(pairs))
    if days < minimum_days or len(pairs) < 48:
        return result
    basis = solar_basis(pd.DatetimeIndex(pairs.index), latitude, longitude)
    target = (pairs.actual / pairs.base / pairs.common).clip(0.35, 1.8).to_numpy() - 1
    age = (cutoff - pd.DatetimeIndex(pairs.index)).total_seconds().to_numpy() / 86400
    weights = np.exp2(-age / 14) * np.minimum(1, pairs.base.to_numpy() / 800)
    # Downweight outliers; strong identity prior shrinks sparsely covered knots.
    weights *= np.minimum(1, 0.35 / np.maximum(np.abs(target), 1e-9))
    matrix = basis.T @ (weights[:, None] * basis) + np.eye(45) * 8
    coefficients = np.linalg.solve(matrix, basis.T @ (weights * target))
    result.update(coefficients=np.clip(coefficients, -0.5, 0.5).tolist(), fitted=True)
    return result


def solar_factors(basis: np.ndarray, model: dict[str, Any]) -> np.ndarray:
    """Bound a fitted empirical response; cold start is exactly identity."""
    coefficients = np.asarray(model.get("coefficients", [0.0] * 45), dtype=float)
    if coefficients.shape != (45,) or not np.isfinite(coefficients).all():
        raise ValueError("Invalid adaptive solar response")
    return np.clip(1 + basis @ coefficients, 0.5, 1.5)


def regime(clearness: float) -> str:
    """Use only forecast irradiance to identify a weather regime."""
    return "cloudy" if clearness < 0.3 else "mixed" if clearness < 0.7 else "clear"


def paired_rows(
    rows: list[dict[str, Any]],
    observations: pd.DataFrame,
    keys: list[str],
    cutoff: pd.Timestamp,
    timezone: str,
) -> list[dict[str, Any]]:
    """Join frozen inputs to causally available, positively eligible observations.

    One target gets one vote per horizon, independent of the polling frequency.
    All candidates and member models are compared on identical support.
    """
    today = cutoff.tz_convert(timezone).normalize()
    paired: dict[tuple[int, str], dict[str, Any]] = {}
    centers = {"2-6h": 240, "6-24h": 900, "24-48h": 2160}
    for row in rows:
        target = pd.Timestamp(row["target_ms"], unit="ms", tz="UTC")
        issued = pd.Timestamp(row["issued_ms"], unit="ms", tz="UTC")
        lead = (target - issued).total_seconds() / 60
        bucket = lead_bucket(lead)
        if (
            bucket is None
            or target >= today
            or target not in observations.index
            or row.get("response_version") != 1
        ):
            continue
        truth = cast("pd.Series[Any]", observations.loc[target])
        if not all(
            pd.notna(truth.get(key))
            and pd.notna(truth.get(key + "_eligible"))
            and bool(truth.get(key + "_eligible", False))
            and pd.notna(truth.get(key + "_available"))
            and truth[key + "_available"] <= cutoff
            for key in keys
        ):
            continue
        planes = row.get("planes", {})
        if not all(key in planes for key in keys):
            continue
        values = {
            method: _sum_powers([planes[key].get(method, np.nan) for key in keys])
            for method in METHODS
        }
        actual = sum(float(truth[key]) for key in keys)
        if not np.isfinite(list(values.values())).all() or max(actual, values["calibrated"]) < 50:
            continue
        members = {
            name: _sum_powers([member.get(key, np.nan) for key in keys])
            for name, member in row.get("members", {}).items()
        }
        sky = _sum_powers([planes[key].get("sky", np.nan) for key in keys])
        record = dict(
            target_ms=row["target_ms"],
            bucket=bucket,
            actual=actual,
            day=str(target.tz_convert(timezone).date()),
            members=members,
            distance=abs(lead - centers[bucket]),
            sky=sky,
            **values,
        )
        identity = (row["target_ms"], bucket)
        if identity not in paired or record["distance"] < paired[identity]["distance"]:
            paired[identity] = record
    return list(paired.values())


def fit_weather_response(
    pairs: list[dict[str, Any]],
    minimum_days: int,
    minimum_samples: int,
) -> dict[str, Any]:
    """Rank actually issued model members and shrink regime bias toward identity."""
    result = {}
    for bucket in LEADS:
        part = [row for row in pairs if row["bucket"] == bucket]
        names = set.intersection(*(set(row["members"]) for row in part)) if part else set()
        part = [
            row
            for row in part
            if np.isfinite([row["members"][name] for name in names]).all()
            and np.isfinite(row["sky"])
            and row["sky"] > 50
        ]
        days = len({row["day"] for row in part})
        value: dict[str, Any] = dict(
            days=days, samples=len(part), weights={}, regimes={}, fitted=False
        )
        if names and days >= minimum_days and len(part) >= minimum_samples:
            errors = {}
            for name in sorted(names):
                frame = pd.DataFrame(
                    dict(day=row["day"], error=abs(row["members"][name] - row["actual"]))
                    for row in part
                )
                errors[name] = float(frame.groupby("day").error.mean().mean())
            scores = {name: 1 / max(error, 20) ** 2 for name, error in errors.items()}
            weights = {
                name: 0.5 / len(names) + 0.5 * score / sum(scores.values())
                for name, score in scores.items()
            }
            for row in part:
                prediction = sum(weights[name] * row["members"][name] for name in names)
                row["weather_prediction"] = prediction
                row["regime"] = regime(prediction / row["sky"])
            factors = {}
            for name in ("cloudy", "mixed", "clear"):
                subset = [row for row in part if row["regime"] == name]
                if len({row["day"] for row in subset}) >= minimum_days:
                    predicted = sum(row["weather_prediction"] for row in subset)
                    measured = sum(row["actual"] for row in subset)
                    factors[name] = float(np.clip((measured + 8000) / (predicted + 8000), 0.5, 1.5))
            value.update(weights=weights, regimes=factors, member_mae_w=errors, fitted=True)
        result[bucket] = value
    return result


def select_responses(
    pairs: list[dict[str, Any]],
    minimum_days: int,
    minimum_samples: int,
    improvement: float,
    enabled_methods: tuple[str, ...] = METHODS,
) -> dict[str, Any]:
    """Activate only better frozen candidates, with day weighting and energy guard."""
    result = {}
    for bucket in LEADS:
        part = pd.DataFrame(row for row in pairs if row["bucket"] == bucket)
        value: dict[str, Any] = dict(
            method="calibrated",
            days=0,
            samples=len(part),
            validated=False,
            mae_w={},
            daily_energy_error_kwh={},
        )
        if not part.empty:
            days = int(part.day.nunique())
            errors = pd.DataFrame({method: part[method] - part.actual for method in METHODS})
            daily_mae = errors.abs().groupby(part.day).mean()
            mae = daily_mae.mean()
            daily_energy = (errors.groupby(part.day).sum() * 0.25 / 1000).abs().mean()
            enough = days >= minimum_days and len(part) >= minimum_samples
            best = "calibrated"
            for method in METHODS[1:]:
                if (
                    method in enabled_methods
                    and enough
                    and mae[method] < mae[best] * (1 - improvement)
                    and daily_energy[method] <= daily_energy[best] * 1.02
                    and (daily_mae[method] < daily_mae[best]).mean() >= 0.6
                ):
                    best = method
            value.update(
                method=best,
                days=days,
                validated=enough,
                mae_w=mae.to_dict(),
                daily_energy_error_kwh=daily_energy.to_dict(),
            )
        result[bucket] = value
    return result
