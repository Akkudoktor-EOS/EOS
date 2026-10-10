from bisect import bisect_left
from typing import Optional

from loguru import logger
from pydantic import Field, field_validator

from akkudoktoreos.devices.devicesabc import (
    interpolate_efficiency_curve,
    solve_ac_for_dc_energy,
    validate_efficiency_curve,
)
from akkudoktoreos.devices.genetic.battery import Battery
from akkudoktoreos.optimization.genetic.geneticdevices import DeviceParameters
from akkudoktoreos.prediction.interpolator import get_eos_load_interpolator


class InverterParameters(DeviceParameters):
    """Inverter Device Simulation Configuration."""

    device_id: str = Field(
        json_schema_extra={"description": "ID of inverter", "examples": ["inverter1"]}
    )
    max_power_wh: float = Field(gt=0, json_schema_extra={"examples": [10000]})
    battery_id: Optional[str] = Field(
        default=None,
        json_schema_extra={"description": "ID of battery", "examples": [None, "battery1"]},
    )
    ac_to_dc_efficiency: float = Field(
        default=1.0,
        ge=0,
        le=1,
        json_schema_extra={
            "description": (
                "Efficiency of AC to DC conversion (for AC/grid charging of battery). "
                "Set to 0 to disable AC charging via inverter. "
                "Default 1.0 for backward compatibility (no additional inverter loss)."
            ),
            "examples": [0.95, 1.0, 0.0],
        },
    )
    dc_to_ac_efficiency: float = Field(
        default=1.0,
        gt=0,
        le=1,
        json_schema_extra={
            "description": (
                "Efficiency of DC to AC conversion (for battery discharging to AC load/grid). "
                "Default 1.0 for backward compatibility (no additional inverter loss)."
            ),
            "examples": [0.95, 1.0],
        },
    )
    dc_to_ac_efficiency_curve: Optional[list[tuple[float, float]]] = Field(
        default=None,
        json_schema_extra={
            "description": (
                "Load-dependent efficiency of DC to AC conversion as a list of "
                "(load_fraction, efficiency) points. The load fraction is the AC power "
                "of the conversion relative to max_power_wh [0.0 ... 1.0]; points must be "
                "strictly increasing in load fraction, efficiencies in (0, 1]. Values are "
                "interpolated linearly and clamped to the first/last point. When set, the "
                "curve replaces dc_to_ac_efficiency. "
                "Default None (constant dc_to_ac_efficiency)."
            ),
            "examples": [None, [[0.02, 0.8], [0.07, 0.93], [0.3, 0.96], [1.0, 0.95]]],
        },
    )
    dc_to_ac_efficiency_reference_load_fraction: float = Field(
        default=0.06,
        ge=0,
        le=1,
        json_schema_extra={
            "description": (
                "Load fraction at which dc_to_ac_efficiency_curve is evaluated where "
                "stored battery energy is valued without a specific conversion "
                "(e.g. remaining battery energy at the end of the horizon, AC charge "
                "break-even). Should represent the typical load the battery serves, "
                "e.g. the night-time base load. Only used with dc_to_ac_efficiency_curve. "
                "Default 0.06."
            ),
            "examples": [0.06, 0.1],
        },
    )
    max_ac_charge_power_w: Optional[float] = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "description": (
                "Maximum AC charging power in watts. "
                "None means no additional limit (battery's own max_charge_power_w applies). "
                "Set to 0 to disable AC charging."
            ),
            "examples": [None, 0, 5000],
        },
    )
    ac_charge_limits_total_charge: bool = Field(
        default=False,
        json_schema_extra={
            "description": (
                "True if the AC charge setpoint caps the battery's total charge power, "
                "PV included. PV surplus above it is exported, not stored. False keeps "
                "the default model: PV surplus charges first and the grid adds "
                "ac_charge x max_charge_power_w on top."
            ),
            "examples": [False, True],
        },
    )

    @field_validator("dc_to_ac_efficiency_curve")
    @classmethod
    def _validate_dc_to_ac_efficiency_curve(
        cls, value: Optional[list[tuple[float, float]]]
    ) -> Optional[list[tuple[float, float]]]:
        return validate_efficiency_curve(value)


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
        self.dc_to_ac_efficiency_curve = self.parameters.dc_to_ac_efficiency_curve
        # DC-to-AC efficiency used to value stored battery energy where no single
        # conversion is known (end-of-horizon value, AC charge break-even). With
        # a curve it is the efficiency at the configured reference load.
        self.reference_dc_to_ac_efficiency = self.dc_to_ac_efficiency_at(
            self.parameters.dc_to_ac_efficiency_reference_load_fraction * self.max_power_wh
        )
        # This value remains a power [W]. GeneticSimulation converts it into a
        # slot-independent charge-factor limit.
        self.max_ac_charge_power_w = self.parameters.max_ac_charge_power_w
        self.ac_charge_limits_total_charge = self.parameters.ac_charge_limits_total_charge

    def dc_to_ac_efficiency_at(self, ac_wh: float) -> float:
        """Return the DC-to-AC efficiency of a conversion delivering ``ac_wh`` in one slot.

        Without ``dc_to_ac_efficiency_curve`` this is the constant
        ``dc_to_ac_efficiency``. With a curve, the efficiency is interpolated at
        the load fraction ``ac_wh / max_power_wh`` of this slot. ``max_power_wh``
        is already scaled to the slot duration, so the fraction equals the mean
        AC power relative to the rated power.
        """
        if self.dc_to_ac_efficiency_curve is None:
            return self.dc_to_ac_efficiency
        curve: list[tuple[float, float]] = self.dc_to_ac_efficiency_curve
        load_fraction = ac_wh / self.max_power_wh if self.max_power_wh > 0 else 0.0
        # This runs for every battery discharge of every candidate plan. Find
        # the curve segment by bisection on the load fractions, split off once
        # per curve, instead of walking the points. The segment and the
        # interpolation are those of interpolate_efficiency_curve().
        if curve is not getattr(self, "_curve_source", None):
            self._curve_fractions = [point[0] for point in curve]
            self._curve_efficiencies = [point[1] for point in curve]
            self._curve_source = curve
        fractions = self._curve_fractions
        if load_fraction != load_fraction or len(fractions) != len(curve):
            # NaN, or a curve changed in place: the plain walk.
            return interpolate_efficiency_curve(curve, load_fraction)
        efficiencies = self._curve_efficiencies
        index = bisect_left(fractions, load_fraction)
        if index == 0:
            return efficiencies[0]
        if index == len(fractions):
            return efficiencies[-1]
        lower_fraction = fractions[index - 1]
        lower_efficiency = efficiencies[index - 1]
        return lower_efficiency + (load_fraction - lower_fraction) / (
            fractions[index] - lower_fraction
        ) * (efficiencies[index] - lower_efficiency)

    def ac_charge_factor(self, factor: float) -> float:
        """Return the AC charge factor the inverter can actually execute.

        The factor is a fraction of the battery's ``max_charge_power_w``. It is
        capped so that the AC input stays within ``max_ac_charge_power_w`` and
        is 0.0 when AC charging is impossible.
        """
        if factor <= 0.0 or not self.battery or self.ac_to_dc_efficiency <= 0.0:
            return 0.0
        if self.max_ac_charge_power_w is not None and self.battery.max_charge_power_w > 0:
            # DC power = max_charge_power_w * factor
            # AC power = DC power / ac_to_dc_eff <= max_ac_charge_power_w
            factor = min(
                factor,
                self.max_ac_charge_power_w
                * self.ac_to_dc_efficiency
                / self.battery.max_charge_power_w,
            )
        return max(factor, 0.0)

    def begin_ac_charge_slot(self, hour: int, factor: float) -> None:
        """Apply the AC charge setpoint of a slot before its PV is processed.

        On inverters whose grid charge setpoint caps the total charge power, the
        battery takes at most ``factor`` of its rated charge power in this slot,
        PV included. Call ``process_energy`` afterwards so PV surplus above the
        cap is exported, as the inverter does.
        """
        if self.ac_charge_limits_total_charge and self.battery and factor > 0.0:
            self.battery.limit_slot_charge(
                hour, self.battery.max_charge_power_w * self.slot_duration_h * factor
            )

    def charge_battery_from_grid(self, hour: int, factor: float) -> tuple[float, float]:
        """Charge the battery from the grid after PV was processed in this slot.

        Default model: the grid adds ``factor`` of the rated charge power on top
        of the PV charge. With ``ac_charge_limits_total_charge`` the grid only
        fills what PV left of the slot cap set by ``begin_ac_charge_slot``.

        Returns:
            tuple[float, float]: AC energy drawn from the grid [Wh] and the
            battery plus AC-to-DC conversion losses [Wh].
        """
        if not self.battery or factor <= 0.0:
            return 0.0, 0.0
        if self.ac_charge_limits_total_charge:
            stored, battery_losses = self.battery.charge_energy(
                self.battery.max_charge_power_w * self.slot_duration_h * factor, hour
            )
        else:
            stored, battery_losses = self.battery.charge_energy(None, hour, charge_factor=factor)
        # DC energy entering the battery (before battery internal efficiency)
        dc_energy = stored + battery_losses
        # AC energy consumed from grid (accounts for AC->DC conversion loss)
        ac_energy = dc_energy / self.ac_to_dc_efficiency
        return ac_energy, battery_losses + (ac_energy - dc_energy)

    def _delivered_ac_wh(
        self,
        battery_discharge_dc: float,
        dc_request: float,
        requested_ac_wh: float,
        dc_to_ac_efficiency: float,
    ) -> float:
        """Return the AC energy of a discharge that delivered ``battery_discharge_dc``.

        If the battery delivered less than requested (low state of charge or its
        power limit), the conversion runs at a lower AC load than requested. With
        an efficiency curve the efficiency must then be the one at the AC energy
        that is actually delivered, not at the request.
        """
        curve = self.dc_to_ac_efficiency_curve
        if curve is not None and battery_discharge_dc < dc_request * (1.0 - 1e-12):
            return solve_ac_for_dc_energy(
                curve, battery_discharge_dc, self.max_power_wh, requested_ac_wh
            )
        return battery_discharge_dc * dc_to_ac_efficiency

    def _discharge_battery_to_ac(self, requested_ac_wh: float, hour: int) -> tuple[float, float]:
        """Discharge battery energy and convert it to AC energy."""
        battery = self.battery
        if not battery or requested_ac_wh <= 0.0:
            return 0.0, 0.0
        if type(battery) is not Battery:
            # Another battery implementation: ask it, step by step.
            if not battery.discharge_released(hour):
                return 0.0, 0.0
            dc_to_ac_efficiency = self.dc_to_ac_efficiency_at(requested_ac_wh)
            dc_request = requested_ac_wh / dc_to_ac_efficiency
            battery_discharge_dc, discharge_losses = battery.discharge_energy(dc_request, hour)
            battery_discharge_ac = self._delivered_ac_wh(
                battery_discharge_dc, dc_request, requested_ac_wh, dc_to_ac_efficiency
            )
            inverter_discharge_losses = battery_discharge_dc - battery_discharge_ac
            return battery_discharge_ac, discharge_losses + inverter_discharge_losses

        # This runs for every slot of every candidate plan in which the battery
        # could serve load or export. For the plain Battery the release check,
        # the efficiency and Battery.discharge_energy() are computed in one go,
        # without the nested calls. The arithmetic is that of
        # Battery.discharge_energy(); keep both in step.
        if battery.discharge_array[hour] == 0:
            # Not released in this slot: the battery delivers nothing.
            return 0.0, 0.0
        # With an efficiency curve, the efficiency is taken at the requested AC
        # energy of this conversion.
        if self.dc_to_ac_efficiency_curve is None:
            dc_to_ac_efficiency = self.dc_to_ac_efficiency
        else:
            dc_to_ac_efficiency = self.dc_to_ac_efficiency_at(requested_ac_wh)
        dc_request = requested_ac_wh / dc_to_ac_efficiency

        soc_wh = battery.soc_wh
        min_soc_wh = battery.min_soc_wh
        discharged_raw_wh = battery._discharged_raw_wh_per_slot
        discharging_efficiency = battery.discharging_efficiency
        raw_available_wh = max(soc_wh - min_soc_wh, 0.0)
        max_raw_wh = max(
            battery.max_charge_power_w * battery.slot_duration_h - discharged_raw_wh[hour], 0.0
        )
        max_deliverable_wh = min(raw_available_wh, max_raw_wh) * discharging_efficiency
        battery_discharge_dc = min(dc_request, max_deliverable_wh)
        raw_used_wh = battery_discharge_dc / discharging_efficiency
        soc_wh -= raw_used_wh
        battery.soc_wh = max(soc_wh, min_soc_wh)
        discharged_raw_wh[hour] += raw_used_wh
        discharge_losses = raw_used_wh - battery_discharge_dc

        # Same rule as _delivered_ac_wh(), without the call: a limited discharge
        # is converted with the efficiency at the AC energy actually delivered.
        if self.dc_to_ac_efficiency_curve is not None and battery_discharge_dc < dc_request * (
            1.0 - 1e-12
        ):
            battery_discharge_ac = solve_ac_for_dc_energy(
                self.dc_to_ac_efficiency_curve,
                battery_discharge_dc,
                self.max_power_wh,
                requested_ac_wh,
            )
        else:
            battery_discharge_ac = battery_discharge_dc * dc_to_ac_efficiency
        inverter_discharge_losses = battery_discharge_dc - battery_discharge_ac
        return battery_discharge_ac, discharge_losses + inverter_discharge_losses

    def process_energy(
        self,
        generation: float,
        consumption: float,
        hour: int,
        allow_battery_grid_export: bool = False,
        battery_grid_export_factor: float = 1.0,
    ) -> tuple[float, float, float, float]:
        """Process one slot using probabilistic direct PV-to-load overlap.

        ``generation`` and ``consumption`` are interval energies. The load
        probability table is evaluated in watts and yields the expected direct
        PV-to-load power. The remaining load and PV surplus are then handled
        independently, because both can occur during different sub-intervals of
        the same hourly or 15-minute slot.

        Args:
            generation: PV energy of the slot [Wh].
            consumption: Load energy of the slot [Wh].
            hour: Slot index.
            allow_battery_grid_export: Whether the battery may discharge into the
                grid in this slot (direct marketing).
            battery_grid_export_factor: Export level as a factor of the battery's
                rated discharge power [0.0 ... 1.0]. 1.0 exports as much as the
                battery and the inverter allow, which is the behaviour when no
                export rates are configured.
        """
        # This method runs once per slot of every candidate plan. The bounds
        # below are written as conditional expressions instead of min()/max()
        # calls: "0.0 if x < 0.0 else x" is exactly max(x, 0.0) and
        # "b if b < a else a" is exactly min(a, b), NaN and signed zero included.
        losses = 0.0
        grid_export = 0.0
        generation = float(generation)
        if generation < 0.0:
            generation = 0.0
        consumption = float(consumption)
        if consumption < 0.0:
            consumption = 0.0
        battery = self.battery
        max_power_wh = self.max_power_wh

        # Convert interval energy [Wh] to mean power [W] for the probability
        # lookup, then convert its expected direct power back to slot energy.
        if generation > 0.0 and consumption > 0.0:
            slot_duration_h = self.slot_duration_h
            expected_direct_power_w = (
                self.self_consumption_predictor.calculate_expected_direct_consumption(
                    consumption / slot_duration_h,
                    generation / slot_duration_h,
                )
            )
            direct_pv_energy = expected_direct_power_w * slot_duration_h
        else:
            direct_pv_energy = 0.0

        # Direct PV is bounded by both input energies and by the AC energy the
        # inverter can move during this slot.
        if direct_pv_energy < 0.0:
            direct_pv_energy = 0.0
        if generation < direct_pv_energy:
            direct_pv_energy = generation
        if consumption < direct_pv_energy:
            direct_pv_energy = consumption
        if max_power_wh < direct_pv_energy:
            direct_pv_energy = max_power_wh
        remaining_load = consumption - direct_pv_energy
        if remaining_load < 0.0:
            remaining_load = 0.0
        pv_surplus = generation - direct_pv_energy
        if pv_surplus < 0.0:
            pv_surplus = 0.0
        remaining_inverter_ac_capacity = max_power_wh - direct_pv_energy
        if remaining_inverter_ac_capacity < 0.0:
            remaining_inverter_ac_capacity = 0.0

        # Load gaps and PV surplus may both occur within the same coarse slot.
        # Cover the load gap first; this preserves the existing chronological
        # approximation and can create headroom for later PV charging.
        battery_discharge_ac = 0.0
        if remaining_load > 0.0 and battery and remaining_inverter_ac_capacity > 0.0:
            requested_ac_wh = (
                remaining_inverter_ac_capacity
                if remaining_inverter_ac_capacity < remaining_load
                else remaining_load
            )
            battery_discharge_ac, battery_discharge_losses = self._discharge_battery_to_ac(
                requested_ac_wh, hour
            )
            remaining_load = remaining_load - battery_discharge_ac
            if remaining_load < 0.0:
                remaining_load = 0.0
            remaining_inverter_ac_capacity = remaining_inverter_ac_capacity - battery_discharge_ac
            if remaining_inverter_ac_capacity < 0.0:
                remaining_inverter_ac_capacity = 0.0
            losses += battery_discharge_losses

        grid_import = remaining_load

        # Without PV surplus (night slots, or all PV consumed directly) there is
        # nothing to charge, export or curtail. (!= instead of >: a NaN forecast
        # keeps propagating into the result as before.)
        if pv_surplus != 0.0:
            # Charge from the probabilistic PV surplus on the DC path. Stored
            # energy plus charge losses equals the PV energy accepted by the
            # battery.
            remaining_surplus = pv_surplus
            if remaining_surplus > 0.0 and battery:
                charged_energy, charge_losses = battery.charge_energy(remaining_surplus, hour)
                remaining_surplus = remaining_surplus - charged_energy - charge_losses
                if remaining_surplus < 0.0:
                    remaining_surplus = 0.0
                losses += charge_losses

            pv_grid_export = (
                remaining_inverter_ac_capacity
                if remaining_inverter_ac_capacity < remaining_surplus
                else remaining_surplus
            )
            grid_export += pv_grid_export
            remaining_inverter_ac_capacity = remaining_inverter_ac_capacity - pv_grid_export
            if remaining_inverter_ac_capacity < 0.0:
                remaining_inverter_ac_capacity = 0.0
            # PV which can neither charge the battery nor pass through the
            # inverter is curtailed and reported as a loss.
            curtailed = remaining_surplus - pv_grid_export
            if curtailed < 0.0:
                curtailed = 0.0
            losses += curtailed

        if allow_battery_grid_export and battery and remaining_inverter_ac_capacity > 0.0:
            export_factor = float(battery_grid_export_factor)
            if export_factor < 0.0:
                export_factor = 0.0
            if 1.0 < export_factor:
                export_factor = 1.0
            # Upper bounds of the export. With an efficiency curve, the
            # efficiency is estimated at the DC energy of each bound; the
            # conversion itself in _discharge_battery_to_ac() is exact and is
            # limited by the energy the battery can actually deliver.
            remaining_battery_dc = battery.remaining_discharge_energy_wh(hour)
            remaining_battery_ac = remaining_battery_dc * self.dc_to_ac_efficiency_at(
                remaining_battery_dc
            )
            # The rate caps the export against the battery's *rated* discharge
            # power, so it stays a plain power setpoint ("export at 50 %") that
            # does not silently grow when self-consumption used less of the slot.
            # At factor 1.0 this bound never binds; behaviour is unchanged.
            rated_export_dc = battery.rated_discharge_energy_wh() * export_factor
            rated_export_ac = rated_export_dc * self.dc_to_ac_efficiency_at(rated_export_dc)
            export_capacity = remaining_inverter_ac_capacity
            if remaining_battery_ac < export_capacity:
                export_capacity = remaining_battery_ac
            if rated_export_ac < export_capacity:
                export_capacity = rated_export_ac
            battery_export_ac, battery_export_losses = self._discharge_battery_to_ac(
                export_capacity, hour
            )
            grid_export += battery_export_ac
            losses += battery_export_losses

        self_consumption = direct_pv_energy + battery_discharge_ac
        return grid_export, grid_import, losses, self_consumption
