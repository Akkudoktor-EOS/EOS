"""Small causal PV calibration model shared by the provider and offline backtest."""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class AdaptivePVModel:
    """Learn energy bias and a smooth intraday shape from completed measurements.

    Inputs are actual 15-minute or hourly interval-start timestamps and mean powers in W.
    ``available_at``
    records when a measured interval became usable, including any ingestion lag.
    This is statistical calibration, not identification of physical fault causes.
    """

    timezone: str = "Europe/Berlin"
    learning_days: int = 60
    min_days: int = 4
    half_life_days: float = 30.0
    shape_prior_kwh: float = 2.0
    min_factor: float = 0.2
    max_factor: float = 3.0
    interval_minutes: int = 15
    gain: float = field(default=1.0, init=False)
    shape: np.ndarray = field(default_factory=lambda: np.ones(12), init=False)
    trained_days: int = field(default=0, init=False)

    def fit(self, history: pd.DataFrame, cutoff: pd.Timestamp) -> "AdaptivePVModel":
        """Fit only completed previous local days available before ``cutoff``.

        Global energy gain uses complete days. Shape uses valid paired intervals
        from those days, with shrinkage and recency weighting. Missing values,
        negative production, and explicitly invalid samples never become zeros.
        """
        self.gain, self.shape, self.trained_days = 1.0, np.ones(12), 0
        cutoff = _aware(cutoff)
        if self.interval_minutes not in (15, 60):
            raise ValueError("Training intervals must be 15 or 60 minutes.")
        if history.empty:
            return self
        history = history.sort_index()
        history_index = _aware_index(history.index)
        utc = history_index.tz_convert("UTC")
        if not utc.equals(utc.floor(f"{self.interval_minutes}min")):
            raise ValueError("Training timestamps must align with their actual interval.")
        if history.index.has_duplicates:
            raise ValueError("One training pair per interval is required.")
        available = pd.to_datetime(history["available_at"], utc=True)
        local_day = history_index.tz_convert(self.timezone).normalize()
        today = cutoff.tz_convert(self.timezone).normalize()
        valid = (
            (available <= cutoff)
            & (local_day < today)
            & (history.index >= cutoff - pd.Timedelta(days=self.learning_days))
            & np.isfinite(history["raw_power"])
            & np.isfinite(history["measured_power"])
            & (history["raw_power"] >= 0)
            & (history["measured_power"] >= 0)
        )
        pairs = history.loc[valid, ["raw_power", "measured_power"]].copy()
        if pairs.empty:
            return self
        pairs["day"] = _aware_index(pairs.index).tz_convert(self.timezone).normalize()
        # Count against elapsed local days, including daylight-saving transitions.
        days = pairs.groupby("day").agg(
            raw=("raw_power", "sum"), actual=("measured_power", "sum"), n=("raw_power", "size")
        )
        expected = np.array(
            [
                (day + pd.DateOffset(days=1) - day).total_seconds() / (self.interval_minutes * 60)
                for day in days.index
            ]
        )
        days = days[(days["n"].to_numpy() == expected) & (days["raw"] > 200.0)]
        self.trained_days = len(days)
        if self.trained_days < self.min_days:
            return self
        pairs = pairs[pairs["day"].isin(days.index)]
        age_days = np.array([(cutoff - day).total_seconds() / 86400 for day in days.index])
        day_weights = np.exp2(-age_days / self.half_life_days)
        self.gain = float(
            np.clip(
                np.dot(days["actual"], day_weights) / np.dot(days["raw"], day_weights),
                self.min_factor,
                self.max_factor,
            )
        )
        local = _aware_index(pairs.index).tz_convert(self.timezone)
        hours = local.hour.to_numpy() + local.minute.to_numpy() / 60 + self.interval_minutes / 120
        weights = np.exp2(
            (_aware_index(pairs.index) - cutoff).total_seconds().to_numpy()
            / 86400
            / self.half_life_days
        )
        prior_wh = self.shape_prior_kwh * 1000
        for number, center in enumerate(np.arange(12) * 2 + 1):
            distance = np.abs(hours - center)
            distance = np.minimum(distance, 24 - distance)
            kernel = np.maximum(0, 1 - distance / 2) * weights
            raw_wh = float(np.dot(kernel, pairs["raw_power"])) * self.interval_minutes / 60
            actual_wh = float(np.dot(kernel, pairs["measured_power"])) * self.interval_minutes / 60
            self.shape[number] = np.clip(
                (actual_wh + prior_wh * self.gain) / (raw_wh + prior_wh),
                self.min_factor,
                self.max_factor,
            )
        return self

    def predict(
        self, raw_power: pd.Series, capacity_w: float = 0.0, resolution_minutes: int = 15
    ) -> pd.Series:
        """Correct shape, conserving the independently learned energy per day."""
        local = _aware_index(raw_power.index).tz_convert(self.timezone)
        hours = local.hour.to_numpy() + local.minute.to_numpy() / 60 + resolution_minutes / 120
        centers = np.arange(12) * 2 + 1
        scales = np.interp(
            hours,
            np.r_[centers[-1] - 24, centers, centers[0] + 24],
            np.r_[self.shape[-1], self.shape, self.shape[0]],
        )
        raw = raw_power.to_numpy(dtype=float)
        days = local.normalize()
        for day in days.unique():
            mask = days == day
            before = float(np.nansum(raw[mask]))
            after = float(np.nansum(raw[mask] * scales[mask]))
            if before > 0 and after > 0:
                # Bounded multiplicative correction with energy constraint. Bisection
                # avoids a renormalization violating the configured factor limits.
                target = before * self.gain
                lo, hi = 0.0, self.max_factor / max(float(scales[mask].min()), 1e-12)
                for _ in range(40):
                    multiplier = (lo + hi) / 2
                    energy = np.nansum(
                        raw[mask]
                        * np.clip(scales[mask] * multiplier, self.min_factor, self.max_factor)
                    )
                    if energy < target:
                        lo = multiplier
                    else:
                        hi = multiplier
                scales[mask] = np.clip(
                    scales[mask] * (lo + hi) / 2, self.min_factor, self.max_factor
                )
        corrected = raw_power * scales
        return corrected.clip(lower=0, upper=capacity_w if capacity_w > 0 else None)


def apply_nowcast(
    prediction: pd.Series,
    history: pd.DataFrame,
    cutoff: pd.Timestamp,
    half_life_hours: float = 1.0,
    max_age_minutes: int = 30,
    capacity_w: float = 0.0,
) -> pd.Series:
    """Blend a recent measured/modelled ratio back to the forecast with lead time.

    Require at least 30 minutes of actual support in the past hour. The short-term
    correction does not survive overnight, is clamped, and expires with stale data.
    History must contain ``calibrated_power``, not already nowcast predictions.
    """
    cutoff = _aware(cutoff)
    if history.empty:
        return prediction.copy()
    _aware_index(history.index)
    available = pd.to_datetime(history["available_at"], utc=True)
    usable = history[
        (available <= cutoff)
        & (history.index + pd.Timedelta(minutes=15) <= cutoff)
        & (history.index >= cutoff - pd.Timedelta(hours=1))
    ]
    usable = usable[
        np.isfinite(usable["calibrated_power"])
        & np.isfinite(usable["measured_power"])
        & (usable["calibrated_power"] > 50)
        & (usable["measured_power"] >= 0)
    ]
    if len(usable) < 2:
        return prediction.copy()
    latest_end = usable.index.max() + pd.Timedelta(minutes=15)
    if cutoff - latest_end > pd.Timedelta(minutes=max_age_minutes):
        return prediction.copy()
    ratio = float(
        np.clip(usable["measured_power"].sum() / usable["calibrated_power"].sum(), 0.5, 1.5)
    )
    lead = np.maximum(0, (prediction.index - latest_end).total_seconds().to_numpy() / 3600)
    scale = 1 + (ratio - 1) * np.exp2(-lead / half_life_hours)
    same_day = _aware_index(prediction.index).tz_convert(cutoff.tz).date == cutoff.date()
    scale = np.where((prediction.index >= cutoff) & same_day, scale, 1.0)
    return (prediction * scale).clip(lower=0, upper=capacity_w if capacity_w > 0 else None)


def apply_local_nowcast(
    prediction: pd.Series,
    clear_sky_power: pd.Series,
    history: pd.DataFrame,
    cutoff: pd.Timestamp,
    half_life_minutes: float = 30.0,
    horizon_minutes: int = 120,
    max_age_minutes: int = 30,
    capacity_w: float = 0.0,
    reference_interval_minutes: int = 15,
) -> pd.Series:
    """Carry a recent local weather residual toward the current weather forecast.

    Reference measurements may be a non-curtailed subset of the plant. Normalize
    their residual by their own clear-sky power, then transfer it to the whole
    plant's clear-sky power. Require two contiguous complete reference intervals.
    Missing, stale, unavailable or demand-limited references yield no correction.
    ``eligible`` is optional; callers must exclude curtailment using measurement
    quality or provide explicit eligibility. This does not infer cloud motion.
    """
    cutoff = _aware(cutoff)
    prediction_index = _aware_index(prediction.index)
    if reference_interval_minutes not in (5, 15):
        raise ValueError("Local reference intervals must be 5 or 15 minutes.")
    _aware_index(history.index, reference_interval_minutes)
    if half_life_minutes <= 0 or horizon_minutes <= 0:
        raise ValueError("Local nowcast decay and horizon must be positive.")
    if history.index.has_duplicates:
        raise ValueError("One reference measurement per interval is required.")
    if history.empty:
        return prediction.copy()
    available = pd.to_datetime(history["available_at"], utc=True)
    recent = (
        history[
            (available <= cutoff)
            & (history.index + pd.Timedelta(minutes=reference_interval_minutes) <= cutoff)
            & (history.index >= cutoff - pd.Timedelta(hours=1))
        ]
        .sort_index()
        .tail(2)
    )
    reference_index = _aware_index(recent.index, reference_interval_minutes)
    if len(recent) != 2 or reference_index[1] - reference_index[0] != pd.Timedelta(
        minutes=reference_interval_minutes
    ):
        return prediction.copy()
    end = reference_index[-1] + pd.Timedelta(minutes=reference_interval_minutes)
    if cutoff - end > pd.Timedelta(minutes=max_age_minutes):
        return prediction.copy()
    if (
        not np.isfinite(recent[["calibrated_power", "measured_power", "clear_sky_power"]])
        .all()
        .all()
    ):
        return prediction.copy()
    if (recent["measured_power"] < 0).any() or (recent["clear_sky_power"] < 150).any():
        return prediction.copy()
    if "eligible" in recent and not recent["eligible"].fillna(False).all():
        return prediction.copy()
    residual = (
        (recent["measured_power"] - recent["calibrated_power"]) / recent["clear_sky_power"]
    ).clip(-1.5, 1.5)
    # Fixed recency weighting: no trend extrapolation from a passing cloud.
    local_error = float(np.dot(residual.to_numpy(), [0.35, 0.65]))
    age = (
        cutoff - (reference_index[-1] + pd.Timedelta(minutes=reference_interval_minutes / 2))
    ).total_seconds() / 60
    lead = (prediction_index - cutoff).total_seconds().to_numpy() / 60
    decay = np.exp2(-np.maximum(0, lead + age) / half_life_minutes)
    # Fade to exactly zero, avoiding a step at the finite horizon.
    taper = np.clip(2 * (1 - lead / horizon_minutes), 0, 1)
    same_day = prediction_index.tz_convert(cutoff.tz).date == cutoff.date()
    usable = (lead >= 0) & (lead < horizon_minutes) & same_day
    envelope = clear_sky_power.reindex(prediction.index)
    correction = envelope * np.where(usable, local_error * decay * taper, 0.0)
    result = (prediction + correction).where(envelope.notna(), prediction)
    return result.clip(lower=0, upper=capacity_w if capacity_w > 0 else None)


def _aware(value: pd.Timestamp) -> pd.Timestamp:
    if value.tzinfo is None:
        raise ValueError("Explicit timezone required.")
    return value


def _aware_index(index: pd.Index, resolution_minutes: int = 15) -> pd.DatetimeIndex:
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError("Timezone-aware DatetimeIndex required.")
    utc = index.tz_convert("UTC")
    if not utc.equals(utc.floor(f"{resolution_minutes}min")):
        raise ValueError(f"PV intervals must start on {resolution_minutes}-minute boundaries.")
    return index
