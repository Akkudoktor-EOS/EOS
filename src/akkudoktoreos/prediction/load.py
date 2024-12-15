"""Load forecast module for load predictions."""

from typing import Optional, Set

from pydantic import Field, computed_field

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class LoadCommonSettings(SettingsBaseModel):
    # Load 0
    load0_provider: Optional[str] = Field(
        default=None, description="Load provider id of provider to be used."
    )
    load0_name: Optional[str] = Field(default=None, description="Name of the load source.")

    # Load 1
    load1_provider: Optional[str] = Field(
        default=None, description="Load provider id of provider to be used."
    )
    load1_name: Optional[str] = Field(default=None, description="Name of the load source.")

    # Load 2
    load2_provider: Optional[str] = Field(
        default=None, description="Load provider id of provider to be used."
    )
    load2_name: Optional[str] = Field(default=None, description="Name of the load source.")

    # Load 3
    load3_provider: Optional[str] = Field(
        default=None, description="Load provider id of provider to be used."
    )
    load3_name: Optional[str] = Field(default=None, description="Name of the load source.")

    # Load 4
    load4_provider: Optional[str] = Field(
        default=None, description="Load provider id of provider to be used."
    )
    load4_name: Optional[str] = Field(default=None, description="Name of the load source.")

    # Computed fields
    @computed_field  # type: ignore[prop-decorator]
    @property
    def load_count(self) -> int:
        """Maximum number of loads."""
        return 5

    @computed_field  # type: ignore[prop-decorator]
    @property
    def load_providers(self) -> Set[str]:
        """Load providers."""
        providers = []
        for i in range(self.load_count):
            load_provider_attr = f"load{i}_provider"
            value = getattr(self, load_provider_attr)
            if value:
                providers.append(value)
        return set(providers)
