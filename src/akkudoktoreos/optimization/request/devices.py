"""The electrical devices of a planning request: batteries, vehicles, inverters, appliances.

Every configured house battery, electric vehicle and inverter is taken over (the inverter is
assigned to its battery by ``battery_id``); the former optimizer accepted one of each. The
state of charge comes from the request or from a fresh measurement; where neither exists the
device starts at 0 % and a warning says so (as the former optimizer did).
"""

import math
from dataclasses import dataclass, field, replace
from typing import Any, Optional

import pandas as pd
from loguru import logger

from akkudoktoreos.core.coreabc import ConfigMixin, MeasurementMixin
from akkudoktoreos.devices.appliancescheduling import (
    AppliancePlanInput,
    ApplianceTimes,
    plan_appliance,
)
from akkudoktoreos.devices.models.appliance import ApplianceParams
from akkudoktoreos.devices.models.inverter import InverterParams
from akkudoktoreos.devices.models.storage import StorageParams
from akkudoktoreos.devices.settings.batterysettings import BatteriesCommonSettings
from akkudoktoreos.devices.settings.homeappliancesettings import HomeApplianceCommonSettings
from akkudoktoreos.optimization.fullcharge import (
    enforced_full_charge_deadline,
    full_charge_deadline,
)
from akkudoktoreos.optimization.objective import FullChargeTarget, VehicleTarget
from akkudoktoreos.optimization.request.grid import TimeGrid
from akkudoktoreos.optimization.solutionmodel import PlanningWarning
from akkudoktoreos.utils.datetimeutil import DateTime, to_datetime

# Codes of the warnings for a storage whose state of charge is unknown and assumed to be 0 %.
BATTERY_SOC_MISSING = "battery_soc_missing"
VEHICLE_SOC_MISSING = "vehicle_soc_missing"

# Code of the warning for a vehicle deadline that was moved to the first slot.
VEHICLE_DEADLINE_ADJUSTED = "vehicle_deadline_adjusted"

# How far back the latest "run started" report of a home appliance is looked up.
RUN_STARTED_LOOKBACK_HOURS = 48


@dataclass
class ElectricDevices:
    """The electrical devices of the planning problem, in plan order.

    Attributes:
        batteries: House batteries.
        battery_ids: Their device ids.
        vehicles: Electric vehicles.
        vehicle_ids: Their device ids.
        inverters: Inverters, each attached to its battery by index.
        inverter_ids: Their device ids.
        appliances: Parameters of the household appliances.
        appliance_inputs: Their runs and allowed start slots.
        appliance_ids: Their device ids.
        vehicle_targets: Charge target per vehicle (None where none is active).
        full_charge_targets: Periodic full charge per battery (None where not due).
        battery_settings: Settings behind the batteries (for the search levels).
        vehicle_settings: Settings behind the vehicles.
        charge_rates: Positive grid charge levels of all batteries, ascending.
        export_rates: Export levels of all batteries, descending.
        warnings: Notes on inputs that were replaced or adjusted while resolving.
    """

    batteries: list[StorageParams] = field(default_factory=list)
    battery_ids: list[str] = field(default_factory=list)
    vehicles: list[StorageParams] = field(default_factory=list)
    vehicle_ids: list[str] = field(default_factory=list)
    inverters: list[InverterParams] = field(default_factory=list)
    inverter_ids: list[str] = field(default_factory=list)
    appliances: list[ApplianceParams] = field(default_factory=list)
    appliance_inputs: list[AppliancePlanInput] = field(default_factory=list)
    appliance_ids: list[str] = field(default_factory=list)
    vehicle_targets: list[Optional[VehicleTarget]] = field(default_factory=list)
    full_charge_targets: list[Optional[FullChargeTarget]] = field(default_factory=list)
    battery_settings: list[BatteriesCommonSettings] = field(default_factory=list)
    vehicle_settings: list[BatteriesCommonSettings] = field(default_factory=list)
    charge_rates: tuple[float, ...] = (1.0,)
    export_rates: tuple[float, ...] = (1.0,)
    warnings: list[PlanningWarning] = field(default_factory=list)


def configured_devices(config: Any) -> dict[str, list[Any]]:
    """Return the configured device groups, checking maxima and unique ids.

    Raises:
        ValueError: If a group exceeds its configured maximum or ids repeat across groups.
    """
    devices = config.devices
    groups: dict[str, list[Any]] = {}
    for name in ("batteries", "electric_vehicles", "inverters", "home_appliances"):
        entries = list((getattr(devices, name) or {}).values())
        maximum = getattr(devices, "max_" + name)
        if maximum is not None and len(entries) > maximum:
            raise ValueError(f"devices.{name} exceeds configured maximum {maximum}.")
        groups[name] = entries
    groups["heat_sources"] = list((devices.heat_sources or {}).values())
    ids = [device.device_id for entries in groups.values() for device in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("Configured device IDs must be unique across device groups.")
    return groups


def latest_value(
    dates: list[Any], values: list[Any], observation: DateTime
) -> Optional[tuple[Any, Any]]:
    """Return the newest record at or before the observation that carries a value, else None."""
    samples = [
        (date, value)
        for date, value in zip(dates, values)
        if date.timestamp() <= observation.timestamp() and value is not None and value is not pd.NA
    ]
    if not samples:
        return None
    return max(samples, key=lambda sample: sample[0].timestamp())


class DeviceResolver(ConfigMixin, MeasurementMixin):
    """Resolve the electrical devices of one planning from configuration and measurements."""

    def __init__(
        self,
        grid: TimeGrid,
        soc: dict[str, float],
        vehicle_available: dict[str, list[bool]],
        max_age_seconds: int,
    ) -> None:
        """Prepare the resolver.

        Args:
            grid: Time grid of the planning.
            soc: State of charge per device id [%] that replaces the measurement.
            vehicle_available: Plugged in per slot (from slot 0) per vehicle id; a vehicle
                without an entry is plugged in all the time.
            max_age_seconds: Oldest SoC measurement that still counts [s].
        """
        self.grid = grid
        self.soc = soc
        self.vehicle_available = vehicle_available
        self.max_age_seconds = max_age_seconds
        self.warnings: list[PlanningWarning] = []

    async def _soc(self, device: BatteriesCommonSettings, *, is_vehicle: bool = False) -> float:
        """Return the state of charge of a storage [%]: request, fresh measurement, else 0.

        Without either the storage is planned as empty, which is not a measurement: the cause is
        logged and reported as a ``battery_soc_missing`` or ``vehicle_soc_missing`` warning.
        """
        if device.device_id in self.soc:
            return self.soc[device.device_id]
        observation = self.grid.observation
        try:
            dates, values = await self.measurement.key_to_lists(
                key=device.measurement_key_soc_factor,
                start_datetime=observation.subtract(seconds=self.max_age_seconds),
                end_datetime=observation.add(seconds=1),
                dropna=False,
            )
            # A record without SoC (a meter reading stored by an adapter, pandas NA) must not
            # hide an older SoC within the age limit. An explicit NaN stays an invalid sample.
            latest = latest_value(dates, values, observation)
            if latest is None:
                raise ValueError("No SoC record")
            date, value = latest
            if (
                observation.timestamp() - date.timestamp() > self.max_age_seconds
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError("Invalid or stale SoC factor")
            return float(value) * 100.0
        except (ValueError, KeyError) as exc:
            cause = "no measurement channel" if isinstance(exc, KeyError) else str(exc)
            message = (
                f"No usable fresh state of charge for {device.device_id}; it is planned as "
                f"0 % (not a measurement): {cause}."
            )
            logger.warning(message)
            self.warnings.append(
                PlanningWarning(
                    code=VEHICLE_SOC_MISSING if is_vehicle else BATTERY_SOC_MISSING,
                    message=message,
                    devices=[device.device_id],
                )
            )
            return 0

    async def _full_charge(
        self, device: BatteriesCommonSettings
    ) -> Optional[tuple[float, Optional[FullChargeTarget]]]:
        """Return the periodic full charge of a battery if one is due within the horizon.

        Returns:
            None if none is due, else ``(target_pct, enforced)``: the state of charge to lift
            the upper limit to, and the enforced target with its deadline slot (None where
            grid charging is not allowed for it and PV surplus only may fill the battery).
        """
        policy = device.resolve_full_charge()
        interval_days = policy.interval_days
        if interval_days is None:
            return None
        grid = self.grid
        observation = grid.observation
        try:
            dates, values = await self.measurement.key_to_lists(
                key=device.measurement_key_soc_factor,
                start_datetime=observation.subtract(days=math.ceil(interval_days)),
                end_datetime=observation.add(seconds=1),
                dropna=False,
            )
        except KeyError:
            dates, values = [], []
        deadline = full_charge_deadline(
            zip(dates, values),
            now=observation,
            interval_days=interval_days,
            detect_percentage=policy.detect_percentage,
        )
        if deadline is None:
            logger.warning(
                "No state of charge history for {}; cannot schedule its periodic full charge.",
                device.device_id,
            )
            return None
        horizon_end = grid.start.add(seconds=grid.control_slots * grid.interval_seconds)
        if deadline.timestamp() > horizon_end.timestamp():
            return None
        target = policy.soc_percentage
        if not device.full_charge_allow_grid:
            logger.info(
                "Periodic full charge of {} to {}% due by {}; PV surplus only.",
                device.device_id,
                target,
                deadline,
            )
            return float(target), None
        enforced = enforced_full_charge_deadline(deadline, observation)
        logger.info(
            "Periodic full charge of {} to {}% due by {}; enforced by {}.",
            device.device_id,
            target,
            deadline,
            enforced,
        )
        slot = self.grid.slot_of(enforced)
        return float(target), FullChargeTarget(float(target), min(max(slot, 0), grid.control_slots))

    def _deadline_adjusted(self, device: BatteriesCommonSettings, cause: str) -> None:
        """Log and report that a vehicle deadline is judged at the end of slot 0."""
        message = f"The target of {device.device_id} is charged as early as possible: {cause}."
        logger.warning(message)
        self.warnings.append(
            PlanningWarning(
                code=VEHICLE_DEADLINE_ADJUSTED, message=message, devices=[device.device_id]
            )
        )

    def _vehicle_deadline_slot(self, device: BatteriesCommonSettings) -> Optional[int]:
        """Return the slot by which a vehicle has to reach its target, None for the horizon end.

        The deadline may be an absolute moment, a maximum duration from now, or both; the
        earlier one wins. The target is judged on the state of charge at the start of the
        deadline slot (the departure inside a slot cannot credit that whole slot, so the slot
        is rounded down). The state at the start of slot 0 is the initial state and cannot be
        changed, so a deadline in slot 0 (a departure still ahead but within the running slot,
        a deadline in the past, or a duration shorter than a slot) is judged at the end of slot
        0: the vehicle charges as early as possible. A deadline already passed or shorter than
        a slot is logged and reported as a ``vehicle_deadline_adjusted`` warning.
        """
        grid = self.grid
        candidates: list[int] = []
        if device.min_soc_deadline_datetime is not None:
            moment = device.min_soc_deadline_datetime
            if moment.timestamp() < grid.observation.timestamp():
                self._deadline_adjusted(device, f"its deadline ({moment}) has passed")
            candidates.append(grid.slot_of(moment))
        if device.min_soc_max_duration_h is not None:
            if device.min_soc_max_duration_h * 3600 < grid.interval_seconds:
                self._deadline_adjusted(
                    device,
                    f"its deadline ({device.min_soc_max_duration_h} h) is shorter than one slot "
                    f"({grid.interval_seconds} s)",
                )
            candidates.append(
                math.floor(device.min_soc_max_duration_h * 3600 / grid.interval_seconds + 1e-9)
            )
        if not candidates:
            return None
        slot = max(min(candidates), 1)
        if slot >= grid.control_slots:
            return None
        return slot

    async def resolve(self) -> ElectricDevices:
        """Resolve all electrical devices.

        Raises:
            ValueError: For an inverter whose battery is not configured, a battery without
                inverter, no inverter at all, SoC for an unknown device, a plug-in series of
                the wrong length, or an appliance that cannot be planned.
        """
        config = self.config
        grid = self.grid
        groups = configured_devices(config)
        batteries, vehicles = groups["batteries"], groups["electric_vehicles"]
        unknown = set(self.soc) - {device.device_id for device in batteries + vehicles}
        if unknown:
            raise ValueError(f"SoC supplied for unconfigured devices: {sorted(unknown)}.")
        unknown = set(self.vehicle_available) - {device.device_id for device in vehicles}
        if unknown:
            raise ValueError(
                f"Plug-in times supplied for unconfigured vehicles: {sorted(unknown)}."
            )
        result = ElectricDevices()
        observation = grid.observation

        # A configuration error of a battery does not cancel the run: it gets default values
        # for what cannot be resolved, and an error is logged.
        for device in batteries:
            parameters, use_tables = await device.resolve_current_operating_parameters_or_defaults(
                observation
            )
            due = await self._full_charge(device)
            unit = device.to_storage_params(
                parameters, use_tables, initial_soc_percentage=await self._soc(device)
            )
            target: Optional[FullChargeTarget] = None
            if due is not None:
                # Due within the horizon: lift the upper limit so the battery can reach the
                # target; the target is enforced only if grid charging is allowed for it. It
                # lies within the technical SoC bounds.
                unit = replace(unit, max_soc_pct=max(unit.max_soc_pct, due[0]))
                target = due[1]
            result.batteries.append(unit)
            result.battery_ids.append(device.device_id)
            result.battery_settings.append(device)
            result.full_charge_targets.append(target)

        for device in vehicles:
            parameters, use_tables = await device.resolve_current_operating_parameters_or_defaults(
                observation
            )
            unit = device.to_storage_params(
                parameters,
                use_tables,
                initial_soc_percentage=await self._soc(device, is_vehicle=True),
                is_vehicle=True,
            )
            window = self.vehicle_available.get(device.device_id)
            if window is not None:
                if len(window) != grid.control_slots:
                    raise ValueError(
                        f"Plug-in times of {device.device_id} need {grid.control_slots} values "
                        f"(one per slot), got {len(window)}."
                    )
                unit = replace(unit, available=tuple(bool(v) for v in window))
            result.vehicles.append(unit)
            result.vehicle_ids.append(device.device_id)
            result.vehicle_settings.append(device)
            result.vehicle_targets.append(
                VehicleTarget(
                    float(parameters.min_soc_percentage), self._vehicle_deadline_slot(device)
                )
            )

        index_of = {device_id: index for index, device_id in enumerate(result.battery_ids)}
        attached: set[str] = set()
        for device in groups["inverters"]:
            battery_id = device.battery_id
            if battery_id is not None and battery_id not in index_of:
                raise ValueError(
                    f"Inverter {device.device_id} refers to battery {battery_id!r}, "
                    f"which is not configured in devices.batteries."
                )
            if battery_id is not None:
                if battery_id in attached:
                    raise ValueError(f"Battery {battery_id} has more than one inverter.")
                attached.add(battery_id)
            # The derating of the inverter profile applies at the measured ambient
            # temperature, 45 degC without a measurement.
            parameters = await device.resolve_current_operating_parameters_or_defaults(observation)
            result.inverters.append(
                device.to_inverter_params(
                    parameters, None if battery_id is None else index_of[battery_id]
                )
            )
            result.inverter_ids.append(device.device_id)
        if not result.inverters:
            raise ValueError("Configure an inverter to model PV and grid energy flows.")
        missing = [i for i in result.battery_ids if i not in attached]
        if missing:
            raise ValueError(
                f"Battery {missing[0]} has no inverter: set battery_id of an inverter to it."
            )

        slot0 = grid.start
        for device in groups["home_appliances"]:
            times = await self._appliance(device)
            planned = plan_appliance(times, slot0)
            result.appliance_ids.append(device.device_id)
            result.appliance_inputs.append(planned)
            result.appliances.append(
                ApplianceParams(
                    run_energy_wh=tuple(float(v) for v in times.run_energy_wh),
                    pinned_load_wh=(
                        tuple(float(v) for v in times.pinned_load)
                        if times.run_in_progress
                        else None
                    ),
                )
            )

        rates: set[float] = set()
        export: set[float] = set()
        for unit, device in zip(result.batteries, result.battery_settings):
            rates.update(r for r in unit.charge_rates if r > 0.0)
            export.update(r for r in (device.grid_export_rates or []) if r > 0.0)
        result.charge_rates = tuple(sorted(rates)) or (1.0,)
        result.export_rates = tuple(sorted(export, reverse=True)) or (1.0,)
        result.warnings = list(self.warnings)
        return result

    async def _run_started(
        self, device: HomeApplianceCommonSettings, observation: DateTime
    ) -> Optional[DateTime]:
        """Return the start of the appliance's run in progress from its measurement, or None.

        The latest report at or before the observation holds the run start as UNIX seconds, 0
        when the device reported the run as finished. An invalid report is logged and treated
        as no run in progress.
        """
        key = device.run_started_measurement_key or f"{device.device_id}.run_started_at"
        try:
            dates, values = await self.measurement.key_to_lists(
                key=key,
                start_datetime=observation.subtract(hours=RUN_STARTED_LOOKBACK_HOURS),
                end_datetime=observation.add(seconds=1),
                dropna=False,
            )
        except KeyError:
            return None
        latest = latest_value(dates, values, observation)
        if latest is None:
            return None
        value = latest[1]
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            seconds = math.nan
        if seconds == 0:
            return None
        if not math.isfinite(seconds) or seconds < 0 or seconds > observation.timestamp():
            logger.error(
                "Invalid run start {!r} in '{}' for home appliance '{}'; planned as not running.",
                value,
                key,
                device.device_id,
            )
            return None
        started = to_datetime(seconds)
        timezone = observation.timezone
        return started.in_timezone(timezone) if timezone is not None else started

    async def _completed_cycles(self, device: HomeApplianceCommonSettings, num_cycles: int) -> int:
        """Return the cycles the appliance completed today from its measurement (0 if unknown)."""
        observation = self.grid.observation
        key = device.cycles_completed_measurement_key or f"{device.device_id}.cycles_completed"
        try:
            dates, counts = await self.measurement.key_to_lists(
                key=key,
                start_datetime=observation.start_of("day"),
                end_datetime=observation.add(seconds=1),
                dropna=False,
            )
        except KeyError:
            return 0
        latest = latest_value(dates, counts, observation)
        if latest is None:
            return 0
        count = latest[1]
        if not math.isfinite(count) or int(count) != count or not 0 <= count <= num_cycles:
            raise ValueError(f"Invalid completed cycle count for {device.device_id}.")
        return int(count)

    async def _appliance(self, device: HomeApplianceCommonSettings) -> ApplianceTimes:
        """Return an appliance with its state: completed cycles and run in progress."""
        grid = self.grid
        started = await self._run_started(device, grid.observation)
        completed = await self._completed_cycles(device, device.effective_num_cycles)
        times = ApplianceTimes(
            device,
            grid.slot_duration_h,
            grid.control_slots,
            completed_cycles=completed,
            run_started=started,
        )
        times.pin_run_in_progress(grid.start, grid.observation)
        return times
