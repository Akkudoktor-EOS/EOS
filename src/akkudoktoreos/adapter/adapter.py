from typing import Optional, Union

from pydantic import Field, computed_field, field_validator

from akkudoktoreos.adapter.adapterabc import AdapterContainer
from akkudoktoreos.adapter.homeassistant import (
    HomeAssistantAdapter,
    HomeAssistantAdapterCommonSettings,
)
from akkudoktoreos.adapter.nodered import NodeREDAdapter, NodeREDAdapterCommonSettings
from akkudoktoreos.config.configabc import SettingsBaseModel


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
        """Available adapter provider ids."""
        adapter_provider_ids = [provider.provider_id() for provider in adapter_providers()]
        return adapter_provider_ids

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return value
        adapter_provider_ids = [provider.provider_id() for provider in adapter_providers()]
        for provider_id in value:
            if provider_id not in adapter_provider_ids:
                raise ValueError(
                    f"Provider '{value}' is not a valid adapter provider: {adapter_provider_ids}."
                )
        return value


# Initialize adapter providers, all are singletons.
homeassistant_adapter = HomeAssistantAdapter()
nodered_adapter = NodeREDAdapter()


def adapter_providers() -> list[Union["HomeAssistantAdapter", "NodeREDAdapter"]]:
    """Return list of adapter providers."""
    global homeassistant_adapter, nodered_adapter

    return [
        homeassistant_adapter,
        nodered_adapter,
    ]


class Adapter(AdapterContainer):
    """Adapter container to manage multiple adapter providers."""

    providers: list[
        Union[
            HomeAssistantAdapter,
            NodeREDAdapter,
        ]
    ] = Field(
        default_factory=adapter_providers,
        json_schema_extra={"description": "List of adapter providers"},
    )
