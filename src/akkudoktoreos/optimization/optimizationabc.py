"""Abstract and base classes for optimization."""

from pydantic import ConfigDict

from akkudoktoreos.core.coreabc import ConfigMixin, PredictionMixin
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class OptimizationBase(ConfigMixin, PredictionMixin, PydanticBaseModel):
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
