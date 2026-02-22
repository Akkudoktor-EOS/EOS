from typing import Optional

from pydantic import Field, computed_field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider
from akkudoktoreos.prediction.feedintarifffixed import FeedInTariffFixedCommonSettings
from akkudoktoreos.prediction.feedintariffimport import FeedInTariffImportCommonSettings


def elecprice_provider_ids() -> list[str]:
    """Valid feedintariff provider ids."""
    try:
        prediction_eos = get_prediction()
    except:
        # Prediction may not be initialized
        # Return at least provider used in example
        return ["FeedInTariffFixed", "FeedInTarifImport"]

    return [
        provider.provider_id()
        for provider in prediction_eos.providers
        if isinstance(provider, FeedInTariffProvider)
    ]


class FeedInTariffCommonProviderSettings(SettingsBaseModel):
    """Feed In Tariff Prediction Provider Configuration."""

    FeedInTariffFixed: Optional[FeedInTariffFixedCommonSettings] = Field(
        default=None,
        json_schema_extra={"description": "FeedInTariffFixed settings", "examples": [None]},
    )
    FeedInTariffImport: Optional[FeedInTariffImportCommonSettings] = Field(
        default=None,
        json_schema_extra={"description": "FeedInTariffImport settings", "examples": [None]},
    )


class FeedInTariffCommonSettings(SettingsBaseModel):
    """Feed In Tariff Prediction Configuration."""

    provider: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Feed in tariff provider id of provider to be used.",
            "examples": ["FeedInTariffFixed", "FeedInTarifImport"],
        },
    )

    provider_settings: FeedInTariffCommonProviderSettings = Field(
        default_factory=FeedInTariffCommonProviderSettings,
        json_schema_extra={
            "description": "Provider settings",
            "examples": [
                # Example 1: Empty/default settings (all providers None)
                {
                    "FeedInTariffFixed": None,
                    "FeedInTariffImport": None,
                },
            ],
        },
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def providers(self) -> list[str]:
        """Available feed in tariff provider ids."""
        return elecprice_provider_ids()

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in elecprice_provider_ids():
            return value
        raise ValueError(
            f"Provider '{value}' is not a valid feed in tariff provider: {elecprice_provider_ids()}."
        )
