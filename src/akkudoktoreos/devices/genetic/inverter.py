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
        slot_duration_h: float = 1.0,
    ):
        self.parameters: InverterParameters = parameters
        self.battery: Optional[Battery] = battery
        self.slot_duration_h: float = slot_duration_h
        self._setup()

    def _setup(self) -> None:
        if self.battery and self.parameters.battery_id != self.battery.parameters.device_id:
            error_msg = f"Battery ID mismatch - {self.parameters.battery_id} is configured; got {self.battery.parameters.device_id}."
            logger.error(error_msg)
            raise ValueError(error_msg)
        self.self_consumption_predictor = get_eos_load_interpolator()
        # max_power_wh is supplied as power [W] but used as the maximum energy
        # the inverter can move during one optimization slot.
        self.max_power_wh = self.parameters.max_power_wh * self.slot_duration_h
        self.dc_to_ac_efficiency = self.parameters.dc_to_ac_efficiency
        self.ac_to_dc_efficiency = self.parameters.ac_to_dc_efficiency
        # This value remains a power [W]. GeneticSimulation converts it into a
        # slot-independent charge-factor limit.
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
        """Process one slot using probabilistic direct PV-to-load overlap.

        ``generation`` and ``consumption`` are interval energies. The load
        probability table is evaluated in watts and yields the expected direct
        PV-to-load power. The remaining load and PV surplus are then handled
        independently, because both can occur during different sub-intervals of
        the same hourly or 15-minute slot.
        """
        losses = 0.0
        grid_export = 0.0
        generation = max(float(generation), 0.0)
        consumption = max(float(consumption), 0.0)

        # Convert interval energy [Wh] to mean power [W] for the probability
        # lookup, then convert its expected direct power back to slot energy.
        if generation > 0.0 and consumption > 0.0:
            expected_direct_power_w = (
                self.self_consumption_predictor.calculate_expected_direct_consumption(
                    consumption / self.slot_duration_h,
                    generation / self.slot_duration_h,
                )
            )
            direct_pv_energy = expected_direct_power_w * self.slot_duration_h
        else:
            direct_pv_energy = 0.0

        # Direct PV is bounded by both input energies and by the AC energy the
        # inverter can move during this slot.
        direct_pv_energy = min(
            max(direct_pv_energy, 0.0),
            generation,
            consumption,
            self.max_power_wh,
        )
        remaining_load = max(consumption - direct_pv_energy, 0.0)
        pv_surplus = max(generation - direct_pv_energy, 0.0)
        remaining_inverter_ac_capacity = max(self.max_power_wh - direct_pv_energy, 0.0)

        # Load gaps and PV surplus may both occur within the same coarse slot.
        # Cover the load gap first; this preserves the existing chronological
        # approximation and can create headroom for later PV charging.
        battery_discharge_ac = 0.0
        if remaining_load > 0.0 and self.battery and remaining_inverter_ac_capacity > 0.0:
            requested_ac_wh = min(remaining_load, remaining_inverter_ac_capacity)
            battery_discharge_ac, battery_discharge_losses = self._discharge_battery_to_ac(
                requested_ac_wh, hour
            )
            remaining_load = max(remaining_load - battery_discharge_ac, 0.0)
            remaining_inverter_ac_capacity = max(
                remaining_inverter_ac_capacity - battery_discharge_ac, 0.0
            )
            losses += battery_discharge_losses

        grid_import = remaining_load

        # Charge from the probabilistic PV surplus on the DC path. Stored energy
        # plus charge losses equals the PV energy accepted by the battery.
        remaining_surplus = pv_surplus
        if remaining_surplus > 0.0 and self.battery:
            charged_energy, charge_losses = self.battery.charge_energy(remaining_surplus, hour)
            remaining_surplus = max(remaining_surplus - charged_energy - charge_losses, 0.0)
            losses += charge_losses

        pv_grid_export = min(remaining_surplus, remaining_inverter_ac_capacity)
        grid_export += pv_grid_export
        remaining_inverter_ac_capacity = max(remaining_inverter_ac_capacity - pv_grid_export, 0.0)
        # PV which can neither charge the battery nor pass through the inverter
        # is curtailed and reported as a loss.
        losses += max(remaining_surplus - pv_grid_export, 0.0)

        if allow_battery_grid_export and self.battery and remaining_inverter_ac_capacity > 0.0:
            remaining_battery_ac = (
                self.battery.remaining_discharge_energy_wh(hour) * self.dc_to_ac_efficiency
            )
            export_capacity = min(remaining_inverter_ac_capacity, remaining_battery_ac)
            battery_export_ac, battery_export_losses = self._discharge_battery_to_ac(
                export_capacity, hour
            )
            grid_export += battery_export_ac
            losses += battery_export_losses

        self_consumption = direct_pv_energy + battery_discharge_ac
        return grid_export, grid_import, losses, self_consumption
