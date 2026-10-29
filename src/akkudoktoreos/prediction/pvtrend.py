"""Causal, lead-specific learning of local PV residual persistence and trends.

Training needs archived features from genuinely issued forecasts, plus the
later availability of their independent reference measurements. A revised
weather history cannot substitute for that archive.
"""

from dataclasses import dataclass, field
from typing import cast

import numpy as np
import pandas as pd

from akkudoktoreos.prediction.pvadaptive import _aware, _aware_index

LEVEL_FEATURES = ("prior_residual", "level", "weather_change", "intercept")
TREND_FEATURES = ("ramp", "slope", "volatility", "consistent_ramp")
FEATURES = LEVEL_FEATURES + TREND_FEATURES


def local_trend_features(
    history: pd.DataFrame,
    future_reference: pd.Series,
    future_reference_sky: pd.Series,
    cutoff: pd.Timestamp,
    reference_interval_minutes: int = 15,
) -> pd.DataFrame:
    """Describe four complete past intervals without reading future actuals.

    Slopes describe deviations from the current weather model, normalized by the
    reference's clear-sky power. They therefore exclude its regular daily solar
    curve. All future quantities must come from a weather run available at cutoff.
    An invalid latest interval disables features instead of selecting older values.
    """
    cutoff = _aware(cutoff)
    future_index = _aware_index(future_reference.index)
    if reference_interval_minutes not in (5, 15):
        raise ValueError("Reference resolution must be 5 or 15 minutes.")
    _aware_index(history.index, reference_interval_minutes)
    if history.index.has_duplicates or future_index.has_duplicates:
        raise ValueError("Distinct reference and forecast timestamps required.")
    result = pd.DataFrame(np.nan, index=future_index, columns=list(FEATURES))
    result["feature_available_at"] = pd.Series(
        pd.NaT, index=future_index, dtype="datetime64[ns, UTC]"
    )
    if history.empty:
        return result
    available = pd.to_datetime(history["available_at"], utc=True)
    recent = (
        history[
            (available <= cutoff)
            & (history.index + pd.Timedelta(minutes=reference_interval_minutes) <= cutoff)
            & (history.index >= cutoff - pd.Timedelta(hours=2))
        ]
        .sort_index()
        .tail(4)
    )
    if len(recent) != 4:
        return result
    reference_index = _aware_index(recent.index, reference_interval_minutes)
    interval = pd.Timedelta(minutes=reference_interval_minutes)
    if not ((reference_index[1:] - reference_index[:-1]) == interval).all():
        return result
    if cutoff - (recent.index[-1] + interval) > pd.Timedelta(minutes=30):
        return result
    values = recent[["calibrated_power", "measured_power", "clear_sky_power"]]
    if not np.isfinite(values.to_numpy()).all():
        return result
    if (values[["calibrated_power", "measured_power"]] < 0).any().any():
        return result
    if (values.clear_sky_power < 150).any():
        return result
    if "eligible" in recent and not recent.eligible.fillna(False).all():
        return result
    residual = (
        ((values.measured_power - values.calibrated_power) / values.clear_sky_power)
        .clip(-1.5, 1.5)
        .to_numpy(dtype=float)
    )
    level = float(np.dot(residual[-2:], [0.35, 0.65]))
    changes = np.diff(residual) * 15 / reference_interval_minutes
    ramp = float(changes[-1])
    slope = float(np.dot(residual, [-0.3, -0.1, 0.1, 0.3]) * 15 / reference_interval_minutes)
    volatility = float(np.std(changes))
    consistent = ramp if np.sign(changes[-1]) == np.sign(changes[-2]) else 0.0
    current_weather = float(values.calibrated_power.iloc[-1] / values.clear_sky_power.iloc[-1])
    sky = future_reference_sky.reindex(future_index)
    lead = (future_index - cutoff).total_seconds().to_numpy() / 60
    age = (cutoff - (recent.index[-1] + interval / 2)).total_seconds() / 60
    decay = np.exp2(-np.maximum(0, lead + age) / 30)
    taper = np.clip(2 * (1 - lead / 120), 0, 1)
    valid = (
        (lead >= 0)
        & (lead < 120)
        & (future_index.tz_convert(cutoff.tz).date == cutoff.date())
        & sky.ge(150).to_numpy()
        & np.isfinite(future_reference.to_numpy())
        & future_reference.ge(0).to_numpy()
    )
    rows = pd.DataFrame(
        {
            "prior_residual": level * decay * taper,
            "level": level,
            "weather_change": (current_weather - future_reference / sky).clip(-1.5, 1.5),
            "intercept": 1.0,
            "ramp": ramp,
            "slope": slope,
            "volatility": volatility,
            "consistent_ramp": consistent,
            "feature_available_at": pd.to_datetime(recent.available_at, utc=True).max(),
        },
        index=future_index,
    )
    result.loc[valid, :] = rows.loc[valid, :]
    return result


@dataclass
class LocalPVTrendModel:
    """Learn a robust correction per forecast lead, shrinking to the old nowcast.

    Each daily fit uses only issued samples whose target measurements were already
    available on completed earlier local days. Refit coefficients from that archive
    instead of training on corrected forecasts or curtailed whole-plant production.
    """

    use_trend: bool = True
    timezone: str = "Europe/Berlin"
    learning_days: int = 21
    half_life_days: float = 14.0
    min_days: int = 5
    min_samples: int = 100
    ridge: float = 20.0
    scale_features: bool = False
    coefficients: dict[int, np.ndarray] = field(default_factory=dict, init=False)
    training_counts: dict[int, int] = field(default_factory=dict, init=False)

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Features shared with the level-only comparison and optional trends."""
        return FEATURES if self.use_trend else LEVEL_FEATURES

    def fit(self, samples: pd.DataFrame, cutoff: pd.Timestamp) -> "LocalPVTrendModel":
        """Fit using available labels from previous days, with recency and Huber weights."""
        cutoff = _aware(cutoff)
        self.coefficients, self.training_counts = {}, {}
        if self.ridge <= 0 or self.half_life_days <= 0:
            raise ValueError("Positive regularization and recency half-life required.")
        if samples.empty:
            return self
        issued = pd.to_datetime(samples.issue, utc=True)
        target_time = pd.to_datetime(samples.time, utc=True)
        available = pd.to_datetime(samples.target_available_at, utc=True)
        feature_available = pd.to_datetime(samples.feature_available_at, utc=True)
        today = cutoff.tz_convert(self.timezone).normalize()
        target_day = target_time.dt.tz_convert(self.timezone).dt.normalize()
        valid = (
            (issued < target_time)
            & (feature_available <= issued)
            & (available >= target_time + pd.Timedelta(minutes=15))
            & (available <= cutoff)
            & (target_day < today)
            & (issued >= cutoff - pd.Timedelta(days=self.learning_days))
            & pd.Series(
                np.isfinite(
                    samples[[*self.feature_names, "target_residual", "reference_sky_w"]].to_numpy(
                        dtype=float
                    )
                ).all(axis=1),
                index=samples.index,
            )
            & samples.reference_sky_w.ge(150)
            & samples.lead_minutes.between(0, 120, inclusive="left")
        )
        selected = samples.loc[valid].copy()
        if selected.empty:
            return self
        selected["bucket"] = np.ceil(selected.lead_minutes / 15).astype(int) * 15
        prior = np.zeros(len(self.feature_names))
        prior[0] = 1.0
        for bucket, part in selected.groupby("bucket"):
            bucket_number = cast(int, bucket)
            count = len(part)
            days = (
                pd.to_datetime(part.time, utc=True).dt.tz_convert(self.timezone).dt.date.nunique()
            )
            self.training_counts[bucket_number] = count
            if count < self.min_samples or days < self.min_days:
                continue
            x = part[list(self.feature_names)].to_numpy(dtype=float)
            y = part.target_residual.clip(-1.5, 1.5).to_numpy(dtype=float)
            age = (cutoff - pd.to_datetime(part.issue, utc=True)).dt.total_seconds() / 86400
            base = np.exp2(-age.to_numpy() / self.half_life_days)
            base *= np.minimum(1, part.reference_sky_w.to_numpy(dtype=float) / 1500)
            weight = base.copy()
            scale = np.ones(len(prior))
            if self.scale_features:
                # Penalize standardized feature contributions. A physically
                # small slope must not be suppressed merely because of its units.
                scale = np.maximum(np.sqrt(np.average(x * x, axis=0, weights=base)), 1e-3)
            penalty = np.diag(scale * scale) * self.ridge
            coef = prior.copy()
            for _ in range(4):
                coef = np.linalg.solve(
                    x.T @ (weight[:, None] * x) + penalty, x.T @ (weight * y) + penalty @ prior
                )
                error = np.abs(y - x @ coef)
                weight = base * np.minimum(1, 0.15 / np.maximum(error, 1e-9))
            self.coefficients[bucket_number] = coef
        return self

    def predict(
        self,
        prediction: pd.Series,
        clear_sky_power: pd.Series,
        features: pd.DataFrame,
        cutoff: pd.Timestamp,
        fallback: pd.Series,
        capacity_w: float = 0.0,
    ) -> pd.Series:
        """Transfer learned reference residuals to the plant; otherwise keep fallback."""
        cutoff = _aware(cutoff)
        index = _aware_index(prediction.index)
        result = fallback.reindex(index).copy()
        rows = features.reindex(index)
        sky = clear_sky_power.reindex(index)
        leads = (index - cutoff).total_seconds().to_numpy() / 60
        buckets = np.ceil(leads / 15).astype(int) * 15
        available = pd.to_datetime(rows.feature_available_at, utc=True)
        valid = (
            (leads >= 0)
            & (leads < 120)
            & (index.tz_convert(cutoff.tz).date == cutoff.date())
            & np.isfinite(rows[list(self.feature_names)].to_numpy(dtype=float)).all(axis=1)
            & (available <= cutoff).to_numpy()
            & np.isfinite(sky.to_numpy())
            & np.isfinite(prediction.to_numpy())
        )
        for bucket, coef in self.coefficients.items():
            mask = valid & (buckets == bucket)
            if not mask.any():
                continue
            residual = rows.loc[mask, list(self.feature_names)].to_numpy(dtype=float) @ coef
            residual = np.clip(residual, -1.5, 1.5)
            # Smoothly remove any learned constant near the finite horizon.
            taper = np.clip((120 - leads[mask]) / 30, 0, 1)
            result.loc[mask] = prediction.loc[mask] + sky.loc[mask].clip(lower=0) * residual * taper
        return result.clip(lower=0, upper=capacity_w if capacity_w > 0 else None)
