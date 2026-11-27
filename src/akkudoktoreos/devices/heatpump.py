"""Pure heat-pump performance maps, with explicit support and provenance.

Power maps describe delivered heat in watts; COP maps relate delivered heat to
electrical input at the documented rating conditions and system boundary. Water delivery
temperature is flow temperature. Air delivery temperature is the indoor rating
condition, not the hot discharge-air temperature. No device communication or
configuration singleton is used here.

The optional frost calculation reproduces a static heuristic from a supplied
HTML sizing tool. It predicts neither ice mass nor a defrost event. Catalog
values and this heuristic have not been independently manufacturer-verified.
"""

import json
import math
from bisect import bisect_left
from importlib.resources import files
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.prediction.weatherabc import WeatherDataRecord

FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, strip_whitespace=False)]
HeatPumpPresetId = Literal[
    "aira_12",
    "aira_6",
    "aira_8",
    "alpha_08",
    "alpha_11",
    "lambda_10L",
    "lambda_13L",
    "lambda_15L",
    "lambda_20L",
    "lambda_35L",
    "lg_12",
    "mitsubishi_13_10",
    "pana_l5",
    "pana_l7",
    "pana_l9",
    "panasonic_vz12ske",
    "stiebel_07_2",
    "stiebel_10_2",
    "vaillant_105",
    "vaillant_35",
    "vaillant_55",
    "vaillant_75",
    "viessmann_250A",
    "weishaupt_11",
    "wolf_cha07",
    "wolf_cha10",
]


class HeatPumpModel(PydanticBaseModel):
    """Immutable, finite data without configuration or control side effects."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class HeatPumpCOPPoint(HeatPumpModel):
    """A COP sample at explicit outdoor and delivery rating temperatures."""

    outdoor_temperature_c: FiniteNumber
    delivery_temperature_c: FiniteNumber
    cop: FiniteNumber = Field(gt=0)


class HeatPumpCapacityPoint(HeatPumpModel):
    """A delivered-thermal power sample; this is not electrical input power."""

    outdoor_temperature_c: FiniteNumber
    delivery_temperature_c: FiniteNumber
    thermal_power_w: FiniteNumber = Field(ge=0)


class HeatPumpOperatingPoint(HeatPumpModel):
    """A paired heat/electric rating, distinct from independent min/max ranges.

    Interpolation across heat load is meaningful only at matching weather and
    delivery conditions. Source-labelled nominal, part-load and maximum tests
    must not be mistaken for a fixed-COP map at every compressor load.
    """

    outdoor_temperature_c: FiniteNumber
    delivery_temperature_c: FiniteNumber
    thermal_power_w: FiniteNumber = Field(gt=0)
    electric_power_w: FiniteNumber = Field(gt=0)
    point_id: Identifier
    source: Identifier
    quality_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_named_source(self) -> Self:
        """Keep rating labels and evidence nonblank."""
        if not self.point_id.strip() or not self.source.strip():
            raise ValueError("Operating-point ID and source must not be blank")
        return self


class HeatPumpProvenance(HeatPumpModel):
    """Declared evidence and rating limitations, not an automatic verification."""

    source: Identifier
    quality: Literal["unverified_html", "user_supplied", "manufacturer_documented", "measured"]
    source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    electric_power_basis: Literal["total_system", "compressor_only", "unspecified"] = "unspecified"
    assumptions: tuple[str, ...] = ()


class HeatPumpProfile(HeatPumpModel):
    """Separate COP, maximum heat and optional minimum modulation maps.

    Points are linearly interpolated along outdoor-temperature slices, then
    between delivery-temperature slices. Missing points are never fabricated.
    A single delivery slice supports only that delivery temperature. Separate
    minimum/maximum maps may have different sample coordinates.
    """

    profile_id: Identifier
    heat_pump_type: Literal[
        "air_to_water",
        "air_to_air",
        "brine_to_water",
        "water_to_water",
        "brine_to_air",
        "water_to_air",
    ]
    cop_points: tuple[HeatPumpCOPPoint, ...] = Field(min_length=1, max_length=2048)
    thermal_max_points: tuple[HeatPumpCapacityPoint, ...] = Field(min_length=1, max_length=2048)
    thermal_min_points: tuple[HeatPumpCapacityPoint, ...] = Field(default=(), max_length=2048)
    cop_includes_defrost: bool | None = Field(default=None, strict=True)
    provenance: HeatPumpProvenance
    operating_points: tuple[HeatPumpOperatingPoint, ...] = Field(default=(), max_length=2048)
    operating_point_interpolation: Literal["exact", "linear_common_heat"] = "exact"
    minimum_operating_outdoor_temperature_c: FiniteNumber | None = None
    maximum_operating_outdoor_temperature_c: FiniteNumber | None = None

    @model_validator(mode="after")
    def validate_maps(self) -> Self:
        """Reject duplicate coordinates and contradictory known capacities."""
        if not self.profile_id.strip() or not self.provenance.source.strip():
            raise ValueError("Profile identifiers and sources must not be blank")
        for points in (self.cop_points, self.thermal_min_points, self.thermal_max_points):
            coordinates = [(p.delivery_temperature_c, p.outdoor_temperature_c) for p in points]
            if len(set(coordinates)) != len(coordinates):
                raise ValueError("Performance maps must not contain duplicate coordinates")
        maxima = {
            (p.delivery_temperature_c, p.outdoor_temperature_c): p.thermal_power_w
            for p in self.thermal_max_points
        }
        for point in self.thermal_min_points:
            maximum = maxima.get((point.delivery_temperature_c, point.outdoor_temperature_c))
            if maximum is not None and point.thermal_power_w > maximum:
                raise ValueError("Minimum thermal power must not exceed maximum thermal power")
        operating_coordinates = [
            (p.delivery_temperature_c, p.outdoor_temperature_c, p.thermal_power_w)
            for p in self.operating_points
        ]
        if len(set(operating_coordinates)) != len(operating_coordinates):
            raise ValueError("Operating maps must not contain duplicate heat-load coordinates")
        slices: dict[tuple[float, float], list[HeatPumpOperatingPoint]] = {}
        for operating_point in self.operating_points:
            slices.setdefault(
                (operating_point.delivery_temperature_c, operating_point.outdoor_temperature_c), []
            ).append(operating_point)
        for operating_slice in slices.values():
            ordered = sorted(operating_slice, key=lambda p: p.thermal_power_w)
            if any(a.electric_power_w > b.electric_power_w for a, b in zip(ordered, ordered[1:])):
                raise ValueError("Electrical power must be nondecreasing along an operating slice")
        if (
            self.minimum_operating_outdoor_temperature_c is not None
            and self.maximum_operating_outdoor_temperature_c is not None
            and self.minimum_operating_outdoor_temperature_c
            > self.maximum_operating_outdoor_temperature_c
        ):
            raise ValueError("Minimum operating outdoor temperature must not exceed maximum")
        return self


class HeatPumpDefrostSettings(HeatPumpModel):
    """Opt-in HTML frost heuristic; unknown COP test basis requires explicit consent.

    If COP already includes defrost, it is never penalized a second time. No
    cycling penalty, minimum-power estimate or capacity derating is invented.
    """

    mode: Literal["off", "html_heuristic"] = "off"
    allow_unknown_cop_basis: bool = Field(default=False, strict=True)


class HeatPumpPerformance(HeatPumpModel):
    """Evaluated rating, including diagnostic clamped values outside support.

    ``supported`` refers to interpolation coordinates, not manufacturer or field
    validation. A planner must reject unsupported ratings. Electrical bounds
    divide the thermal bounds by the same COP; without a part-load COP map this
    is a constant-COP approximation, explicitly flagged.
    """

    cop: FiniteNumber = Field(gt=0)
    min_electric_power_w: FiniteNumber | None = Field(default=None, ge=0)
    max_electric_power_w: FiniteNumber = Field(ge=0)
    min_thermal_power_w: FiniteNumber | None = Field(default=None, ge=0)
    max_thermal_power_w: FiniteNumber = Field(ge=0)
    supported: bool
    quality_flags: tuple[str, ...]
    dew_point_c: FiniteNumber | None = None
    estimated_evaporator_temperature_c: FiniteNumber | None = None
    defrost_multiplier: FiniteNumber = Field(default=1.0, gt=0, le=1)


class HeatPumpElectricalSettings(HeatPumpModel):
    """Optional electrical consumption outside the supplied COP system boundary.

    ``None`` means unknown or not modelled, whereas an explicit zero declares
    no additional consumption. Positive values require an explicit boundary
    declaration to avoid adding a pump, fan or transient twice. Startup energy
    is additional electrical energy per OFF-to-ON transition, not heat output
    and not a generic COP reduction. Standby here is a continuous electronics
    load in both ON and OFF states; running auxiliaries are additional ON load.
    """

    standby_power_w: FiniteNumber | None = Field(default=None, ge=0)
    running_auxiliary_power_w: FiniteNumber | None = Field(default=None, ge=0)
    startup_energy_wh: FiniteNumber | None = Field(default=None, ge=0)
    # Optional: startup loss as warm-up time. Extra electricity per OFF-to-ON transition =
    # warm-up time x electric power of the start slot (compressor draws full power while the
    # heat output still ramps up). Added to startup_energy_wh; None/0 keeps the old behaviour.
    startup_warmup_seconds: FiniteNumber | None = Field(default=None, ge=0, le=900)
    extras_are_outside_cop: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def require_explicit_electrical_boundary(self) -> Self:
        """Reject positive additions unless their exclusion from COP is declared."""
        if not self.extras_are_outside_cop and any(
            value is not None and value > 0
            for value in (
                self.standby_power_w,
                self.running_auxiliary_power_w,
                self.startup_energy_wh,
                self.startup_warmup_seconds,
            )
        ):
            raise ValueError("Additional electricity must be explicitly outside the COP boundary")
        return self

    @property
    def startup_modelled(self) -> bool:
        """True when any startup loss (fixed energy and/or warm-up time) is configured."""
        return (self.startup_energy_wh or 0.0) > 0 or (self.startup_warmup_seconds or 0.0) > 0


class HeatPumpElectricalBreakdown(HeatPumpModel):
    """Interval electricity with explicit, separately auditable components."""

    base_energy_wh: FiniteNumber = Field(ge=0)
    standby_energy_wh: FiniteNumber = Field(ge=0)
    auxiliary_energy_wh: FiniteNumber = Field(ge=0)
    startup_energy_wh: FiniteNumber = Field(ge=0)
    total_energy_wh: FiniteNumber = Field(ge=0)
    mean_electric_power_w: FiniteNumber = Field(ge=0)
    started: bool
    quality_flags: tuple[str, ...]


class HeatPumpThermalEstimate(HeatPumpModel):
    """Heat inferred from an electrical snapshot, never a heat-meter sample."""

    thermal_power_w: FiniteNumber = Field(ge=0)
    estimated: Literal[True] = True
    quality_flags: tuple[str, ...]


class WaterHeatingCurve(HeatPumpModel):
    """Explicit linear water-flow curve at a declared reference room target.

    At design outdoor temperature the uncorrected curve reaches maximum flow;
    at the heating limit it reaches minimum flow. A configured target correction
    shifts flow by ``gain * (room_target - reference_room_target)`` before the
    final min/max clamp. No room-feedback gain or adaptive calibration is
    inferred. This curve is not an air-to-air appliance temperature command.
    """

    design_outdoor_temperature_c: FiniteNumber
    heating_limit_outdoor_temperature_c: FiniteNumber
    min_flow_temperature_c: FiniteNumber
    max_flow_temperature_c: FiniteNumber
    reference_room_temperature_c: FiniteNumber
    room_target_correction_gain: FiniteNumber = Field(ge=0)

    @model_validator(mode="after")
    def require_ordered_curve(self) -> Self:
        """Reject inverted or undefined design and delivery-temperature spans."""
        if self.design_outdoor_temperature_c >= self.heating_limit_outdoor_temperature_c:
            raise ValueError("Design outdoor temperature must be below the heating limit")
        if self.min_flow_temperature_c > self.max_flow_temperature_c:
            raise ValueError("Minimum flow temperature must not exceed maximum")
        return self


class WaterHeatingCurveResult(HeatPumpModel):
    """Bounded delivery rating and diagnostic heating-limit request.

    ``heating_requested`` describes only the outdoor heating threshold. It must
    not veto cold-room recovery, hot-water preparation or a manual request.
    Actual room dynamics and upper comfort limits remain the planner's task.
    """

    flow_temperature_c: FiniteNumber
    unbounded_flow_temperature_c: FiniteNumber
    heating_requested: bool
    quality_flags: tuple[str, ...]


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def evaluate_water_heating_curve(
    curve: WaterHeatingCurve, weather: WeatherDataRecord, room_target_c: float
) -> WaterHeatingCurveResult:
    """Evaluate a configured water curve using current or forecast outdoor air.

    The native weather timestamp and its freshness are the caller's concern.
    The curve uses no hidden 24-hour outdoor filter or fixed night setback.
    """
    if weather.weather_temp_air is None:
        raise ValueError("Water heating curve requires outdoor air temperature")
    outdoor = _finite(weather.weather_temp_air, "Outdoor temperature")
    target = _finite(room_target_c, "Room target")
    slope = (curve.max_flow_temperature_c - curve.min_flow_temperature_c) / (
        curve.heating_limit_outdoor_temperature_c - curve.design_outdoor_temperature_c
    )
    unbounded = (
        curve.min_flow_temperature_c
        + slope * (curve.heating_limit_outdoor_temperature_c - outdoor)
        + curve.room_target_correction_gain * (target - curve.reference_room_temperature_c)
    )
    bounded = min(curve.max_flow_temperature_c, max(curve.min_flow_temperature_c, unbounded))
    flags = ["configured_static_water_curve", "outdoor_threshold_not_a_recovery_veto"]
    if bounded != unbounded:
        flags.append("flow_temperature_clamped_to_configured_bounds")
    return WaterHeatingCurveResult(
        flow_temperature_c=bounded,
        unbounded_flow_temperature_c=unbounded,
        heating_requested=outdoor < curve.heating_limit_outdoor_temperature_c,
        quality_flags=tuple(flags),
    )


def evaluate_heat_pump_electricity(
    performance: HeatPumpPerformance,
    thermal_power_w: float,
    duration_seconds: float,
    running: bool,
    was_running: bool,
    settings: HeatPumpElectricalSettings,
) -> HeatPumpElectricalBreakdown:
    """Account for constant interval heat and explicitly additional electricity.

    A running interval represents a single uninterrupted ON state. Unknown
    prior state must be resolved by the caller; it cannot prove a startup. No
    fractional ON duration or hidden subinterval cycling is reconstructed.
    Unsupported ratings, load outside known modulation and heat while OFF are
    rejected. Unknown minimum modulation is retained as a quality limitation.
    """
    power = _finite(thermal_power_w, "Thermal power")
    duration = _finite(duration_seconds, "Duration")
    if duration <= 0 or power < 0:
        raise ValueError("Duration must be positive and thermal power nonnegative")
    if not isinstance(running, bool) or not isinstance(was_running, bool):
        raise ValueError("Running and previous running states must be booleans")
    if not performance.supported:
        raise ValueError("Electricity accounting requires a supported performance rating")
    if not running and power != 0:
        raise ValueError("An OFF heat pump must not deliver positive modelled heat")
    # Solver feasibility is numerical, while physical violations remain errors.
    # This is ten microwatts, never a permission to extrapolate a power map.
    power_tolerance_w = 1e-5
    if power > performance.max_thermal_power_w + power_tolerance_w:
        raise ValueError("Thermal load exceeds the rated maximum capacity")
    if running and performance.min_thermal_power_w is not None:
        if power < performance.min_thermal_power_w - power_tolerance_w:
            raise ValueError("Running thermal load is below the known minimum modulation")
    hours = duration / 3600
    started = running and not was_running
    base = power / performance.cop * hours
    standby = (settings.standby_power_w or 0.0) * hours
    auxiliary = (settings.running_auxiliary_power_w or 0.0) * hours if running else 0.0
    startup = (settings.startup_energy_wh or 0.0) if started else 0.0
    if started and settings.startup_warmup_seconds:
        startup += settings.startup_warmup_seconds / 3600 * (base / hours)
    total = base + standby + auxiliary + startup
    flags = list(performance.quality_flags)
    if power > performance.max_thermal_power_w or (
        running
        and performance.min_thermal_power_w is not None
        and power < performance.min_thermal_power_w
    ):
        flags.append("capacity_boundary_roundoff_tolerance_1e-5_w")
    for name in ("standby_power_w", "running_auxiliary_power_w", "startup_energy_wh"):
        if name == "startup_energy_wh" and settings.startup_warmup_seconds is not None:
            continue
        if getattr(settings, name) is None:
            flags.append(f"unmodelled_electricity:{name}")
    if settings.extras_are_outside_cop:
        flags.append("configured_additional_electricity_outside_cop")
    flags.append("startup_electricity_not_assumed_delivered_heat")
    return HeatPumpElectricalBreakdown(
        base_energy_wh=base,
        standby_energy_wh=standby,
        auxiliary_energy_wh=auxiliary,
        startup_energy_wh=startup,
        total_energy_wh=total,
        mean_electric_power_w=total / hours,
        started=started,
        quality_flags=tuple(flags),
    )


def calculate_dew_point(temperature_c: float, relative_humidity: float) -> float:
    """Return the Magnus liquid-water approximation in Celsius.

    Humidity must be in (0, 100] percent. Zero humidity has no finite dew point
    and is rejected rather than silently replaced with the HTML's 0.1% floor.
    This approximation is not an ice/frost-point measurement.
    """
    temperature_c = _finite(temperature_c, "Temperature")
    relative_humidity = _finite(relative_humidity, "Relative humidity")
    if not 0 < relative_humidity <= 100 or temperature_c <= -243.12:
        raise ValueError("Magnus approximation requires RH in (0,100] and temperature > -243.12")
    alpha = math.log(relative_humidity / 100) + 17.62 * temperature_c / (243.12 + temperature_c)
    if alpha >= 17.62:
        raise ValueError("Conditions are outside the finite Magnus approximation")
    return 243.12 * alpha / (17.62 - alpha)


def _linear(samples: list[tuple[float, float]], value: float) -> tuple[float, bool]:
    samples = sorted(samples)
    xs = [point[0] for point in samples]
    if value <= xs[0]:
        return samples[0][1], value == xs[0]
    if value >= xs[-1]:
        return samples[-1][1], value == xs[-1]
    right = bisect_left(xs, value)
    if xs[right] == value:
        return samples[right][1], True
    x0, y0 = samples[right - 1]
    x1, y1 = samples[right]
    return y0 + (y1 - y0) * (value - x0) / (x1 - x0), True


def _map_value(
    points: tuple[HeatPumpCOPPoint, ...] | tuple[HeatPumpCapacityPoint, ...],
    outdoor: float,
    delivery: float,
) -> tuple[float, bool]:
    slices: dict[float, list[tuple[float, float]]] = {}
    for point in points:
        value = point.cop if isinstance(point, HeatPumpCOPPoint) else point.thermal_power_w
        slices.setdefault(point.delivery_temperature_c, []).append(
            (point.outdoor_temperature_c, value)
        )
    levels = sorted(slices)
    right = bisect_left(levels, delivery)
    if right < len(levels) and levels[right] == delivery:
        return _linear(slices[delivery], outdoor)
    if right == 0 or right == len(levels):
        level = levels[0] if right == 0 else levels[-1]
        value, _ = _linear(slices[level], outdoor)
        return value, False
    low, high = levels[right - 1], levels[right]
    v0, supported0 = _linear(slices[low], outdoor)
    v1, supported1 = _linear(slices[high], outdoor)
    return v0 + (v1 - v0) * (delivery - low) / (high - low), supported0 and supported1


def _source_temperature(
    profile: HeatPumpProfile, weather: WeatherDataRecord, value: float | None
) -> float:
    """Resolve the map's source axis without confusing ground/water with weather.

    Existing catalog coordinates retain their ``outdoor_temperature_c`` name
    for compatibility; for non-air sources this axis is source inlet temperature.
    """
    if value is None:
        if not profile.heat_pump_type.startswith("air_to_"):
            raise ValueError("Non-air heat pumps require explicit source inlet temperature")
        value = weather.weather_temp_air
    if value is None:
        raise ValueError("Heat-pump evaluation requires source temperature")
    return _finite(value, "Source temperature")


def evaluate_heat_pump(
    profile: HeatPumpProfile,
    weather: WeatherDataRecord,
    delivery_temperature_c: float,
    thermal_load_w: float | None = None,
    defrost: HeatPumpDefrostSettings | None = None,
    *,
    source_temperature_c: float | None = None,
) -> HeatPumpPerformance:
    """Evaluate a supplied profile without accessing configuration or devices.

    Args:
        profile: Explicit rating data, technology and provenance.
        weather: EOS weather record; outdoor temperature is required.
        delivery_temperature_c: Water flow or indoor-air rating temperature.
        thermal_load_w: Optional delivered-heat load, needed by the frost heuristic.
        defrost: Optional, disabled by default. Never adjusts defrost-inclusive COP.

    Returns:
        Rating with support, missing-data and heuristic quality flags.

    Raises:
        ValueError: Required inputs are missing, nonfinite or contradictory.
    """
    outdoor = _source_temperature(profile, weather, source_temperature_c)
    if (
        not profile.heat_pump_type.startswith("air_to_")
        and defrost is not None
        and defrost.mode != "off"
    ):
        raise ValueError("Air frost correction cannot be applied to water or brine sources")
    delivery = _finite(delivery_temperature_c, "Delivery temperature")
    if thermal_load_w is not None:
        thermal_load_w = _finite(thermal_load_w, "Thermal load")
        if thermal_load_w < 0:
            raise ValueError("Thermal load must be nonnegative")
    cop, cop_supported = _map_value(profile.cop_points, outdoor, delivery)
    maximum, maximum_supported = _map_value(profile.thermal_max_points, outdoor, delivery)
    minimum, minimum_supported = None, True
    flags = ["constant_cop_across_modulation", f"provenance:{profile.provenance.quality}"]
    flags.append(f"electric_power_basis:{profile.provenance.electric_power_basis}")
    if not cop_supported:
        flags.append("cop_outside_support_clamped")
    if not maximum_supported:
        flags.append("maximum_capacity_outside_support_clamped")
    if profile.thermal_min_points:
        minimum, minimum_supported = _map_value(profile.thermal_min_points, outdoor, delivery)
        if not minimum_supported:
            flags.append("minimum_capacity_outside_support_clamped")
        if minimum > maximum:
            raise ValueError("Interpolated minimum thermal power exceeds maximum")
    else:
        flags.append("minimum_modulation_unknown")
    if profile.cop_includes_defrost is None:
        flags.append("cop_defrost_basis_unknown")
    operating_supported = True
    if profile.operating_points:
        operating_supported = False
        flags.append("load_axis_requires_segment_planning")
    if (
        profile.minimum_operating_outdoor_temperature_c is not None
        and outdoor < profile.minimum_operating_outdoor_temperature_c
    ) or (
        profile.maximum_operating_outdoor_temperature_c is not None
        and outdoor > profile.maximum_operating_outdoor_temperature_c
    ):
        operating_supported = False
        flags.append("outside_declared_operating_temperature_range")
    air_source = profile.heat_pump_type.startswith("air_to_")
    dew_point = weather.weather_dew_point if air_source else None
    humidity = weather.weather_relative_humidity if air_source else None
    if dew_point is not None:
        dew_point = _finite(dew_point, "Dew point")
        if dew_point > outdoor + 0.5:
            raise ValueError("Dew point must not exceed outdoor temperature by more than 0.5 K")
    elif humidity is not None:
        humidity = _finite(humidity, "Relative humidity")
        if humidity == 0:
            flags.append("finite_dew_point_unavailable_at_zero_humidity")
        else:
            dew_point = calculate_dew_point(outdoor, humidity)
            flags.append("dew_point_magnus_derived")
    if humidity is not None and not 0 <= _finite(humidity, "Relative humidity") <= 100:
        raise ValueError("Relative humidity must be in [0,100]")
    multiplier, evaporator = 1.0, None
    settings = defrost or HeatPumpDefrostSettings()
    if settings.mode == "html_heuristic":
        flags.append("defrost_static_html_heuristic_not_event_prediction")
        if profile.cop_includes_defrost is True:
            flags.append("defrost_correction_skipped_already_in_cop")
        elif profile.cop_includes_defrost is None and not settings.allow_unknown_cop_basis:
            flags.append("defrost_correction_skipped_unknown_cop_basis")
        elif minimum is None or thermal_load_w is None or humidity is None or dew_point is None:
            flags.append("defrost_correction_skipped_missing_inputs")
        elif maximum > 0:
            load_factor = min(1.0, thermal_load_w / maximum)
            evaporator = outdoor - (0.5 + 3 * load_factor)
            if (
                thermal_load_w > minimum * 1.2
                and evaporator < -0.5
                and evaporator < dew_point
                and humidity > 88
                and -4 <= outdoor <= 3
            ):
                multiplier = 1 - 0.15 * load_factor
                flags.append("defrost_heuristic_cop_penalty_applied")
    effective_cop = cop * multiplier
    return HeatPumpPerformance(
        cop=effective_cop,
        min_electric_power_w=None if minimum is None else minimum / effective_cop,
        max_electric_power_w=maximum / effective_cop,
        min_thermal_power_w=minimum,
        max_thermal_power_w=maximum,
        supported=cop_supported and maximum_supported and minimum_supported and operating_supported,
        quality_flags=tuple(flags),
        dew_point_c=dew_point,
        estimated_evaporator_temperature_c=evaporator,
        defrost_multiplier=multiplier,
    )


def get_heat_pump_operating_points(
    profile: HeatPumpProfile,
    weather: WeatherDataRecord,
    delivery_temperature_c: float,
    *,
    source_temperature_c: float | None = None,
) -> tuple[HeatPumpOperatingPoint, ...]:
    """Return a supported source/delivery slice, sorted by heat.

    Exact lookup is the default. Explicit linear interpolation uses only the
    common heat-load range of enclosing source/delivery slices, never extrapolation.
    Empty means no supported slice. This function neither borrows a +7 C
    part-load curve at another outdoor temperature nor pairs independently
    listed power/capacity extrema. A single point supports only that ON point;
    two or more points permit a separately declared piecewise interpolation.
    """
    outdoor = _source_temperature(profile, weather, source_temperature_c)
    delivery = _finite(delivery_temperature_c, "Delivery temperature")
    if (
        profile.minimum_operating_outdoor_temperature_c is not None
        and outdoor < profile.minimum_operating_outdoor_temperature_c
    ) or (
        profile.maximum_operating_outdoor_temperature_c is not None
        and outdoor > profile.maximum_operating_outdoor_temperature_c
    ):
        return ()
    exact = tuple(
        sorted(
            (
                point
                for point in profile.operating_points
                if point.outdoor_temperature_c == outdoor
                and point.delivery_temperature_c == delivery
            ),
            key=lambda point: point.thermal_power_w,
        )
    )
    if exact or profile.operating_point_interpolation == "exact":
        return exact

    def bracket(values: list[float], value: float) -> list[tuple[float, float]]:
        levels = sorted(set(values))
        if not levels or value < levels[0] or value > levels[-1]:
            return []
        i = bisect_left(levels, value)
        if levels[i] == value:
            return [(value, 1.0)]
        low, high = levels[i - 1], levels[i]
        weight = (value - low) / (high - low)
        return [(low, 1 - weight), (high, weight)]

    corners: list[tuple[float, list[HeatPumpOperatingPoint]]] = []
    for sink, sink_weight in bracket(
        [p.delivery_temperature_c for p in profile.operating_points], delivery
    ):
        sink_points = [p for p in profile.operating_points if p.delivery_temperature_c == sink]
        sources = bracket([p.outdoor_temperature_c for p in sink_points], outdoor)
        if not sources:
            return ()
        for source, source_weight in sources:
            points = sorted(
                [p for p in sink_points if p.outdoor_temperature_c == source],
                key=lambda p: p.thermal_power_w,
            )
            corners.append((sink_weight * source_weight, points))
    if not corners:
        return ()
    lower = max(points[0].thermal_power_w for _, points in corners)
    upper = min(points[-1].thermal_power_w for _, points in corners)
    if lower > upper:
        return ()
    loads = sorted(
        {
            lower,
            upper,
            *[
                p.thermal_power_w
                for _, points in corners
                for p in points
                if lower <= p.thermal_power_w <= upper
            ],
        }
    )
    return tuple(
        HeatPumpOperatingPoint(
            outdoor_temperature_c=outdoor,
            delivery_temperature_c=delivery,
            thermal_power_w=heat,
            electric_power_w=sum(
                weight * _linear([(p.thermal_power_w, p.electric_power_w) for p in points], heat)[0]
                for weight, points in corners
            ),
            point_id=f"interpolated-{index}",
            source=profile.provenance.source,
            quality_flags=tuple(
                sorted(
                    {
                        "source_delivery_interpolation_at_common_thermal_load",
                        *[
                            flag
                            for _, points in corners
                            for p in points
                            for flag in p.quality_flags
                        ],
                    }
                )
            ),
        )
        for index, heat in enumerate(loads)
    )


def infer_thermal_power_from_electric(
    profile: HeatPumpProfile,
    weather: WeatherDataRecord,
    delivery_temperature_c: float,
    electric_power_w: float,
    electrical: HeatPumpElectricalSettings,
    *,
    source_temperature_c: float | None = None,
) -> HeatPumpThermalEstimate:
    """Infer heat after explicitly declared non-thermal electrical consumption.

    Only an exact operating-point weather/delivery slice can be inverted. A
    plateau with several possible heat outputs is ambiguous and rejected.
    Other profiles use the supported fixed-COP approximation and known load
    bounds. No independent range extrema are paired and no weather or load
    extrapolation is performed. A one-hundred-millionth watt boundary tolerance
    only absorbs floating-point cancellation when declared extras are removed.

    Positive startup energy cannot be allocated to an instantaneous snapshot
    and requires an actual heat measurement or a separate transient model.
    Unknown additions remain explicitly unknown; they are not measured zeros.
    """
    electric = _finite(electric_power_w, "Electrical power")
    delivery = _finite(delivery_temperature_c, "Delivery temperature")
    if electric < 0:
        raise ValueError("Electrical input must be nonnegative; declare idle noise upstream")
    _source_temperature(profile, weather, source_temperature_c)
    if electrical.startup_modelled:
        raise ValueError("Positive startup energy cannot be separated from an electrical snapshot")
    flags = {
        "thermal_power_inferred_from_electricity_not_measured",
        "no_transient_or_stored_device_heat_identified",
        f"provenance:{profile.provenance.quality}",
        f"electric_power_basis:{profile.provenance.electric_power_basis}",
    }
    for name in ("standby_power_w", "running_auxiliary_power_w", "startup_energy_wh"):
        if name == "startup_energy_wh" and electrical.startup_warmup_seconds is not None:
            continue
        if getattr(electrical, name) is None:
            flags.add(f"unmodelled_electricity:{name}")
    tolerance_w = 1e-8
    if (
        electrical.extras_are_outside_cop
        and electrical.standby_power_w is not None
        and electric <= electrical.standby_power_w + tolerance_w
    ):
        flags.add("electric_snapshot_in_declared_off_standby_band")
        return HeatPumpThermalEstimate(thermal_power_w=0.0, quality_flags=tuple(sorted(flags)))
    base = (
        electric
        - (electrical.standby_power_w or 0.0)
        - (electrical.running_auxiliary_power_w or 0.0)
    )
    if base < -tolerance_w:
        raise ValueError("Electrical input is below declared running auxiliary consumption")
    if base < 0:
        base = 0.0
        flags.add("electric_boundary_roundoff_tolerance_1e-8_w")
    if base == 0:
        raise ValueError("Zero active input requires an explicitly declared OFF/standby band")
    if electrical.extras_are_outside_cop:
        flags.add("declared_nonthermal_electricity_subtracted")
    if not profile.operating_points:
        performance = evaluate_heat_pump(
            profile, weather, delivery, source_temperature_c=source_temperature_c
        )
        if not performance.supported:
            raise ValueError("Thermal inference requires supported heat-pump map conditions")
        thermal = base * performance.cop
        if thermal > performance.max_thermal_power_w + 1e-5 or (
            performance.min_thermal_power_w is not None
            and thermal < performance.min_thermal_power_w - 1e-5
        ):
            raise ValueError("Electrical snapshot is outside the supported modulation range")
        flags.update(performance.quality_flags)
        flags.add("fixed_cop_inverse_estimate_at_declared_rating")
        return HeatPumpThermalEstimate(thermal_power_w=thermal, quality_flags=tuple(sorted(flags)))
    points = get_heat_pump_operating_points(
        profile, weather, delivery, source_temperature_c=source_temperature_c
    )
    if not points:
        raise ValueError("No exact operating-point weather/delivery slice for thermal inference")
    if (
        base < points[0].electric_power_w - tolerance_w
        or base > points[-1].electric_power_w + tolerance_w
    ):
        raise ValueError("Electrical snapshot is outside the paired operating-point support")
    bounded = min(points[-1].electric_power_w, max(points[0].electric_power_w, base))
    if bounded != base:
        flags.add("electric_boundary_roundoff_tolerance_1e-8_w")
    base = bounded
    candidates: list[float] = []
    for point in points:
        if abs(base - point.electric_power_w) <= tolerance_w:
            candidates.append(point.thermal_power_w)
            flags.update(point.quality_flags)
            flags.add(f"inverse_point:{point.point_id}")
    for lower, upper in zip(points, points[1:]):
        if lower.electric_power_w <= base <= upper.electric_power_w:
            if upper.electric_power_w - lower.electric_power_w <= tolerance_w:
                raise ValueError("Ambiguous electrical plateau has different possible heat outputs")
            fraction = (base - lower.electric_power_w) / (
                upper.electric_power_w - lower.electric_power_w
            )
            candidates.append(
                lower.thermal_power_w + fraction * (upper.thermal_power_w - lower.thermal_power_w)
            )
            for point in (lower, upper):
                flags.update(point.quality_flags)
                flags.add(f"inverse_point:{point.point_id}")
    if not candidates or max(candidates) - min(candidates) > 1e-5:
        raise ValueError("Electrical snapshot has no unique supported thermal inverse")
    flags.add("paired_operating_points_inverse_interpolation")
    return HeatPumpThermalEstimate(
        thermal_power_w=candidates[0], quality_flags=tuple(sorted(flags))
    )


def _read_catalog() -> dict[str, HeatPumpProfile]:
    result: dict[str, HeatPumpProfile] = {}
    for filename in ("heatpumppresets.json", "heatpumpmanufacturerpresets.json"):
        data = json.loads(files("akkudoktoreos").joinpath(filename).read_text("utf-8"))
        for key, value in data["profiles"].items():
            if key in result:
                raise ValueError("Duplicate catalog profile identifier")
            result[key] = HeatPumpProfile.model_validate(value)
    return result


def list_heat_pump_presets() -> tuple[str, ...]:
    """Return sorted catalog identifiers; the catalog is read only on demand."""
    return tuple(sorted(_read_catalog()))


def get_heat_pump_preset(preset_id: str) -> HeatPumpProfile:
    """Load a source-labelled profile without imputing missing performance data."""
    try:
        return _read_catalog()[preset_id]
    except KeyError as error:
        raise ValueError(f"Unknown heat-pump preset: {preset_id}") from error
