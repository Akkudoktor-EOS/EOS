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
        self.dc_to_ac_efficiency = self.parameters.dc_to_ac_efficiency
        self.ac_to_dc_efficiency = self.parameters.ac_to_dc_efficiency
        self.max_ac_charge_power_w = self.parameters.max_ac_charge_power_w

    def _discharge_battery_to_ac(self, requested_ac_wh: float, hour: int) -> tuple[float, float]:
        """Discharge battery energy and convert it to AC energy."""
        if not self.battery or requested_ac_wh <= 0.0:
            return 0.0, 0.0

        dc_request = requested_ac_wh / self.dc_to_ac_efficiency
        battery_discharge_dc, discharge_losses = self.battery.discharge_energy(dc_request, hour)
        battery_discharge_ac = battery_discharge_dc * self.dc_to_ac_efficiency
        inverter_discharge_losses = battery_discharge_dc - battery_discharge_ac
        return battery_discharge_ac, discharge_losses + inverter_discharge_losses

    def process_energy(
        self,
        generation: float,
        consumption: float,
        hour: int,
        allow_battery_grid_export: bool = False,
    ) -> tuple[float, float, float, float]:
        losses = 0.0
        grid_export = 0.0
        grid_import = 0.0
        self_consumption = 0.0

        # Cache inverter DC→AC efficiency for discharge path
        dc_to_ac_eff = self.dc_to_ac_efficiency

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

                from_battery_dc = 0.0
                if remaining_load_evq > 0:
                    # Akku muss den Restverbrauch decken
                    if self.battery:
                        # Request more DC from battery to account for DC→AC conversion loss
                        dc_request = remaining_load_evq / dc_to_ac_eff
                        from_battery_dc, discharge_losses = self.battery.discharge_energy(
                            dc_request, hour
                        )
                        # Convert DC output to AC
                        from_battery_ac = from_battery_dc * dc_to_ac_eff
                        inverter_discharge_losses = from_battery_dc - from_battery_ac
                        remaining_load_evq -= from_battery_ac
                        losses += discharge_losses + inverter_discharge_losses
                    else:
                        from_battery_ac = 0.0

                    # Wenn der Akku den Restverbrauch nicht vollständig decken kann, wird der Rest ins Netz gezogen
                    if remaining_load_evq > 0:
                        grid_import += remaining_load_evq
                        remaining_load_evq = 0
                else:
                    from_battery_ac = 0.0

                if remaining_power > 0:
                    # Load battery with excess energy (DC path, no inverter conversion needed)
                    charge_losses = 0.0
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
                    consumption + from_battery_ac
                )  # Self-consumption is equal to the load

                if allow_battery_grid_export and self.battery:
                    export_capacity = max(self.max_power_wh - consumption - grid_export, 0.0)
                    max_discharge_dc = getattr(self.battery, "max_charge_power_w", None)
                    if max_discharge_dc is not None:
                        remaining_battery_ac = max(
                            (max_discharge_dc - from_battery_dc) * dc_to_ac_eff, 0.0
                        )
                        export_capacity = min(export_capacity, remaining_battery_ac)
                    battery_export_ac, battery_export_losses = self._discharge_battery_to_ac(
                        export_capacity, hour
                    )
                    grid_export += battery_export_ac
                    losses += battery_export_losses

        else:
            # Case 2: Insufficient generation, cover shortfall
            shortfall = consumption - generation
            available_ac_power = max(self.max_power_wh - generation, 0)

            # Discharge battery to cover shortfall, if possible
            if self.battery:
                # Need shortfall in AC, request more DC from battery for DC→AC conversion
                ac_needed = min(shortfall, available_ac_power)
                dc_request = ac_needed / dc_to_ac_eff
                battery_discharge_dc, discharge_losses = self.battery.discharge_energy(
                    dc_request, hour
                )
                # Convert DC output to AC
                battery_discharge_ac = battery_discharge_dc * dc_to_ac_eff
                inverter_discharge_losses = battery_discharge_dc - battery_discharge_ac
                losses += discharge_losses + inverter_discharge_losses
            else:
                battery_discharge_ac = 0

            # Draw remaining required power from the grid (discharge_losses are already subtracted in the battery)
            grid_import = shortfall - battery_discharge_ac
            self_consumption = generation + battery_discharge_ac

            if allow_battery_grid_export and self.battery and grid_import <= 0.0:
                export_capacity = max(self.max_power_wh - consumption, 0.0)
                max_discharge_dc = getattr(self.battery, "max_charge_power_w", None)
                if max_discharge_dc is not None:
                    remaining_battery_ac = max(
                        (max_discharge_dc - battery_discharge_dc) * dc_to_ac_eff, 0.0
                    )
                    export_capacity = min(export_capacity, remaining_battery_ac)
                battery_export_ac, battery_export_losses = self._discharge_battery_to_ac(
                    export_capacity, hour
                )
                grid_export += battery_export_ac
                losses += battery_export_losses

        return grid_export, grid_import, losses, self_consumption
