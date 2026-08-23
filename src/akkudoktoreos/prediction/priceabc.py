"""Shared base for price-like predictions (electricity price, feed-in tariff)."""

from abc import abstractmethod
from typing import cast

import numpy as np
import pandas as pd
from loguru import logger
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from akkudoktoreos.core.coreabc import PredictionMixin
from akkudoktoreos.prediction.predictionabc import PredictionProvider
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime, to_duration


class PricePredictionProviderBase(PredictionMixin, PredictionProvider):
    """Common forecasting + fee-application logic shared by price-like providers.

    Subclasses must supply the raw/gross record keys, the fee keys to pull from
    the prediction store, and the formula that combines raw price + fees.
    """

    # --- identity hooks -------------------------------------------------

    @property
    @abstractmethod
    def _raw_key(self) -> str:
        """Record key holding the fee-free raw series."""

    @property
    @abstractmethod
    def _gross_key(self) -> str:
        """Record key to write the fee-inclusive series to."""

    @property
    @abstractmethod
    def _fee_keys(self) -> list[str]:
        """Prediction keys to fetch for fee computation."""

    @abstractmethod
    def _compute_gross(self, raw_amt_wh: pd.Series, df_fee: pd.DataFrame) -> pd.Series:
        """Combine the raw series with the fetched fee dataframe."""

    # --- forecasting helpers (verbatim, shared) --------------------------

    def _resolution_seconds(self, series: pd.Series) -> int:
        """Infer the native slot size in seconds from the series timestamps.

        Uses the median of the timestamp differences so that a single outlier gap does
        not distort the result. Falls back to hourly (3600 s) when fewer than two
        timestamps are available.
        """
        if len(series) < 2:
            return 3600
        index = pd.DatetimeIndex(series.sort_index().index).drop_duplicates()
        deltas = index.to_series().diff().dropna().dt.total_seconds()
        deltas = deltas[deltas > 0].tail(96)
        if deltas.empty:
            return 3600
        resolution = int(round(float(deltas.median())))
        return resolution if resolution > 0 and 3600 % resolution == 0 else 3600

    def _cap_outliers(self, data: np.ndarray, sigma: int = 2) -> np.ndarray:
        """Clip extreme values in a price history to a range around the mean.

        Values further than ``sigma`` standard deviations from the mean are clipped
        to the corresponding bound. Used to keep single-point spikes (e.g. negative
        price events or data glitches) from dominating seasonal decomposition or a
        median fallback.

        Args:
            data: The raw price history to clip.
            sigma: Number of standard deviations from the mean to allow before
                clipping. Defaults to 2.

        Returns:
            A copy of ``data`` with outliers clipped to ``[mean - sigma * std,
            mean + sigma * std]``.
        """
        mean = data.mean()
        std = data.std()
        lower_bound = mean - sigma * std
        upper_bound = mean + sigma * std
        return data.clip(min=lower_bound, max=upper_bound)

    def _predict_ets(self, history: np.ndarray, seasonal_periods: int, hours: int) -> np.ndarray:
        """Forecast future prices with additive Exponential Smoothing (ETS).

        Fits a Holt-Winters model with an additive seasonal component to the
        outlier-capped history and forecasts the requested number of hours ahead.

        Args:
            history: Historical price values, ordered oldest to newest.
            seasonal_periods: Length of one seasonal cycle in the same unit as
                ``history`` (e.g. 24 for daily seasonality, 168 for weekly
                seasonality on hourly data).
            hours: Number of hours to forecast beyond the end of ``history``.

        Returns:
            An array of ``hours`` forecasted values.

        Raises:
            ValueError: If ``history`` has fewer than ``2 * seasonal_periods``
                observations, which ETS needs to reliably estimate the seasonal
                component.
        """
        required_observations = 2 * seasonal_periods
        if len(history) < required_observations:
            raise ValueError(
                f"Not enough history for ETS with seasonal_periods="
                f"{seasonal_periods}: got {len(history)}, "
                f"need at least {required_observations}"
            )
        clean_history = self._cap_outliers(history)
        model = ExponentialSmoothing(
            clean_history, seasonal="add", seasonal_periods=seasonal_periods
        ).fit()
        return model.forecast(hours)

    def _predict_median(self, history: np.ndarray, hours: int) -> np.ndarray:
        """Forecast future prices as a constant equal to the historical median.

        Fallback used when there isn't enough history for a seasonal ETS forecast.

        Args:
            history: Historical price values, ordered oldest to newest.
            hours: Number of hours to forecast.

        Returns:
            An array of ``hours`` values, all equal to the median of the
            outlier-capped history.
        """
        clean_history = self._cap_outliers(history)
        return np.full(hours, np.median(clean_history))

    def _predict(self, history: np.ndarray, hours: int, slots_per_hour: int = 1) -> np.ndarray:
        """Forecast future prices, choosing seasonality by available history length.

        Uses weekly-seasonal ETS if there's enough history for it, falls back to
        daily-seasonal ETS with less, and to a constant median with too little
        history for either.

        Args:
            history: Historical price values, ordered oldest to newest.
            hours: Number of forecast steps to produce, at the resolution implied
                by ``slots_per_hour`` (despite the name, not necessarily clock hours).
            slots_per_hour: Number of samples per hour in ``history`` (e.g. 4 for
                15-minute data). Scales the seasonal period so a "week" or "day"
                still spans the right number of samples at sub-hourly resolution.
                Defaults to 1 (hourly data).

        Returns:
            An array of ``hours`` forecasted values.

        Raises:
            ValueError: If ``history`` is empty.
        """
        weekly_periods = 168 * slots_per_hour
        daily_periods = 24 * slots_per_hour
        history_length = len(history)
        if history_length >= 2 * weekly_periods:
            return self._predict_ets(history, seasonal_periods=weekly_periods, hours=hours)
        elif history_length >= 2 * daily_periods:
            return self._predict_ets(history, seasonal_periods=daily_periods, hours=hours)
        elif history_length > 0:
            logger.warning(
                "Using median fallback to predict prices with only {} values.", len(history)
            )
            return self._predict_median(history, hours=hours)
        logger.error("No data available for prediction")
        raise ValueError("No data available")

    # --- fee application (shared plumbing, subclass supplies formula) ----

    async def _apply_fees(self, raw_price_amt_wh: pd.Series) -> pd.Series:
        """Apply fees to a raw price-like time series to produce the gross series.

        The raw series is first normalized to a strictly uniform, sorted,
        duplicate-free DatetimeIndex (resampling to a fixed 15-minute grid with
        forward-fill if the input spacing isn't already uniform), since fees are
        fetched from the prediction store over the resulting `[start, end)` window
        at that resolution. The fee values are then combined with the raw series
        via `_compute_gross`, which each subclass implements with its own formula
        (e.g. add-then-percent for consumption, percent-then-subtract for feed-in).

        Args:
            raw_price_amt_wh: Raw price-like series (amount/Wh), indexed by a
                timezone-aware DatetimeIndex. Excludes fees.

        Returns:
            pd.Series: Raw series with fees applied (amount/Wh), named the same
            as `raw_price_amt_wh`. If the input index was uniform, the returned
            index matches it; otherwise the returned index is the fixed
            15-minute, forward-filled resampling of the input index.

        Raises:
            ValueError: If `raw_price_amt_wh` is empty, or has fewer than two
                entries (so no interval can be derived).
            TypeError: If `raw_price_amt_wh` is not indexed by a DatetimeIndex,
                or if the derived interval is not a `pd.Timedelta`.
        """
        if raw_price_amt_wh.empty:
            raise ValueError("raw_price_amt_wh must not be empty.")
        if len(raw_price_amt_wh.index) < 2:
            raise ValueError(
                "raw_price_amt_wh must have at least two entries to derive the interval."
            )

        # Normalize the index: sorted, unique timestamps only. Later duplicate
        # timestamps win, since they're assumed to be the more recently written value.
        index = raw_price_amt_wh.index.sort_values()
        if not isinstance(index, pd.DatetimeIndex):
            raise TypeError("raw_price_amt_wh must have a DatetimeIndex")
        index = cast(pd.DatetimeIndex, index)

        raw_price_amt_wh = raw_price_amt_wh.reindex(index)
        raw_price_amt_wh = raw_price_amt_wh[~raw_price_amt_wh.index.duplicated(keep="last")]
        index = cast(pd.DatetimeIndex, raw_price_amt_wh.index)

        # Determine whether the (deduplicated) index has a single, uniform spacing.
        diffs = index.to_series().diff().dropna().unique()
        if len(diffs) != 1:
            # Spacing is irregular (e.g. gaps or mixed resolutions): fall back to a
            # fixed 15-minute grid spanning the same range, forward-filling gaps so
            # every slot has a value before fees are fetched/applied.
            diff0 = pd.Timedelta(minutes=15)
            uniform_index = pd.date_range(start=index[0], end=index[-1], freq=diff0, tz=index.tz)
            raw_price_amt_wh = (
                raw_price_amt_wh.reindex(raw_price_amt_wh.index.union(uniform_index))
                .sort_index()
                .ffill()
                .reindex(uniform_index)
            )
            index = uniform_index
            logger.warning(
                f"raw_price_amt_wh has non uniform spacing {diffs}; "
                "resampled to fixed 15-minutes grid with forward filling gaps"
            )
        else:
            # Already uniform: use the single observed spacing as-is.
            diff = diffs[0]
            if not isinstance(diff, pd.Timedelta):
                raise TypeError("Expected a Timedelta")
            diff0 = diff

        # Window and resolution used to fetch fee data matching the raw series exactly.
        # end_datetime is exclusive, so it's one interval past the last raw timestamp.
        start_datetime = to_datetime(index[0].to_pydatetime())
        end_datetime = to_datetime(index[-1].to_pydatetime() + diff0.to_pytimedelta())
        interval = to_duration(f"{diff0.total_seconds()} seconds")

        # Fetch the fee series/percentages this provider needs (subclass-specific keys).
        keys = self._fee_keys
        try:
            df_fee = await self.prediction.keys_to_dataframe(
                keys=keys,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                interval=interval,
                fill_method="linear",
                resample_method="mean",
                dropna=False,
                boundary="context",
                align_to_interval=True,
            )
        except KeyError:
            # No fee provider enabled/configured for these keys: treat as zero
            # fees rather than failing the whole price calculation.
            df_fee = pd.DataFrame(0.0, index=raw_price_amt_wh.index, columns=keys)

        # Guard against any boundary/resample mismatch between the fee dataframe
        # and the raw price index (e.g. missing edge timestamps) by reindexing
        # onto the raw index exactly and treating anything still missing as zero.
        df_fee = df_fee.reindex(raw_price_amt_wh.index).fillna(0.0)

        # Subclass-specific formula combining raw price and fees.
        price_amt_wh = self._compute_gross(raw_price_amt_wh, df_fee)
        price_amt_wh.name = raw_price_amt_wh.name
        return price_amt_wh

    async def _store_gross_series(
        self,
        start_datetime: DateTime | None = None,
        end_datetime: DateTime | None = None,
    ) -> None:
        """Derive the fee-inclusive series from the fee-free (raw) series.

        Recomputes the fee-inclusive series over `[start_datetime, end_datetime)`
        only, so that historic and predicted values within that window get their
        own correct time-window/weekday-specific fee amount. Deliberately bounded
        rather than covering the entire retained history, which can span years -
        callers are responsible for choosing bounds that cover every timestamp
        written or possibly affected during the current update cycle.

        Note: timestamps outside the given bounds keep whatever gross value was
        computed for them in an earlier update cycle. If the fee schedule changes
        in a way that should retroactively affect already-processed history, that
        older range needs to be explicitly recomputed (e.g. via a forced refetch),
        it will not happen automatically here.

        Args:
            start_datetime: Inclusive lower bound of the raw series to recompute.
            end_datetime: Exclusive upper bound of the raw series to recompute.
        """
        # Read back only the fee-free slice that needs recomputing...
        full_raw_series = await self.key_to_raw_series(
            key=self._raw_key, start_datetime=start_datetime, end_datetime=end_datetime
        )
        # ...apply fees to it...
        full_series_with_fees = await self._apply_fees(full_raw_series)
        # ...and persist the result under the gross key.
        await self.key_from_series(self._gross_key, full_series_with_fees)
