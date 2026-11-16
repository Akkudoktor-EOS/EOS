"""Resolve optional thermal shadow plans from native EOS configuration and data.

No installation addresses, SQL queries, device commands, EMS publication or
implicit forecast fetches belong here. An explicit native bundle is admitted,
stored through Measurement/Prediction, then resolved under a coherent snapshot.
"""

import asyncio
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from loguru import logger
from pydantic import Field, field_validator, model_validator

from akkudoktoreos.config.config import ConfigEOS, SettingsEOSDefaults
from akkudoktoreos.devices.heatpump import (
    evaluate_heat_pump,
    evaluate_water_heating_curve,
    get_heat_pump_operating_points,
    infer_thermal_power_from_electric,
)
from akkudoktoreos.measurement.energy import energy_intervals
from akkudoktoreos.measurement.measurement import Measurement
from akkudoktoreos.measurement.quality import MeasurementSample
from akkudoktoreos.optimization.fleet import FleetRequest
from akkudoktoreos.prediction.prediction import Prediction
from akkudoktoreos.prediction.presence import (
    PresenceAbsence,
    PresenceCommonSettings,
    PresenceCorrection,
    PresenceHistory,
)
from akkudoktoreos.prediction.weatherabc import WeatherDataRecord
from akkudoktoreos.prediction.weatherimport import WeatherImport
from akkudoktoreos.thermal.thermalcomfort import (
    apply_comfort_windows,
    comfort_bands_from_presence,
    reported_absence_band,
)
from akkudoktoreos.thermal.thermaldistribution import (
    AirDistributionSettings,
    DirectDistributionSettings,
    ThermalDistributionSettings,
    WaterDistributionSettings,
    build_distribution_dynamics,
    distribution_additional_state_names,
    distribution_temperature_bounds,
    source_flow_conductance_w_per_k,
    source_temperature_state_index,
)
from akkudoktoreos.thermal.thermaldistributionlearning import (
    estimate_emitter_temperature,
)
from akkudoktoreos.thermal.thermalheatplanning import (
    ThermalHeatPlanningRequest,
    minimum_power_warning,
    optimize_heat_pump_shadow,
)
from akkudoktoreos.thermal.thermallearning import (
    RoomModel,
    estimate_thermal_state,
    fit_room_model,
    latest_measurement,
    measured_samples,
    rc2_transition,
    sample_values,
)
from akkudoktoreos.thermal.thermalmodels import (
    FiniteNumber,
    NonemptyText,
    NonnegativeRevision,
    TemperatureInterval,
    ThermalBuildingRC2Parameters,
    ThermalComfortBand,
    ThermalModel,
    ThermalRC2Parameters,
    ThermalState,
    UTCDatetime,
)
from akkudoktoreos.thermal.thermaloptimization import (
    ThermalRecoverySettings,
    ThermalShadowRequest,
    optimize_thermal_shadow,
)
from akkudoktoreos.thermal.thermalsettings import ThermalZoneSettings
from akkudoktoreos.utils.datetimeutil import to_datetime

_MINIMUM_POWER_WARNED: set[str] = set()


def thermal_now() -> datetime:
    """Return the native admission clock, independently patchable in tests."""
    return datetime.now(timezone.utc)


class ThermalDataBundle(ThermalModel):
    """A coherent native electrical/weather snapshot plus original measured samples."""

    fleet: FleetRequest
    load_basis: Literal["residual_excluding_heat"]
    forecast_issued_at: UTCDatetime
    sampled_at: UTCDatetime
    weather: tuple[WeatherDataRecord, ...] = Field(min_length=1, max_length=288)
    samples: tuple[MeasurementSample, ...] = Field(default=(), max_length=10000)
    control_revision: NonnegativeRevision | None = None
    quality_notes: tuple[str, ...] = Field(default=(), max_length=64)
    distribution_fan_levels: dict[str, tuple[NonemptyText, ...]] = Field(default_factory=dict)
    distribution_load_excluded_zone_ids: tuple[NonemptyText, ...] = ()
    source_temperature_forecasts: dict[str, tuple[FiniteNumber, ...]] = Field(default_factory=dict)

    @field_validator("weather", mode="before")
    @classmethod
    def finite_weather(cls, value: Any) -> Any:
        """Keep native coercion from accepting booleans as actual weather."""
        if isinstance(value, (tuple, list)):
            for row in value:
                for key in ("weather_temp_air", "weather_relative_humidity", "weather_dew_point"):
                    item = row.get(key) if isinstance(row, dict) else getattr(row, key, None)
                    if isinstance(item, bool) or (
                        item is not None
                        and (not isinstance(item, (int, float)) or not math.isfinite(item))
                    ):
                        raise ValueError("Weather values must be finite numeric values")
        return value

    @model_validator(mode="after")
    def aligned_snapshot(self) -> "ThermalDataBundle":
        """Require complete native horizons and no future measurement relabelling."""
        if self.fleet.interval_seconds != 900 or len(self.weather) != len(self.fleet.load_w):
            raise ValueError("Native thermal data requires one complete quarter-hour horizon")
        for i, weather in enumerate(self.weather):
            if weather.date_time != self.fleet.start_datetime + timedelta(seconds=i * 900):
                raise ValueError("Weather records must exactly match native fleet timestamps")
            if weather.weather_temp_air is None:
                raise ValueError("Outdoor forecast temperatures are required")
        for key, values in self.source_temperature_forecasts.items():
            if (
                not key.strip()
                or len(values) != len(self.weather)
                or not all(math.isfinite(v) for v in values)
            ):
                raise ValueError("Source temperature forecasts require complete finite horizons")
        if any(not key.strip() for key in self.distribution_fan_levels):
            raise ValueError("Distribution fan schedules require nonblank zone IDs")
        if any(
            len(values) != len(self.weather) for values in self.distribution_fan_levels.values()
        ):
            raise ValueError("Distribution fan schedules must exactly match the native horizon")
        if len(set(self.distribution_load_excluded_zone_ids)) != len(
            self.distribution_load_excluded_zone_ids
        ):
            raise ValueError(
                "Distribution load acknowledgements must not contain duplicate zone IDs"
            )
        if self.forecast_issued_at > self.sampled_at + timedelta(seconds=30):
            raise ValueError("Forecast issue time is after the sampling boundary")
        if any(
            sample.date_time > self.sampled_at + timedelta(seconds=30) for sample in self.samples
        ):
            raise ValueError("A sample lies after its bundle sampling boundary")
        return self


class ThermalConfigRequest(ThermalModel):
    """Runtime intent only; source maps, measurements and preferences use EOS config."""

    zone_id: NonemptyText
    heat_source_id: NonemptyText
    basis_control_revision: NonnegativeRevision
    presence_revision: NonnegativeRevision | None = None
    revision: NonnegativeRevision | None = None
    mode: Literal["auto", "effective_electric", "heat_pump"] = "auto"
    target_temperature_c: FiniteNumber | None = Field(default=None, ge=5, le=35)
    max_electric_power_w: FiniteNumber | None = Field(default=None, gt=0)
    master_power: Literal["On", "Off"] = "On"
    presence_correction: PresenceCorrection | None = None
    absences: tuple[PresenceAbsence, ...] = Field(
        default=(),
        max_length=16,
        description="Reported absences for this plan run (added to presence.absences).",
    )
    comfort_windows: tuple[TemperatureInterval, ...] = Field(default=(), max_length=384)
    rest_slot_mask: tuple[bool, ...] = ()
    comfort_windows_policy: Literal["override", "presence_fallback", "economic_setback"] = (
        "override"
    )

    @model_validator(mode="after")
    def validate_comfort_windows(self) -> "ThermalConfigRequest":
        """Require explicit, bounded and unambiguous runtime comfort requirements."""
        apply_comfort_windows([], (), self.comfort_windows)
        if any(w.min_temperature_c < 5 or w.max_temperature_c > 35 for w in self.comfort_windows):
            raise ValueError("Comfort requirements must remain between 5 and 35 Celsius")
        return self


class ThermalRuntimeContext:
    """Native non-actuating data pipeline independent of an operational EMS run."""

    def __init__(self, config: ConfigEOS, measurement: Measurement, prediction: Prediction):
        self.config, self.measurement, self.prediction = config, measurement, prediction
        self._bundle: ThermalDataBundle | None = None
        self._data_lock = asyncio.Lock()
        self._solve_lock = asyncio.Lock()
        self._model_cache: dict[str, tuple[RoomModel, dict[str, Any]]] = {}
        self._last: dict[str, Any] | None = None
        self._generation = 0
        self._intent_generation = 0
        self.absence_store: Any = None  # run-time absences (REST); None = feature unused
        self._presence_forecasts: dict[str, list[dict[str, Any]]] = {}
        self._presence_issued_at: str | None = None

    def _config_fingerprint(self) -> str:
        """Detect concurrent settings changes without copying the singleton."""
        raw = self.config.model_dump(mode="json", exclude_computed_fields=True)
        return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()

    def status(self) -> dict[str, Any]:
        """Identify native data age and the last proposal without implying execution."""
        return {
            "shadow_only": True,
            "execution_allowed": False,
            "generation": self._generation,
            "has_bundle": self._bundle is not None,
            "forecast_issued_at": self._bundle.forecast_issued_at.isoformat()
            if self._bundle
            else None,
            "sampled_at": self._bundle.sampled_at.isoformat() if self._bundle else None,
            "last_plan": self._last,
            "presence_forecasts": self._presence_forecasts,
            "presence_issued_at": self._presence_issued_at,
        }

    async def ingest(self, bundle: ThermalDataBundle) -> dict[str, Any]:
        """Validate all channels before writes and atomically publish a complete context."""
        bundle = ThermalDataBundle.model_validate(bundle.model_dump(exclude_computed_fields=True))
        now = thermal_now()
        if not -30 <= (now - bundle.sampled_at).total_seconds() <= 180:
            raise ValueError("Native data bundle sampling time is stale or in the future")
        if self.config.thermal.mode != "shadow":
            raise ValueError("Native thermal data ingestion requires mode=shadow")
        for sample in bundle.samples:
            self.measurement.measurement_channel(sample.key)
        weather = self.prediction.provider_by_id("WeatherImport")
        if not isinstance(weather, WeatherImport):
            raise ValueError("Native weather import provider is unavailable")
        for zone_id, levels in bundle.distribution_fan_levels.items():
            zone = self.config.thermal.zones.get(zone_id)
            if zone is None or not isinstance(zone.distribution, AirDistributionSettings):
                raise ValueError("Fan schedules require a configured air-distribution zone")
            allowed = {level.level_id for level in zone.distribution.fan_levels}
            if any(level not in allowed for level in levels):
                raise ValueError("Native fan schedule contains an unknown level ID")
        for zone_id in bundle.distribution_load_excluded_zone_ids:
            zone = self.config.thermal.zones.get(zone_id)
            if zone is None or zone.distribution is None or zone.distribution.kind == "direct":
                raise ValueError(
                    "Distribution load acknowledgement requires a configured air/water zone"
                )
        async with self._data_lock:
            if self._bundle and bundle.forecast_issued_at < self._bundle.forecast_issued_at:
                raise ValueError("Native forecast snapshots must not move backwards")
            await self.measurement.import_samples(list(bundle.samples))
            for record in bundle.weather:
                await weather.insert_by_datetime(record)
            weather.update_datetime = to_datetime(bundle.forecast_issued_at)
            await self.measurement.save()
            self._bundle = bundle
            self._generation += 1
            # Occupancy is an independent native prediction, even when a user
            # has switched heating off. It must not depend on a successful solve.
            if self.config.presence.provider == "PresenceHistory":
                provider = self.prediction.provider_by_id("PresenceHistory")
                if not isinstance(provider, PresenceHistory):
                    raise ValueError("Native presence provider is unavailable")
                starts = [
                    row.date_time
                    for row in bundle.weather
                    if row.date_time is not None and row.date_time >= now
                ]
                self._presence_forecasts = {
                    area: [
                        row.model_dump(mode="json")
                        for row in await provider.forecast_zone(area, now=now, starts=starts)
                    ]
                    for area in self.config.presence.zones
                }
                self._presence_issued_at = now.isoformat()
        return {
            "shadow_only": True,
            "execution_allowed": False,
            "generation": self._generation,
            "samples_imported": len(bundle.samples),
            "weather_intervals_imported": len(bundle.weather),
            "presence_forecasts": self._presence_forecasts,
            "presence_issued_at": self._presence_issued_at,
        }

    async def _history(
        self, key: str | None, start: datetime, now: datetime
    ) -> list[MeasurementSample]:
        if key is None:
            raise ValueError("A required native measurement binding is missing")
        return await self.measurement.get_samples(key, start, now + timedelta(seconds=1))

    async def _distribution_initial_state(
        self,
        zone: ThermalZoneSettings,
        model: ThermalBuildingRC2Parameters,
        distribution: ThermalDistributionSettings,
        room: list[MeasurementSample],
        outdoor: list[MeasurementSample],
        power: list[MeasurementSample],
        *,
        now: datetime,
        start: datetime,
        age: int,
        thermal_input_w: float,
        source_delivery_temperature_c: float,
        negative_idle_tolerance_w: float,
    ) -> tuple[ThermalState, tuple[float, ...], datetime, dict[str, Any]]:
        """Initialize coherent causal nodes, then project the entire state together."""
        import numpy as np

        anchor = latest_measurement(room, now, age).date_time
        base, notes = estimate_thermal_state(
            model,
            room,
            outdoor,
            power,
            now=anchor,
            start=anchor,
            max_age_seconds=age,
            minimum_history_hours=zone.minimum_state_history_hours,
            negative_idle_tolerance_w=negative_idle_tolerance_w,
            model_input_power_w=0.0,
        )
        histories: dict[str, list[MeasurementSample]] = {}
        observed: dict[str, MeasurementSample] = {}
        extra, dates, details = [], [], []
        names = distribution_additional_state_names(distribution)
        for name in names:
            key = getattr(zone.distribution_measurements, name[:-2] + "_measurement_key")
            if key is None:
                if name == "emitter_temperature_c" and zone.distribution_observer is not None:
                    continue
                raise ValueError(f"Distribution state {name} requires a native temperature binding")
            channel = self.measurement.measurement_channel(key)
            if (channel.quantity, channel.unit) != ("temperature", "degC"):
                raise ValueError(f"Distribution binding {key!r} requires temperature/degC")
            rows = await self._history(key, now - timedelta(hours=zone.state_history_hours), now)
            histories[name] = rows
            try:
                latest_measurement(rows, now, age)
                sample = latest_measurement(rows, anchor, age)
                if (now - sample.date_time).total_seconds() > age:
                    raise ValueError("Distribution sample before the common anchor is stale")
                observed[name] = sample
            except ValueError:
                if name != "emitter_temperature_c" or zone.distribution_observer is None:
                    raise
        for name in names:
            state_sample = observed.get(name)
            if state_sample is not None and state_sample.value is not None:
                extra.append(state_sample.value)
                dates.append(state_sample.date_time)
                details.append(
                    {
                        "state": name,
                        "measurement_key": state_sample.key,
                        "observed_at": state_sample.date_time.isoformat(),
                        "estimated": False,
                    }
                )
            else:
                if (
                    not isinstance(distribution, WaterDistributionSettings)
                    or zone.distribution_observer is None
                ):
                    raise ValueError(
                        "Distribution state is unavailable and no bounded observer is configured"
                    )
                observer = zone.distribution_observer
                bounds = distribution.emitter_temperature_bounds
                estimate = estimate_emitter_temperature(
                    room,
                    histories["water_temperature_c"],
                    now=anchor,
                    capacity_j_per_k=distribution.emitter_capacity_j_per_k,
                    water_conductance_w_per_k=distribution.water_to_emitter_conductance_w_per_k,
                    room_conductance_w_per_k=distribution.emitter_to_room_conductance_w_per_k,
                    minimum_temperature_c=bounds.min_temperature_c,
                    maximum_temperature_c=bounds.max_temperature_c,
                    maximum_initial_uncertainty_c=observer.maximum_emitter_uncertainty_c,
                    max_age_seconds=age,
                    minimum_history_hours=zone.minimum_state_history_hours,
                    maximum_steps=observer.maximum_steps,
                )
                if (now - estimate.based_on_observed_at).total_seconds() > age:
                    raise ValueError("Emitter observation inputs are stale at request time")
                extra.append(estimate.temperature_c)
                dates.append(estimate.based_on_observed_at)
                details.append(
                    {"state": name, "estimated": True, "observer": estimate.model_dump(mode="json")}
                )
        initial = np.array([base.room_temperature_c, base.mass_temperature_c, *extra])
        node_bounds = distribution_temperature_bounds(distribution)

        def check_bounds(values: np.ndarray) -> None:
            if any(
                value < low - 1e-5 or value > high + 1e-5
                for value, (low, high) in zip(values[2:], node_bounds)
            ):
                raise ValueError(
                    "Distribution initialization/projection exceeds calibrated state bounds"
                )

        check_bounds(initial)
        flow_g = source_flow_conductance_w_per_k(distribution)
        recipient = source_temperature_state_index(distribution)

        def check_source_flow(values: np.ndarray) -> None:
            if flow_g is not None and recipient is not None:
                maximum_heat = flow_g * max(0.0, source_delivery_temperature_c - values[recipient])
                if thermal_input_w > maximum_heat + 1e-5:
                    raise ValueError(
                        "Estimated source heat/input hold contradicts the declared flow and delivery temperature"
                    )

        check_source_flow(initial)
        outside_value = latest_measurement(outdoor, now, age).value
        if outside_value is None:
            raise ValueError("Distribution projection requires measured outdoor temperature")
        current = initial
        elapsed = 0.0
        duration = (start - anchor).total_seconds()
        while elapsed < duration:
            seconds = min(60.0, duration - elapsed)
            a, b, e = build_distribution_dynamics(model, distribution, seconds=seconds)
            current = a @ current + b * thermal_input_w + e * outside_value
            check_bounds(current)
            check_source_flow(current)
            elapsed += seconds
        state = ThermalState(
            estimated_at=start,
            based_on_observed_at=anchor,
            room_temperature_c=float(current[0]),
            mass_temperature_c=float(current[1]),
        )
        return (
            state,
            tuple(float(value) for value in current[2:]),
            min(dates),
            {
                **notes,
                "room_observation_age_seconds": (now - anchor).total_seconds(),
                "power_observation_age_seconds": (
                    now - latest_measurement(power, now, age).date_time
                ).total_seconds(),
                "outdoor_observation_age_seconds": (
                    now - latest_measurement(outdoor, now, age).date_time
                ).total_seconds(),
                "distribution_states": details,
                "projection_seconds": duration,
                "full_state_projected_together": True,
                "projection_input_hold_assumed": True,
                "extra_observations_held_to_room_anchor": True,
                "pre_origin_distribution_profile": "Configured fixed default; future fan schedule starts at forecast origin",
                "distribution_initial_state_uncertainty_is_not_a_model_error_bound": True,
            },
        )

    def _reported_absences(self, intent: ThermalConfigRequest) -> tuple[Any, ...]:
        """Absences of the request plus the run-time store; empty (bit-identical) without any."""
        stored = self.absence_store.planning_absences() if self.absence_store is not None else ()
        return (*intent.absences, *stored)

    async def _resolve(self, intent: ThermalConfigRequest) -> tuple[Any, dict[str, Any]]:
        now = thermal_now()
        settings = SettingsEOSDefaults.model_validate(
            self.config.model_dump(exclude_computed_fields=True)
        )
        if settings.thermal.mode != "shadow":
            raise ValueError("Native thermal planning requires mode=shadow")
        zone = settings.thermal.zones.get(intent.zone_id)
        source = settings.devices.heat_sources.get(intent.heat_source_id)
        if (
            zone is None
            or not zone.enabled
            or source is None
            or not source.enabled
            or zone.zone_id not in source.zone_ids
        ):
            raise ValueError("An enabled, consistently bound zone and heat source are required")
        power_key = source.electric_power_measurement_key
        if power_key is None:
            raise ValueError("A heat-source electrical measurement binding is required")
        if source.zone_ids != [zone.zone_id]:
            raise ValueError("The first native planner supports exactly one zone per source")
        for key, quantity, unit in (
            (zone.room_temperature_measurement_key, "temperature", "degC"),
            (zone.outdoor_temperature_measurement_key, "temperature", "degC"),
            (power_key, "power", "W"),
        ):
            if key is None:
                raise ValueError("A required native measurement binding is missing")
            channel = self.measurement.measurement_channel(key)
            if (channel.quantity, channel.unit) != (quantity, unit):
                raise ValueError(f"Native binding {key!r} requires {quantity}/{unit}")
        if self._bundle is None:
            raise ValueError("No native forecast snapshot has been admitted")
        bundle = ThermalDataBundle.model_validate(
            self._bundle.model_dump(exclude_computed_fields=True)
        )
        if not -30 <= (now - bundle.forecast_issued_at).total_seconds() <= 1800:
            raise ValueError("Native electrical/weather forecasts are stale or in the future")
        if (
            bundle.control_revision is not None
            and bundle.control_revision != intent.basis_control_revision
        ):
            raise ValueError("Runtime intent no longer matches the sampled manual control revision")
        start = bundle.fleet.start_datetime
        if not 0 <= start.timestamp() - now.timestamp() <= 900:
            raise ValueError("A fresh prospective native fleet origin is required")
        mode = (
            ("heat_pump" if source.kind == "heat_pump" else "effective_electric")
            if intent.mode == "auto"
            else intent.mode
        )
        distribution = zone.distribution or DirectDistributionSettings()
        distributed = not isinstance(distribution, DirectDistributionSettings)
        if distributed and mode != "heat_pump":
            raise ValueError(
                "Air/water distribution requires heat-pump mode and thermal building calibration"
            )
        fan_levels = bundle.distribution_fan_levels.get(zone.zone_id)
        first = now - timedelta(hours=zone.state_history_hours)
        room = await self._history(zone.room_temperature_measurement_key, first, now)
        outside = await self._history(zone.outdoor_temperature_measurement_key, first, now)
        power = await self._history(source.electric_power_measurement_key, first, now)
        room = measured_samples(room, now)
        outside = measured_samples(outside, now)
        power = measured_samples(power, now)
        age = settings.thermal.measurement_max_age_seconds
        current_outside = latest_measurement(outside, now, age)
        current_power = latest_measurement(power, now, age)
        if current_power.value is not None and current_power.value < -source.idle_power_tolerance_w:
            raise ValueError("Negative source power exceeds the declared idle-noise tolerance")
        if source.heat_pump is None and mode == "heat_pump":
            raise ValueError("Heat-pump mode requires a selected source map")
        hp = source.heat_pump
        profile = hp.resolve_profile() if hp else None
        heat_history = None
        current_heat = None
        if mode == "heat_pump" and source.thermal_power_measurement_key:
            channel = self.measurement.measurement_channel(source.thermal_power_measurement_key)
            if (channel.quantity, channel.unit) != ("power", "W"):
                raise ValueError("Delivered heat measurement requires power/W")
            heat_history = measured_samples(
                await self._history(source.thermal_power_measurement_key, first, now), now
            )
            threshold = source.thermal_power_idle_electric_threshold_w
            if threshold is not None:
                electric_at_heat = sample_values(
                    power, [row.date_time for row in heat_history], age
                )
                heat_history = [
                    row.model_copy(
                        update={
                            "value": None
                            if electric is None
                            else (0.0 if electric <= threshold else row.value)
                        }
                    )
                    for row, electric in zip(heat_history, electric_at_heat)
                ]
            current_heat = latest_measurement(heat_history, now, age).value
            if current_heat is None or current_heat < 0:
                raise ValueError("Delivered heat input must be fresh and nonnegative")
        source_history = None
        source_forecast = bundle.source_temperature_forecasts.get(source.device_id)
        current_source = hp.source_temperature_c if hp else None
        if hp and hp.source_temperature_measurement_key:
            channel = self.measurement.measurement_channel(hp.source_temperature_measurement_key)
            if (channel.quantity, channel.unit) != ("temperature", "degC"):
                raise ValueError("Source inlet measurement requires temperature/degC")
            source_history = await self._history(hp.source_temperature_measurement_key, first, now)
            current_source = latest_measurement(source_history, now, age).value
        if source_forecast is None and hp and hp.source_temperature_c is not None:
            source_forecast = (hp.source_temperature_c,) * len(bundle.weather)
        if profile and not profile.heat_pump_type.startswith("air_to_"):
            if current_source is None or source_forecast is None:
                raise ValueError(
                    "Water/brine sources require current inlet temperature and a declared forecast"
                )
        if distributed and profile is not None:
            expected = (
                "_to_air" if isinstance(distribution, AirDistributionSettings) else "_to_water"
            )
            if not profile.heat_pump_type.endswith(expected):
                raise ValueError("Heat-pump type and distribution type must agree")
        if hp and hp.heating_curve and profile and not profile.heat_pump_type.endswith("_to_water"):
            raise ValueError("Water heating curves cannot be applied to an air-to-air appliance")
        occupied = zone.comfort
        if occupied is None:
            raise ValueError("Occupied temperature preferences are required")
        if intent.target_temperature_c is not None:
            shift = intent.target_temperature_c - occupied.target_temperature_c
            occupied = ThermalComfortBand(
                min_temperature_c=occupied.min_temperature_c + shift,
                target_temperature_c=intent.target_temperature_c,
                max_temperature_c=occupied.max_temperature_c + shift,
            )
        delivery = []
        for weather in bundle.weather:
            if hp and hp.heating_curve:
                delivery.append(
                    evaluate_water_heating_curve(
                        hp.heating_curve, weather, occupied.target_temperature_c
                    ).flow_temperature_c
                )
            elif hp and hp.delivery_temperature_c is not None:
                delivery.append(hp.delivery_temperature_c)
            elif mode == "heat_pump":
                raise ValueError("Configure a delivery rating temperature or a water heating curve")
        delivery_history = None
        current_delivery = delivery[0] if delivery else None
        if hp is not None and hp.delivery_temperature_measurement_key is not None:
            key = hp.delivery_temperature_measurement_key
            channel = self.measurement.measurement_channel(key)
            if (channel.quantity, channel.unit) != ("temperature", "degC"):
                raise ValueError("Delivery rating measurement requires temperature/degC")
            delivery_history = await self._history(key, first, now)
            current_delivery = latest_measurement(delivery_history, now, age).value
        model = zone.model_parameters
        if distributed and not isinstance(model, ThermalBuildingRC2Parameters):
            raise ValueError(
                "Air/water distribution requires explicitly calibrated thermal building parameters; aggregate direct fitting is not a distribution calibration"
            )
        cache_payload = {
            "zone": zone.model_dump(mode="json"),
            "source": source.model_dump(mode="json"),
            "resolved_profile": profile.model_dump(mode="json") if profile is not None else None,
            "mode": mode,
        }
        cache_key = hashlib.sha256(json.dumps(cache_payload, sort_keys=True).encode()).hexdigest()
        fit_diagnostics: dict[str, Any] = {
            "model_source": "configured_calibration",
            "physical_savings_proven": False,
        }
        if model is None and cache_key in self._model_cache:
            model, fit_diagnostics = self._model_cache[cache_key]
        if model is None:
            count = int(zone.state_history_hours * 3600 // 300)
            stamps = [
                now.replace(second=0, microsecond=0) - timedelta(seconds=(count - i) * 300)
                for i in range(count)
            ]
            rooms = sample_values(room, stamps, age)
            outdoors = sample_values(outside, stamps, age)
            intervals = energy_intervals(
                [(row.date_time, row.value) for row in power],
                self.measurement.measurement_channel(power_key),
                stamps[0],
                stamps[-1] + timedelta(seconds=300),
                300,
                {row.date_time.timestamp(): row.quality for row in power},
            )
            powers = [
                interval.energy_wh * 12 if interval.energy_wh is not None else None
                for interval in intervals
            ]
            if mode == "heat_pump" and heat_history is not None:
                thermal_intervals = energy_intervals(
                    [(row.date_time, row.value) for row in heat_history],
                    self.measurement.measurement_channel(source.thermal_power_measurement_key),
                    stamps[0],
                    stamps[-1] + timedelta(seconds=300),
                    300,
                    {row.date_time.timestamp(): row.quality for row in heat_history},
                )
                powers = [
                    interval.energy_wh * 12 if interval.energy_wh is not None else None
                    for interval in thermal_intervals
                ]
            elif mode == "heat_pump" and profile is not None and hp is not None:
                derived_heat: list[tuple[datetime, float | None]] = []
                past_outdoor = sample_values(outside, [row.date_time for row in power], age)
                past_source = (
                    sample_values(source_history, [row.date_time for row in power], age)
                    if source_history is not None
                    else [current_source] * len(power)
                )
                past_delivery = (
                    sample_values(delivery_history, [row.date_time for row in power], age)
                    if delivery_history is not None
                    else [None] * len(power)
                )
                for sample, outdoor, measured_delivery, inlet in zip(
                    power, past_outdoor, past_delivery, past_source
                ):
                    electric = sample.value
                    value = None
                    if (
                        outdoor is not None
                        and electric is not None
                        and sample.quality.status == "measured"
                    ):
                        weather = WeatherDataRecord(weather_temp_air=outdoor)
                        historical_delivery = (
                            measured_delivery
                            if delivery_history is not None
                            else (
                                evaluate_water_heating_curve(
                                    hp.heating_curve, weather, occupied.target_temperature_c
                                ).flow_temperature_c
                                if hp.heating_curve
                                else hp.delivery_temperature_c
                            )
                        )
                        if historical_delivery is not None:
                            try:
                                estimate = infer_thermal_power_from_electric(
                                    profile,
                                    weather,
                                    historical_delivery,
                                    max(0, electric),
                                    hp.electrical,
                                    source_temperature_c=inlet,
                                )
                                value = estimate.thermal_power_w
                            except ValueError:
                                pass  # Unsupported source conditions remain a gap, never zero heat.
                    derived_heat.append((sample.date_time, value))
                thermal_intervals = energy_intervals(
                    derived_heat,
                    self.measurement.measurement_channel(power_key),
                    stamps[0],
                    stamps[-1] + timedelta(seconds=300),
                    300,
                    {row.date_time.timestamp(): row.quality for row in power},
                )
                powers = [
                    interval.energy_wh * 12 if interval.energy_wh is not None else None
                    for interval in thermal_intervals
                ]
            fit = await asyncio.to_thread(
                fit_room_model,
                stamps,
                rooms,
                outdoors,
                powers,
                input_basis="thermal" if mode == "heat_pump" else "electric",
            )
            model, fit_diagnostics = fit.model, fit.model_dump(exclude={"model"}, mode="json")
            self._model_cache[cache_key] = (model, fit_diagnostics)
        if (mode == "heat_pump") != isinstance(model, ThermalBuildingRC2Parameters):
            raise ValueError(
                "Model input basis does not match source mode; COP must be applied exactly once"
            )
        scale = 1.0
        input_override = None
        if mode == "heat_pump" and heat_history is not None:
            input_override = current_heat
            fit_diagnostics = {
                **fit_diagnostics,
                "delivered_heat_is_measured": not source.thermal_power_is_estimated,
                "thermal_input_basis": "Bound thermal power channel; estimated"
                if source.thermal_power_is_estimated
                else "Bound thermal power measurement",
                "thermal_power_idle_electric_threshold_w": source.thermal_power_idle_electric_threshold_w,
                "current_thermal_input_w": current_heat,
            }
        elif mode == "heat_pump" and profile is not None and hp is not None:
            if current_delivery is None:
                raise ValueError("A current source rating temperature is required")
            estimate = infer_thermal_power_from_electric(
                profile,
                WeatherDataRecord(weather_temp_air=current_outside.value),
                current_delivery,
                max(0.0, current_power.value or 0.0),
                hp.electrical,
                source_temperature_c=current_source,
            )
            input_override = estimate.thermal_power_w
            fit_diagnostics = {
                **fit_diagnostics,
                "delivered_heat_is_measured": False,
                "thermal_input_basis": "Estimated from source map and measured electricity; no absolute heat-meter identification",
                "current_heat_estimate_flags": estimate.quality_flags,
                "historical_delivery_basis": "Measured native delivery/rating channel"
                if delivery_history is not None
                else "Bound rating or outdoor heating curve with current requested room target; historical target changes not identified",
            }
        distribution_state: tuple[float, ...] = ()
        distribution_observed_at = None
        if distributed:
            if (
                not isinstance(model, ThermalBuildingRC2Parameters)
                or input_override is None
                or current_delivery is None
            ):
                raise ValueError(
                    "Distribution projection requires a thermal building and estimated source heat"
                )
            (
                state,
                distribution_state,
                distribution_observed_at,
                state_diagnostics,
            ) = await self._distribution_initial_state(
                zone,
                model,
                distribution,
                room,
                outside,
                power,
                now=now,
                start=start,
                age=age,
                thermal_input_w=input_override,
                source_delivery_temperature_c=current_delivery,
                negative_idle_tolerance_w=source.idle_power_tolerance_w,
            )
        else:
            state, state_diagnostics = estimate_thermal_state(
                model,
                room,
                outside,
                power,
                now=now,
                start=start,
                max_age_seconds=age,
                minimum_history_hours=zone.minimum_state_history_hours,
                power_to_model_input=scale,
                negative_idle_tolerance_w=source.idle_power_tolerance_w,
                model_input_power_w=input_override,
                model_input_history=heat_history,
                room_observation_window_seconds=zone.state_room_observation_window_seconds,
            )
        estimates = []
        if zone.presence_area_id is not None and settings.presence.provider == "PresenceHistory":
            presence_settings = settings.presence
            provider = self.prediction.provider_by_id("PresenceHistory")
            if not isinstance(provider, PresenceHistory):
                raise ValueError("Native presence provider is unavailable")
            if intent.presence_correction is not None:
                if intent.presence_correction.zone_id != zone.presence_area_id:
                    raise ValueError("Manual presence correction belongs to another area")
                provider.set_correction(intent.presence_correction)
            await provider.forecast_zone(
                zone.presence_area_id,
                now=now,
                starts=[
                    weather.date_time for weather in bundle.weather if weather.date_time is not None
                ],
                settings=presence_settings,
                absences=self._reported_absences(intent),
            )
            thermal_presence_settings = PresenceCommonSettings.model_validate(
                {
                    **presence_settings.model_dump(),
                    "absence_delay_seconds": max(
                        presence_settings.absence_delay_seconds,
                        settings.thermal.absence_delay_seconds,
                    ),
                    "sample_max_age_seconds": min(
                        presence_settings.sample_max_age_seconds,
                        settings.thermal.presence_max_age_seconds,
                    ),
                }
            )
            estimates = await provider.forecast_zone(
                zone.presence_area_id,
                now=now,
                starts=[row.date_time for row in bundle.weather if row.date_time is not None],
                settings=thermal_presence_settings,
                publish=False,
                absences=self._reported_absences(intent),
            )
        maximum = min(
            source.max_electric_power_w, intent.max_electric_power_w or source.max_electric_power_w
        )
        maximum_model_input = maximum
        if mode == "heat_pump" and hp is not None and profile is not None:
            base_cap = max(
                0.0,
                (
                    maximum
                    - (hp.electrical.standby_power_w or 0.0)
                    - (hp.electrical.running_auxiliary_power_w or 0.0)
                    - (hp.electrical.startup_energy_wh or 0.0) / 0.25
                )
                / (1.0 + (hp.electrical.startup_warmup_seconds or 0.0) / 900.0),
            )
            capacities = []
            for index, (weather, sink) in enumerate(zip(bundle.weather, delivery)):
                inlet = source_forecast[index] if source_forecast else None
                if profile.operating_points:
                    points = get_heat_pump_operating_points(
                        profile, weather, sink, source_temperature_c=inlet
                    )
                    possibilities = [
                        point.thermal_power_w
                        for point in points
                        if point.electric_power_w <= base_cap
                    ]
                    for a, b in zip(points, points[1:]):
                        if (
                            min(a.electric_power_w, b.electric_power_w)
                            < base_cap
                            < max(a.electric_power_w, b.electric_power_w)
                        ):
                            fraction = (base_cap - a.electric_power_w) / (
                                b.electric_power_w - a.electric_power_w
                            )
                            possibilities.append(
                                a.thermal_power_w
                                + fraction * (b.thermal_power_w - a.thermal_power_w)
                            )
                    capacities.append(max(possibilities, default=0.0))
                else:
                    rating = evaluate_heat_pump(profile, weather, sink, source_temperature_c=inlet)
                    capacities.append(
                        min(base_cap, rating.max_electric_power_w) * rating.cop
                        if rating.supported
                        else 0.0
                    )
            maximum_model_input = min(capacities, default=0.0)
        lead = 0
        reported_band = (
            reported_absence_band(
                occupied, zone.unoccupied_comfort, zone.absence_minimum_temperature_c
            )
            if any(row.source == "reported_absence" for row in estimates)
            else None
        )
        absence_floor = reported_band if reported_band is not None else zone.unoccupied_comfort
        if estimates and absence_floor is not None:
            import numpy as np

            if distributed:
                if not isinstance(model, ThermalBuildingRC2Parameters):
                    raise ValueError("Distribution preheat requires a thermal building model")
                da, db, de = build_distribution_dynamics(model, distribution, seconds=60)
            else:
                transition = rc2_transition(model, 60)
                da, db, de = transition[:2, :2], transition[:2, 2], transition[:2, 3]
            cold = min(
                float(row.weather_temp_air)
                for row in bundle.weather
                if row.weather_temp_air is not None
            )
            hypothetical = np.array(
                [absence_floor.min_temperature_c] * 2
                + (
                    [bounds[0] for bounds in distribution_temperature_bounds(distribution)]
                    if distributed
                    else []
                )
            )
            for minute in range(1, 241):
                model_power = maximum_model_input
                if distributed and current_delivery is not None:
                    flow_g = source_flow_conductance_w_per_k(distribution)
                    source_index = source_temperature_state_index(distribution)
                    if flow_g is not None and source_index is not None:
                        model_power = min(
                            model_power,
                            flow_g * max(0.0, min(delivery) - hypothetical[source_index]),
                        )
                hypothetical = da @ hypothetical + db * model_power + de * cold
                if hypothetical[0] >= occupied.min_temperature_c:
                    lead = math.ceil(minute / 15) * 900 + 900
                    break
            else:
                lead = 14400
        bands = (
            comfort_bands_from_presence(
                estimates,
                occupied,
                zone.unoccupied_comfort,
                preheat_seconds=lead,
                allow_warm_coasting=zone.allow_warm_coasting,
                reported_absence=reported_band,
            )
            if estimates
            else tuple(occupied for _ in bundle.weather)
        )
        reported_bands = bands
        recovery_seconds = 14400
        if intent.comfort_windows:
            presence_bands = bands
            bands = apply_comfort_windows(
                [start + timedelta(seconds=i * 900) for i in range(len(bands))],
                bands,
                intent.comfort_windows,
            )
            if intent.comfort_windows_policy == "presence_fallback":
                # A bootstrap routine never overwrites learned or observed
                # occupancy, nor a user's explicit presence correction.
                bands = tuple(
                    presence_bands[i] if estimates and estimates[i].source != "unknown" else band
                    for i, band in enumerate(bands)
                )
            if intent.comfort_windows_policy == "economic_setback":
                # Explicit windows declare rest/setback bounds; native forecast guards comfort.
                if intent.rest_slot_mask and len(intent.rest_slot_mask) != len(bands):
                    raise ValueError("Rest-slot mask must match horizon")
                bands = tuple(
                    presence_bands[i]
                    if estimates
                    and presence_bands[i].min_temperature_c > band.min_temperature_c
                    and (
                        estimates[i].source in ("sensor_present", "manual_comfort")
                        or (
                            estimates[i].require_comfort
                            and not (intent.rest_slot_mask and intent.rest_slot_mask[i])
                        )
                    )
                    else band
                    for i, band in enumerate(bands)
                )
            # Recovery may waive only the initial cold condition, never a later
            # declared morning/return minimum. Respect the earliest rising floor.
            rising_floor = next(
                (
                    i
                    for i in range(1, len(bands))
                    if bands[i].min_temperature_c > bands[0].min_temperature_c
                ),
                None,
            )
            if rising_floor is not None:
                recovery_seconds = min(recovery_seconds, rising_floor * 900)
        if reported_band is not None or any(row.source == "reported_return" for row in estimates):
            # A reported absence and its return beat routine calendar windows (for example a
            # fixed morning comfort hour): those slots keep the presence-derived band.
            bands = tuple(
                reported_bands[i]
                if estimates[i].source in ("reported_absence", "reported_return")
                else band
                for i, band in enumerate(bands)
            )
        tracking = settings.thermal.tracking
        if (
            tracking.enabled
            and tracking.operation == "continuous"
            and tracking.zone_id == zone.zone_id
        ):
            ceiling = tracking.maximum_for_target(occupied.target_temperature_c)
            executable_bands = []
            for band in bands:
                lower = max(band.min_temperature_c, tracking.minimum_temperature_c)
                upper = min(band.max_temperature_c, ceiling)
                if lower > upper:
                    raise ValueError("Planning comfort band exceeds configured execution limits")
                executable_bands.append(
                    ThermalComfortBand(
                        min_temperature_c=lower,
                        target_temperature_c=min(upper, max(lower, band.target_temperature_c)),
                        max_temperature_c=upper,
                    )
                )
            bands = tuple(executable_bands)
        required_return = None
        first_required_comfort = next(
            (
                index
                for index, estimate in enumerate(estimates)
                if estimate.require_comfort or estimate.source == "unknown"
            ),
            None,
        )
        if (
            estimates
            and absence_floor is not None
            and not (estimates[0].require_comfort or estimates[0].source == "unknown")
        ):
            required_return = first_required_comfort
            if required_return is not None:
                # An initially absent cold-recovery exception must never spill
                # into a later occupied requirement. Unreachable returns stay
                # infeasible; the planner may not waive that future comfort.
                recovery_seconds = min(recovery_seconds, required_return * 900)
        common: dict[str, Any] = dict(
            fleet=bundle.fleet,
            load_basis=bundle.load_basis,
            zone_id=zone.zone_id,
            heat_source_id=source.device_id,
            state=state,
            comfort_bands=bands,
            comfort_required=tuple(b.min_temperature_c >= occupied.min_temperature_c for b in bands)
            if intent.comfort_windows_policy == "economic_setback"
            else (),
            issued_at=now,
            revision=intent.revision
            if intent.revision is not None
            else int(now.timestamp() * 1000),
            basis_control_revision=intent.basis_control_revision,
            plan_ttl_seconds=settings.thermal.plan_ttl_seconds,
            planning_time_limit_seconds=settings.thermal.planning_time_limit_seconds,
            smoothing_seconds=settings.thermal.electric_power_block_seconds,
            pattern_polish_seconds=settings.thermal.pattern_polish_seconds,
            measurement_max_age_seconds=age,
            max_electric_power_w=maximum,
            recovery=ThermalRecoverySettings(
                max_duration_seconds=recovery_seconds,
                allow_passive_cooldown=intent.target_temperature_c is not None,
                max_heating_rate_kelvin_per_hour=zone.recovery_max_heating_rate_kelvin_per_hour,
            ),
        )
        request: ThermalHeatPlanningRequest | ThermalShadowRequest
        if mode == "heat_pump":
            if hp is None or profile is None or not isinstance(model, ThermalBuildingRC2Parameters):
                raise ValueError("A heat-pump profile and thermal building model are required")
            warning = minimum_power_warning(profile)
            if warning and profile.profile_id not in _MINIMUM_POWER_WARNED:
                _MINIMUM_POWER_WARNED.add(profile.profile_id)  # once per profile and service start
                logger.warning(warning)
            request = ThermalHeatPlanningRequest(
                **common,
                building=model,
                heat_pump_profile=profile,
                weather=bundle.weather,
                delivery_temperature_c=tuple(delivery),
                source_temperature_c=source_forecast,
                defrost=hp.defrost,
                electrical=hp.electrical,
                min_electric_power_w=source.min_electric_power_w,
                initial_running=None,
                distribution=distribution,
                distribution_state=distribution_state,
                distribution_observed_at=distribution_observed_at,
                distribution_fan_levels=fan_levels,
                distribution_load_basis=(
                    "residual_excludes_external_distribution"
                    if zone.zone_id in bundle.distribution_load_excluded_zone_ids
                    else None
                ),
            )
        else:
            if not isinstance(model, ThermalRC2Parameters):
                raise ValueError("Effective electrical mode requires an electrical input model")
            request = ThermalShadowRequest(
                **common,
                model=model,
                outdoor_temperature_c=tuple(
                    float(weather.weather_temp_air)
                    for weather in bundle.weather
                    if weather.weather_temp_air is not None
                ),
                min_electric_power_w=source.min_electric_power_w,
            )
        from akkudoktoreos.thermal.thermaldiagnostics import planning_components

        diagnostics = {
            "components": planning_components(
                mode=mode,
                model=model.model_dump(mode="json"),
                weather=[row.model_dump(mode="json") for row in bundle.weather],
                maximum_w=maximum,
                minimum_w=source.min_electric_power_w,
                distribution=distribution.kind,
            ),
            "mode": mode,
            "native_data_generation": self._generation,
            "presence_revision": intent.presence_revision,
            "forecast_issued_at": bundle.forecast_issued_at.isoformat(),
            "quality_notes": bundle.quality_notes,
            "state": state_diagnostics,
            "calibration": fit_diagnostics,
            "presence": [estimate.model_dump(mode="json") for estimate in estimates],
            "presence_is_forecast_not_guarantee": True,
            "runtime_comfort_windows_policy": intent.comfort_windows_policy,
            "runtime_comfort_windows": [
                window.model_dump(mode="json") for window in intent.comfort_windows
            ],
            "preheat_lead_seconds": lead,
            "initial_recovery_max_duration_seconds": recovery_seconds,
            "initial_passive_cooldown_allowed": intent.target_temperature_c is not None,
            "recovery_limit_basis": (
                "Earlier of first required comfort after initial absence and four hours"
                if required_return is not None
                else "Explicit initial recovery, at most four hours"
            ),
            "first_required_comfort_time": (
                start.isoformat()
                if not estimates or zone.unoccupied_comfort is None
                else (
                    estimates[first_required_comfort].date_time.isoformat()
                    if first_required_comfort is not None
                    else None
                )
            ),
            "initial_recovery_presence_deadline": (
                estimates[required_return].date_time.isoformat()
                if required_return is not None
                else None
            ),
            "presence_warm_coasting_enabled": zone.allow_warm_coasting,
            "presence_absence_band_adjustments": [
                {
                    "slot": index,
                    "start_datetime": estimate.date_time.isoformat(),
                    "configured_absence_band": zone.unoccupied_comfort.model_dump(),
                    "effective_band": band.model_dump(),
                    "preheat_target_requested": band.target_temperature_c
                    == occupied.target_temperature_c,
                    "warm_coasting_allowed": zone.allow_warm_coasting,
                }
                for index, (estimate, band) in enumerate(zip(estimates, bands))
                if zone.unoccupied_comfort is not None
                and not (estimate.require_comfort or estimate.source == "unknown")
                and band != zone.unoccupied_comfort
            ],
            "preheat_lead_basis": (
                "Coupled distribution/building dynamics from declared lower state bounds at coldest horizon weather; maximum declared heat input plus one quarter-hour guard"
                if distributed
                else "RC2 from absence lower bound at coldest horizon weather; maximum declared heat input plus one quarter-hour guard"
            ),
            "preheat_physical_timing_validated": False,
            "preheat_maximum_model_input_w": maximum_model_input,
            "negative_idle_power_clamped_w": min(0.0, current_power.value or 0.0),
            "automatic_model_correction_active": False,
            "distribution_kind": distribution.kind,
            "distribution_state_is_calibrated_model_estimate_not_heat_measurement": distributed,
            "config_fingerprint": self._config_fingerprint(),
            "manual_revision_verified": bundle.control_revision is not None,
            "initial_compressor_state_confirmed": False,
            "initial_compressor_power_proxy": (current_power.value or 0) > 50,
            "thermal_presence_policy": "Longer provider/thermal absence delay and shorter freshness lease; provider's own forecast remains separately published",
        }
        return request, diagnostics

    async def plan(self, intent: ThermalConfigRequest) -> dict[str, Any]:
        """Resolve a coherent native scenario and compute a private shadow proposal."""
        if intent.master_power == "Off":
            self._intent_generation += 1
            self._last = {
                "shadow_only": True,
                "execution_allowed": False,
                "status": "disabled_by_user",
                "result": None,
                "basis_control_revision": intent.basis_control_revision,
            }
            return self._last
        if self._solve_lock.locked():
            raise ValueError("A native thermal shadow solve is already running")
        async with self._solve_lock:
            # Snapshot before any await: a manual Off arriving while the scenario is resolved
            # must be seen, otherwise the solver would start for an already cancelled intent.
            intent_generation = self._intent_generation
            async with self._data_lock:
                request, diagnostics = await self._resolve(intent)
            if self._intent_generation != intent_generation:
                return {
                    "shadow_only": True,
                    "execution_allowed": False,
                    "physical_savings_proven": False,
                    "economic_evidence": "conditional_model_projection",
                    "status": "superseded",
                    "reason": "Manual off superseded this computation",
                    "request": request.model_dump(mode="json", exclude_computed_fields=True),
                    "result": None,
                    "diagnostics": diagnostics,
                }
            solver = (
                optimize_heat_pump_shadow
                if isinstance(request, ThermalHeatPlanningRequest)
                else optimize_thermal_shadow
            )
            result = await asyncio.to_thread(solver, request)
            computed = thermal_now()
            stale_reasons = []
            if self._intent_generation != intent_generation:
                stale_reasons.append("Manual off superseded this computation")
            if diagnostics["native_data_generation"] != self._generation:
                stale_reasons.append("New native data superseded this computation")
            if diagnostics["config_fingerprint"] != self._config_fingerprint():
                stale_reasons.append("Configuration changed during computation")
            if (
                self._bundle
                and self._bundle.control_revision is not None
                and self._bundle.control_revision != intent.basis_control_revision
            ):
                stale_reasons.append("Manual control revision changed during computation")
            if request.fleet.start_datetime < computed:
                stale_reasons.append("Computation finished after its prospective origin")
            if result.plan and result.plan.valid_until <= computed:
                stale_reasons.append("Plan lease expired during computation")
            if stale_reasons:
                result = result.model_copy(
                    update={
                        "comparison_qualified": False,
                        "savings_cash_eur": None,
                        "savings_objective_eur": None,
                    }
                )
            wrapper = {
                "shadow_only": True,
                "execution_allowed": False,
                "physical_savings_proven": False,
                "economic_evidence": "conditional_model_projection",
                "status": "superseded" if stale_reasons else result.status,
                "reason": "; ".join(stale_reasons) if stale_reasons else result.reason,
                "request": request.model_dump(mode="json", exclude_computed_fields=True),
                "result": result.model_dump(mode="json"),
                "diagnostics": diagnostics,
            }
            if self._intent_generation == intent_generation:
                self._last = wrapper
            return wrapper
