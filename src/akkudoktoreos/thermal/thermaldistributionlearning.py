"""Causal bounded emitter-state observation from measured room and water history.

Only initial-state uncertainty under declared parameters is bounded here. This
is not a temperature measurement or a bound on sensor/model-parameter error.
"""

import math
from bisect import bisect_right
from datetime import datetime, timezone

from pydantic import Field

from akkudoktoreos.measurement.quality import MeasurementSample
from akkudoktoreos.thermal.thermallearning import latest_measurement, measured_samples
from akkudoktoreos.thermal.thermalmodels import FiniteNumber, ThermalModel, UTCDatetime


class ThermalEmitterEstimate(ThermalModel):
    """Estimated solid/slab temperature and its remaining initial-state interval."""

    temperature_c: FiniteNumber
    lower_temperature_c: FiniteNumber
    upper_temperature_c: FiniteNumber
    remaining_initial_uncertainty_c: FiniteNumber = Field(ge=0)
    history_start: UTCDatetime
    estimated_at: UTCDatetime
    based_on_observed_at: UTCDatetime
    continuous_history_seconds: FiniteNumber = Field(ge=0)
    used_segments: int = Field(ge=0)
    discarded_gap_segments: int = Field(ge=0)
    quality_flags: tuple[str, ...]


def estimate_emitter_temperature(
    room: list[MeasurementSample],
    water: list[MeasurementSample],
    *,
    now: datetime,
    capacity_j_per_k: float,
    water_conductance_w_per_k: float,
    room_conductance_w_per_k: float,
    minimum_temperature_c: float,
    maximum_temperature_c: float,
    maximum_initial_uncertainty_c: float,
    max_age_seconds: int,
    minimum_history_hours: float,
    maximum_steps: int = 20000,
) -> ThermalEmitterEstimate:
    """Integrate Ce*dTe/dt=Gwe*(Tw-Te)+Groom*(Tr-Te), exactly for held inputs.

    The last continuous input-history tail must satisfy every sample lease.
    Gaps reset the entire initial interval rather than compressing time or
    pretending its uncertainty decayed through unknown inputs. No heat-meter
    or pump-flow inference is used. The uncertainty limit is the full interval
    width in kelvin, not a statistical confidence interval.
    """
    numbers = (
        capacity_j_per_k,
        water_conductance_w_per_k,
        room_conductance_w_per_k,
        minimum_temperature_c,
        maximum_temperature_c,
        maximum_initial_uncertainty_c,
        minimum_history_hours,
    )
    if any(isinstance(value, bool) or not math.isfinite(value) for value in numbers):
        raise ValueError("Emitter observer parameters must be finite real numbers")
    if (
        capacity_j_per_k <= 0
        or water_conductance_w_per_k <= 0
        or room_conductance_w_per_k < 0
        or minimum_temperature_c >= maximum_temperature_c
        or maximum_initial_uncertainty_c <= 0
        or minimum_history_hours <= 0
        or isinstance(maximum_steps, bool)
        or not isinstance(maximum_steps, int)
        or maximum_steps <= 0
        or max_age_seconds <= 0
    ):
        raise ValueError(
            "Emitter observation requires positive capacity, leases and history bounds"
        )
    room = measured_samples(room, now)
    water = measured_samples(water, now)
    last_room = latest_measurement(room, now, max_age_seconds)
    last_water = latest_measurement(water, now, max_age_seconds)
    room_times = [row.date_time.timestamp() for row in room]
    water_times = [row.date_time.timestamp() for row in water]
    start = max(room_times[0], water_times[0])
    end = now.timestamp()
    events = {start, end}
    for times in (room_times, water_times):
        events.update(t for t in times if start <= t <= end)
        events.update(t + max_age_seconds for t in times if start <= t + max_age_seconds <= end)
    ordered = sorted(events)
    if len(ordered) - 1 > maximum_steps:
        raise ValueError("Emitter observer history exceeds its declared step budget")

    def held(
        rows: list[MeasurementSample], times: list[float], at: float, until: float
    ) -> float | None:
        index = bisect_right(times, at) - 1
        if index < 0:
            return None
        row = rows[index]
        if (
            row.quality.status != "measured"
            or row.value is None
            or not math.isfinite(row.value)
            or until - times[index] > max_age_seconds + 1e-9
        ):
            return None
        return row.value

    lower, upper = minimum_temperature_c, maximum_temperature_c
    known_since = None
    used, gaps = 0, 0
    conductance = water_conductance_w_per_k + room_conductance_w_per_k
    for at, until in zip(ordered, ordered[1:]):
        tr, tw = held(room, room_times, at, until), held(water, water_times, at, until)
        if tr is None or tw is None:
            lower, upper = minimum_temperature_c, maximum_temperature_c
            known_since, used = None, 0
            gaps += 1
            continue
        if known_since is None:
            known_since = at
        equilibrium = (water_conductance_w_per_k * tw + room_conductance_w_per_k * tr) / conductance
        decay = math.exp(-conductance * (until - at) / capacity_j_per_k)
        lower, upper = (
            equilibrium + (lower - equilibrium) * decay,
            equilibrium + (upper - equilibrium) * decay,
        )
        used += 1
    if known_since is None or end - known_since < minimum_history_hours * 3600:
        raise ValueError("Insufficient continuous fresh room/water history for emitter observation")
    width = upper - lower
    if width > maximum_initial_uncertainty_c + 1e-10:
        raise ValueError("Emitter initial-state uncertainty remains above the configured limit")
    temperature = (upper + lower) / 2
    if not minimum_temperature_c <= temperature <= maximum_temperature_c:
        raise ValueError("Observed emitter state lies outside its calibrated temperature bounds")
    return ThermalEmitterEstimate(
        temperature_c=temperature,
        lower_temperature_c=lower,
        upper_temperature_c=upper,
        remaining_initial_uncertainty_c=width,
        history_start=datetime.fromtimestamp(known_since, timezone.utc),
        estimated_at=now,
        based_on_observed_at=min(last_room.date_time, last_water.date_time),
        continuous_history_seconds=end - known_since,
        used_segments=used,
        discarded_gap_segments=gaps,
        quality_flags=(
            "emitter_state_is_estimated_not_measured",
            "initial_interval_from_declared_temperature_bounds",
            "initial_state_uncertainty_only_not_sensor_or_parameter_error",
            "exact_causal_zero_order_hold_between_source_events",
        ),
    )
