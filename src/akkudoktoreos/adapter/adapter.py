from typing import Literal, Optional, TypeAlias, Union

from pydantic import Field

from akkudoktoreos.adapter.adapterabc import AdapterContainer
from akkudoktoreos.adapter.homeassistant import (
    HomeAssistantAdapter,
    HomeAssistantAdapterCommonSettings,
)
from akkudoktoreos.adapter.nodered import NodeREDAdapter, NodeREDAdapterCommonSettings
from akkudoktoreos.config.configabc import SettingsBaseModel

# All the IDs of the adapter providers
AdapterProviders: TypeAlias = Literal["HomeAssistant", "NodeRED"]


class AdapterCommonSettings(SettingsBaseModel):
    """Adapter Configuration."""

    provider: Optional[Union[AdapterProviders, list[AdapterProviders]]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Adapter provider id(s) of provider(s) to be used [HomeAssistant, NodeRED, None]."
            ),
            "examples": ["HomeAssistant", ["HomeAssistant", "NodeRED"]],
        },
    )

    homeassistant: HomeAssistantAdapterCommonSettings = Field(
        default_factory=HomeAssistantAdapterCommonSettings,
        json_schema_extra={"description": "Home Assistant adapter settings."},
    )

    nodered: NodeREDAdapterCommonSettings = Field(
        default_factory=NodeREDAdapterCommonSettings,
        json_schema_extra={"description": "NodeRED adapter settings."},
    )


class Adapter(AdapterContainer):
    """Adapter container to manage multiple adapter providers.

    Attributes:
        providers (List[Union[PVForecastAkkudoktor, WeatherBrightSky, WeatherClearOutside]]):
            List of forecast provider instances, in the order they should be updated.
            Providers may depend on updates from others.
    """

    providers: list[
        Union[
            HomeAssistantAdapter,
            NodeREDAdapter,
        ]
    ] = Field(default_factory=list, json_schema_extra={"description": "List of adapter providers"})


# Initialize adapter providers, all are singletons.
homeassistant_adapter = HomeAssistantAdapter()
nodered_adapter = NodeREDAdapter()


def get_adapter() -> Adapter:
    """Gets the EOS adapter data."""
    # Initialize Adapter instance with providers in the required order
    # Care for provider sequence as providers may rely on others to be updated before.
    adapter = Adapter(
        providers=[
            homeassistant_adapter,
            nodered_adapter,
        ]
    )
    return adapter
