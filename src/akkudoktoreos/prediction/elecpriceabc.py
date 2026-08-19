"""Abstract and base classes for electricity price predictions.

Notes:
    - Ensure appropriate API keys or configurations are set up if required by external data sources.
"""

from abc import abstractmethod
from typing import List, Optional, cast

import pandas as pd
from pydantic import Field, computed_field

from akkudoktoreos.core.coreabc import PredictionMixin
from akkudoktoreos.prediction.predictionabc import PredictionProvider, PredictionRecord
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


class ElecPriceDataRecord(PredictionRecord):
    """Represents a electricity price data record containing various price attributes at a specific datetime.

    Attributes:
        date_time (Optional[AwareDatetime]): The datetime of the record.

    """

    elecprice_marketprice_wh: Optional[float] = Field(
        None, json_schema_extra={"description": "Electricity market price per Wh [amount/Wh]"}
    )

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def elecprice_marketprice_kwh(self) -> Optional[float]:
        """Electricity market price per kWh [amount/kWh].

        Convenience attribute calculated from `elecprice_marketprice_wh`.
        """
        if self.elecprice_marketprice_wh is None:
            return None
        return self.elecprice_marketprice_wh * 1000.0


class ElecPriceProvider(PredictionMixin, PredictionProvider):
    """Abstract base class for electricity price providers.

    ElecPriceProvider is a thread-safe singleton, ensuring only one instance of this class is created.
    """

    # overload
    records: List[ElecPriceDataRecord] = Field(
        default_factory=list,
        json_schema_extra={"description": "List of ElecPriceDataRecord records"},
    )

    @classmethod
    @abstractmethod
    def provider_id(cls) -> str:
        return "ElecPriceProvider"

    def enabled(self) -> bool:
        return self.provider_id() == self.config.elecprice.provider

    # --- helper ---
    def _apply_fees_enabled(self) -> bool:
        """Application of fees by apply_fees() enabled.

        Can be overwritten by derived classes.
        """
        return False

    async def apply_fees(self, raw_price_amt_wh: pd.Series) -> pd.Series:
        """Apply the predicted consumption fees to a raw energy market price time data series.

        If `raw_price_amt_wh` has a non-uniform interval, it is first
        resampled to the shortest interval found in the index, forward-filling
        the gaps, before fees are applied.

        Args:
            raw_price_amt_wh: Raw market price series (amount/Wh), indexed by a
                timezone-aware DatetimeIndex.

        Returns:
            pd.Series: Market price plus consumption fees (amount/Wh). If the
            input index was uniform, the returned index matches it; otherwise
            the returned index is the shortest-interval, forward-filled
            resampling of the input index.

        Raises:
            ValueError: If `raw_price_amt_wh` is empty, or has fewer than two
                entries (so no interval can be derived).
        """
        if not self._apply_fees_enabled():
            return raw_price_amt_wh
        if raw_price_amt_wh.empty:
            raise ValueError("raw_price_amt_wh must not be empty.")
        if len(raw_price_amt_wh.index) < 2:
            raise ValueError(
                "raw_price_amt_wh must have at least two entries to derive the interval."
            )

        index = raw_price_amt_wh.index.sort_values()
        if not isinstance(index, pd.DatetimeIndex):
            raise TypeError("raw_price_amt_wh must have a DatetimeIndex")
        index = cast(pd.DatetimeIndex, index)  # Make mypy happy

        raw_price_amt_wh = raw_price_amt_wh.reindex(index)
        # Drop duplicate timestamps (keep the last value for each).
        raw_price_amt_wh = raw_price_amt_wh[~raw_price_amt_wh.index.duplicated(keep="last")]
        index = cast(pd.DatetimeIndex, raw_price_amt_wh.index)  # Make mypy happy
        diffs = index.to_series().diff().dropna().unique()
        if len(diffs) != 1:
            # Non-uniform spacing: resample onto a fixed 15-minute grid,
            # forward-filling the gaps.
            diff0 = pd.Timedelta(minutes=15)
            uniform_index = pd.date_range(start=index[0], end=index[-1], freq=diff0, tz=index.tz)
            raw_price_amt_wh = (
                raw_price_amt_wh.reindex(raw_price_amt_wh.index.union(uniform_index))
                .sort_index()
                .ffill()
                .reindex(uniform_index)
            )
            index = uniform_index
        else:
            diff = diffs[0]
            if not isinstance(diff, pd.Timedelta):  # Make mypy happy
                raise TypeError("Expected a Timedelta")
            diff0 = diff
        start_datetime = to_datetime(index[0].to_pydatetime())
        end_datetime = to_datetime(index[-1].to_pydatetime() + diff0.to_pytimedelta())
        interval = to_duration(f"{diff0.total_seconds()} seconds")
        # Prepare electricty consumption fee data for the electricty price calculation
        #
        # We need a dataframe with the following columns:
        #
        # - elecfee_consumption_amt_wh
        # - elecfee_consumption_percent_amt
        #
        keys = [
            "elecfee_consumption_amt_wh",
            "elecfee_consumption_percent_amt",
        ]
        df_elecfee = await self.prediction.keys_to_dataframe(
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
        # Guard against any boundary/resample mismatch with the raw price index
        df_elecfee = df_elecfee.reindex(raw_price_amt_wh.index).fillna(0.0)
        price_amt_wh = (
            (raw_price_amt_wh + df_elecfee["elecfee_consumption_amt_wh"])
            * (100.0 + df_elecfee["elecfee_consumption_percent_amt"])
            / 100.0
        )
        price_amt_wh.name = raw_price_amt_wh.name
        return price_amt_wh
