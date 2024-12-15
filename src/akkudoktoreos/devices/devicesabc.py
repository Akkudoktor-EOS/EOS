"""Abstract and base classes for devices."""

from typing import Optional

from pendulum import DateTime
from pydantic import ConfigDict, computed_field

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    EnergyManagementSystemMixin,
    PredictionMixin,
)
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.utils.datetimeutil import to_duration
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class DevicesStartEndMixin(ConfigMixin, EnergyManagementSystemMixin):
    """A mixin to manage start, end datetimes for devices data.

    The starting datetime for devices data generation is provided by the energy management
    system. Device data cannot be computed if this value is `None`.
    """

    # Computed field for end_datetime and keep_datetime
    @computed_field  # type: ignore[prop-decorator]
    @property
    def end_datetime(self) -> Optional[DateTime]:
        """Compute the end datetime based on the `start_datetime` and `prediction_hours`.

        Ajusts the calculated end time if DST transitions occur within the prediction window.

        Returns:
            Optional[DateTime]: The calculated end datetime, or `None` if inputs are missing.
        """
        if self.ems.start_datetime and self.config.prediction_hours:
            end_datetime = self.ems.start_datetime + to_duration(
                f"{self.config.prediction_hours} hours"
            )
            dst_change = end_datetime.offset_hours - self.ems.start_datetime.offset_hours
            logger.debug(
                f"Pre: {self.ems.start_datetime}..{end_datetime}: DST change: {dst_change}"
            )
            if dst_change < 0:
                end_datetime = end_datetime + to_duration(f"{abs(int(dst_change))} hours")
            elif dst_change > 0:
                end_datetime = end_datetime - to_duration(f"{abs(int(dst_change))} hours")
            logger.debug(
                f"Pst: {self.ems.start_datetime}..{end_datetime}: DST change: {dst_change}"
            )
            return end_datetime
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_hours(self) -> Optional[int]:
        """Compute the hours from `start_datetime` to `end_datetime`.

        Returns:
            Optional[pendulum.period]: The duration hours, or `None` if either datetime is unavailable.
        """
        end_dt = self.end_datetime
        if end_dt is None:
            return None
        duration = end_dt - self.ems.start_datetime
        return int(duration.total_hours())


class DeviceBase(DevicesStartEndMixin, PredictionMixin):
    """Base class for device simulations.

    Enables access to EOS configuration data (attribute `config`) and EOS prediction data (attribute
    `prediction`).

    Note:
        Validation on assignment of the Pydantic model is disabled to speed up simulation runs.
    """

    # Disable validation on assignment to speed up simulation runs.
    model_config = ConfigDict(
        validate_assignment=False,
    )


class DevicesBase(DevicesStartEndMixin, PredictionMixin, PydanticBaseModel):
    """Base class for handling device data.

    Enables access to EOS configuration data (attribute `config`) and EOS prediction data (attribute
    `prediction`).

    Note:
        Validation on assignment of the Pydantic model is disabled to speed up simulation runs.
    """

    # Disable validation on assignment to speed up simulation runs.
    model_config = ConfigDict(
        validate_assignment=False,
    )
