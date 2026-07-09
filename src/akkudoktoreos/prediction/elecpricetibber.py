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
          }
          tomorrow {
            startsAt
            total
          }
        }
        priceInfoRange(resolution: HOURLY, last: 840) {
          nodes {
            startsAt
            total
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

        response = requests.post(
            TIBBER_GRAPHQL_URL,
            json={"query": TIBBER_PRICE_QUERY},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        logger.debug(f"Response from Tibber GraphQL API: {response}")
        response.raise_for_status()
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

    def _hourly_series(self, series: pd.Series) -> pd.Series:
        """Normalize Tibber prices to hourly values for EOS optimization."""
        if series.empty:
            return series
        series = series.sort_index()
        series.index = pd.to_datetime([to_datetime(index).isoformat() for index in series.index])
        return series.resample("1h").mean().dropna()

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

    def _predict_missing_prices(self, history: np.ndarray, hours: int) -> np.ndarray:
        """Forecast missing future prices from the available hourly history."""
        numeric_history = np.asarray(history, dtype=float)
        numeric_history = numeric_history[np.isfinite(numeric_history)]
        history_hours = len(numeric_history)

        if history_hours > TIBBER_WEEKLY_SEASONAL_HOURS:
            logger.info(
                "Using weekly seasonal ETS forecast for Tibber electricity prices "
                "with {} historical hourly values.",
                history_hours,
            )
            return self._predict_ets(numeric_history, seasonal_periods=168, hours=hours)
        if history_hours > TIBBER_DAILY_SEASONAL_HOURS:
            logger.info(
                "Using daily seasonal ETS forecast for Tibber electricity prices "
                "with {} historical hourly values.",
                history_hours,
            )
            return self._predict_ets(numeric_history, seasonal_periods=24, hours=hours)
        if history_hours > 0:
            logger.warning(
                "Using median fallback for Tibber electricity prices because only {} "
                "historical hourly values are available.",
                history_hours,
            )
            return self._predict_median(numeric_history, hours=hours)

        logger.error("No data available for prediction")
        raise ValueError("No data available")

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update Tibber price data and extrapolate missing future prices."""
        tibber_data = self._request_forecast(force_update=force_update)  # type: ignore
        if not self.ems_start_datetime:
            raise ValueError(f"Start DateTime not set: {self.ems_start_datetime}")

        api_history_count, api_today_count, api_tomorrow_count = self._api_price_counts(tibber_data)
        series_data = self._hourly_series(self._parse_data(tibber_data))
        if series_data.empty:
            raise ValueError("Tibber response contains no usable hourly price points")

        highest_orig_datetime = to_datetime(series_data.index.max())
        self.key_from_series("elecprice_marketprice_wh", series_data)

        history = self.key_to_array(
            key="elecprice_marketprice_wh",
            end_datetime=highest_orig_datetime,
            fill_method="linear",
        )

        if not highest_orig_datetime:
            error_msg = f"Highest original datetime not available: {highest_orig_datetime}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        needed_hours = int(
            self.config.prediction.hours
            - ((highest_orig_datetime - self.ems_start_datetime).total_seconds() // 3600)
        )

        if needed_hours <= 0:
            logger.warning(
                "No prediction needed. "
                f"needed_hours={needed_hours}, "
                f"hours={self.config.prediction.hours}, "
                f"highest_orig_datetime={highest_orig_datetime}, "
                f"start_datetime={self.ems_start_datetime}"
            )
            return

        logger.info(
            "Tibber electricity price input: api_history_hours={}, api_today_hours={}, "
            "api_tomorrow_hours={}, combined_history_hours={}, needed_forecast_hours={}.",
            api_history_count,
            api_today_count,
            api_tomorrow_count,
            len(history),
            needed_hours,
        )
        prediction = self._predict_missing_prices(history, hours=needed_hours)

        prediction_series = pd.Series(
            data=prediction,
            index=[
                highest_orig_datetime + to_duration(f"{i + 1} hours")
                for i in range(len(prediction))
            ],
        )
        self.key_from_series("elecprice_marketprice_wh", prediction_series)
