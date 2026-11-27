"""Build the thermal device of the optimizer from the thermal models.

``build_thermal_device`` turns what the thermal package knows - the building model, the heat
distribution, the heat pump with its performance map, the comfort bands and the outdoor
temperature forecast - into the ``ThermalParams`` of a ``PlanningProblem``. It adds no physics:
the matrices come from ``build_distribution_dynamics`` and the performance from
``evaluate_heat_pump`` and ``get_heat_pump_operating_points``. What it prepares is per slot:

- the exact dynamics ``T[k+1] = A T[k] + B Q + E T_out`` (one set of matrices per distinct slot
  duration and fan level, so a shortened first slot and fan profiles are exact),
- the table of operating points ``(heat, compressor electric power)``: the paired points of the
  profile where it has them, otherwise two points on the line of the coefficient of performance
  between the minimum and the maximum heat of the slot,
- the electricity outside the compressor (standby, circulation, auxiliaries, start),
- the electric power limit of the heat source: the table of a slot ends where compressor,
  standby, circulation and auxiliaries together reach it; the energy of a start (the ramp of
  the run share times the energy of one start, spread over the slot) counts against the limit
  on top (the planning and the check of the plan count it the same way),
- the comfort bounds and the protection minimum,
- the reference state of the end value of the stored heat.

Water and buffer distributions (4-5 states) are rejected.
"""

import math
from collections.abc import Sequence
from typing import Optional

import numpy as np
import numpy.typing as npt

from akkudoktoreos.devices.heatpump import (
    HeatPumpDefrostSettings,
    HeatPumpElectricalSettings,
    HeatPumpProfile,
    evaluate_heat_pump,
    get_heat_pump_operating_points,
)
from akkudoktoreos.devices.models.thermal import ThermalParams
from akkudoktoreos.prediction.weatherabc import WeatherDataRecord
from akkudoktoreos.thermal.thermalcomfort import DEFAULT_ABSENCE_MINIMUM_C
from akkudoktoreos.thermal.thermaldistribution import (
    AirDistributionSettings,
    DirectDistributionSettings,
    ThermalDistributionSettings,
    build_distribution_dynamics,
    distribution_capacities_j_per_k,
    distribution_electric_power_w,
)
from akkudoktoreos.thermal.thermalmodels import ThermalBuildingRC2Parameters, ThermalComfortBand

# Number of operating points per slot that the table has room for.
MAX_OPERATING_POINTS = 16


def _per_slot(value: npt.ArrayLike | None, slots: int, name: str) -> np.ndarray:
    """Return a scalar or a sequence as an array with one value per slot."""
    if value is None:
        raise ValueError(f"{name} is required.")
    array = np.asarray(value, dtype=float)
    if array.ndim == 0:
        return np.full(slots, float(array))
    if array.shape != (slots,):
        raise ValueError(f"{name} needs {slots} values, got {array.shape}.")
    return array


def steady_reference(
    a: np.ndarray, b: np.ndarray, e: np.ndarray, outdoor_c: float, room_c: float
) -> tuple[np.ndarray, float]:
    """Return the steady state that holds a room temperature, and the heat that does it.

    Solves ``T = A T + B Q + E T_out`` with ``T[0] = room_c`` for ``(T, Q)``.

    Args:
        a: Transition matrix of one slot.
        b: Heat input vector [K/W].
        e: Outdoor temperature vector.
        outdoor_c: Outdoor temperature [°C].
        room_c: Room temperature to hold [°C].

    Returns:
        ``(temperatures, heat_w)``; the heat is negative where the building holds the room
        temperature on its own (no heating load).
    """
    states = len(b)
    system = np.zeros((states + 1, states + 1))
    system[:states, :states] = np.eye(states) - a
    system[:states, states] = -b
    system[states, 0] = 1.0
    rhs = np.zeros(states + 1)
    rhs[:states] = e * outdoor_c
    rhs[states] = room_c
    solution = np.linalg.solve(system, rhs)
    return solution[:states], float(solution[states])


def _within_electric_limit(
    heat: list[float], power: list[float], limit_w: float
) -> tuple[list[float], list[float]]:
    """Cut an operating table at a compressor power limit.

    The table is followed up to the first point above the limit; the heat at which the line to
    that point reaches the limit becomes the new last point. A table whose first point is
    already above the limit is empty (the heat pump cannot run).
    """
    for index, value in enumerate(power):
        if value <= limit_w + 1e-9:
            continue
        if index == 0:
            return [], []
        weight = (limit_w - power[index - 1]) / (value - power[index - 1])
        cut = heat[index - 1] + weight * (heat[index] - heat[index - 1])
        kept_heat, kept_power = heat[:index], power[:index]
        if cut > kept_heat[-1] + 1e-6:
            kept_heat, kept_power = kept_heat + [cut], kept_power + [limit_w]
        return kept_heat, kept_power
    return heat, power


def build_thermal_device(
    building: ThermalBuildingRC2Parameters,
    distribution: ThermalDistributionSettings,
    profile: HeatPumpProfile,
    *,
    outdoor_c: npt.ArrayLike,
    comfort_bands: Sequence[ThermalComfortBand],
    initial_temperature_c: npt.ArrayLike,
    delivery_temperature_c: npt.ArrayLike,
    slot_seconds: float = 900.0,
    first_slot_fraction: float = 1.0,
    electrical: Optional[HeatPumpElectricalSettings] = None,
    initial_running: bool = False,
    blocked: Optional[npt.ArrayLike] = None,
    fan_levels: Optional[Sequence[str]] = None,
    source_temperature_c: Optional[npt.ArrayLike] = None,
    relative_humidity: Optional[npt.ArrayLike] = None,
    defrost: Optional[HeatPumpDefrostSettings] = None,
    assumed_thermal_load_fraction: float = 1.0,
    protection_min_c: Optional[float] = DEFAULT_ABSENCE_MINIMUM_C,
    reference_room_c: Optional[float] = None,
    allow_clamped_rating: bool = False,
    max_electric_power_w: Optional[float] = None,
    under_weight: Optional[np.ndarray] = None,
    name: str = "heat_pump",
) -> ThermalParams:
    """Build the thermal device of a planning problem.

    Args:
        building: Room and building mass model (delivered heat as input).
        distribution: ``direct`` (2 states) or ``air`` (3 states); water and buffer
            distributions are rejected.
        profile: Heat pump with performance map or paired operating points.
        outdoor_c: Outdoor temperature forecast per slot [°C]; this also defines the horizon.
        comfort_bands: Room temperature band per slot; the band of a slot holds for the room
            temperature at the start of the slot.
        initial_temperature_c: Temperatures at slot 0 (room, mass, and the air node with air
            distribution).
        delivery_temperature_c: Delivery rating condition of the map (water flow or indoor air
            rating temperature), one value or one per slot.
        slot_seconds: Slot duration [s].
        first_slot_fraction: Part of slot 0 still ahead; it shortens the first slot.
        electrical: Electricity outside the rating (standby, auxiliaries, start); none by default.
        initial_running: Whether the heat pump was running before slot 0.
        blocked: True per slot where the heat pump must not run.
        fan_levels: Air distribution: fan level id per slot (default level if omitted).
        source_temperature_c: Source temperature per slot for water or brine sources.
        relative_humidity: Relative humidity per slot (percent) for the defrost heuristic.
        defrost: Defrost heuristic of the fixed coefficient of performance path.
        assumed_thermal_load_fraction: Share of the maximum heat at which the coefficient of
            performance of the fixed path is evaluated (as the planning of the source did).
        protection_min_c: Protection minimum of the room temperature, never above the comfort
            minimum of a slot; None for none.
        reference_room_c: Room temperature of the reference state of the end value of the heat;
            default the target temperature of the last band.
        allow_clamped_rating: Use the clamped value of a rating outside the support of the map
            instead of rejecting it.
        max_electric_power_w: Electric power the heat source may draw [W]: compressor plus
            standby, circulation and auxiliaries. The heat of a slot is limited to what stays
            within it; a slot where not even the lowest point does is blocked. The energy of a
            start (the ramp of the run share, with its warm-up) counts against the limit on top.
            None = no limit.
        under_weight: ``(slots, 3)`` weight of the shortfall below the comfort minimum per tier
            (``ThermalParams.under_weight``); None = priced at the comfort price.
        name: Name of the device.

    Raises:
        ValueError: For an unsupported distribution, inconsistent lengths, or a slot whose rating
            lies outside the support of the performance map.
    """
    if isinstance(distribution, AirDistributionSettings):
        if not profile.heat_pump_type.endswith("_to_air"):
            raise ValueError("An air distribution needs a heat pump that heats air.")
    elif not isinstance(distribution, DirectDistributionSettings):
        raise ValueError(
            "Only direct and air distributions are supported (2 or 3 states); water and "
            "buffer distributions (4-5 states) are not available yet."
        )
    outdoor = np.asarray(outdoor_c, dtype=float)
    slots = len(outdoor)
    if slots == 0 or len(comfort_bands) != slots:
        raise ValueError("outdoor_c and comfort_bands need one value per slot.")
    delivery = _per_slot(delivery_temperature_c, slots, "delivery_temperature_c")
    source_series = (
        None if source_temperature_c is None else np.asarray(source_temperature_c, dtype=float)
    )
    humidity_series = (
        None if relative_humidity is None else np.asarray(relative_humidity, dtype=float)
    )
    if not 0.0 < first_slot_fraction <= 1.0:
        raise ValueError("first_slot_fraction must be in (0, 1].")
    electrical = electrical or HeatPumpElectricalSettings()
    defrost = defrost or HeatPumpDefrostSettings()
    seconds = np.full(slots, float(slot_seconds))
    seconds[0] *= first_slot_fraction
    hours = seconds / 3600.0

    # Exact dynamics per slot; identical (duration, fan) pairs share their matrices.
    cache: dict[tuple[float, Optional[str]], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    states = 3 if isinstance(distribution, AirDistributionSettings) else 2
    initial = np.asarray(initial_temperature_c, dtype=float)
    if initial.shape != (states,):
        raise ValueError(f"initial_temperature_c needs {states} values.")
    dyn_a = np.zeros((slots, states, states))
    dyn_b = np.zeros((slots, states))
    dyn_e = np.zeros((slots, states))
    fixed = np.zeros(slots)
    standby = electrical.standby_power_w or 0.0
    for slot in range(slots):
        fan = None if fan_levels is None else fan_levels[slot]
        key = (float(seconds[slot]), fan)
        if key not in cache:
            cache[key] = build_distribution_dynamics(
                building, distribution, seconds=key[0], flow_or_fan=fan
            )
        dyn_a[slot], dyn_b[slot], dyn_e[slot] = cache[key]
        circulation, _ = distribution_electric_power_w(distribution, fan)
        fixed[slot] = standby + (circulation or 0.0)

    # Operating table per slot.
    points = np.zeros((slots, MAX_OPERATING_POINTS))
    electric = np.zeros((slots, MAX_OPERATING_POINTS))
    count = np.zeros(slots, dtype=np.int64)
    for slot in range(slots):
        record = WeatherDataRecord(
            weather_temp_air=float(outdoor[slot]),
            weather_relative_humidity=(
                None if humidity_series is None else float(humidity_series[slot])
            ),
        )
        source = None if source_series is None else float(source_series[slot])
        if profile.operating_points:
            table = get_heat_pump_operating_points(
                profile, record, float(delivery[slot]), source_temperature_c=source
            )
            if len(table) > MAX_OPERATING_POINTS:
                raise ValueError(f"At most {MAX_OPERATING_POINTS} operating points per slot.")
            heat = [p.thermal_power_w for p in table]
            power = [p.electric_power_w for p in table]
        else:
            rating = evaluate_heat_pump(
                profile, record, float(delivery[slot]), source_temperature_c=source
            )
            if not rating.supported and not allow_clamped_rating:
                raise ValueError(
                    f"The rating of slot {slot} is outside of the support of the performance map "
                    f"({', '.join(rating.quality_flags)})."
                )
            performance = evaluate_heat_pump(
                profile,
                record,
                float(delivery[slot]),
                thermal_load_w=rating.max_thermal_power_w * assumed_thermal_load_fraction,
                defrost=defrost,
                source_temperature_c=source,
            )
            low = performance.min_thermal_power_w or 0.0
            high = performance.max_thermal_power_w
            heat = [low, high] if high > low else [high]
            power = [h / performance.cop for h in heat]
        if max_electric_power_w is not None:
            compressor_w = (
                max_electric_power_w - fixed[slot] - (electrical.running_auxiliary_power_w or 0.0)
            )
            heat, power = _within_electric_limit(heat, power, compressor_w)
        count[slot] = len(heat)
        if heat:
            points[slot, : len(heat)] = heat
            points[slot, len(heat) :] = heat[-1]
            electric[slot, : len(power)] = power
            electric[slot, len(power) :] = power[-1]

    lower = np.array([band.min_temperature_c for band in comfort_bands])
    upper = np.array([band.max_temperature_c for band in comfort_bands])
    protection = None if protection_min_c is None else np.minimum(float(protection_min_c), lower)
    # Reference state of the end value: holds the target temperature at the mean outdoor
    # temperature of the last 24 hours.
    last = max(slots - int(round(86400.0 / slot_seconds)), 0)
    mean_outdoor = float(np.mean(outdoor[last:]))
    reference_room = (
        comfort_bands[-1].target_temperature_c if reference_room_c is None else reference_room_c
    )
    reference_state, reference_heat = steady_reference(
        dyn_a[-1], dyn_b[-1], dyn_e[-1], mean_outdoor, float(reference_room)
    )
    return ThermalParams(
        name=name,
        initial_temperature_c=initial,
        initial_running=initial_running,
        dynamics_a=dyn_a,
        dynamics_b=dyn_b,
        dynamics_e=dyn_e,
        outdoor_c=outdoor,
        slot_hours=hours,
        comfort_min_c=lower,
        comfort_max_c=upper,
        protection_min_c=protection,
        operating_heat_w=points,
        operating_electric_w=electric,
        operating_count=count,
        blocked=None if blocked is None else np.asarray(blocked, dtype=bool),
        fixed_power_w=fixed,
        running_auxiliary_power_w=electrical.running_auxiliary_power_w or 0.0,
        startup_energy_wh=electrical.startup_energy_wh or 0.0,
        startup_warmup_s=electrical.startup_warmup_seconds or 0.0,
        capacity_wh_per_k=distribution_capacities_j_per_k(building, distribution) / 3600.0,
        reference_temperature_c=reference_state,
        reference_heat_w=reference_heat,
        max_electric_power_w=math.inf if max_electric_power_w is None else max_electric_power_w,
        under_weight=under_weight,
    )
