"""Retrieves pvforecast data from a Home Assistant entity attribute."""

import os
from typing import Any, Literal, Optional

import requests
from loguru import logger
from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.pvforecastabc import PVForecastProvider
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

# Supervisor API endpoint (injected automatically when running as a Home Assistant add-on)
CORE_API = "http://supervisor/core/api"


class PVForecastHomeAssistantCommonSettings(SettingsBaseModel):
    """Common settings for pvforecast data from a Home Assistant entity."""

    entity_id: str = Field(
        default="sensor.pv_forecast",
        json_schema_extra={
            "description": "Home Assistant entity providing the PV forecast.",
            "examples": ["sensor.pv1_power_now"],
        },
    )
    attribute: str = Field(
        default="forecast",
        json_schema_extra={
            "description": "Entity attribute holding the forecast list.",
            "examples": ["forecast"],
        },
    )
    datetime_key: str = Field(
        default="datetime",
        json_schema_extra={
            "description": "Key for the timestamp in each forecast entry.",
            "examples": ["datetime"],
        },
    )
    value_key: str = Field(
        default="watts",
        json_schema_extra={
            "description": "Key for the AC power value in each forecast entry.",
            "examples": ["watts"],
        },
    )
    value_unit: Literal["W", "kW"] = Field(
        default="W",
        json_schema_extra={
            "description": "Unit of the forecast value. Converted to W internally.",
            "examples": ["W", "kW"],
        },
    )
    base_url: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Base URL of the Home Assistant instance. Only required when EOS is not "
                "running as a Home Assistant add-on (no SUPERVISOR_TOKEN available)."
            ),
            "examples": ["http://homeassistant.local:8123"],
        },
    )
    token: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Long-lived access token for the Home Assistant instance. Only required "
                "when EOS is not running as a Home Assistant add-on."
            ),
            "examples": [None],
        },
    )


class PVForecastHomeAssistant(PVForecastProvider):
    """Fetch and process PV forecast data from a Home Assistant entity attribute.

    Reads a list of ``{<datetime_key>: ..., <value_key>: ...}`` entries from the
    configured entity attribute (matching the ``forecast`` attribute shape exposed
    by common Home Assistant PV forecast integrations, e.g. Helios Forecast or
    Solcast) and maps it to ``pvforecast_ac_power``.
    """

    @classmethod
    def provider_id(cls) -> str:
        """Return the unique identifier for the PVForecastHomeAssistant provider."""
        return "PVForecastHomeAssistant"

    def _api_base_and_token(self) -> tuple[str, str]:
        settings = self.config.pvforecast.homeassistant
        if settings.base_url:
            base_url = settings.base_url.rstrip("/") + "/api"
            token = settings.token
        else:
            base_url = CORE_API
            token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise RuntimeError(
                "No Home Assistant access token available. Set "
                "'pvforecast.homeassistant.token' (and 'base_url') when EOS is not "
                "running as a Home Assistant add-on."
            )
        return base_url, token

    def _request_entity_state(self) -> dict[str, Any]:
        settings = self.config.pvforecast.homeassistant
        base_url, token = self._api_base_and_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{base_url}/states/{settings.entity_id}"
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch pvforecast entity '{settings.entity_id}': {e}")
            raise RuntimeError(
                f"Failed to fetch pvforecast entity '{settings.entity_id}' from Home Assistant"
            ) from e
        return response.json()

    async def _update_data(self, force_update: Optional[bool] = False) -> None:
        """Update forecast data in the PVForecastDataRecord format."""
        settings = self.config.pvforecast.homeassistant
        data = self._request_entity_state()
        attributes = data.get("attributes", {})
        forecast = attributes.get(settings.attribute)
        if not forecast:
            error_msg = (
                f"Entity '{settings.entity_id}' has no '{settings.attribute}' attribute "
                "or it is empty."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        factor = 1000.0 if settings.value_unit == "kW" else 1.0
        parsed: list[tuple[DateTime, float]] = []
        for entry in forecast:
            try:
                dt = to_datetime(
                    entry[settings.datetime_key], in_timezone=self.config.general.timezone
                )
                watts = round(float(entry[settings.value_key]) * factor, 2)
            except (KeyError, TypeError, ValueError) as e:
                logger.error(f"Skipping malformed forecast entry {entry!r}: {e}")
                continue
            parsed.append((dt, watts))

        if not parsed:
            error_msg = (
                f"Entity '{settings.entity_id}' attribute '{settings.attribute}' contained no "
                "usable forecast entries."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Clear the whole active forecast window first, so a response that is shorter than a
        # previous one (or has gaps) can't leave stale pvforecast_ac_power values behind at
        # timestamps the new data no longer covers.
        start_date = self.ems_start_datetime.start_of("day")
        end_date = self.ems_start_datetime.add(hours=self.config.prediction.hours)
        await self.key_delete_by_datetime(
            "pvforecast_ac_power", start_datetime=start_date, end_datetime=end_date
        )

        for dt, watts in parsed:
            await self.update_value(dt, {"pvforecast_ac_power": watts})

        logger.debug(
            f"Updated pvforecast_ac_power with {len(parsed)} entries from '{settings.entity_id}'."
        )
        self.update_datetime = to_datetime(in_timezone=self.config.general.timezone)
