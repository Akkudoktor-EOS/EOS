"""Load forecast module for load predictions."""

from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.logging import get_logger

logger = get_logger(__name__)


class LoadCommonSettings(SettingsBaseModel):
    """Common settings for loaod forecast providers."""

    load_provider: Optional[str] = Field(
        default=None, description="Load provider id of provider to be used."
    )
