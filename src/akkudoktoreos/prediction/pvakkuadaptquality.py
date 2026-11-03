"""Causal selection of issued PV candidates on completed, independent target days."""

from typing import Any, cast

import numpy as np
import pandas as pd

METHODS = ("calibrated", "local", "trend")


def select_corrections(
    rows: list[dict[str, Any]],
    observations: pd.DataFrame,
    groups: dict[str, list[str]],
    cutoff: pd.Timestamp,
    timezone: str,
    reference_group: str | None = None,
    validation_days: int = 7,
    minimum_days: int = 3,
    minimum_samples: int = 32,
    improvement: float = 0.02,
    enabled_methods: tuple[str, ...] = METHODS,
) -> dict[str, Any]:
    """Compare frozen candidates, never coefficients refitted to their own labels.

    Each target interval gets one vote per lead, irrespective of polling rate.
    Predictions issued today, unavailable labels and censored observations do
    not validate a candidate. Ties and insufficient evidence retain calibration.
    Legacy archives can validate only their explicitly recorded reference sum.
    """
    today = cutoff.tz_convert(timezone).normalize()
    start = today - pd.DateOffset(days=validation_days)
    start_ms, end_ms = int(start.timestamp() * 1000), int(today.timestamp() * 1000)
    if (
        not isinstance(observations.index, pd.DatetimeIndex)
        or observations.index.tz is None
        or observations.index.has_duplicates
    ):
        raise ValueError("Validation requires distinct, timezone-aware observation intervals.")
    selected: dict[str, Any] = {}
    for group, keys in groups.items():
        comparisons = []
        truth = {}
        for timestamp, values_row in observations.iterrows():
            target_time = cast(pd.Timestamp, timestamp)
            if not start <= target_time < today:
                continue
            obs = cast("pd.Series[Any]", values_row)
            if all(
                pd.notna(obs.get(key))
                and pd.notna(obs.get(key + "_eligible", True))
                and bool(obs.get(key + "_eligible", True))
                and pd.notna(obs.get(key + "_available"))
                and obs[key + "_available"] <= cutoff
                for key in keys
            ):
                truth[int(target_time.timestamp() * 1000)] = (
                    sum(float(obs[key]) for key in keys),
                    str(target_time.tz_convert(timezone).date()),
                )
        for row in rows:
            target_ms, issued_ms = row["target_ms"], row["issue_ms"]
            lead = float(row["lead_minutes"])
            if (
                not start_ms <= target_ms < end_ms
                or not issued_ms < target_ms
                or not 0 <= lead < 120
            ):
                continue
            if target_ms not in truth:
                continue
            candidate = row.get("groups", {}).get(group)
            if candidate is None:
                if group != reference_group:
                    continue
                candidate = {
                    "calibrated": row.get("reference_calibrated_w"),
                    "local": row.get("reference_local_w"),
                    "trend": row.get("reference_adaptive_w"),
                }
            values = [candidate.get(method) for method in METHODS]
            if any(value is None or not np.isfinite(value) for value in values):
                continue
            actual, day = truth[target_ms]
            if actual <= 50 and float(candidate["calibrated"]) <= 50:
                continue
            bucket = int(np.ceil(lead / 15) * 15)
            comparisons.append(
                dict(
                    target=row["target_ms"],
                    bucket=bucket,
                    distance=abs(lead - bucket + 7.5),
                    day=day,
                    **{method: abs(float(candidate[method]) - actual) for method in METHODS},
                )
            )
        per_lead = {}
        if comparisons:
            frame = (
                pd.DataFrame(comparisons)
                .sort_values("distance")
                .drop_duplicates(["target", "bucket"])
            )
            for bucket_label, part in frame.groupby("bucket"):
                # Equal day weighting prevents a bright long day or faster poller
                # from choosing a model for every other day.
                errors = part.groupby("day")[list(METHODS)].mean().mean()
                days = int(part.day.nunique())
                enough = days >= minimum_days and len(part) >= minimum_samples
                choice = "calibrated"
                best = float(errors[choice])
                for method in METHODS[1:]:
                    value = float(errors[method])
                    if method in enabled_methods and enough and value < best * (1 - improvement):
                        choice, best = method, value
                per_lead[str(int(cast(int, bucket_label)))] = dict(
                    method=choice,
                    days=days,
                    samples=len(part),
                    validated=enough,
                    mae_w={method: float(errors[method]) for method in METHODS},
                )
        selected[group] = per_lead
    return selected


def choose_correction(
    calibrated: pd.Series,
    local: pd.Series,
    trend: pd.Series,
    issue: pd.Timestamp,
    validation: dict[str, Any],
    mode: str = "auto",
) -> pd.Series:
    """Use a validated candidate per lead; outside two hours keep calibration."""
    result = calibrated.copy()
    leads = (pd.DatetimeIndex(calibrated.index) - issue).total_seconds().to_numpy() / 60
    buckets = np.ceil(leads / 15).astype(int) * 15
    candidates = {"calibrated": calibrated, "local": local, "trend": trend}
    for bucket in np.unique(buckets):
        method = mode
        if mode == "auto":
            method = validation.get(str(int(bucket)), {}).get("method", "calibrated")
        if method not in candidates:
            method = "calibrated"
        mask = (buckets == bucket) & (leads >= 0) & (leads < 120)
        result.loc[mask] = candidates[method].loc[mask]
    return result
