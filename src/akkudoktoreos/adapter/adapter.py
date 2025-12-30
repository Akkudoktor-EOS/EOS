from typing import TYPE_CHECKING, Optional, Union

from pydantic import Field, computed_field, field_validator

from akkudoktoreos.adapter.adapterabc import AdapterContainer
from akkudoktoreos.adapter.homeassistant import (
    HomeAssistantAdapter,
    HomeAssistantAdapterCommonSettings,
)
from akkudoktoreos.adapter.nodered import NodeREDAdapter, NodeREDAdapterCommonSettings
from akkudoktoreos.config.configabc import SettingsBaseModel

if TYPE_CHECKING:
    adapter_providers: list[str]


class AdapterCommonSettings(SettingsBaseModel):
    """Adapter Configuration."""

    provider: Optional[list[str]] = Field(
        default=None,
        json_schema_extra={
            "description": ("List of adapter provider id(s) of provider(s) to be used."),
            "examples": [["HomeAssistant"], ["HomeAssistant", "NodeRED"]],
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def providers(self) -> list[str]:
        """Available electricity price provider ids."""
        return adapter_providers

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return value
        for provider_id in value:
            if provider_id not in adapter_providers:
                raise ValueError(
                    f"Provider '{value}' is not a valid adapter provider: {adapter_providers}."
                )
        return value


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


# Valid adapter providers
adapter_providers = [provider.provider_id() for provider in get_adapter().providers]
