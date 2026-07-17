"""Retrieves PV forecast data from the Forecast.Solar API.

Forecast.Solar (https://forecast.solar) is a free public PV forecast service
(no API key required; an optional key raises the rate/feature limits). Each
request covers a single plane:

    GET https://api.forecast.solar[/{api_key}]/estimate/{lat}/{lon}/{dec}/{az}/{kwp}

``result.watts`` is the instantaneous AC power per timestamp — exactly what the
optimizer consumes as ``pvforecast_ac_power``. EOS plants with several roof
planes issue one request per plane and the instantaneous powers are summed per
timestamp.

Note on conventions:
    - Forecast.Solar azimuth is -180=N, -90=E, 0=S, 90=W, whereas EOS
      ``surface_azimuth`` is north=0, east=90, south=180, west=270. The provider
      converts via ``az = surface_azimuth - 180``.
    - Response timestamps are local wall-clock; ``message.info.timezone`` is used
      to resolve them to absolute instants before EOS resamples them.
"""

import re
from typing import Any, Optional

import pendulum
import requests
from loguru import logger
from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.prediction.pvforecastabc import PVForecastProvider
from akkudoktoreos.utils.datetimeutil import to_datetime

FORECAST_SOLAR_BASE = "https://api.forecast.solar"

_TZ_SUFFIX = re.compile(r"([zZ]|[+-]\d\d:?\d\d)$")


class PVForecastForecastSolarCommonSettings(SettingsBaseModel):
    """Common settings for the Forecast.Solar PV forecast provider."""

    api_key: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Forecast.Solar API key. Optional — the public endpoint works "
                "without a key (lower rate limit)."
            ),
            "examples": [None, "your-forecast-solar-key"],
        },
    )


class PVForecastForecastSolar(PVForecastProvider):
    """Fetch and process PV forecast data from the Forecast.Solar API."""

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the PV-Forecast-Provider."""
        return "PVForecastForecastSolar"

    @property
    def _api_key(self) -> Optional[str]:
        settings = self.config.pvforecast.provider_settings.PVForecastForecastSolar
        return settings.api_key if settings is not None else None

    def _to_utc_datetime(self, local_ts: Any, iana_tz: Optional[str]) -> Any:
        """Resolve a Forecast.Solar wall-clock timestamp to a timezone-aware datetime."""
        s = str(local_ts).strip()
        if _TZ_SUFFIX.search(s):
            return to_datetime(s)
        tz = iana_tz or str(self.config.general.timezone)
        dt = pendulum.parse(s, tz=tz)
        return to_datetime(dt.isoformat())

    def _plane_url(self, plane: Any) -> str:
        """Build the single-plane estimate URL for the given plane configuration."""
        latitude = self.config.general.latitude
        longitude = self.config.general.longitude
        if latitude is None or longitude is None:
            raise ValueError("PVForecastForecastSolar needs general.latitude/longitude")
        tilt = getattr(plane, "surface_tilt", None)
        azimuth = getattr(plane, "surface_azimuth", None)
        peakpower = getattr(plane, "peakpower", None)
        if tilt is None or azimuth is None or peakpower is None:
            raise ValueError(
                "PVForecastForecastSolar needs surface_tilt, surface_azimuth and "
                "peakpower on each pvforecast.planes entry"
            )
        # EOS azimuth (north=0..south=180) -> Forecast.Solar (north=-180..south=0).
        fs_az = float(azimuth) - 180.0
        base = FORECAST_SOLAR_BASE
        api_key = self._api_key
        if api_key:
            base = f"{base}/{api_key}"
        return f"{base}/estimate/{latitude}/{longitude}/{float(tilt)}/{fs_az}/{float(peakpower)}"

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> dict:
        """Fetch and aggregate the Forecast.Solar estimate across all configured planes."""
        planes = self.config.pvforecast.planes or []
        if not planes:
            raise ValueError("PVForecastForecastSolar needs at least one pvforecast.planes entry")

        summed: dict[str, float] = {}
        timezone: Optional[str] = None
        for plane in planes:
            url = self._plane_url(plane)
            logger.debug(f"Requesting Forecast.Solar estimate: {url}")
            try:
                response = requests.get(url, headers={"Accept": "application/json"}, timeout=30)
                response.raise_for_status()
            except requests.RequestException as e:
                logger.error(f"Failed to fetch pvforecast from Forecast.Solar: {e}")
                raise RuntimeError("Failed to fetch pvforecast from Forecast.Solar API") from e
            data = response.json()
            if timezone is None:
                timezone = (data.get("message", {}).get("info", {}) or {}).get("timezone")
            watts = (data.get("result", {}) or {}).get("watts", {}) or {}
            for ts, power in watts.items():
                try:
                    summed[ts] = summed.get(ts, 0.0) + float(power)
                except (TypeError, ValueError):
                    continue

        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return {"timezone": timezone, "watts": summed}

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update forecast data in the PVForecastDataRecord format."""
        if not self.enabled():
            logger.info("PVForecastForecastSolar is disabled, skipping update.")
            return

        body = self._request_forecast(force_update=force_update)  # type: ignore[call-arg]
        timezone = body.get("timezone")
        watts = body.get("watts", {})

        count = 0
        for ts, power_w in sorted(watts.items()):
            try:
                date = self._to_utc_datetime(ts, timezone)
            except Exception as e:  # noqa: BLE001 - skip unparseable rows
                logger.warning(f"Forecast.Solar: skipping unparseable timestamp {ts!r}: {e}")
                continue
            value = round(float(power_w), 1)
            await self.update_value(
                date,
                {"pvforecast_ac_power": value, "pvforecast_dc_power": value},
            )
            count += 1

        logger.debug(f"Updated pvforecast from Forecast.Solar with {count} entries.")
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)


# Example usage
if __name__ == "__main__":
    import asyncio

    pv = PVForecastForecastSolar()
    asyncio.run(pv._update_data())
