"""Load forecast module for load predictions."""

from typing import Optional, Union

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.prediction.loadakkudoktor import LoadAkkudoktorCommonSettings
from akkudoktoreos.prediction.loadimport import LoadImportCommonSettings

logger = get_logger(__name__)


class LoadCommonSettings(SettingsBaseModel):
    """Common settings for loaod forecast providers."""

    load_provider: Optional[str] = Field(
        default=None, description="Load provider id of provider to be used."
    )

    provider_settings: Optional[Union[LoadAkkudoktorCommonSettings, LoadImportCommonSettings]] = (
        None
    )
