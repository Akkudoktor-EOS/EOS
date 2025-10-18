from typing import Optional

from loguru import logger

from akkudoktoreos.devices.genetic.battery import Battery
from akkudoktoreos.optimization.genetic.geneticdevices import InverterParameters
from akkudoktoreos.prediction.interpolator import get_eos_load_interpolator


class Inverter:
    def __init__(
        self,
        parameters: InverterParameters,
        battery: Optional[Battery] = None,
    ):
        self.parameters: InverterParameters = parameters
        self.battery: Optional[Battery] = battery
        self._setup()

    def _setup(self) -> None:
        if self.battery and self.parameters.battery_id != self.battery.parameters.device_id:
            error_msg = f"Battery ID mismatch - {self.parameters.battery_id} is configured; got {self.battery.parameters.device_id}."
            logger.error(error_msg)
            raise ValueError(error_msg)
        self.self_consumption_predictor = get_eos_load_interpolator()
        self.max_power_wh = (
            self.parameters.max_power_wh
        )  # Maximum power that the inverter can handle

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
                # Calculate scr using cached results per energy management/optimization run
                scr = self.self_consumption_predictor.calculate_self_consumption(
                    consumption, generation
                )

                # Remaining power after consumption
                remaining_power = (generation - consumption) * scr  # EVQ
                # Remaining load Self Consumption not perfect
                remaining_load_evq = (generation - consumption) * (1.0 - scr)

                if remaining_load_evq > 0:
                    # Akku muss den Restverbrauch decken
                    if self.battery:
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
                    if self.battery:
                        charged_energie, charge_losses = self.battery.charge_energy(
                            remaining_power, hour
                        )
                        remaining_surplus = remaining_power - (charged_energie + charge_losses)
                    else:
                        remaining_surplus = remaining_power

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
            if self.battery:
                battery_discharge, discharge_losses = self.battery.discharge_energy(
                    min(shortfall, available_ac_power), hour
                )
                losses += discharge_losses
            else:
                battery_discharge = 0

            # Draw remaining required power from the grid (discharge_losses are already substraved in the battery)
            grid_import = shortfall - battery_discharge
            self_consumption = generation + battery_discharge

        return grid_export, grid_import, losses, self_consumption
