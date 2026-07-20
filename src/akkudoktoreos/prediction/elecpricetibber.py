"""Retrieves and processes electricity price forecast data from Tibber."""

from typing import Any, List, Optional, Union

import numpy as np
import pandas as pd
import requests
from loguru import logger
from pydantic import Field, ValidationError
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.elecpriceabc import ElecPriceProvider
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

TIBBER_GRAPHQL_URL = "https://api.tibber.com/v1-beta/gql"
TIBBER_DAILY_SEASONAL_HOURS = 24 * 7
TIBBER_WEEKLY_SEASONAL_HOURS = 24 * 35
TIBBER_PRICE_QUERY = """
query TibberPriceInfo {
  viewer {
    homes {
      id
      currentSubscription {
        priceInfo {
          today {
            startsAt
            total
            energy
          }
          tomorrow {
            startsAt
            total
            energy
          }
        }
        priceInfoRange(resolution: QUARTER_HOURLY, last: 672) {
          nodes {
            startsAt
            total
            energy
          }
        }
      }
    }
  }
}
"""

# Same query, but requesting priceInfo (and therefore its today/tomorrow
# fields) at quarter-hourly resolution. Tibber defines ``resolution`` on
# Subscription.priceInfo, not on the nested PriceInfo.today/tomorrow fields.
# Tried first; on a GraphQL schema error from an older API the provider falls
# back to TIBBER_PRICE_QUERY.
TIBBER_PRICE_QUERY_QUARTER_HOURLY = """
query TibberPriceInfo {
  viewer {
    homes {
      id
      currentSubscription {
        priceInfo(resolution: QUARTER_HOURLY) {
          today {
            startsAt
            total
            energy
          }
          tomorrow {
            startsAt
            total
            energy
          }
        }
        priceInfoRange(resolution: QUARTER_HOURLY, last: 672) {
          nodes {
            startsAt
            total
            energy
          }
        }
      }
    }
  }
}
"""


class ElecPriceTibberCommonSettings(SettingsBaseModel):
    """Common settings for the Tibber electricity price provider."""

    access_token: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Tibber API access token.",
            "examples": ["tibber_pat_..."],
        },
    )

    home_id: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Optional Tibber home id. If omitted, the first home with a subscription is used."
            ),
            "examples": ["00000000-0000-0000-0000-000000000000"],
        },
    )


class TibberPricePoint(PydanticBaseModel):
    """Single Tibber price point."""

    startsAt: str
    total: float
    energy: Optional[float] = None


class TibberPriceConnection(PydanticBaseModel):
    """Tibber connection for historical price nodes."""

    nodes: List[TibberPricePoint] = Field(default_factory=list)


class TibberPriceInfo(PydanticBaseModel):
    """Tibber price info for today and tomorrow."""

    today: List[TibberPricePoint] = Field(default_factory=list)
    tomorrow: List[TibberPricePoint] = Field(default_factory=list)


class TibberSubscription(PydanticBaseModel):
    """Tibber subscription data."""

    priceInfo: Optional[TibberPriceInfo] = None
    priceInfoRange: Optional[TibberPriceConnection] = None


class TibberHome(PydanticBaseModel):
    """Tibber home data."""

    id: str
    currentSubscription: Optional[TibberSubscription] = None


class TibberViewer(PydanticBaseModel):
    """Tibber viewer data."""

    homes: List[TibberHome] = Field(default_factory=list)


class TibberData(PydanticBaseModel):
    """Tibber GraphQL data payload."""

    viewer: TibberViewer


class TibberGraphQLError(PydanticBaseModel):
    """Tibber GraphQL error item."""

    message: str


class TibberGraphQLResponse(PydanticBaseModel):
    """Tibber GraphQL response payload."""

    data: Optional[TibberData] = None
    errors: Optional[List[TibberGraphQLError]] = None


class ElecPriceTibber(ElecPriceProvider):
    """Fetch and process electricity price forecast data from Tibber."""

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the Tibber provider."""
        return "ElecPriceTibber"

    def historic_hours_min(self) -> int:
        """Keep enough history for weekly seasonal price extrapolation."""
        return 24 * 35

    @classmethod
    def _validate_data(cls, json_str: Union[bytes, Any]) -> TibberGraphQLResponse:
        """Validate Tibber GraphQL response data."""
        try:
            tibber_data = TibberGraphQLResponse.model_validate_json(json_str)
        except ValidationError as e:
            error_msg = ""
            for error in e.errors():
                field = " -> ".join(str(x) for x in error["loc"])
                message = error["msg"]
                error_type = error["type"]
                error_msg += f"Field: {field}\nError: {message}\nType: {error_type}\n"
            logger.error(f"Tibber schema change: {error_msg}")
            raise ValueError(error_msg)
        if tibber_data.errors:
            error_msg = "; ".join(error.message for error in tibber_data.errors)
            error_msg = f"Tibber GraphQL error: {error_msg}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        return tibber_data

    def _api_price_counts(self, response: TibberGraphQLResponse) -> tuple[int, int, int]:
        """Return Tibber API price counts for history, today, and tomorrow."""
        home = self._select_home(response)
        subscription = home.currentSubscription
        if subscription is None:
            raise ValueError("Tibber home has no current subscription")

        history_count = 0
        today_count = 0
        tomorrow_count = 0
        if subscription.priceInfoRange is not None:
            history_count = len(subscription.priceInfoRange.nodes)
        if subscription.priceInfo is not None:
            today_count = len(subscription.priceInfo.today)
            tomorrow_count = len(subscription.priceInfo.tomorrow)
        return history_count, today_count, tomorrow_count

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> TibberGraphQLResponse:
        """Fetch electricity price data from the Tibber GraphQL API."""
        access_token = self.config.elecprice.tibber.access_token
        if not access_token:
            raise ValueError("Tibber access_token is required")

        # Prefer quarter-hourly today/tomorrow prices; fall back to the hourly
        # query when the Tibber API rejects the resolution argument. Tibber
        # signals schema errors either as HTTP 400 or as HTTP 200 with an
        # "errors" array, so both must route to the fallback (raise_for_status
        # must NOT run before the fallback check).
        response = None
        queries = (TIBBER_PRICE_QUERY_QUARTER_HOURLY, TIBBER_PRICE_QUERY)
        for attempt, query in enumerate(queries, start=1):
            response = requests.post(
                TIBBER_GRAPHQL_URL,
                json={"query": query},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            logger.debug(f"Response from Tibber GraphQL API: {response}")
            if response.ok and b'"errors"' not in response.content:
                break
            if attempt < len(queries):
                logger.info(
                    "Tibber rejected the quarter-hourly priceInfo query "
                    "(HTTP {}): {} - falling back to hourly today/tomorrow prices.",
                    response.status_code,
                    response.text[:300],
                )
            else:
                # Final (hourly) attempt failed for real - surface the error.
                response.raise_for_status()
        if response is None:  # pragma: no cover - the query tuple is never empty
            raise RuntimeError("No Tibber GraphQL query was attempted")
        tibber_data = self._validate_data(response.content)
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return tibber_data

    def _select_home(self, response: TibberGraphQLResponse) -> TibberHome:
        """Select the configured Tibber home from a GraphQL response."""
        if response.data is None:
            raise ValueError("Tibber response does not contain data")

        home_id = self.config.elecprice.tibber.home_id
        if home_id:
            for home in response.data.viewer.homes:
                if home.id == home_id:
                    if home.currentSubscription is None:
                        raise ValueError(f"Tibber home '{home_id}' has no current subscription")
                    return home
            raise ValueError("Tibber home_id not found")

        for home in response.data.viewer.homes:
            if home.currentSubscription is not None:
                return home
        raise ValueError("No Tibber home with a current subscription found")

    def _parse_data(self, response: TibberGraphQLResponse) -> pd.Series:
        """Parse Tibber prices into EOS market prices in EUR/Wh."""
        home = self._select_home(response)
        subscription = home.currentSubscription
        if subscription is None:
            raise ValueError("Tibber home has no current subscription")

        points: list[TibberPricePoint] = []
        if subscription.priceInfoRange is not None:
            points.extend(subscription.priceInfoRange.nodes)
        if subscription.priceInfo is not None:
            points.extend(subscription.priceInfo.today)
            points.extend(subscription.priceInfo.tomorrow)
            if not subscription.priceInfo.tomorrow:
                logger.warning("Tibber tomorrow prices not available yet")

        if not points:
            raise ValueError("Tibber response contains no price points")

        series_data = pd.Series(dtype=float)
        for point in points:
            orig_datetime = to_datetime(point.startsAt, in_timezone=self.config.general.timezone)
            series_data.at[orig_datetime] = point.total / 1000.0

        return series_data.sort_index()

    def _normalize_series(self, series: pd.Series) -> pd.Series:
        """Normalize Tibber prices while preserving their native resolution.

        The Tibber API delivers either hourly or quarter-hourly prices. EOS resamples
        the stored records onto the optimization grid on demand (``key_to_array``), so
        the provider must keep the native step size (e.g. 15 minutes) instead of
        pre-aggregating to hourly values. Duplicate timestamps are collapsed (mean) and
        the series is sorted, but the resolution is left untouched.
        """
        if series.empty:
            return series
        series = series.sort_index()
        series.index = pd.to_datetime([to_datetime(index).isoformat() for index in series.index])
        series = series.groupby(level=0).mean().sort_index()
        return series.dropna()

    def _resolution_seconds(self, series: pd.Series) -> int:
        """Infer the native slot size in seconds from the series timestamps.

        Uses the median of the timestamp differences so that a single outlier gap does
        not distort the result. Falls back to hourly (3600 s) when fewer than two
        timestamps are available.
        """
        if len(series) < 2:
            return 3600
        deltas = pd.DatetimeIndex(series.index).to_series().diff().dropna()
        if deltas.empty:
            return 3600
        resolution = int(round(deltas.dt.total_seconds().median()))
        return resolution if resolution > 0 else 3600

    def _cap_outliers(self, data: np.ndarray, sigma: int = 2) -> np.ndarray:
        mean = data.mean()
        std = data.std()
        lower_bound = mean - sigma * std
        upper_bound = mean + sigma * std
        capped_data = data.clip(min=lower_bound, max=upper_bound)
        return capped_data

    def _predict_ets(self, history: np.ndarray, seasonal_periods: int, hours: int) -> np.ndarray:
        clean_history = self._cap_outliers(history)
        model = ExponentialSmoothing(
            clean_history, seasonal="add", seasonal_periods=seasonal_periods
        ).fit()
        return model.forecast(hours)

    def _predict_median(self, history: np.ndarray, hours: int) -> np.ndarray:
        clean_history = self._cap_outliers(history)
        return np.full(hours, np.median(clean_history))

    def _predict_missing_prices(
        self, history: np.ndarray, slots: int, slots_per_hour: int
    ) -> np.ndarray:
        """Forecast missing future prices from the available history.

        Works on the native resolution of the series: ``slots_per_hour`` scales the
        hour-based seasonal windows into slot counts, so the seasonal periods and
        history thresholds stay correct at both hourly (``slots_per_hour == 1``) and
        quarter-hourly (``slots_per_hour == 4``) resolution.
        """
        numeric_history = np.asarray(history, dtype=float)
        numeric_history = numeric_history[np.isfinite(numeric_history)]
        history_slots = len(numeric_history)

        weekly_seasonal_slots = TIBBER_WEEKLY_SEASONAL_HOURS * slots_per_hour
        daily_seasonal_slots = TIBBER_DAILY_SEASONAL_HOURS * slots_per_hour

        if history_slots > weekly_seasonal_slots:
            logger.info(
                "Using weekly seasonal ETS forecast for Tibber electricity prices "
                "with {} historical values.",
                history_slots,
            )
            return self._predict_ets(
                numeric_history, seasonal_periods=168 * slots_per_hour, hours=slots
            )
        if history_slots > daily_seasonal_slots:
            logger.info(
                "Using daily seasonal ETS forecast for Tibber electricity prices "
                "with {} historical values.",
                history_slots,
            )
            return self._predict_ets(
                numeric_history, seasonal_periods=24 * slots_per_hour, hours=slots
            )
        if history_slots > 0:
            logger.warning(
                "Using median fallback for Tibber electricity prices because only {} "
                "historical values are available.",
                history_slots,
            )
            return self._predict_median(numeric_history, hours=slots)

        logger.error("No data available for prediction")
        raise ValueError("No data available")

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update Tibber price data and extrapolate missing future prices."""
        tibber_data = self._request_forecast(force_update=force_update)  # type: ignore
        if not self.ems_start_datetime:
            raise ValueError(f"Start DateTime not set: {self.ems_start_datetime}")

        api_history_count, api_today_count, api_tomorrow_count = self._api_price_counts(tibber_data)
        series_data = self._normalize_series(self._parse_data(tibber_data))
        if series_data.empty:
            raise ValueError("Tibber response contains no usable price points")

        resolution_seconds = self._resolution_seconds(series_data)
        slots_per_hour = round(3600 / resolution_seconds)

        highest_orig_datetime = to_datetime(series_data.index.max())
        await self.key_from_series("elecprice_marketprice_wh", series_data)

        history = await self.key_to_array(
            key="elecprice_marketprice_wh",
            end_datetime=highest_orig_datetime,
            interval=to_duration(f"{resolution_seconds} seconds"),
            fill_method="linear",
        )

        if not highest_orig_datetime:
            error_msg = f"Highest original datetime not available: {highest_orig_datetime}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        covered_slots = (
            highest_orig_datetime - self.ems_start_datetime
        ).total_seconds() // resolution_seconds
        needed_slots = int(self.config.prediction.hours * slots_per_hour - covered_slots)

        if needed_slots <= 0:
            logger.warning(
                "No prediction needed. "
                f"needed_slots={needed_slots}, "
                f"hours={self.config.prediction.hours}, "
                f"slots_per_hour={slots_per_hour}, "
                f"highest_orig_datetime={highest_orig_datetime}, "
                f"start_datetime={self.ems_start_datetime}"
            )
            return

        logger.info(
            "Tibber electricity price input: api_history={}, api_today={}, "
            "api_tomorrow={}, resolution_seconds={}, combined_history_slots={}, "
            "needed_forecast_slots={}.",
            api_history_count,
            api_today_count,
            api_tomorrow_count,
            resolution_seconds,
            len(history),
            needed_slots,
        )
        prediction = self._predict_missing_prices(
            history, slots=needed_slots, slots_per_hour=slots_per_hour
        )

        prediction_series = pd.Series(
            data=prediction,
            index=[
                highest_orig_datetime + to_duration(f"{(i + 1) * resolution_seconds} seconds")
                for i in range(len(prediction))
            ],
        )
        await self.key_from_series("elecprice_marketprice_wh", prediction_series)
