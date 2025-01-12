from typing import Optional

from pydantic import Field

from akkudoktoreos.core.logging import get_logger
from akkudoktoreos.devices.devicesabc import DeviceBase, DeviceParameters
from akkudoktoreos.prediction.interpolator import get_eos_load_interpolator

logger = get_logger(__name__)


class InverterParameters(DeviceParameters):
    device_id: str = Field(description="ID of inverter")
    max_power_wh: float = Field(gt=0)
    battery: Optional[str] = Field(default=None, description="ID of battery")


class Inverter(DeviceBase):
    def __init__(
        self,
        parameters: Optional[InverterParameters] = None,
    ):
        self.parameters: Optional[InverterParameters] = None
        super().__init__(parameters)

    def _setup(self) -> None:
        assert self.parameters is not None
        if self.parameters.battery is None:
            # For the moment raise exception
            # TODO: Make battery configurable by config
            error_msg = "Battery for PV inverter is mandatory."
            logger.error(error_msg)
            raise NotImplementedError(error_msg)
        self.self_consumption_predictor = get_eos_load_interpolator()
        self.max_power_wh = (
            self.parameters.max_power_wh
        )  # Maximum power that the inverter can handle

    def _post_setup(self) -> None:
        assert self.parameters is not None
        self.battery = self.devices.get_device_by_id(self.parameters.battery)

    def process_energy(
        self, generation: float, consumption: float, hour: int
    ) -> tuple[float, float, float, float]:
        losses = 0.0
        grid_export = 0.0
        grid_import = 0.0
        self_consumption = 0.0

        if generation >= consumption:
            if consumption > self.max_power_wh:
                # If consumption exceeds maximum inverter power
                losses += generation - self.max_power_wh
                remaining_power = self.max_power_wh - consumption
                grid_import = -remaining_power  # Negative indicates feeding into the grid
                self_consumption = self.max_power_wh
            else:
                scr = self.self_consumption_predictor.calculate_self_consumption(
                    consumption, generation
                )

                # Remaining power after consumption
                remaining_power = (generation - consumption) * scr  # EVQ
                # Remaining load Self Consumption not perfect
                remaining_load_evq = (generation - consumption) * (1.0 - scr)

                if remaining_load_evq > 0:
                    # Akku muss den Restverbrauch decken
                    from_battery, discharge_losses = self.battery.discharge_energy(
                        remaining_load_evq, hour
                    )
                    remaining_load_evq -= from_battery  # Restverbrauch nach Akkuentladung
                    losses += discharge_losses

                    # Wenn der Akku den Restverbrauch nicht vollständig decken kann, wird der Rest ins Netz gezogen
                    if remaining_load_evq > 0:
                        grid_import += remaining_load_evq
                        remaining_load_evq = 0
                else:
                    from_battery = 0.0

                if remaining_power > 0:
                    # Load battery with excess energy
                    charged_energie, charge_losses = self.battery.charge_energy(
                        remaining_power, hour
                    )
                    remaining_surplus = remaining_power - (charged_energie + charge_losses)

                    # Feed-in to the grid based on remaining capacity
                    if remaining_surplus > self.max_power_wh - consumption:
                        grid_export = self.max_power_wh - consumption
                        losses += remaining_surplus - grid_export
                    else:
                        grid_export = remaining_surplus

                    losses += charge_losses
                self_consumption = (
                    consumption + from_battery
                )  # Self-consumption is equal to the load

        else:
            # Case 2: Insufficient generation, cover shortfall
            shortfall = consumption - generation
            available_ac_power = max(self.max_power_wh - generation, 0)

            # Discharge battery to cover shortfall, if possible
            battery_discharge, discharge_losses = self.battery.discharge_energy(
                min(shortfall, available_ac_power), hour
            )
            losses += discharge_losses

            # Draw remaining required power from the grid (discharge_losses are already substraved in the battery)
            grid_import = shortfall - battery_discharge
            self_consumption = generation + battery_discharge

        return grid_export, grid_import, losses, self_consumption
