from typing import Optional, Tuple

from pydantic import BaseModel, Field

from akkudoktoreos.devices.battery import Battery
from akkudoktoreos.devices.devicesabc import DeviceBase
from akkudoktoreos.utils.logutil import get_logger

logger = get_logger(__name__)


class InverterParameters(BaseModel):
    max_power_wh: float = Field(default=10000, gt=0)


class Inverter(DeviceBase):
    def __init__(
        self,
        parameters: Optional[InverterParameters] = None,
        akku: Optional[Battery] = None,
        provider_id: Optional[str] = None,
    ):
        # Configuration initialisation
        self.provider_id = provider_id
        self.prefix = "<invalid>"
        if self.provider_id == "GenericInverter":
            self.prefix = "inverter"
        # Parameter initialisiation
        self.parameters = parameters
        if akku is None:
            # For the moment raise exception
            # TODO: Make akku configurable by config
            error_msg = "Battery for PV inverter is mandatory."
            logger.error(error_msg)
            raise NotImplementedError(error_msg)
        self.akku = akku  # Connection to a battery object

        self.initialised = False
        # Run setup if parameters are given, otherwise setup() has to be called later when the config is initialised.
        if self.parameters is not None:
            self.setup()

    def setup(self) -> None:
        if self.initialised:
            return
        if self.provider_id is not None:
            # Setup by configuration
            self.max_power_wh = getattr(self.config, f"{self.prefix}_power_max")
        elif self.parameters is not None:
            # Setup by parameters
            self.max_power_wh = (
                self.parameters.max_power_wh  # Maximum power that the inverter can handle
            )
        else:
            error_msg = "Parameters and provider ID missing. Can't instantiate."
            logger.error(error_msg)
            raise ValueError(error_msg)

    def process_energy(
        self, generation: float, consumption: float, hour: int
    ) -> Tuple[float, float, float, float]:
        losses = 0.0
        grid_export = 0.0
        grid_import = 0.0
        self_consumption = 0.0

        if generation >= consumption:
            # Case 1: Sufficient or excess generation
            actual_consumption = min(consumption, self.max_power_wh)
            remaining_energy = generation - actual_consumption

            # Charge battery with excess energy
            charged_energy, charging_losses = self.akku.charge_energy(remaining_energy, hour)
            losses += charging_losses

            # Calculate remaining surplus after battery charge
            remaining_surplus = remaining_energy - (charged_energy + charging_losses)
            grid_export = min(remaining_surplus, self.max_power_wh - actual_consumption)

            # If any remaining surplus can't be fed to the grid, count as losses
            losses += max(remaining_surplus - grid_export, 0)
            self_consumption = actual_consumption

        else:
            # Case 2: Insufficient generation, cover shortfall
            shortfall = consumption - generation
            available_ac_power = max(self.max_power_wh - generation, 0)

            # Discharge battery to cover shortfall, if possible
            battery_discharge, discharge_losses = self.akku.discharge_energy(
                min(shortfall, available_ac_power), hour
            )
            losses += discharge_losses

            # Draw remaining required power from the grid (discharge_losses are already substraved in the battery)
            grid_import = shortfall - battery_discharge
            self_consumption = generation + battery_discharge

        return grid_export, grid_import, losses, self_consumption
