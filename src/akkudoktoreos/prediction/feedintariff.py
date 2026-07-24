from typing import Optional

from pydantic import Field, computed_field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider
from akkudoktoreos.prediction.feedintariffenergycharts import (
    FeedInTariffEnergyChartsCommonSettings,
)
from akkudoktoreos.prediction.feedintarifffixed import FeedInTariffFixedCommonSettings
from akkudoktoreos.prediction.feedintariffimport import FeedInTariffImportCommonSettings


def feedintariff_provider_ids() -> list[str]:
    """Valid feedintariff provider ids."""
    try:
        prediction_eos = get_prediction()
    except Exception:
        # Prediction may not be initialized. Return static built-in provider ids.
        return [
            "FeedInTariffAkkudoktor",
            "FeedInTariffEnergyCharts",
            "FeedInTariffFixed",
            "FeedInTariffImport",
            "FeedInTariffTibber",
        ]

    return [
        provider.provider_id()
        for provider in prediction_eos.providers
        if isinstance(provider, FeedInTariffProvider)
    ]


class FeedInTariffCommonSettings(SettingsBaseModel):
    """Feed In Tariff Prediction Configuration."""

    provider: Optional[str] = Field(
        default=None,
        json_schema_extra={
            "description": "Feed in tariff provider id of provider to be used.",
            "examples": ["FeedInTariffFixed", "FeedInTarifImport"],
        },
    )

    feedintarifffixed: FeedInTariffFixedCommonSettings = Field(
        default_factory=FeedInTariffFixedCommonSettings,
        json_schema_extra={"description": "Fixed feed in tariff provider settings."},
    )

    feedintariffimport: FeedInTariffImportCommonSettings = Field(
        default_factory=FeedInTariffImportCommonSettings,
        json_schema_extra={"description": "Feed in tarif import provider settings."},
    )

    energycharts: FeedInTariffEnergyChartsCommonSettings = Field(
        default_factory=FeedInTariffEnergyChartsCommonSettings,
        json_schema_extra={"description": "EnergyCharts feed in tariff provider settings."},
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def providers(self) -> list[str]:
        """Available feed in tariff provider ids."""
        return feedintariff_provider_ids()

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in feedintariff_provider_ids():
            return value
        raise ValueError(
            f"Provider '{value}' is not a valid feed in tariff provider: {feedintariff_provider_ids()}."
        )
