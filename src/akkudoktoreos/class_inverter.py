from typing import Tuple


class Inverter:
    def __init__(self, max_power_wh: float, battery) -> None:
        self.max_power_wh = max_power_wh  # Maximum power inverter can handle
        self.battery = battery  # Battery object

    def process_energy(
        self, generation: float, consumption: float, hour: int
    ) -> Tuple[float, float, float, float]:
        losses = 0.0
        grid_feed_in = 0.0
        grid_draw = 0.0
        self_consumption = 0.0

        if generation >= consumption:
            # Case 1: Sufficient or excess generation
            actual_consumption = min(consumption, self.max_power_wh)
            remaining_energy = generation - actual_consumption

            # Charge battery with excess energy
            charged_energy, charging_losses = self.battery.energie_laden(remaining_energy, hour)
            losses += charging_losses

            # Calculate remaining surplus after battery charge
            remaining_surplus = remaining_energy - (charged_energy + charging_losses)
            grid_feed_in = min(remaining_surplus, self.max_power_wh - actual_consumption)

            # If any remaining surplus can't be fed to the grid, count as losses
            losses += max(remaining_surplus - grid_feed_in, 0)
            self_consumption = actual_consumption

        else:
            # Case 2: Insufficient generation, cover shortfall
            shortfall = consumption - generation
            available_ac_power = max(self.max_power_wh - generation, 0)

            # Discharge battery to cover shortfall, if possible
            battery_discharge, discharge_losses = self.battery.energie_abgeben(
                min(shortfall, available_ac_power), hour
            )
            losses += discharge_losses

            # Draw remaining required power from the grid
            grid_draw = shortfall - battery_discharge
            self_consumption = generation + battery_discharge

        return grid_feed_in, grid_draw, losses, self_consumption
