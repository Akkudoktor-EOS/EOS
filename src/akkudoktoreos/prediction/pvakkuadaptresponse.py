"""Regularized angular response and issued-weather calibration with causal guards."""

from typing import Any, cast

import numpy as np
import pandas as pd

LEADS = ("2-6h", "6-24h", "24-48h")
METHODS = ("calibrated", "solar", "weather")
RESPONSE_VERSION = 2


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
    distance = (azimuth[:, None] - np.arange(0, 360, 40) + 180) % 360 - 180
    az = np.exp(-0.5 * (distance / 30) ** 2)
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
        & (history.base > 0)
        & (history.actual >= 0)
        & history.common.between(0.35, 2.5)
    )
    pairs = history.loc[valid].copy()
    if pairs.empty:
        return result
    scale = float(pairs.base.quantile(0.9))
    pairs = pairs[pairs.base > scale * 0.05]
    days = pd.DatetimeIndex(pairs.index).tz_convert(timezone).normalize().nunique()
    result.update(days=int(days), samples=len(pairs))
    if days < minimum_days or len(pairs) < 48:
        return result
    basis = solar_basis(pd.DatetimeIndex(pairs.index), latitude, longitude)
    target = (pairs.actual / pairs.base / pairs.common).clip(0.35, 1.8).to_numpy() - 1
    age = (cutoff - pd.DatetimeIndex(pairs.index)).total_seconds().to_numpy() / 86400
    weights = np.exp2(-age / 14) * np.minimum(1, pairs.base.to_numpy() / scale)
    # Downweight outliers; strong identity prior shrinks sparsely covered knots.
    weights *= np.minimum(1, 0.35 / np.maximum(np.abs(target), 1e-9))
    matrix = basis.T @ (weights[:, None] * basis) + np.eye(45) * 8
    coefficients = np.linalg.solve(matrix, basis.T @ (weights * target))
    support = (
        pd.DataFrame(
            basis > 0.1, index=pd.DatetimeIndex(pairs.index).tz_convert(timezone).normalize()
        )
        .groupby(level=0)
        .max()
        .sum()
        .to_numpy()
    )
    coefficients[support < minimum_days] = 0
    result.update(
        coefficients=np.clip(coefficients, -0.5, 0.5).tolist(),
        support_days=support.tolist(),
        fitted=True,
    )
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
    response_version: int | None = None,
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
            or row.get("response_version") not in (1, RESPONSE_VERSION)
            or (response_version is not None and row.get("response_version") != response_version)
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
        if not np.isfinite(list(values.values())).all() or actual < 0:
            continue
        members = {
            name: _sum_powers([member.get(key, np.nan) for key in keys])
            for name, member in row.get("members", {}).items()
        }
        sky = _sum_powers([planes[key].get("sky", np.nan) for key in keys])
        if not np.isfinite(sky) or sky <= 0 or max(actual, values["calibrated"]) < sky * 0.02:
            continue
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


def _member_groups(part: list[dict[str, Any]], names: list[str]) -> list[list[str]]:
    """Group numerically equivalent forecasts without provider-specific names.

    Complete-link grouping avoids merging a chain of increasingly different
    members. The tolerance is relative to the same clear-sky power per sample.
    """
    sky = np.array([row["sky"] for row in part])
    curves = {name: np.array([row["members"][name] for row in part]) / sky for name in names}
    groups: list[list[str]] = []
    for name in names:
        for group in groups:
            if all(np.max(np.abs(curves[name] - curves[other])) <= 0.001 for other in group):
                group.append(name)
                break
        else:
            groups.append([name])
    return groups


def fit_weather_response(
    pairs: list[dict[str, Any]],
    minimum_days: int,
    minimum_samples: int,
) -> dict[str, Any]:
    """Fit scale-free, day-weighted weather families and regularized regime bias.

    Inputs must already be paired with causally available independent targets.
    The identity prior is expressed in days, not plant-specific power units.
    """
    result = {}
    for bucket in LEADS:
        part = [row for row in pairs if row["bucket"] == bucket]
        names = set.intersection(*(set(row["members"]) for row in part)) if part else set()
        part = [
            row
            for row in part
            if np.isfinite([row["members"][name] for name in names]).all()
            and np.isfinite(row["sky"])
            and row["sky"] > 0
            and np.isfinite(row["actual"])
            and row["actual"] >= 0
            and all(row["members"][name] >= 0 for name in names)
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
            groups = _member_groups(part, sorted(names))
            normalized_errors = []
            for group in groups:
                frame = pd.DataFrame(
                    dict(
                        day=row["day"],
                        error=abs(np.mean([row["members"][name] for name in group]) - row["actual"])
                        / row["sky"],
                    )
                    for row in part
                )
                normalized_errors.append(float(frame.groupby("day").error.mean().mean()))
            scores = np.array([1 / max(error, 0.01) ** 2 for error in normalized_errors])
            prior = minimum_days / (days + minimum_days)
            family_weights = prior / len(groups) + (1 - prior) * scores / scores.sum()
            weights = {
                name: float(weight / len(group))
                for group, weight in zip(groups, family_weights)
                for name in group
            }
            predictions = []
            for row in part:
                prediction = sum(weights[name] * row["members"][name] for name in names)
                predictions.append(
                    dict(
                        day=row["day"],
                        regime=regime(prediction / row["sky"]),
                        predicted=prediction / row["sky"],
                        measured=row["actual"] / row["sky"],
                    )
                )
            factors = {}
            regime_days = {}
            for name in ("cloudy", "mixed", "clear"):
                subset = pd.DataFrame(row for row in predictions if row["regime"] == name)
                if subset.empty:
                    continue
                daily = subset.groupby("day")[["predicted", "measured"]].sum()
                daily = daily[daily.predicted > 0]
                regime_days[name] = len(daily)
                if len(daily) >= minimum_days:
                    ratios = (daily.measured / daily.predicted).clip(0.5, 1.5)
                    factors[name] = float(
                        (ratios.sum() + minimum_days) / (len(daily) + minimum_days)
                    )
            value.update(
                weights=weights,
                regimes=factors,
                member_mae_w=errors,
                fitted=True,
                member_groups=groups,
                regime_days=regime_days,
                prior_days=minimum_days,
            )
        result[bucket] = value
    return result


def select_responses(
    pairs: list[dict[str, Any]],
    minimum_days: int,
    minimum_samples: int,
    improvement: float,
    enabled_methods: tuple[str, ...] = METHODS,
) -> dict[str, Any]:
    """Require positive day-block evidence, energy protection and recent stability.

    The conservative two-standard-error margin is an evidence guard, not a
    calibrated confidence interval for correlated meteorological days.
    """
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
            evidence = {}
            for method in METHODS[1:]:
                delta = daily_mae[best] - daily_mae[method]
                margin = float(delta.mean() - 2 * delta.std(ddof=1) / np.sqrt(len(delta)))
                recent = float(delta.sort_index().tail(minimum_days).mean())
                evidence[method] = dict(
                    lower_margin_w=margin if np.isfinite(margin) else None,
                    recent_improvement_w=recent,
                )
                if (
                    method in enabled_methods
                    and enough
                    and mae[method] < mae[best] * (1 - improvement)
                    and daily_energy[method] <= daily_energy[best] * 1.02
                    and (daily_mae[method] < daily_mae[best]).mean() >= 0.6
                    and margin > improvement * mae[best]
                    and recent > 0
                ):
                    best = method
            value.update(
                method=best,
                days=days,
                validated=enough,
                mae_w=mae.to_dict(),
                daily_energy_error_kwh=daily_energy.to_dict(),
                evidence=evidence,
            )
        result[bucket] = value
    return result
