"""Retrieves PV forecast data from the Forecast.Solar API.

Forecast.Solar (https://forecast.solar) is a free public PV forecast service
(no API key required; an optional key raises the rate/feature limits). Each
request covers a single plane:

    GET https://api.forecast.solar[/{api_key}]/estimate/{lat}/{lon}/{dec}/{az}/{kwp}

``result.watts`` contains instantaneous AC power, including non-aligned sunrise
and sunset samples. Integrate each plane's piecewise-linear power curve into
15-minute interval averages before summing planes. This is the interval-start
power representation consumed by EOS; conversion to slot Wh happens downstream.

Note on conventions:
    - Forecast.Solar azimuth is -180=N, -90=E, 0=S, 90=W, whereas EOS
      ``surface_azimuth`` is north=0, east=90, south=180, west=270. The provider
      converts via ``az = surface_azimuth - 180``.
    - Response timestamps are local wall-clock; ``message.info.timezone`` is used
      to resolve them to absolute instants before EOS resamples them.
"""

import math
import re
from typing import Any, Optional

import pendulum
import requests
from loguru import logger
from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.cache import cache_in_file
from akkudoktoreos.prediction.pvforecastabc import PVForecastProvider
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

FORECAST_SOLAR_BASE = "https://api.forecast.solar"

_TZ_SUFFIX = re.compile(r"([zZ]|[+-]\d\d:?\d\d)$")
_INTERVAL_SECONDS = 900


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
        settings = self.config.pvforecast.forecastsolar
        return settings.api_key if settings is not None else None

    def _to_utc_datetime(self, local_ts: Any, iana_tz: Optional[str]) -> DateTime:
        """Resolve a Forecast.Solar wall-clock timestamp to a timezone-aware datetime."""
        s = str(local_ts).strip()
        if _TZ_SUFFIX.search(s):
            return to_datetime(s)
        tz = iana_tz or str(self.config.general.timezone)
        dt = pendulum.parse(s, tz=tz)
        if not isinstance(dt, pendulum.DateTime):
            raise ValueError(f"Expected a datetime, got {local_ts!r}")
        return to_datetime(dt.isoformat())

    def _interval_power(self, watts: dict[str, Any], timezone: Optional[str]) -> dict[int, float]:
        """Integrate instantaneous samples without bridging missing daylight data.

        Forecast.Solar supplies at most hourly daylight samples. A larger gap,
        invalid power, or an unobserved day remains missing. Only an explicit
        zero at the first/last sample of a day establishes the adjacent night,
        up to that local day's midnight. Never extend into an unforecast day.
        UTC seconds preserve energy and both occurrences of a DST fall-back hour.
        """
        tz = timezone or str(self.config.general.timezone)
        days: dict[DateTime, dict[int, float]] = {}
        for ts, power in watts.items():
            try:
                date = self._to_utc_datetime(ts, timezone).in_timezone(tz)
            except (ValueError, TypeError) as exc:
                logger.warning(f"Forecast.Solar: skipping unparseable timestamp {ts!r}: {exc}")
                continue
            try:
                value = float(power)
            except (ValueError, TypeError):
                value = math.nan
            if not math.isfinite(value) or value < 0:
                value = math.nan
            days.setdefault(date.start_of("day"), {})[int(date.timestamp())] = value

        averages: dict[int, float] = {}
        for day, samples in sorted(days.items()):
            start = int(day.timestamp())
            end = int(day.add(days=1).timestamp())
            points = sorted(samples.items())
            segments: list[tuple[int, int, float, float]] = []
            if len(points) >= 2:
                if points[0][1] == 0:
                    segments.append((start, points[0][0], 0.0, 0.0))
                for (left, p_left), (right, p_right) in zip(points, points[1:]):
                    if right - left <= 3600 and math.isfinite(p_left) and math.isfinite(p_right):
                        segments.append((left, right, p_left, p_right))
                if points[-1][1] == 0:
                    segments.append((points[-1][0], end, 0.0, 0.0))

            for slot in range(start, end, _INTERVAL_SECONDS):
                energy = 0.0
                covered = 0
                for left, right, p_left, p_right in segments:
                    a = max(slot, left)
                    b = min(slot + _INTERVAL_SECONDS, right)
                    if b <= a:
                        continue
                    slope = (p_right - p_left) / (right - left)
                    # Exact trapezoidal integral of this linear segment, in W s.
                    energy += (p_left + slope * ((a + b) / 2 - left)) * (b - a)
                    covered += b - a
                averages[slot] = (
                    energy / _INTERVAL_SECONDS if covered == _INTERVAL_SECONDS else math.nan
                )
        return averages

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

        plane_power: list[dict[int, float]] = []
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
            timezone = (data.get("message", {}).get("info", {}) or {}).get("timezone")
            watts = (data.get("result", {}) or {}).get("watts", {}) or {}
            plane_power.append(self._interval_power(watts, timezone))

        # Align before adding: planes can have different sunrise/sunset timestamps.
        # Missing coverage on any plane must not become a valid partial plant forecast.
        stamps = sorted({stamp for power in plane_power for stamp in power})
        summed = {
            to_datetime(stamp, in_timezone="UTC").isoformat(): sum(
                power.get(stamp, math.nan) for power in plane_power
            )
            for stamp in stamps
        }

        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return {"timezone": "UTC", "watts": summed}

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update forecast data in the PVForecastDataRecord format."""
        if not self.enabled():
            logger.info("PVForecastForecastSolar is disabled, skipping update.")
            return

        body = self._request_forecast(force_update=force_update)  # type: ignore[call-arg]
        timezone = body.get("timezone")
        watts = body.get("watts", {})

        if watts:
            # Replace the refreshed forecast, including old irregular timestamps
            # and stale tails. Cadence inference also sees historical rows, so
            # invalidate legacy non-aligned forecasts once on upgrade. Subsequent
            # refreshes preserve the normalized forecast history.
            stored = await self.key_to_raw_series("pvforecast_ac_power", dropna=False)
            legacy = any(stamp.timestamp() % _INTERVAL_SECONDS for stamp in stored.index)
            start = min(self._to_utc_datetime(ts, timezone) for ts in watts)
            await self.delete_by_datetime(start_datetime=None if legacy else start)

        count = 0
        for ts, power_w in sorted(watts.items()):
            try:
                date = self._to_utc_datetime(ts, timezone)
            except Exception as e:  # noqa: BLE001 - skip unparseable rows
                logger.warning(f"Forecast.Solar: skipping unparseable timestamp {ts!r}: {e}")
                continue
            value = float(power_w)
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
