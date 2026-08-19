"""Retrieve German day-ahead electricity prices directly from SMARD."""

import time
from datetime import datetime
from typing import List, Optional

import requests
from loguru import logger
from pydantic import Field, ValidationError

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.elecpriceenergycharts import (
    ElecPriceEnergyCharts,
    EnergyChartsElecPrice,
)
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration

SMARD_BASE_URL = "https://www.smard.de/app/chart_data"


class SmardIndex(PydanticBaseModel):
    """Available SMARD data-chunk timestamps."""

    timestamps: List[int]


class SmardChunkMetadata(PydanticBaseModel):
    """Metadata included in a SMARD data chunk."""

    version: int
    created: int


class SmardChunk(PydanticBaseModel):
    """SMARD data chunk with millisecond timestamps and EUR/MWh values."""

    meta_data: SmardChunkMetadata
    series: List[tuple[int, Optional[float]]]


class ElecPriceSMARDCommonSettings(SettingsBaseModel):
    """Common settings for the direct SMARD electricity-price provider."""

    apply_fees: bool = Field(
        default=False,
        json_schema_extra={
            "description": (
                "Apply electricity fees as given by the ElecFee provider to the electricity prices. "
                "Electricity fees are added to the consumed energy prices."
            ),
        },
    )

    filter_id: int = Field(
        default=4169,
        gt=0,
        json_schema_extra={
            "description": "SMARD filter id for the German/Luxembourg day-ahead price.",
            "examples": [4169],
        },
    )
    region: str = Field(
        default="DE",
        min_length=2,
        json_schema_extra={
            "description": "SMARD market region used in the chart-data endpoint.",
            "examples": ["DE"],
        },
    )


class ElecPriceSMARD(ElecPriceEnergyCharts):
    """Fetch SMARD day-ahead prices and extend them with the seasonal EOS forecast.

    The provider uses the public SMARD chart-data endpoint directly. It reuses the
    Energy-Charts parsing and ETS pipeline after normalizing the response because both
    sources expose the same EUR/MWh day-ahead market-price concept.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the direct SMARD provider."""
        return "ElecPriceSMARD"

    @classmethod
    def _validate_index(cls, json_data: bytes) -> SmardIndex:
        """Validate a SMARD chunk index response."""
        try:
            return SmardIndex.model_validate_json(json_data)
        except ValidationError as exc:
            logger.error("SMARD index schema change: {}", exc)
            raise ValueError(f"SMARD index schema change: {exc}") from exc

    @classmethod
    def _validate_chunk(cls, json_data: bytes) -> SmardChunk:
        """Validate a SMARD price chunk response."""
        try:
            return SmardChunk.model_validate_json(json_data)
        except ValidationError as exc:
            logger.error("SMARD price schema change: {}", exc)
            raise ValueError(f"SMARD price schema change: {exc}") from exc

    @staticmethod
    def _get(url: str) -> bytes:
        """Request a SMARD JSON resource with bounded retries."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                response = requests.get(
                    url,
                    headers={"User-Agent": "Akkudoktor-EOS/SMARD price provider"},
                    timeout=(5, 30),
                )
                response.raise_for_status()
                return response.content
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                logger.warning("SMARD request attempt {}/3 failed for {}: {}", attempt, url, exc)
                if attempt < 3:
                    time.sleep(2 * attempt)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"SMARD request failed without an exception: {url}")

    def _apply_fees_enabled(self) -> bool:
        """Application of fees by apply_fees() enabled."""
        return self.config.elecprice.smard.apply_fees

    def _chunk_timestamps(
        self, index: SmardIndex, start_datetime: datetime, end_datetime: datetime
    ) -> list[int]:
        """Select all weekly chunks overlapping the requested datetime range."""
        start_ms = int(to_datetime(start_datetime).timestamp() * 1000)
        end_ms = int(to_datetime(end_datetime).timestamp() * 1000)
        timestamps = sorted(set(index.timestamps))
        selected: list[int] = []
        for position, chunk_start in enumerate(timestamps):
            next_start = timestamps[position + 1] if position + 1 < len(timestamps) else None
            overlaps_start = next_start is None or next_start > start_ms
            if chunk_start <= end_ms and overlaps_start:
                selected.append(chunk_start)
        return selected

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self, start_date: Optional[str] = None) -> EnergyChartsElecPrice:
        """Fetch and normalize quarter-hourly German/Luxembourg day-ahead prices from SMARD."""
        if not self.ems_start_datetime:
            raise ValueError(f"Start DateTime not set: {self.ems_start_datetime}")
        if start_date is None:
            start_datetime = self.ems_start_datetime - to_duration("35 days")
        else:
            start_datetime = to_datetime(
                start_date, in_timezone=self.config.general.timezone
            ).start_of("day")
        end_datetime = to_datetime(self.end_datetime).end_of("day")

        settings = self.config.elecprice.smard
        filter_id = settings.filter_id
        region = settings.region
        resolution = "quarterhour"
        index_url = f"{SMARD_BASE_URL}/{filter_id}/{region}/index_{resolution}.json"
        index = self._validate_index(self._get(index_url))
        chunk_timestamps = self._chunk_timestamps(index, start_datetime, end_datetime)
        if not chunk_timestamps:
            raise ValueError("SMARD index contains no price chunks for the requested period")

        values_by_timestamp: dict[int, float] = {}
        latest_created = 0
        for chunk_timestamp in chunk_timestamps:
            chunk_url = (
                f"{SMARD_BASE_URL}/{filter_id}/{region}/"
                f"{filter_id}_{region}_{resolution}_{chunk_timestamp}.json"
            )
            chunk = self._validate_chunk(self._get(chunk_url))
            latest_created = max(latest_created, chunk.meta_data.created)
            for timestamp_ms, price_eur_mwh in chunk.series:
                if price_eur_mwh is None:
                    continue
                if (
                    int(start_datetime.timestamp() * 1000)
                    <= timestamp_ms
                    <= int(end_datetime.timestamp() * 1000)
                ):
                    values_by_timestamp[timestamp_ms] = price_eur_mwh

        if not values_by_timestamp:
            raise ValueError("SMARD response contains no usable day-ahead prices")

        ordered_values = sorted(values_by_timestamp.items())
        self.update_datetime = to_datetime(
            latest_created / 1000, in_timezone=self.config.general.timezone
        )
        return EnergyChartsElecPrice(
            license_info="CC BY 4.0 Bundesnetzagentur | SMARD.de",
            unix_seconds=[timestamp_ms // 1000 for timestamp_ms, _ in ordered_values],
            price=[price for _, price in ordered_values],
            unit="EUR/MWh",
            deprecated=False,
        )
