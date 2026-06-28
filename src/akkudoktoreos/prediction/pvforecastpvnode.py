"""Retrieves PV forecast data from the pvnode.com V2 API.

pvnode.com delivers native 15-minute PV power forecasts. Two request modes,
decided by configuration:

* ``site_id`` set  -> ``GET /v2/forecast/{site_id}`` — a saved (and possibly
  calibrated) site managed in the pvnode web app. This is the operator's primary
  path: register the plant once on pvnode.com, then enter the site id + API key.
* ``site_id`` empty -> ``POST /v2/forecast/inline`` — geometry is sent inline from
  the configured ``pvforecast.planes`` (works without any web-app setup).

V2 response timestamps are SITE-LOCAL wall-clock (no offset) accompanied by an
IANA ``timezone`` field. We resolve them to absolute instants here so the rest of
EOS keeps working in its own timezone. ``pv_power`` is nullable (e.g. at night) —
null is treated as 0 W so the optimizer's linear resampling does not interpolate
phantom production across the night.

Notes:
    - Requires ``pvforecast.provider_settings.PVForecastPVNode.api_key`` (Bearer auth).
    - API: https://api.pvnode.com/v2  (15-minute resolution).
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

PVNODE_BASE = "https://api.pvnode.com/v2"

_TZ_SUFFIX = re.compile(r"([zZ]|[+-]\d\d:?\d\d)$")


class PVForecastPVNodeCommonSettings(SettingsBaseModel):
    """Common settings for the pvnode.com PV forecast provider."""

    api_key: str = Field(
        default="",
        json_schema_extra={
            "description": "pvnode.com API key (Bearer auth). Required.",
            "examples": ["pvn_live_xxxxxxxxxxxxxxxx"],
        },
    )
    site_id: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "pvnode.com site id of the saved plant ('Anlagen-ID'). When set, the "
                "saved (possibly calibrated) site is used. Leave empty to send the "
                "configured pvforecast.planes inline instead."
            ),
            "examples": ["abcd-1234"],
        },
    )
    forecast_days: int = Field(
        default=2,
        ge=1,
        le=7,
        json_schema_extra={
            "description": "Forecast horizon in days (1-7, capped by the pvnode plan).",
            "examples": [2],
        },
    )


class PVForecastPVNode(PVForecastProvider):
    """Fetch and process PV forecast data from the pvnode.com V2 API."""

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the PV-Forecast-Provider."""
        return "PVForecastPVNode"

    @property
    def _settings(self) -> PVForecastPVNodeCommonSettings:
        settings = self.config.pvforecast.provider_settings.PVForecastPVNode
        if settings is None:
            settings = PVForecastPVNodeCommonSettings()
        return settings

    def _to_utc_datetime(self, local_ts: Any, iana_tz: Optional[str]) -> Any:
        """Resolve a pvnode V2 wall-clock timestamp to a timezone-aware datetime.

        V2 timestamps are local wall-clock without offset (e.g. "2026-06-22T14:00:00")
        plus a response-level IANA ``timezone``. If the string already carries an
        explicit offset or 'Z' it is trusted as-is.
        """
        s = str(local_ts).strip()
        if _TZ_SUFFIX.search(s):
            # Already absolute (offset or Z present) — parse as-is.
            return to_datetime(s)
        tz = iana_tz or str(self.config.general.timezone)
        # Interpret the naive wall-clock string AS local time in tz, then resolve.
        dt = pendulum.parse(s, tz=tz)
        return to_datetime(dt.isoformat())

    def _extract_values(self, body: Any) -> list[tuple[Any, float]]:
        """Extract (datetime, power_w) rows from a pvnode V2 response body.

        Canonical shape: ``{"timezone": ..., "values": [{"timestamp", "pv_power"}, ...]}``.
        Tolerant of edge/legacy shapes (mirrors the production DVhub client).
        """
        tz: Optional[str] = None
        arr: Any = None
        if isinstance(body, list):
            arr = body
        elif isinstance(body, dict):
            tz = body.get("timezone") if isinstance(body.get("timezone"), str) else None
            for key in ("values", "forecasts", "data", "forecast"):
                if isinstance(body.get(key), list):
                    arr = body[key]
                    break
        if not isinstance(arr, list):
            return []

        rows: list[tuple[Any, float]] = []
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            ts = (
                entry.get("timestamp")
                or entry.get("time")
                or entry.get("ts")
                or entry.get("ts_utc")
                or entry.get("datetime")
            )
            if ts is None:
                continue
            # pv_power is nullable (night) -> treat missing as 0 W, not a gap, so the
            # optimizer's linear resampling does not interpolate across the night.
            raw_power = entry.get("pv_power")
            if raw_power is None:
                raw_power = entry.get("power_w")
            if raw_power is None:
                raw_power = entry.get("power")
            if raw_power is None:
                raw_power = entry.get("watts")
            power = 0.0 if raw_power is None else float(raw_power)
            try:
                date = self._to_utc_datetime(ts, tz)
            except Exception as e:  # noqa: BLE001 - skip unparseable rows
                logger.warning(f"pvnode: skipping unparseable timestamp {ts!r}: {e}")
                continue
            rows.append((date, round(power, 1)))
        return rows

    @cache_in_file(with_ttl="1 hour")
    def _request_forecast(self) -> Any:
        """Fetch the PV forecast from pvnode.com (saved site or inline planes)."""
        settings = self._settings
        api_key = settings.api_key
        if not api_key:
            raise ValueError("PVForecastPVNode requires pvforecast...PVForecastPVNode.api_key")

        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        params = {"forecast_days": str(settings.forecast_days)}
        site_id = (settings.site_id or "").strip()

        try:
            if site_id:
                url = f"{PVNODE_BASE}/forecast/{requests.utils.quote(site_id, safe='')}"
                response = requests.get(url, headers=headers, params=params, timeout=30)
            else:
                body = self._inline_body()
                url = f"{PVNODE_BASE}/forecast/inline"
                headers["Content-Type"] = "application/json"
                response = requests.post(url, headers=headers, params=params, json=body, timeout=30)
            logger.debug(f"Requesting pvnode forecast: {url}")
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch pvforecast from pvnode: {e}")
            raise RuntimeError("Failed to fetch pvforecast from pvnode API") from e

        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
        return response.json()

    def _inline_body(self) -> dict:
        """Build the inline-mode request body from latitude/longitude + planes."""
        latitude = self.config.general.latitude
        longitude = self.config.general.longitude
        if latitude is None or longitude is None:
            raise ValueError(
                "PVForecastPVNode inline mode needs general.latitude/longitude "
                "(or set pvforecast...PVForecastPVNode.site_id)"
            )
        planes = self.config.pvforecast.planes or []
        strings = []
        for plane in planes:
            tilt = getattr(plane, "surface_tilt", None)
            azimuth = getattr(plane, "surface_azimuth", None)
            peakpower = getattr(plane, "peakpower", None)
            if peakpower is None or tilt is None or azimuth is None:
                continue
            strings.append(
                {
                    "slope": float(tilt),
                    # pvnode V2 azimuth convention (0=N, 90=E, 180=S, 270=W) matches
                    # EOS surface_azimuth, so it is forwarded unchanged.
                    "orientation": float(azimuth),
                    "power_kw": float(peakpower),
                }
            )
        if not strings:
            raise ValueError(
                "PVForecastPVNode inline mode needs at least one pvforecast.planes "
                "entry with peakpower, surface_tilt and surface_azimuth"
            )
        return {"latitude": float(latitude), "longitude": float(longitude), "strings": strings}

    def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update forecast data in the PVForecastDataRecord format."""
        if not self.enabled():
            logger.info("PVForecastPVNode is disabled, skipping update.")
            return

        body = self._request_forecast(force_update=force_update)  # type: ignore[call-arg]
        rows = self._extract_values(body)

        for date, power_w in rows:
            # pvnode returns the plant's expected output power; feed it as AC power
            # (the key the optimizer reads) and mirror it to DC for reporting.
            self.update_value(
                date,
                {"pvforecast_ac_power": power_w, "pvforecast_dc_power": power_w},
            )

        logger.debug(f"Updated pvforecast from pvnode with {len(rows)} entries.")
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)


# Example usage
if __name__ == "__main__":
    pv = PVForecastPVNode()
    pv._update_data()
