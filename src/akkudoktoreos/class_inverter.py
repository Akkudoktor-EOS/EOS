<<<<<<< HEAD
class Wechselrichter:
    def __init__(self, max_leistung_wh, akku):
        self.max_leistung_wh = max_leistung_wh  # Maximum power that the inverter can handle
        self.akku = akku  # Connection to a battery object
=======
from typing import Tuple
>>>>>>> 5d367d1 (inverter rewritten)


<<<<<<< HEAD
        if erzeugung >= verbrauch:
            if verbrauch > self.max_leistung_wh:
                # If consumption exceeds maximum inverter power
                verluste += erzeugung - self.max_leistung_wh
                restleistung_nach_verbrauch = self.max_leistung_wh - verbrauch
                netzbezug = -restleistung_nach_verbrauch  # Negative indicates feeding into the grid
                eigenverbrauch = self.max_leistung_wh
            else:
                # Remaining power after consumption
                restleistung_nach_verbrauch = erzeugung - verbrauch
=======
class Inverter:
    def __init__(self, max_power_wh: float, battery) -> None:
        self.max_power_wh = max_power_wh  # Maximum power inverter can handle
        self.battery = battery  # Battery object
>>>>>>> 5d367d1 (inverter rewritten)

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
            charged_energy, charging_losses = self.battery.energie_laden(
                remaining_energy, hour
            )
            losses += charging_losses

            # Calculate remaining surplus after battery charge
            remaining_surplus = remaining_energy - (charged_energy + charging_losses)
            grid_feed_in = min(
                remaining_surplus, self.max_power_wh - actual_consumption
            )

            # If any remaining surplus can't be fed to the grid, count as losses
            losses += max(remaining_surplus - grid_feed_in, 0)
            self_consumption = actual_consumption

        else:
<<<<<<< HEAD
            benötigte_energie = verbrauch - erzeugung  # Energy needed from external sources
            max_akku_leistung = self.akku.max_ladeleistung_w  # Maximum battery discharge power
=======
            # Case 2: Insufficient generation, cover shortfall
            shortfall = consumption - generation
            available_ac_power = max(self.max_power_wh - generation, 0)
>>>>>>> 5d367d1 (inverter rewritten)

            # Discharge battery to cover shortfall, if possible
            battery_discharge, discharge_losses = self.battery.energie_abgeben(
                min(shortfall, available_ac_power), hour
            )
            losses += discharge_losses

<<<<<<< HEAD
            # Discharge energy from the battery based on need
            if benötigte_energie < rest_ac_leistung:
                aus_akku, akku_entladeverluste = self.akku.energie_abgeben(benötigte_energie, hour)
            else:
                aus_akku, akku_entladeverluste = self.akku.energie_abgeben(rest_ac_leistung, hour)
=======
            # Draw remaining required power from the grid
            grid_draw = shortfall - battery_discharge
            self_consumption = generation + battery_discharge
>>>>>>> 5d367d1 (inverter rewritten)

        return grid_feed_in, grid_draw, losses, self_consumption
