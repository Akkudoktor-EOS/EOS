"""Abstract and base classes for devices."""

from enum import Enum
from typing import Optional, Type

from pendulum import DateTime
from pydantic import Field, computed_field

from akkudoktoreos.core.coreabc import (
    ConfigMixin,
    DevicesMixin,
    EnergyManagementSystemMixin,
    PredictionMixin,
)
from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.core.pydantic import ParametersBaseModel
from akkudoktoreos.utils.datetimeutil import to_duration

logger = get_logger(__name__)


class DeviceParameters(ParametersBaseModel):
    device_id: str = Field(description="ID of device", examples="device1")
    hours: Optional[int] = Field(
        default=None,
        gt=0,
        description="Number of prediction hours. Defaults to global config prediction hours.",
        examples=[None],
    )


class DeviceOptimizeResult(ParametersBaseModel):
    device_id: str = Field(description="ID of device", examples=["device1"])
    hours: int = Field(gt=0, description="Number of hours in the simulation.", examples=[24])


class DeviceState(Enum):
    UNINITIALIZED = 0
    PREPARED = 1
    INITIALIZED = 2


class DevicesStartEndMixin(ConfigMixin, EnergyManagementSystemMixin):
    """A mixin to manage start, end datetimes for devices data.

    The starting datetime for devices data generation is provided by the energy management
    system. Device data cannot be computed if this value is `None`.
    """

    # Computed field for end_datetime and keep_datetime
    @computed_field  # type: ignore[prop-decorator]
    @property
    def end_datetime(self) -> Optional[DateTime]:
        """Compute the end datetime based on the `start_datetime` and `hours`.

        Ajusts the calculated end time if DST transitions occur within the prediction window.

        Returns:
            Optional[DateTime]: The calculated end datetime, or `None` if inputs are missing.
        """
        if self.ems.start_datetime and self.config.prediction.hours:
            end_datetime = self.ems.start_datetime + to_duration(
                f"{self.config.prediction.hours} hours"
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


class DeviceBase(DevicesStartEndMixin, PredictionMixin, DevicesMixin):
    """Base class for device simulations.

    Enables access to EOS configuration data (attribute `config`), EOS prediction data (attribute
    `prediction`) and EOS device registry (attribute `devices`).

    Behavior:
        - Several initialization phases (setup, post_setup):
            - setup: Initialize class attributes from DeviceParameters (pydantic input validation)
            - post_setup: Set connections between devices
        - NotImplemented:
            - hooks during optimization

    Notes:
        - This class is base to concrete devices like battery, inverter, etc. that are used in optimization.
        - Not a pydantic model for a low footprint during optimization.
    """

    def __init__(self, parameters: Optional[DeviceParameters] = None):
        self.device_id: str = "<invalid>"
        self.parameters: Optional[DeviceParameters] = None
        self.hours = -1
        if self.total_hours is not None:
            self.hours = self.total_hours

        self.initialized = DeviceState.UNINITIALIZED

        if parameters is not None:
            self.setup(parameters)

    def setup(self, parameters: DeviceParameters) -> None:
        if self.initialized != DeviceState.UNINITIALIZED:
            return

        self.parameters = parameters
        self.device_id = self.parameters.device_id

        if self.parameters.hours is not None:
            self.hours = self.parameters.hours
        if self.hours < 0:
            raise ValueError("hours is unset")

        self._setup()

        self.initialized = DeviceState.PREPARED

    def post_setup(self) -> None:
        if self.initialized.value >= DeviceState.INITIALIZED.value:
            return

        self._post_setup()
        self.initialized = DeviceState.INITIALIZED

    def _setup(self) -> None:
        """Implement custom setup in derived device classes."""
        pass

    def _post_setup(self) -> None:
        """Implement custom setup in derived device classes that is run when all devices are initialized."""
        pass


class DevicesBase(DevicesStartEndMixin, PredictionMixin):
    """Base class for handling device data.

    Enables access to EOS configuration data (attribute `config`) and EOS prediction data (attribute
    `prediction`).
    """

    def __init__(self) -> None:
        super().__init__()
        self.devices: dict[str, "DeviceBase"] = dict()

    def get_device_by_id(self, device_id: str) -> Optional["DeviceBase"]:
        return self.devices.get(device_id)

    def add_device(self, device: Optional["DeviceBase"]) -> None:
        if device is None:
            return
        assert device.device_id not in self.devices, f"{device.device_id} already registered"
        self.devices[device.device_id] = device

    def remove_device(self, device: Type["DeviceBase"] | str) -> bool:
        if isinstance(device, DeviceBase):
            device = device.device_id
        return self.devices.pop(device, None) is not None  # type: ignore[arg-type]

    def reset(self) -> None:
        self.devices = dict()
