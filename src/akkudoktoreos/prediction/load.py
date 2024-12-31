"""Load forecast module for load predictions."""

from typing import Optional

from pydantic import Field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class LoadCommonSettings(SettingsBaseModel):
    load_provider: Optional[str] = Field(
        default=None, description="Load provider id of provider to be used."
    )
