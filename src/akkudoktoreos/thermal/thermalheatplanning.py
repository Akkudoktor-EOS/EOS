"""Pure native-weather/heat-pump adapter for single-zone joint shadow planning.

The building takes delivered thermal power. Heat-pump COP converts electrical
power to heat exactly once. COP is evaluated at an explicit assumed load, not
optimized across a complete nonlinear modulation/defrost trajectory.
"""

import hashlib
import math
from datetime import timedelta
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from akkudoktoreos.devices.heatpump import (
    HeatPumpDefrostSettings,
    HeatPumpElectricalBreakdown,
    HeatPumpElectricalSettings,
    HeatPumpOperatingPoint,
    HeatPumpPerformance,
    HeatPumpProfile,
    evaluate_heat_pump,
    evaluate_heat_pump_electricity,
    get_heat_pump_operating_points,
)
from akkudoktoreos.prediction.weatherabc import WeatherDataRecord
from akkudoktoreos.thermal.thermaldistribution import (
    DirectDistributionSettings,
    ThermalDistributionSettings,
    build_distribution_dynamics,
    distribution_additional_state_names,
    distribution_electric_power_w,
    distribution_quality_flags,
    distribution_temperature_bounds,
    source_flow_conductance_w_per_k,
    source_temperature_state_index,
)
from akkudoktoreos.thermal.thermalmodels import (
    FiniteNumber,
    NonemptyText,
    ThermalBuildingRC2Parameters,
    ThermalModel,
    UTCDatetime,
)
from akkudoktoreos.thermal.thermaloptimization import (
    ThermalPlanningInputs,
    ThermalShadowSolution,
    _base_electric_at_input,
    _finish_planning,
    _input_at_electric_floor,
    _NoSolution,
    _optimize_problem,
    _PlanningBudget,
    _SourceHorizon,
)


class ThermalHeatPlanningRequest(ThermalPlanningInputs):
    """Explicit source-independent building and native EOS forecast records.

    Delivery temperature is water flow for air-to-water devices and the indoor
    rating condition for air-to-air devices, never the hot discharge-air sensor.
    A per-slot COP is held fixed at ``assumed_thermal_load_fraction`` of maximum
    rated thermal output. The optimizer does not optimize that assumption.
    """

    building: ThermalBuildingRC2Parameters
    heat_pump_profile: HeatPumpProfile
    weather: tuple[WeatherDataRecord, ...] = Field(min_length=1, max_length=288)
    delivery_temperature_c: tuple[FiniteNumber, ...] = Field(min_length=1, max_length=288)
    source_temperature_c: tuple[FiniteNumber, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=288,
        description="Source inlet forecast; required for water/brine, independent of outdoor weather.",
    )
    assumed_thermal_load_fraction: FiniteNumber = Field(default=1.0, gt=0, le=1)
    defrost: HeatPumpDefrostSettings = Field(default_factory=HeatPumpDefrostSettings)
    max_electric_power_w: FiniteNumber | None = Field(default=None, gt=0)
    min_electric_power_w: FiniteNumber = Field(default=0.0, ge=0)
    electrical: HeatPumpElectricalSettings = Field(default_factory=HeatPumpElectricalSettings)
    initial_running: bool | None = Field(default=None, strict=True)
    distribution: ThermalDistributionSettings = Field(default_factory=DirectDistributionSettings)
    distribution_state: tuple[FiniteNumber, ...] = Field(
        default=(),
        max_length=3,
        description="Additional temperatures projected to fleet start, in model state order.",
    )
    distribution_observed_at: UTCDatetime | None = Field(
        default=None,
        description="Oldest fresh observation supporting the projected distribution state.",
    )
    distribution_fan_levels: tuple[NonemptyText, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=288,
        description="Declared exogenous fan profile; not native actuator commands.",
    )
    distribution_load_basis: Literal["residual_excludes_external_distribution"] | None = None

    @field_validator("weather", mode="before")
    @classmethod
    def reject_boolean_weather_values(cls, value: Any) -> Any:
        """Do not let native numeric coercion turn a boolean into weather data."""
        if isinstance(value, (list, tuple)):
            for record in value:
                for key in ("weather_temp_air", "weather_relative_humidity", "weather_dew_point"):
                    raw = (
                        record.get(key) if isinstance(record, dict) else getattr(record, key, None)
                    )
                    if isinstance(raw, bool):
                        raise ValueError("Weather temperature and humidity must not be booleans")
        return value

    @model_validator(mode="after")
    def check_native_forecast_horizon(self) -> Self:
        """Require exact timestamp alignment without resampling or hidden filling."""
        n = len(self.fleet.load_w)
        if self.source_temperature_c is not None and len(self.source_temperature_c) != n:
            raise ValueError("Source temperatures must match the fleet horizon")
        if not self.heat_pump_profile.heat_pump_type.startswith("air_to_"):
            if self.source_temperature_c is None:
                raise ValueError(
                    "Water/brine sources require an explicit source temperature horizon"
                )
            if self.defrost.mode != "off":
                raise ValueError("Air frost correction cannot be applied to water/brine sources")
        if (
            self.max_electric_power_w is not None
            and self.min_electric_power_w > self.max_electric_power_w
        ):
            raise ValueError(
                "Minimum steady source electricity exceeds the total electric power cap"
            )
        if len(self.weather) != n or len(self.delivery_temperature_c) != n:
            raise ValueError("Weather and delivery temperatures must match the fleet horizon")
        for k, record in enumerate(self.weather):
            expected = self.fleet.start_datetime + timedelta(seconds=k * 900)
            if record.date_time is None or record.date_time != expected:
                raise ValueError(
                    "Native weather timestamps must exactly match every fleet interval"
                )
            value = record.weather_temp_air
            if value is None or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError("Every interval requires finite native weather_temp_air")
        expected_states = distribution_additional_state_names(self.distribution)
        if len(self.distribution_state) != len(expected_states):
            raise ValueError("Explicit distribution state must match the selected model")
        if self.distribution.kind != "direct":
            expected_sink = "_to_air" if self.distribution.kind == "air" else "_to_water"
            if not self.heat_pump_profile.heat_pump_type.endswith(expected_sink):
                raise ValueError("Heat-pump type and heat-distribution type must agree")
            if self.distribution_observed_at is None:
                raise ValueError(
                    "Distribution initialization requires an explicit observation time"
                )
            age = (self.issued_at - self.distribution_observed_at).total_seconds()
            if not 0 <= age <= self.measurement_max_age_seconds:
                raise ValueError("Distribution observation must be causal and fresh")
        elif self.distribution_observed_at is not None or self.distribution_fan_levels is not None:
            raise ValueError("Direct distribution does not accept unused distribution inputs")
        if self.distribution_fan_levels is not None:
            if self.distribution.kind != "air" or len(self.distribution_fan_levels) != n:
                raise ValueError("Fan selections require an air-distribution entry for every slot")
        return self


class HeatSourceHorizon(ThermalModel):
    """Evaluated source ratings plus explicit, possibly restrictive electric caps."""

    performance: tuple[HeatPumpPerformance, ...]
    effective_min_electric_power_w: tuple[FiniteNumber, ...]
    effective_max_electric_power_w: tuple[FiniteNumber, ...]
    assumed_thermal_load_fraction: FiniteNumber
    approximations: tuple[str, ...]
    operating_points: tuple[tuple[HeatPumpOperatingPoint, ...], ...] = ()
    minimum_modulation_known: tuple[bool, ...] = ()
    configured_minimum_base_electric_power_w: FiniteNumber = Field(default=0.0, ge=0)
    configured_minimum_components_known: bool = True


class ThermalHeatPlanningSolution(ThermalShadowSolution):
    """Generic source result with thermal/electrical distinction and limitations."""

    source_horizon: HeatSourceHorizon | None = None
    reference_thermal_power_w: tuple[FiniteNumber, ...] = ()
    candidate_thermal_power_w: tuple[FiniteNumber, ...] = ()
    reference_cop: tuple[FiniteNumber | None, ...] = ()
    candidate_cop: tuple[FiniteNumber | None, ...] = ()
    reference_electricity: tuple[HeatPumpElectricalBreakdown, ...] = ()
    candidate_electricity: tuple[HeatPumpElectricalBreakdown, ...] = ()
    distribution_electric_power_w: tuple[FiniteNumber, ...] = ()
    distribution_quality_flags: tuple[str, ...] = ()


def _system_boundary_known(request: ThermalHeatPlanningRequest) -> bool:
    """Require either a total-system rating or explicitly completed auxiliaries."""
    basis = request.heat_pump_profile.provenance.electric_power_basis
    return basis == "total_system" or (
        basis == "compressor_only"
        and request.electrical.extras_are_outside_cop
        and all(
            value is not None
            for value in (
                request.electrical.standby_power_w,
                request.electrical.running_auxiliary_power_w,
                request.electrical.startup_energy_wh
                if request.electrical.startup_energy_wh is not None
                else request.electrical.startup_warmup_seconds,
            )
        )
    )


def _configured_minimum(request: ThermalHeatPlanningRequest) -> tuple[float, bool]:
    """Convert active steady total input to the source's COP input boundary.

    Startup energy never satisfies a continuous-power floor. Missing additional
    standby/auxiliary components remain unknown and block savings qualification.
    """
    electrical = request.electrical
    known = request.min_electric_power_w == 0 or (
        electrical.standby_power_w is not None and electrical.running_auxiliary_power_w is not None
    )
    return (
        max(
            0.0,
            request.min_electric_power_w
            - (electrical.standby_power_w or 0.0)
            - (electrical.running_auxiliary_power_w or 0.0),
        ),
        known,
    )


def _electricity_breakdown(
    request: ThermalHeatPlanningRequest,
    source: HeatSourceHorizon,
    base_power_w: tuple[float, ...],
    thermal_power_w: tuple[float, ...],
) -> tuple[HeatPumpElectricalBreakdown, ...]:
    """Reconstruct source components, resolving unknown initial state as OFF."""
    previous = request.initial_running or False
    values = []
    for power, thermal, performance in zip(base_power_w, thermal_power_w, source.performance):
        running = power > 1e-5
        if source.operating_points and running:
            performance = performance.model_copy(update={"cop": thermal / power})
        values.append(
            evaluate_heat_pump_electricity(
                performance,
                thermal if running else 0.0,
                900.0,
                running,
                previous,
                request.electrical,
            )
        )
        previous = running
    return tuple(values)


def _operating_source_horizon(request: ThermalHeatPlanningRequest) -> HeatSourceHorizon:
    """Use only exactly supported paired ratings, never independent extrema."""
    if request.defrost.mode != "off" and request.heat_pump_profile.cop_includes_defrost is not True:
        raise ValueError("Defrost correction of paired load-axis maps is not implemented")
    slices, performance, known_minima = [], [], []
    base_floor, components_known = _configured_minimum(request)
    for index, (weather, delivery) in enumerate(
        zip(request.weather, request.delivery_temperature_c)
    ):
        source_temperature = (
            request.source_temperature_c[index] if request.source_temperature_c else None
        )
        points = get_heat_pump_operating_points(
            request.heat_pump_profile, weather, delivery, source_temperature_c=source_temperature
        )
        if not points:
            raise ValueError(
                f"No exact paired operating-point weather/delivery slice in interval {index}"
            )
        if len(points) > 16:
            raise ValueError("At most 16 paired operating points per interval are supported")
        _input_at_electric_floor(
            tuple((point.thermal_power_w, point.electric_power_w) for point in points), base_floor
        )
        diagnostic = evaluate_heat_pump(
            request.heat_pump_profile, weather, delivery, source_temperature_c=source_temperature
        )
        minimum_known = (
            diagnostic.min_thermal_power_w is not None
            and "minimum_capacity_outside_support_clamped" not in diagnostic.quality_flags
            and abs(diagnostic.min_thermal_power_w - points[0].thermal_power_w) <= 1e-5
        )
        flags = {
            "adjacent_paired_operating_point_interpolation",
            "cop_field_is_upper_operating_point_diagnostic_only",
            "no_outdoor_or_delivery_slice_extrapolation",
            f"electric_power_basis:{request.heat_pump_profile.provenance.electric_power_basis}",
            f"provenance:{request.heat_pump_profile.provenance.quality}",
        }
        if not minimum_known:
            flags.add("minimum_supported_operating_point_not_verified_device_minimum")
        for point in points:
            flags.update(point.quality_flags)
        if request.defrost.mode != "off":
            flags.add("defrost_correction_skipped_already_in_cop")
        performance.append(
            HeatPumpPerformance(
                cop=points[-1].thermal_power_w / points[-1].electric_power_w,
                min_thermal_power_w=points[0].thermal_power_w,
                max_thermal_power_w=points[-1].thermal_power_w,
                min_electric_power_w=points[0].electric_power_w,
                max_electric_power_w=points[-1].electric_power_w,
                supported=True,
                quality_flags=tuple(sorted(flags)),
            )
        )
        known_minima.append(minimum_known)
        slices.append(points)
    return HeatSourceHorizon(
        performance=tuple(performance),
        effective_min_electric_power_w=tuple(
            max(value.min_electric_power_w or 0.0, base_floor) for value in performance
        ),
        effective_max_electric_power_w=tuple(value.max_electric_power_w for value in performance),
        assumed_thermal_load_fraction=request.assumed_thermal_load_fraction,
        operating_points=tuple(slices),
        minimum_modulation_known=tuple(known_minima),
        configured_minimum_base_electric_power_w=base_floor,
        configured_minimum_components_known=components_known,
        approximations=(
            "Only adjacent documented thermal/electrical operating-point pairs are interpolated; no outdoor/delivery extrapolation is performed.",
            "The supported pair range may be narrower than the device's physical modulation range; absent paired minimum power blocks savings qualification.",
            "Startup energy is a 15-minute electrical average, not a transient peak or delivered heat.",
            "Native thermostat tracking and measured billing savings are not validated.",
            "Configured active minimum electricity excludes startup energy; unknown standby/auxiliary components prevent savings qualification.",
        ),
    )


def _source_horizon(request: ThermalHeatPlanningRequest) -> HeatSourceHorizon:
    if request.heat_pump_profile.operating_points:
        return _operating_source_horizon(request)
    values, minima, maxima = [], [], []
    base_floor, components_known = _configured_minimum(request)
    approximations = [
        "Slot COP is fixed at the declared assumed thermal load; modulation-dependent COP is not optimized.",
        "Defrost is a static optional heuristic, not an identified defrost event schedule.",
        "Native thermostat tracking and measured billing savings are not validated.",
    ]
    if not components_known:
        approximations.append(
            "Configured active minimum electricity uses undeclared standby/auxiliary components as zero for a conditional calculation; startup energy never satisfies this steady floor."
        )
    if not _system_boundary_known(request):
        approximations.append(
            "The electrical system boundary is not documented as total-system input; auxiliary electricity is not invented and modeled costs cannot qualify as savings."
        )
    if request.initial_running is None and request.electrical.startup_modelled:
        approximations.append(
            "Initial running state is unknown; startup accounting assumes initially OFF and cannot qualify as savings."
        )
    if request.max_electric_power_w is not None and request.electrical.startup_modelled:
        approximations.append(
            "Startup energy is spread over its 15-minute slot; the total-power bound is not a verified transient peak limit."
        )
    for index, (record, delivery) in enumerate(
        zip(request.weather, request.delivery_temperature_c)
    ):
        source_temperature = (
            request.source_temperature_c[index] if request.source_temperature_c else None
        )
        rating = evaluate_heat_pump(
            request.heat_pump_profile, record, delivery, source_temperature_c=source_temperature
        )
        performance = evaluate_heat_pump(
            request.heat_pump_profile,
            record,
            delivery,
            thermal_load_w=rating.max_thermal_power_w * request.assumed_thermal_load_fraction,
            defrost=request.defrost,
            source_temperature_c=source_temperature,
        )
        minimum = performance.min_electric_power_w
        if minimum is None:
            minimum = 0.0
            if not any("Minimum modulation" in text for text in approximations):
                approximations.append(
                    "Minimum modulation is unknown; zero minimum is an explicit relaxed planning bound, not an invented device rating."
                )
        minimum = max(minimum, base_floor)
        maximum = performance.max_electric_power_w
        if request.max_electric_power_w is not None:
            maximum = min(
                maximum,
                max(
                    0.0,
                    request.max_electric_power_w
                    - (request.electrical.standby_power_w or 0.0)
                    - (request.electrical.running_auxiliary_power_w or 0.0),
                ),
            )
        if minimum > maximum:
            raise ValueError("Explicit electric cap is below the source's known minimum power")
        values.append(performance)
        minima.append(minimum)
        maxima.append(maximum)
    return HeatSourceHorizon(
        performance=tuple(values),
        effective_min_electric_power_w=tuple(minima),
        effective_max_electric_power_w=tuple(maxima),
        assumed_thermal_load_fraction=request.assumed_thermal_load_fraction,
        approximations=tuple(approximations),
        minimum_modulation_known=tuple(value.min_electric_power_w is not None for value in values),
        configured_minimum_base_electric_power_w=base_floor,
        configured_minimum_components_known=components_known,
    )


def minimum_power_warning(profile: HeatPumpProfile) -> str | None:
    """Plain-language warning when a map has no power floor (thermal_min_points missing)."""
    if profile.thermal_min_points:
        return None
    return (
        f"Waermepumpen-Kennfeld '{profile.profile_id}' hat keine Leistungsuntergrenze "
        "(thermal_min_points fehlt): EOS nimmt an, das Geraet koenne bis zum kleinsten "
        "Kennfeldpunkt herunterregeln. Echte Geraete laufen darunter nur getaktet, der Plan "
        "kann dann Betriebszustaende enthalten, die das Geraet nicht kann. Bitte je "
        "Temperaturscheibe den kleinsten Betriebspunkt als thermal_min_points eintragen "
        "(gleiche Waerme wie der unterste Betriebspunkt)."
    )


def optimize_heat_pump_shadow(request: ThermalHeatPlanningRequest) -> ThermalHeatPlanningSolution:
    """Evaluate typed native inputs and jointly plan source electricity/storage.

    This function performs no source fetching, configuration lookup or actuator
    communication. Unsupported map coordinates are rejected, never accepted as
    extrapolated economic advantages. Existing electrical RC2 APIs are separate.
    """
    budget = _PlanningBudget(request.planning_time_limit_seconds)
    try:
        result = _optimize_heat_pump_shadow(request, budget)
    except _NoSolution as error:
        result = ThermalHeatPlanningSolution(status=error.status, reason=str(error))
    return _finish_planning(result, budget)


def _optimize_heat_pump_shadow(
    request: ThermalHeatPlanningRequest, budget: _PlanningBudget
) -> ThermalHeatPlanningSolution:
    request = ThermalHeatPlanningRequest.model_validate(
        request.model_dump(exclude_computed_fields=True)
    )
    try:
        source = _source_horizon(request)
        budget.remaining("source_map_preparation")
        if not all(value.supported for value in source.performance):
            return ThermalHeatPlanningSolution(
                status="unsupported",
                reason="Heat-pump map coordinates are outside support",
                source_horizon=source,
            )
        if (
            (request.electrical.running_auxiliary_power_w or 0) > 0
            or request.electrical.startup_modelled
        ) and any(minimum <= 1e-5 for minimum in source.effective_min_electric_power_w):
            return ThermalHeatPlanningSolution(
                status="unsupported",
                reason="Auxiliary/start accounting requires known positive minimum modulation",
                source_horizon=source,
            )
        incomplete_defrost = {
            "defrost_correction_skipped_missing_inputs",
            "defrost_correction_skipped_unknown_cop_basis",
        }
        if any(
            incomplete_defrost.intersection(value.quality_flags) for value in source.performance
        ):
            return ThermalHeatPlanningSolution(
                status="unsupported",
                reason="Requested defrost correction lacks required evidence",
                source_horizon=source,
            )
        paired = bool(source.operating_points)
        selectors: tuple[str | None, ...] = (
            request.distribution_fan_levels
            if request.distribution_fan_levels is not None
            else (None,) * len(source.performance)
        )
        distribution_flags: set[str] = set()
        distribution_power: list[float] = []
        distribution_electricity_known = True
        for selector in selectors:
            power, flags = distribution_electric_power_w(request.distribution, flow_or_fan=selector)
            distribution_flags.update(flags)
            distribution_flags.update(
                distribution_quality_flags(request.distribution, flow_or_fan=selector)
            )
            distribution_electricity_known = distribution_electricity_known and power is not None
            distribution_power.append(power if power is not None else 0.0)
        if any(distribution_power):
            if request.distribution_load_basis != "residual_excludes_external_distribution":
                return ThermalHeatPlanningSolution(
                    status="unsupported",
                    reason="External distribution electricity needs an explicit residual-load exclusion",
                    source_horizon=source,
                    distribution_electric_power_w=tuple(distribution_power),
                    distribution_quality_flags=tuple(sorted(distribution_flags)),
                )
            # These are explicitly external, fixed circulation/mixing loads.
            # They share the native AC bus but are outside the source's COP and
            # electrical nameplate limit. Never count them as useful heat.
            request = request.model_copy(
                update={
                    "fleet": request.fleet.model_copy(
                        update={
                            "load_w": [
                                load + extra
                                for load, extra in zip(request.fleet.load_w, distribution_power)
                            ]
                        }
                    )
                }
            )
        source_node = source_temperature_state_index(request.distribution)
        flow = source_flow_conductance_w_per_k(request.distribution)
        flow_known = source_node is None or flow is not None
        if not flow_known:
            distribution_flags.add("water_source_flow_unknown_temperature_only_supply_constraint")
        by_selector = {
            selector: tuple(
                build_distribution_dynamics(
                    request.building,
                    request.distribution,
                    seconds=minute * 60,
                    flow_or_fan=selector,
                )
                for minute in range(16)
            )
            for selector in dict.fromkeys(selectors)
        }
        selected_steps = tuple(by_selector[selector] for selector in selectors)
        if request.distribution.kind != "direct":
            distribution_flags.add("declared_linear_distribution_parameters_not_online_identified")
        if request.distribution.kind == "air":
            distribution_flags.add("fan_schedule_is_exogenous_not_optimized")
        horizon = _SourceHorizon(
            outdoor_c=tuple(
                float(record.weather_temp_air)
                for record in request.weather
                if record.weather_temp_air is not None
            ),
            power_gain_scales=(1.0,) * len(source.performance)
            if paired
            else tuple(value.cop for value in source.performance),
            min_power_w=tuple(
                _input_at_electric_floor(
                    tuple((point.thermal_power_w, point.electric_power_w) for point in points),
                    source.configured_minimum_base_electric_power_w,
                )
                for points in source.operating_points
            )
            if paired
            else source.effective_min_electric_power_w,
            max_power_w=tuple(points[-1].thermal_power_w for points in source.operating_points)
            if paired
            else source.effective_max_electric_power_w,
            steps=selected_steps[0],
            slot_steps=selected_steps if request.distribution_fan_levels is not None else (),
            additional_initial_temperatures_c=request.distribution_state,
            additional_state_names=distribution_additional_state_names(request.distribution),
            additional_temperature_bounds_c=distribution_temperature_bounds(request.distribution),
            source_temperature_state_index=source_node,
            source_flow_conductance_w_per_k=(flow,) * len(source.performance)
            if flow is not None
            else (),
            delivery_temperature_c=tuple(request.delivery_temperature_c)
            if source_node is not None
            else (),
            fingerprint=hashlib.sha256(
                request.model_dump_json(
                    exclude_computed_fields=True, exclude={"pattern_polish_seconds"}
                ).encode()
            ).hexdigest(),
            model_kind="thermal_building_with_paired_heat_pump"
            if paired
            else "thermal_building_with_heat_pump",
            standby_power_w=request.electrical.standby_power_w or 0.0,
            running_auxiliary_power_w=request.electrical.running_auxiliary_power_w or 0.0,
            startup_energy_wh=request.electrical.startup_energy_wh or 0.0,
            startup_warmup_s=request.electrical.startup_warmup_seconds or 0.0,
            initial_running=request.initial_running or False,
            total_electric_power_cap_w=request.max_electric_power_w,
            operating_points=tuple(
                tuple((point.thermal_power_w, point.electric_power_w) for point in points)
                for points in source.operating_points
            ),
        )
        budget.remaining("distribution_preparation")
        result = _optimize_problem(request, horizon, budget=budget)
        budget.remaining("source_result_validation")
        system_boundary_known = _system_boundary_known(request)
        minimum_modulation_known = all(source.minimum_modulation_known)
        initial_running_known = (
            not request.electrical.startup_modelled or request.initial_running is not None
        )
        source_qualified = (
            system_boundary_known
            and minimum_modulation_known
            and initial_running_known
            and source.configured_minimum_components_known
            and distribution_electricity_known
            and flow_known
        )
        reason = result.reason
        if not source_qualified:
            limitations = []
            if not system_boundary_known:
                limitations.append("electrical system boundary is not documented as total_system")
            if not minimum_modulation_known:
                limitations.append("minimum modulation is not covered by the source planning model")
            if not initial_running_known:
                limitations.append("positive startup energy with unknown initial running state")
            if not source.configured_minimum_components_known:
                limitations.append(
                    "configured active minimum has unknown standby/auxiliary components"
                )
            if not distribution_electricity_known:
                limitations.append(
                    "distribution fan/pump electricity or its COP boundary is unknown"
                )
            if not flow_known:
                limitations.append("source water-flow capacity is not known")
            reason += "; Conditional source calculation only: " + "; ".join(limitations)
        reference_thermal = tuple(
            power if paired else power * performance.cop
            for power, performance in zip(result.reference_heat_power_w, source.performance)
        )
        candidate_thermal = tuple(
            power if paired else power * performance.cop
            for power, performance in zip(result.candidate_heat_power_w, source.performance)
        )
        reference_base = tuple(
            _base_electric_at_input(horizon, k, power)
            for k, power in enumerate(result.reference_heat_power_w)
        )
        candidate_base = tuple(
            _base_electric_at_input(horizon, k, power)
            for k, power in enumerate(result.candidate_heat_power_w)
        )
        return ThermalHeatPlanningSolution.model_validate(
            {
                **result.model_dump(),
                "reason": reason,
                "comparison_qualified": result.comparison_qualified and source_qualified,
                "savings_cash_eur": result.savings_cash_eur if source_qualified else None,
                "savings_objective_eur": result.savings_objective_eur if source_qualified else None,
                "checks": {
                    **result.checks,
                    "source_system_boundary_known": system_boundary_known,
                    "source_minimum_modulation_known": minimum_modulation_known,
                    "source_initial_running_known_for_startup": initial_running_known,
                    "source_configured_minimum_components_known": source.configured_minimum_components_known,
                    "distribution_electricity_known": distribution_electricity_known,
                    "distribution_source_flow_known": flow_known,
                    "distribution_is_direct": request.distribution.kind == "direct",
                    # source_minimum_modulation_known already says it as a bool; this adds the
                    # plain-language text, only when the floor is unknown (runs with a floor
                    # stay identical).
                    **(
                        {
                            "source_minimum_power_warning": minimum_power_warning(
                                request.heat_pump_profile
                            )
                            or "Mindestleistung des Geraets ist durch das Kennfeld nicht bestaetigt."
                        }
                        if not minimum_modulation_known
                        else {}
                    ),
                },
                "source_horizon": source,
                "reference_heat_power_w": reference_base,
                "candidate_heat_power_w": candidate_base,
                "reference_thermal_power_w": reference_thermal,
                "candidate_thermal_power_w": candidate_thermal,
                "reference_cop": tuple(
                    q / p if p > 0 and q > 0 else None
                    for q, p in zip(reference_thermal, reference_base)
                ),
                "candidate_cop": tuple(
                    q / p if p > 0 and q > 0 else None
                    for q, p in zip(candidate_thermal, candidate_base)
                ),
                "distribution_electric_power_w": tuple(distribution_power),
                "distribution_quality_flags": tuple(sorted(distribution_flags)),
                "reference_electricity": _electricity_breakdown(
                    request, source, reference_base, reference_thermal
                ),
                "candidate_electricity": _electricity_breakdown(
                    request, source, candidate_base, candidate_thermal
                ),
            }
        )
    except ValueError as error:
        return ThermalHeatPlanningSolution(status="unsupported", reason=str(error))
