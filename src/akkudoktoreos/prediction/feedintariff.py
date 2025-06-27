from typing import Optional, Union

from pydantic import Field, field_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.prediction.feedintariffabc import FeedInTariffProvider
from akkudoktoreos.prediction.feedintarifffixed import FeedInTariffFixedCommonSettings
from akkudoktoreos.prediction.feedintariffimport import FeedInTariffImportCommonSettings
from akkudoktoreos.prediction.prediction import get_prediction

prediction_eos = get_prediction()

# Valid feedintariff providers
feedintariff_providers = [
    provider.provider_id()
    for provider in prediction_eos.providers
    if isinstance(provider, FeedInTariffProvider)
]


class FeedInTariffCommonSettings(SettingsBaseModel):
    """Electricity Price Prediction Configuration."""

    provider: Optional[str] = Field(
        default=None,
        description="Feed in tariff provider id of provider to be used.",
        examples=["FeedInTariffFixed", "FeedInTarifImport"],
    )

    provider_settings: Optional[
        Union[FeedInTariffFixedCommonSettings, FeedInTariffImportCommonSettings]
    ] = Field(default=None, description="Provider settings", examples=[None])

    # Validators
    @field_validator("provider", mode="after")
    @classmethod
    def validate_provider(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value in feedintariff_providers:
            return value
        raise ValueError(
            f"Provider '{value}' is not a valid feed in tariff provider: {feedintariff_providers}."
        )
