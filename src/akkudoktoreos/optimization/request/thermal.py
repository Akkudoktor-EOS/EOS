"""The thermal devices of a planning request: one per heat source with a thermal zone.

Each enabled heat pump source whose zone is enabled becomes one thermal device
(``thermal.planninginput.build_thermal_device``). What it needs and where it comes from:

- building model and distribution: ``thermal.zones.<zone>.model_parameters`` and
  ``.distribution`` (learned parameters are not stored in the configuration);
- heat pump with performance map: ``devices.heat_sources.<source>.heat_pump``;
- initial temperatures: the causal state estimate of ``thermal.thermallearning`` from the
  measured room, outdoor and heat source power history, projected to the run time;
- outdoor temperature per slot: the weather forecast (``weather_temp_air``);
- delivery temperature per slot: the heating curve or the fixed rating temperature;
- comfort band per slot: ``thermal.zones.<zone>.comfort``, lowered by the presence forecast and
  reported absences where a presence area is bound, with the protection minimum below it;
- operating state before slot 0: the measured heat source power.

Whatever is missing is an error that names it; nothing is assumed.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np
from loguru import logger

from akkudoktoreos.core.coreabc import ConfigMixin, MeasurementMixin, PredictionMixin
from akkudoktoreos.devices.heatpump import (
    HeatPumpProfile,
    evaluate_water_heating_curve,
    infer_thermal_power_from_electric,
)
from akkudoktoreos.devices.models.thermal import ThermalParams
from akkudoktoreos.devices.settings.heatsourcesettings import (
    HeatPumpConfiguration,
    HeatSourceSettings,
)
from akkudoktoreos.measurement.quality import MeasurementSample
from akkudoktoreos.optimization.request.grid import TimeGrid, read_series
from akkudoktoreos.optimization.solutionmodel import PlanningWarning
from akkudoktoreos.prediction.presence import PresenceCommonSettings, PresenceEstimate
from akkudoktoreos.prediction.weatherabc import WeatherDataRecord
from akkudoktoreos.thermal.planninginput import build_thermal_device
from akkudoktoreos.thermal.thermalcomfort import (
    DEFAULT_ABSENCE_MINIMUM_C,
    comfort_bands_from_presence,
    reported_absence_band,
)
from akkudoktoreos.thermal.thermaldistribution import (
    AirDistributionSettings,
    DirectDistributionSettings,
)
from akkudoktoreos.thermal.thermallearning import (
    estimate_thermal_state,
    latest_measurement,
    measured_samples,
    sample_values,
)
from akkudoktoreos.thermal.thermalmodels import ThermalBuildingRC2Parameters, ThermalComfortBand
from akkudoktoreos.thermal.thermalsettings import ThermalZoneSettings

# Rated power above which a heat source counts as running: its standby power plus the larger of
# half its configured minimum electric power and this floor [W].
RUNNING_THRESHOLD_FLOOR_W = 50.0

# Highest room target of the user that moves the comfort band [degC].
MAX_ROOM_TARGET_C = 24.0

# Code of the warning for a heat source that is left out of the planning.
THERMAL_NOT_PLANNED = "thermal_not_planned"

# Code of the warning for a heat plan that the solver could not compute.
THERMAL_HEAT_PLAN_FAILED = "thermal_heat_plan_failed"

# Code of the warning for a room that falls below its protection minimum in the plan.
THERMAL_PROTECTION_VIOLATED = "thermal_protection_violated"

# Failures of the process, not of the input: never turned into a warning about the heat source.
SYSTEM_ERRORS = (MemoryError, RecursionError, SystemError)


@dataclass(frozen=True, eq=False)
class ThermalDeviceInfo:
    """A thermal device of the problem with what the solution needs to describe its plan.

    Attributes:
        source_id: Device id of the heat source.
        zone_id: Id of the thermal zone it heats.
        params: The device of the planning problem.
        bands: Comfort band per slot (minimum, target, maximum room temperature).
        comfort_eur_per_kh: Price of a Kelvin hour outside the comfort band.
        protection_eur_per_kh: Additional price of a Kelvin hour below the protection minimum.
    """

    source_id: str
    zone_id: str
    params: ThermalParams
    bands: tuple[ThermalComfortBand, ...]
    comfort_eur_per_kh: float
    protection_eur_per_kh: float


def max_heat_w(profile: HeatPumpProfile) -> float:
    """Return the size of a heat pump: its largest heat in the performance map [W].

    The delivered heat of a defrost cannot take more heat from the room than this.
    """
    return max(
        [point.thermal_power_w for point in profile.thermal_max_points]
        + [point.thermal_power_w for point in profile.operating_points]
    )


def _latest(
    samples: list[MeasurementSample], now: datetime, age: int, what: str
) -> MeasurementSample:
    """Return the newest sample, which must be a fresh measured value; the error names the input."""
    try:
        return latest_measurement(samples, now, age)
    except ValueError as exc:
        raise ValueError(f"The measurement of {what} is unusable: {exc}.") from exc


def _quarter_start(moment: datetime) -> datetime:
    """Return the start of the quarter hour containing a moment."""
    return datetime.fromtimestamp(math.floor(moment.timestamp() / 900) * 900, moment.tzinfo)


class ThermalResolver(ConfigMixin, MeasurementMixin, PredictionMixin):
    """Resolve the thermal devices of one planning from configuration, measurements, forecasts."""

    def __init__(
        self,
        grid: TimeGrid,
        blocks: Optional[dict[str, list[Any]]] = None,
        absences: Optional[list[Any]] = None,
    ) -> None:
        """Prepare the resolver.

        Args:
            grid: Time grid of the planning.
            blocks: Per heat source id the periods (objects with ``start`` and ``end``) in
                which it must not run.
            absences: Reported absences of this run, for the presence forecast.
        """
        self.grid = grid
        self.blocks = blocks or {}
        self.absences = absences or []
        self._forecast: dict[str, tuple[list[PresenceEstimate], int]] = {}
        unknown = set(self.blocks) - {
            s.device_id for s in self.config.devices.heat_sources.values() if s.enabled
        }
        if unknown:
            raise ValueError(
                f"Blocks supplied for unknown or disabled heat sources: {sorted(unknown)}."
            )

    def _blocked(self, source_id: str) -> Optional[np.ndarray]:
        """Return the blocked mask per slot of a heat source, None without blocks."""
        periods = self.blocks.get(source_id)
        if not periods:
            return None
        grid = self.grid
        mask = np.zeros(grid.control_slots, dtype=bool)
        for slot in range(grid.control_slots):
            begin = grid.slot_start(slot).timestamp()
            end = begin + grid.interval_seconds
            mask[slot] = any(
                p.start.timestamp() < end and p.end.timestamp() > begin for p in periods
            )
        return mask

    async def resolve(self) -> list[ThermalDeviceInfo]:
        """Return one thermal device per enabled heat source (none without any).

        Raises:
            ValueError: For an incomplete or unsupported configuration, a missing measurement
                or forecast, or a state that cannot be estimated; the message names the input.
        """
        sources = [s for s in (self.config.devices.heat_sources or {}).values() if s.enabled]
        if not sources:
            return []
        result = []
        for source in sources:
            result.append(await self._device(source))
        return result

    async def resolve_available(self) -> tuple[list[ThermalDeviceInfo], list[PlanningWarning]]:
        """Return the thermal devices that can be set up and a warning for each that cannot.

        A heat source whose device cannot be built (stale or missing measurement, no outdoor
        forecast, incomplete configuration) is left out with a ``thermal_not_planned`` warning
        that names the cause, so the electrical planning (batteries, vehicles, appliances) is
        not lost with it. Nothing is hidden: the cause is logged and goes into the solution.
        A failure of the process itself (``MemoryError``, ``RecursionError``, ``SystemError``)
        says nothing about the heat source and is raised, not reported as a missing input.

        Raises:
            MemoryError: When the process runs out of memory.
            RecursionError: When the interpreter stack is exhausted.
            SystemError: For an internal error of the interpreter.
        """
        sources = [s for s in (self.config.devices.heat_sources or {}).values() if s.enabled]
        devices: list[ThermalDeviceInfo] = []
        warnings: list[PlanningWarning] = []
        for source in sources:
            try:
                device = await self._device(source)
                devices.append(device)
            except SYSTEM_ERRORS:
                raise
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                logger.warning(
                    "Heat source {} is not planned, the other devices are: {}",
                    source.device_id,
                    message,
                )
                warnings.append(
                    PlanningWarning(
                        code=THERMAL_NOT_PLANNED,
                        message=message,
                        devices=[source.device_id],
                    )
                )
        return devices, warnings

    @staticmethod
    def _heat_pump(source: HeatSourceSettings) -> HeatPumpConfiguration:
        """Return the heat pump of a source, which the settings guarantee for its kind."""
        if source.heat_pump is None:
            raise ValueError(f"Heat source {source.device_id} has no heat pump configuration.")
        return source.heat_pump

    def _zone(self, source: HeatSourceSettings) -> ThermalZoneSettings:
        """Return the enabled zone of a heat source."""
        if source.kind != "heat_pump" or source.heat_pump is None:
            raise ValueError(
                f"Heat source {source.device_id} is of kind {source.kind!r}; only heat pump "
                "sources with a performance map are planned as thermal devices."
            )
        if len(source.zone_ids) != 1:
            raise ValueError(
                f"Heat source {source.device_id} needs exactly one zone in zone_ids, "
                f"has {len(source.zone_ids)}."
            )
        zone_id = source.zone_ids[0]
        zone = self.config.thermal.zones.get(zone_id)
        if zone is None or not zone.enabled:
            raise ValueError(
                f"Thermal zone {zone_id!r} of heat source {source.device_id} is not configured "
                "or not enabled (thermal.zones)."
            )
        return zone

    async def _history(
        self, key: Optional[str], what: str, hours: int, until: datetime
    ) -> list[MeasurementSample]:
        """Read the samples of a measurement channel up to a moment."""
        if key is None:
            raise ValueError(f"A measurement key for {what} is required.")
        try:
            return await self.measurement.get_samples(
                key, until - timedelta(hours=hours), until + timedelta(seconds=1)
            )
        except ValueError as exc:
            raise ValueError(f"Measurement {what} ({key!r}): {exc}") from exc

    async def _occupied_band(
        self, zone: ThermalZoneSettings, configured: ThermalComfortBand
    ) -> ThermalComfortBand:
        """Move the occupied band with the room target the user set, when a fresh one exists.

        A missing, stale or implausible value leaves the configured band: the preparation of a
        planning never fails because of it.
        """
        key = zone.target_measurement_key
        if key is None:
            return configured
        try:
            samples = await self.measurement.get_samples(
                key,
                self.grid.observation - timedelta(seconds=zone.target_max_age_seconds),
                self.grid.observation + timedelta(seconds=1),
            )
            target = latest_measurement(
                samples, self.grid.observation, zone.target_max_age_seconds
            ).value
        except ValueError as exc:
            logger.debug("Room target of zone {} not used: {}", zone.zone_id, exc)
            return configured
        floor = zone.absence_minimum_temperature_c or DEFAULT_ABSENCE_MINIMUM_C
        if target is None or not floor <= target <= MAX_ROOM_TARGET_C:
            logger.debug("Room target {} of zone {} out of range, not used", target, zone.zone_id)
            return configured
        shift = target - configured.target_temperature_c
        return ThermalComfortBand(
            min_temperature_c=max(floor, configured.min_temperature_c + shift),
            target_temperature_c=target,
            max_temperature_c=max(configured.max_temperature_c, target),
        )

    async def _device(self, source: HeatSourceSettings) -> ThermalDeviceInfo:
        """Build the thermal device of one heat source."""
        grid = self.grid
        zone = self._zone(source)
        hp = self._heat_pump(source)
        profile = hp.resolve_profile()
        model = zone.model_parameters
        if not isinstance(model, ThermalBuildingRC2Parameters):
            raise ValueError(
                f"Thermal zone {zone.zone_id!r} needs calibrated building parameters in "
                "thermal.zones.<zone>.model_parameters (room_loss_per_hour, "
                "room_mass_exchange_per_hour, mass_capacity_ratio, heat_gain_kelvin_per_kwh); "
                "learned parameters are not stored in the configuration."
            )
        occupied = zone.comfort
        if occupied is None:
            raise ValueError(f"Thermal zone {zone.zone_id!r} needs a comfort band (thermal.zones).")
        occupied = await self._occupied_band(zone, occupied)
        distribution = zone.distribution or DirectDistributionSettings()
        slots = grid.control_slots
        observation = grid.observation
        age = self.config.thermal.measurement_max_age_seconds
        history_hours = zone.state_history_hours

        outdoor = await read_series(self.prediction, grid, "weather_temp_air")
        outdoor = outdoor[grid.first : grid.first + slots]
        if len(outdoor) < slots or not np.all(np.isfinite(outdoor)):
            raise ValueError(
                "The weather forecast lacks the outdoor temperature (weather_temp_air) for the "
                "control horizon; configure a weather provider that covers it."
            )
        values = await read_series(self.prediction, grid, "weather_relative_humidity")
        values = values[grid.first : grid.first + slots]
        humidity: Optional[np.ndarray] = (
            values if len(values) == slots and np.all(np.isfinite(values)) else None
        )

        delivery = self._delivery(hp, outdoor, occupied.target_temperature_c)
        source_temperature = await self._source_temperature(hp, profile.heat_pump_type, slots, age)

        room = measured_samples(
            await self._history(
                zone.room_temperature_measurement_key,
                f"room temperature of zone {zone.zone_id}",
                history_hours,
                observation,
            ),
            observation,
        )
        outside = measured_samples(
            await self._history(
                zone.outdoor_temperature_measurement_key,
                f"outdoor temperature of zone {zone.zone_id}",
                history_hours,
                observation,
            ),
            observation,
        )
        power = measured_samples(
            await self._history(
                source.electric_power_measurement_key,
                f"electric power of heat source {source.device_id}",
                history_hours,
                observation,
            ),
            observation,
        )
        initial = await self._initial_state(
            source, zone, model, distribution, room, outside, power, delivery, source_temperature
        )
        running = self._operating_state(source, power)
        bands = await self._bands(zone, occupied)
        weight = await self._under_weight(zone, source)
        lp_bands = bands
        if weight is not None:
            # Expected cost: the occupied minimum always applies, weighed with the probability.
            lp_bands = [
                band.model_copy(update={"min_temperature_c": occupied.min_temperature_c})
                for band in bands
            ]
        protection = (
            zone.absence_minimum_temperature_c
            if zone.absence_minimum_temperature_c is not None
            else DEFAULT_ABSENCE_MINIMUM_C
        )
        params = build_thermal_device(
            model,
            distribution,
            profile,
            outdoor_c=outdoor,
            comfort_bands=lp_bands,
            initial_temperature_c=initial,
            delivery_temperature_c=delivery,
            slot_seconds=float(grid.interval_seconds),
            first_slot_fraction=grid.first_slot_fraction,
            electrical=hp.electrical,
            initial_running=running,
            source_temperature_c=source_temperature,
            relative_humidity=humidity,
            blocked=self._blocked(source.device_id),
            defrost=hp.defrost,
            protection_min_c=float(protection),
            max_electric_power_w=source.max_electric_power_w,
            under_weight=weight,
            name=source.device_id,
        )
        return ThermalDeviceInfo(
            source_id=source.device_id,
            zone_id=zone.zone_id,
            params=params,
            bands=tuple(bands),
            comfort_eur_per_kh=source.comfort_penalty_eur_per_kh,
            protection_eur_per_kh=source.protection_penalty_eur_per_kh,
        )

    def _delivery(self, hp: Any, outdoor: np.ndarray, target_c: float) -> np.ndarray:
        """Return the delivery rating temperature per slot: heating curve or fixed value."""
        if hp.heating_curve is not None:
            return np.array(
                [
                    evaluate_water_heating_curve(
                        hp.heating_curve, WeatherDataRecord(weather_temp_air=float(t)), target_c
                    ).flow_temperature_c
                    for t in outdoor
                ]
            )
        if hp.delivery_temperature_c is not None:
            return np.full(len(outdoor), float(hp.delivery_temperature_c))
        raise ValueError(
            "Configure a delivery rating temperature or a water heating curve of the heat pump."
        )

    async def _source_temperature(
        self, hp: Any, heat_pump_type: str, slots: int, age: int
    ) -> Optional[np.ndarray]:
        """Return the source temperature per slot of water or brine sources, None for air."""
        if heat_pump_type.startswith("air_to_"):
            return None
        value = hp.source_temperature_c
        if value is None and hp.source_temperature_measurement_key is not None:
            samples = await self._history(
                hp.source_temperature_measurement_key,
                "source temperature of the heat pump",
                1,
                self.grid.observation,
            )
            value = latest_measurement(samples, self.grid.observation, age).value
        if value is None:
            raise ValueError(
                "Water and brine heat pumps need a source temperature: set "
                "heat_pump.source_temperature_c or a measurement key for it."
            )
        return np.full(slots, float(value))

    async def _initial_state(
        self,
        source: HeatSourceSettings,
        zone: ThermalZoneSettings,
        model: ThermalBuildingRC2Parameters,
        distribution: Any,
        room: list[MeasurementSample],
        outside: list[MeasurementSample],
        power: list[MeasurementSample],
        delivery: np.ndarray,
        source_temperature: Optional[np.ndarray],
    ) -> np.ndarray:
        """Estimate the temperatures at the run time from the measured history."""
        grid = self.grid
        now = grid.observation
        age = self.config.thermal.measurement_max_age_seconds
        hp = self._heat_pump(source)
        profile = hp.resolve_profile()
        heat_pump_size_w = max_heat_w(profile)
        current_power = _latest(power, now, age, f"the electric power of {source.device_id}")
        current_outside = _latest(outside, now, age, f"the outdoor temperature of {zone.zone_id}")
        heat_history: Optional[list[MeasurementSample]] = None
        if source.thermal_power_measurement_key is not None:
            heat_history = measured_samples(
                await self._history(
                    source.thermal_power_measurement_key,
                    f"delivered heat of heat source {source.device_id}",
                    zone.state_history_hours,
                    now,
                ),
                now,
            )
            threshold = source.thermal_power_idle_electric_threshold_w
            if threshold is not None:
                electric = sample_values(power, [row.date_time for row in heat_history], age)
                heat_history = [
                    row.model_copy(
                        update={
                            "value": None
                            if value is None
                            else (0.0 if value <= threshold else row.value)
                        }
                    )
                    for row, value in zip(heat_history, electric)
                ]
            heat = _latest(
                heat_history, now, age, f"the delivered heat of {source.device_id}"
            ).value
            # Negative delivered heat is a defrost that takes heat from the room: it counts,
            # but not more than the heat pump delivers at most (a sensor error otherwise).
            if heat is None or not math.isfinite(heat):
                raise ValueError("Delivered heat input must be fresh and finite.")
            if heat < -heat_pump_size_w:
                raise ValueError(
                    f"Delivered heat input {heat:.0f} W is below the plausible minimum "
                    f"{-heat_pump_size_w:.0f} W (the size of the heat pump, sensor error)."
                )
            input_override = heat
        else:
            record = WeatherDataRecord(weather_temp_air=current_outside.value)
            current_source = None if source_temperature is None else float(source_temperature[0])
            try:
                estimate = infer_thermal_power_from_electric(
                    profile,
                    record,
                    float(delivery[0]),
                    max(0.0, current_power.value or 0.0),
                    hp.electrical,
                    source_temperature_c=current_source,
                )
            except ValueError as exc:
                raise ValueError(
                    f"The delivered heat of {source.device_id} cannot be inferred from its "
                    f"electric power ({exc}); bind a delivered heat measurement in "
                    "devices.heat_sources.<source>.thermal_power_measurement_key."
                ) from exc
            input_override = estimate.thermal_power_w
        air = isinstance(distribution, AirDistributionSettings)
        room_now = _latest(room, now, age, f"the room temperature of {zone.zone_id}")
        anchor = room_now.date_time if air else now
        try:
            state, _ = estimate_thermal_state(
                model,
                room,
                outside,
                power,
                now=anchor,
                start=anchor,
                max_age_seconds=age,
                minimum_history_hours=zone.minimum_state_history_hours,
                negative_idle_tolerance_w=source.idle_power_tolerance_w,
                model_input_power_w=input_override,
                model_input_history=heat_history,
                room_observation_window_seconds=zone.state_room_observation_window_seconds,
                min_model_input_w=-heat_pump_size_w,
            )
        except ValueError as exc:
            raise ValueError(
                f"The thermal state of zone {zone.zone_id!r} cannot be estimated: {exc}"
            ) from exc
        values = [state.room_temperature_c, state.mass_temperature_c]
        if air:
            key = zone.distribution_measurements.air_temperature_measurement_key
            if key is None:
                raise ValueError(
                    f"Thermal zone {zone.zone_id!r} has an air distribution and needs "
                    "distribution_measurements.air_temperature_measurement_key."
                )
            samples = await self._history(
                key, f"air temperature of zone {zone.zone_id}", zone.state_history_hours, now
            )
            air_sample = _latest(samples, anchor, age, f"the air temperature of {zone.zone_id}")
            if air_sample.value is None:
                raise ValueError(f"The air temperature measurement {key!r} has no value.")
            values.append(float(air_sample.value))
        return np.array(values, dtype=float)

    def _operating_state(self, source: HeatSourceSettings, power: list[MeasurementSample]) -> bool:
        """Return whether the heat source was running before slot 0.

        It counts as running when its electric power is at or above its standby power plus the
        larger of half its minimum electric power and ``RUNNING_THRESHOLD_FLOOR_W``.
        """
        hp = self._heat_pump(source)
        threshold = (hp.electrical.standby_power_w or 0.0) + max(
            0.5 * source.min_electric_power_w, RUNNING_THRESHOLD_FLOOR_W
        )
        rows = [
            (row.date_time.timestamp(), row.value >= threshold)
            for row in power
            if row.value is not None and math.isfinite(row.value)
        ]
        if not rows:
            raise ValueError(
                f"No electric power measurement of heat source {source.device_id} to tell "
                "whether it was running."
            )
        return rows[-1][1]

    async def _presence(
        self, zone: ThermalZoneSettings
    ) -> Optional[tuple[list[PresenceEstimate], int]]:
        """Return the presence estimates per quarter hour and the index of the first on the grid.

        None without a presence forecast for the zone. One forecast serves the whole run.
        """
        grid = self.grid
        if zone.presence_area_id is None or self.config.presence.provider != "PresenceHistory":
            return None
        if zone.zone_id in self._forecast:
            return self._forecast[zone.zone_id]
        provider = self.prediction.provider_by_id("PresenceHistory")
        first_quarter = _quarter_start(grid.observation)
        start_index = max(int((first_quarter.timestamp() - grid.start.timestamp()) // 900), 0)
        total = grid.control_slots * (grid.interval_seconds // 900)
        if total - start_index > 288:
            raise ValueError(
                "The presence forecast covers at most 288 quarter hours (72 h); "
                "shorten the control horizon."
            )
        starts = [
            datetime.fromtimestamp(grid.start.timestamp() + i * 900, grid.start.tzinfo)
            for i in range(start_index, total)
        ]
        presence = self.config.presence
        settings = PresenceCommonSettings.model_validate(
            {
                **presence.model_dump(),
                "absence_delay_seconds": max(
                    presence.absence_delay_seconds, self.config.thermal.absence_delay_seconds
                ),
                "sample_max_age_seconds": min(
                    presence.sample_max_age_seconds, self.config.thermal.presence_max_age_seconds
                ),
            }
        )
        # The provider takes no quarter hour that has begun, and a run is rarely on a boundary:
        # the quarter of the run takes the estimate of the first one ahead.
        begun = starts[0] < grid.observation
        asked = starts[1:] if begun else starts
        if not asked:
            asked = [starts[0] + timedelta(seconds=900)]
        estimates: list[PresenceEstimate] = await provider.forecast_zone(
            zone.presence_area_id,
            now=grid.observation,
            starts=asked,
            settings=settings,
            publish=False,
            absences=self.absences,
        )
        if begun:
            first = estimates[0].model_copy(update={"date_time": starts[0]})
            estimates = [first, *estimates][: len(starts)]
        self._forecast[zone.zone_id] = (estimates, start_index)
        return estimates, start_index

    def _quarters(self, slot: int, start_index: int, count: int) -> list[int]:
        """Return the indices of the quarter hours of a slot; the passed ones take the first ahead."""
        per_slot = self.grid.interval_seconds // 900
        return [
            min(max(q - start_index, 0), count - 1)
            for q in range(slot * per_slot, (slot + 1) * per_slot)
        ]

    async def _bands(
        self, zone: ThermalZoneSettings, occupied: ThermalComfortBand
    ) -> list[ThermalComfortBand]:
        """Return the comfort band per slot from the zone and the presence forecast."""
        forecast = await self._presence(zone)
        if forecast is None:
            return [occupied] * self.grid.control_slots
        estimates, start_index = forecast
        reported = (
            reported_absence_band(
                occupied, zone.unoccupied_comfort, zone.absence_minimum_temperature_c
            )
            if any(row.source == "reported_absence" for row in estimates)
            else None
        )
        quarter_bands = comfort_bands_from_presence(
            estimates,
            occupied,
            zone.unoccupied_comfort,
            preheat_seconds=0,
            allow_warm_coasting=zone.allow_warm_coasting,
            reported_absence=reported,
        )
        # A slot takes the strictest band of its quarter hours.
        return [
            max(
                (quarter_bands[q] for q in self._quarters(slot, start_index, len(quarter_bands))),
                key=lambda band: band.min_temperature_c,
            )
            for slot in range(self.grid.control_slots)
        ]

    async def _under_weight(
        self, zone: ThermalZoneSettings, source: HeatSourceSettings
    ) -> Optional[np.ndarray]:
        """Return the weight of the shortfall below the comfort minimum per slot and tier.

        The presence probability of the slot (the highest of its quarter hours, an unknown one
        counts as 1) times the tier price over the comfort price. None without a presence forecast.
        """
        forecast = await self._presence(zone)
        if forecast is None:
            return None
        estimates, start_index = forecast
        chance = [1.0 if row.probability is None else row.probability for row in estimates]
        probability = np.array(
            [
                max(chance[q] for q in self._quarters(slot, start_index, len(chance)))
                for slot in range(self.grid.control_slots)
            ]
        )
        price = max(source.comfort_penalty_eur_per_kh, 1e-12)
        prices = (
            source.shortfall_price_first_eur_per_kh,
            source.shortfall_price_second_eur_per_kh,
            source.comfort_penalty_eur_per_kh,
        )
        ratios = [min(p / price, 1.0) for p in prices]
        return probability[:, None] * np.array(ratios)[None, :]
