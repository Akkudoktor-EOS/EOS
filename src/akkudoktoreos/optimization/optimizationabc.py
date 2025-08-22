"""Abstract and base classes for optimization."""

from pydantic import ConfigDict

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    EnergyManagementSystemMixin,
    PredictionMixin,
)


class OptimizationBase(ConfigMixin, PredictionMixin, EnergyManagementSystemMixin):
    """Base class for handling optimization data.

    Enables access to EOS configuration data (attribute `config`) and EOS prediction data (attribute
    `prediction`).

    Note:
        Validation on assignment of the Pydantic model is disabled to speed up optimization runs.
    """

    # Disable validation on assignment to speed up optimization runs.
    model_config = ConfigDict(
        validate_assignment=False,
    )
