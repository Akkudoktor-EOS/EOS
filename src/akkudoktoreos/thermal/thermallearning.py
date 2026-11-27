"""Causal room-state estimation and bounded RC2 fitting from native EOS samples.

Model fitting is a conditional heat-response test using measured input power.
It does not validate future appliance tracking or prove monetary savings.
"""

import math
from bisect import bisect_right
from datetime import datetime, timezone
from typing import Literal

import numpy as np
from pydantic import Field

from akkudoktoreos.measurement.quality import MeasurementSample
from akkudoktoreos.thermal.thermalmodels import (
    FiniteNumber,
    ThermalBuildingRC2Parameters,
    ThermalModel,
    ThermalRC2Parameters,
    ThermalState,
)

RoomModel = ThermalRC2Parameters | ThermalBuildingRC2Parameters

# Lowest plausible signed thermal model input [W] where the caller knows no device size: heat a
# defrost takes from the room. Larger than the heat of any single-family heat pump (up to about
# 30 kW); a value below it is a sensor error and is rejected like a non-finite one. A caller that
# knows the heat pump passes its size instead (``min_model_input_w``).
DEFAULT_MIN_MODEL_INPUT_W = -30000.0


def measured_samples(samples: list[MeasurementSample], now: datetime) -> list[MeasurementSample]:
    """Keep causal, finite measured replacements; invalidation still ends a hold."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Timezone-aware sampling boundary required")
    latest = {
        sample.date_time.timestamp(): sample
        for sample in samples
        if sample.date_time.timestamp() <= now.timestamp()
    }
    return [latest[key] for key in sorted(latest)]


def latest_measurement(
    samples: list[MeasurementSample], now: datetime, maximum_age_seconds: int
) -> MeasurementSample:
    """Require the newest source event to be a fresh real value, not an older good sample."""
    rows = measured_samples(samples, now)
    if not rows:
        raise ValueError("No measured source samples")
    sample = rows[-1]
    if (
        sample.quality.status != "measured"
        or sample.value is None
        or not math.isfinite(sample.value)
        or now.timestamp() - sample.date_time.timestamp() > maximum_age_seconds
    ):
        raise ValueError(f"Missing, invalid or stale measurement: {sample.key}")
    return sample


def _sample_at_indexed(
    samples: list[MeasurementSample], stamps: list[float], at: datetime, max_age_seconds: int
) -> float | None:
    if max_age_seconds <= 0:
        raise ValueError("Sample hold lease must be positive")
    i = bisect_right(stamps, at.timestamp()) - 1
    if i < 0:
        return None
    row = samples[i]
    if (
        row.quality.status != "measured"
        or row.value is None
        or not math.isfinite(row.value)
        or at.timestamp() - stamps[i] > max_age_seconds
    ):
        return None
    return row.value


def sample_values(
    samples: list[MeasurementSample], times: list[datetime], max_age_seconds: int
) -> list[float | None]:
    """Evaluate a grid with one causal sorted index, preserving missing timestamps."""
    if not times:
        return []
    rows = measured_samples(samples, max(times, key=lambda value: value.timestamp()))
    stamps = [row.date_time.timestamp() for row in rows]
    return [_sample_at_indexed(rows, stamps, at, max_age_seconds) for at in times]


def rc2_transition(model: RoomModel, seconds: float) -> np.ndarray:
    """Exact room/mass transition with input watts and outdoor degrees Celsius."""
    from scipy.linalg import expm

    if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Transition duration must be finite and nonnegative")

    gain = (
        model.electric_gain_kelvin_per_kwh
        if isinstance(model, ThermalRC2Parameters)
        else model.heat_gain_kelvin_per_kwh
    )
    a = np.zeros((4, 4))
    loss, exchange, ratio = (
        model.room_loss_per_hour,
        model.room_mass_exchange_per_hour,
        model.mass_capacity_ratio,
    )
    a[:2, :2] = [[-loss - exchange, exchange], [exchange / ratio, -exchange / ratio]]
    a[0, 2], a[0, 3] = gain / 1000, loss
    return np.asarray(expm(a * seconds / 3600), dtype=float)


def estimate_thermal_state(
    model: RoomModel,
    room: list[MeasurementSample],
    outdoor: list[MeasurementSample],
    power: list[MeasurementSample],
    *,
    now: datetime,
    start: datetime,
    max_age_seconds: int,
    minimum_history_hours: int,
    power_to_model_input: float = 1.0,
    negative_idle_tolerance_w: float = 0.0,
    model_input_power_w: float | None = None,
    model_input_history: list[MeasurementSample] | None = None,
    room_observation_window_seconds: int | None = None,
    min_model_input_w: float = DEFAULT_MIN_MODEL_INPUT_W,
) -> tuple[ThermalState, dict[str, bool | float | str]]:
    """Filter latent mass from past room data, then project fresh inputs to plan start.

    Mass initialization is an assumption. A sufficiently long, largely covered
    history is required and reported; the latent state is never called measured.

    A measured thermal input history (``model_input_history``) is signed: a negative delivered
    heat (defrost) cools the room and enters the projection as it is. Not below
    ``min_model_input_w`` though (the size of the heat pump, or ``DEFAULT_MIN_MODEL_INPUT_W``
    where it is unknown): a lower value, in the history or as ``model_input_power_w``, is a
    sensor error and is rejected like a non-finite one. Non-negative values are not limited.

    ``room_observation_window_seconds`` (default ``None``: unchanged behavior)
    replaces the single newest raw room value by the time-weighted mean of the
    measured room values in the window ending at that value, because stepwise
    or burst-wise reporting sensors jump by several tenths of a kelvin without
    the room really doing so. The projection to the plan start then begins in the
    window middle so the mean does not lag. Freshness gates stay on the newest raw
    value. With fewer than three measured values in the window the raw value is used
    and the diagnostics say so.
    """
    if not 0 <= start.timestamp() - now.timestamp() <= 900:
        raise ValueError("State projection requires a prospective start within fifteen minutes")
    if (
        not math.isfinite(power_to_model_input)
        or power_to_model_input <= 0
        or not math.isfinite(negative_idle_tolerance_w)
        or negative_idle_tolerance_w < 0
    ):
        raise ValueError("Positive finite input gain and nonnegative idle tolerance required")
    if not math.isfinite(min_model_input_w) or min_model_input_w > 0:
        raise ValueError("The lowest plausible model input must be finite and not positive")
    if model_input_power_w is not None and not math.isfinite(model_input_power_w):
        raise ValueError("Model input override must be finite")
    if model_input_power_w is not None and model_input_power_w < min_model_input_w:
        raise ValueError(
            f"Model input override {model_input_power_w:.0f} W is below the plausible minimum "
            f"{min_model_input_w:.0f} W (sensor error)"
        )
    room = measured_samples(room, now)
    source = latest_measurement(room, now, max_age_seconds)
    outside = latest_measurement(outdoor, now, max_age_seconds)
    electric = latest_measurement(power, now, max_age_seconds)
    if electric.value is not None and electric.value < -negative_idle_tolerance_w:
        raise ValueError("Negative source power exceeds the declared idle-noise tolerance")
    first = next(
        (row for row in room if row.quality.status == "measured" and row.value is not None), None
    )
    if first is None or first.value is None:
        raise ValueError("Insufficient room history for latent mass initialization")
    mass = float(first.value)
    segment_start = first.date_time.timestamp()
    restarted_after_gap = False
    step = math.exp(-model.room_mass_exchange_per_hour / model.mass_capacity_ratio / 60)
    covered, count, last_good = 0, 0, first.date_time.timestamp()
    long_gap_open = False
    cursor = float(math.ceil(first.date_time.timestamp() / 60) * 60)
    room_stamps = [row.date_time.timestamp() for row in room]
    smoothing: dict[str, bool | float | str] = {}
    smoothed_room: float | None = None
    center = source.date_time.timestamp()
    if room_observation_window_seconds is not None:
        window = float(room_observation_window_seconds)
        if (
            isinstance(room_observation_window_seconds, bool)
            or not math.isfinite(window)
            or window <= 0
        ):
            raise ValueError("Room observation window must be positive")
        smoothing = {
            "room_state_window_seconds": window,
            "room_state_raw_c": float(source.value) if source.value is not None else math.nan,
            "room_state_window_values": 0.0,
            "room_state_smoothing_used": False,
        }
        t_end = source.date_time.timestamp()
        in_window = [
            row
            for row in room
            if t_end - window < row.date_time.timestamp() <= t_end
            and row.quality.status == "measured"
            and row.value is not None
            and math.isfinite(row.value)
        ]
        smoothing["room_state_window_values"] = float(len(in_window))
        if len(in_window) < 3:
            smoothing["room_state_smoothing_fallback"] = (
                "fewer than three measured values in window"
            )
        else:
            # Time-weighted mean of the held measured value over the window; the
            # value before the first in-window sample is the last real sample.
            grid = [t_end - window + 60.0 * (i + 1) for i in range(int(window // 60))]
            held = [
                _sample_at_indexed(
                    room, room_stamps, datetime.fromtimestamp(at, timezone.utc), max_age_seconds
                )
                for at in grid
            ]
            held_values = [value for value in held if value is not None]
            if len(held_values) >= max(3, int(0.8 * len(grid))):
                smoothed_room = float(sum(held_values) / len(held_values))
                center = t_end - window / 2
                smoothing["room_state_smoothing_used"] = True
            else:
                smoothing["room_state_smoothing_fallback"] = "window coverage below eighty percent"
    mass_at_center: float | None = None
    while cursor < source.date_time.timestamp():
        if smoothed_room is not None and mass_at_center is None and cursor >= center:
            mass_at_center = mass
        value = _sample_at_indexed(
            room, room_stamps, datetime.fromtimestamp(cursor, timezone.utc), max_age_seconds
        )
        if value is not None:
            if long_gap_open:
                # Never propagate a latent mass estimate across a long outage.
                # A new contiguous suffix must independently meet the history gate.
                mass = float(value)
                segment_start = cursor
                covered = count = 0
                restarted_after_gap = True
                long_gap_open = False
            else:
                mass = step * mass + (1 - step) * value
            count += 1
            covered += 1
            last_good = cursor
        elif cursor - last_good > 1800:
            long_gap_open = True
        else:
            count += 1
        cursor += 60
    if long_gap_open or source.date_time.timestamp() - segment_start < minimum_history_hours * 3600:
        raise ValueError("Insufficient contiguous room history after last long gap")
    if not count or covered / count < 0.9:
        raise ValueError("Room-history coverage below ninety percent")
    if source.value is None or outside.value is None or electric.value is None:
        raise ValueError("Missing state inputs")
    if smoothed_room is not None:
        # The earlier start needs measured power and outdoor values from there on.
        probe_power = measured_samples(power, now)
        probe_outdoor = measured_samples(outdoor, now)
        probe_power_t = [row.date_time.timestamp() for row in probe_power]
        probe_outdoor_t = [row.date_time.timestamp() for row in probe_outdoor]
        probe = center
        while probe < source.date_time.timestamp():
            at = datetime.fromtimestamp(min(probe, now.timestamp()), timezone.utc)
            if (
                _sample_at_indexed(probe_power, probe_power_t, at, max_age_seconds) is None
                or _sample_at_indexed(probe_outdoor, probe_outdoor_t, at, max_age_seconds) is None
            ):
                smoothed_room = None
                center = source.date_time.timestamp()
                smoothing["room_state_smoothing_used"] = False
                smoothing["room_state_smoothing_fallback"] = "projection inputs missing in window"
                break
            probe += 60.0
    if smoothed_room is not None:
        smoothing["room_state_smoothed_c"] = smoothed_room
        smoothing["room_state_projection_start_offset_seconds"] = (
            source.date_time.timestamp() - center
        )
        x = np.array([smoothed_room, mass if mass_at_center is None else mass_at_center])
    else:
        x = np.array([source.value, mass])
    # Use observed inputs up to ``now``. Only the still unobserved lead to the
    # next planning boundary may assume a held input.
    power_rows = measured_samples(power, now)
    outdoor_rows = measured_samples(outdoor, now)
    power_stamps = [row.date_time.timestamp() for row in power_rows]
    outdoor_stamps = [row.date_time.timestamp() for row in outdoor_rows]
    heat_rows = (
        measured_samples(model_input_history, now) if model_input_history is not None else None
    )
    heat_stamps = [row.date_time.timestamp() for row in heat_rows] if heat_rows else []
    cursor = center
    state = x
    measured_projection_seconds = 0.0
    while cursor < start.timestamp():
        next_cursor = min(cursor + 60.0, start.timestamp())
        observed = min(cursor, now.timestamp())
        at = datetime.fromtimestamp(observed, timezone.utc)
        watts = _sample_at_indexed(power_rows, power_stamps, at, max_age_seconds)
        outside_c = _sample_at_indexed(outdoor_rows, outdoor_stamps, at, max_age_seconds)
        if watts is None or outside_c is None:
            raise ValueError("Projection input history is stale")
        if watts < -negative_idle_tolerance_w:
            raise ValueError("Negative source power exceeds the declared idle-noise tolerance")
        transition = rc2_transition(model, next_cursor - cursor)
        model_watts = (
            max(0.0, watts) * power_to_model_input
            if model_input_power_w is None
            else model_input_power_w
        )
        if heat_rows is not None:
            heat = _sample_at_indexed(heat_rows, heat_stamps, at, max_age_seconds)
            if heat is None or not math.isfinite(heat):
                raise ValueError("Projection thermal input history is stale or not finite")
            if heat < min_model_input_w:
                raise ValueError(
                    f"Projection thermal input history has {heat:.0f} W, below the plausible "
                    f"minimum {min_model_input_w:.0f} W (sensor error)"
                )
            # A negative delivered heat is real: the heat pump takes heat from the room (or
            # the air distribution) while it defrosts. The model input is signed.
            model_watts = heat
        state = (
            transition[:2, :2] @ state
            + transition[:2, 2] * model_watts
            + transition[:2, 3] * outside_c
        )
        if model_input_power_w is None or heat_rows is not None:
            measured_projection_seconds += max(0.0, min(next_cursor, now.timestamp()) - cursor)
        cursor = next_cursor
    return ThermalState(
        estimated_at=start,
        based_on_observed_at=source.date_time,
        room_temperature_c=float(state[0]),
        mass_temperature_c=float(state[1]),
    ), {
        "mass_is_measured": False,
        "room_history_coverage": covered / count,
        "mass_initialization": "First contiguous room temperature; causal room filter",
        "mass_history_restarted_after_gap": restarted_after_gap,
        "mass_history_hours": (source.date_time.timestamp() - segment_start) / 3600,
        "projection_seconds": start.timestamp() - source.date_time.timestamp(),
        "projection_input_hold_assumed": start > now,
        "projection_measured_input_seconds": measured_projection_seconds,
        "projection_thermal_input_estimate_held": model_input_power_w is not None
        and heat_rows is None,
        "projection_thermal_history_used": heat_rows is not None,
        "projection_unobserved_seconds": max(0.0, start.timestamp() - now.timestamp()),
        "negative_power_floored_to_zero": electric.value < 0,
        "model_input_power_override_used": model_input_power_w is not None,
        "room_observation_age_seconds": now.timestamp() - source.date_time.timestamp(),
        "power_observation_age_seconds": now.timestamp() - electric.date_time.timestamp(),
        "outdoor_observation_age_seconds": now.timestamp() - outside.date_time.timestamp(),
        **smoothing,
    }


class ThermalFitResult(ThermalModel):
    """Conditional parameter estimate and held-out known-input response error."""

    model: ThermalBuildingRC2Parameters | ThermalRC2Parameters
    training_intervals: int
    validation_origins: int
    heldout_known_input_mae_c: FiniteNumber = Field(ge=0)
    fitted_until: datetime
    input_basis: Literal["electric", "thermal"]
    physical_savings_proven: bool = False


def fit_room_model(
    times: list[datetime],
    room_c: list[float | None],
    outdoor_c: list[float | None],
    input_w: list[float | None],
    *,
    input_basis: Literal["electric", "thermal"] = "electric",
) -> ThermalFitResult:
    """Fit bounded positive RC2 rates using 30-minute windows and held-out final third.

    Inputs are a preserved five-minute grid. Missing intervals are not squeezed
    together. Validation uses future *measured* power, an explicitly known-input
    component test, not an ex-ante controller/energy forecast.
    """
    from scipy.optimize import least_squares

    n = len(times)
    if input_basis not in ("electric", "thermal"):
        raise ValueError("Unknown room model input basis")
    if any(value.tzinfo is None or value.utcoffset() is None for value in times):
        raise ValueError("Timezone-aware fitting times required")
    if n < 288 or not (len(room_c) == len(outdoor_c) == len(input_w) == n):
        raise ValueError("Room fitting requires at least 24 hours of aligned five-minute data")
    if any(b.timestamp() - a.timestamp() != 300 for a, b in zip(times, times[1:])):
        raise ValueError("Room fitting requires a preserved five-minute time grid")
    room = np.array([np.nan if value is None else value for value in room_c])
    outside = np.array([np.nan if value is None else value for value in outdoor_c])
    power = np.array([np.nan if value is None else value for value in input_w])
    valid = np.isfinite(room) & np.isfinite(outside) & np.isfinite(power) & (power >= 0)
    split = n * 2 // 3
    origins = [i for i in range(0, n - 6, 6) if valid[i : i + 7].all()]
    train, test = [i for i in origins if i + 6 < split], [i for i in origins if i >= split]
    if len(train) < 20 or len(test) < 8 or np.nanstd(power[:split]) < 50:
        raise ValueError("Insufficient covered heating variation or held-out calibration windows")

    def model_from(theta: np.ndarray) -> RoomModel:
        loss, exchange, ratio, gain = np.exp(theta)
        common = dict(
            room_loss_per_hour=float(loss),
            room_mass_exchange_per_hour=float(exchange),
            mass_capacity_ratio=float(ratio),
        )
        return (
            ThermalRC2Parameters(**common, electric_gain_kelvin_per_kwh=float(gain))
            if input_basis == "electric"
            else ThermalBuildingRC2Parameters(**common, heat_gain_kelvin_per_kwh=float(gain))
        )

    def errors(theta: np.ndarray, selected: list[int]) -> np.ndarray:
        model = model_from(theta)
        transition = rc2_transition(model, 300)
        mass = np.empty(n)
        mass[0] = room[np.flatnonzero(np.isfinite(room))[0]]
        decay = math.exp(-model.room_mass_exchange_per_hour / model.mass_capacity_ratio / 12)
        for i in range(1, n):
            mass[i] = decay * mass[i - 1] + (1 - decay) * (
                room[i - 1] if np.isfinite(room[i - 1]) else mass[i - 1]
            )
        output = []
        for origin in selected:
            state = np.array([room[origin], mass[origin]])
            for k in range(origin, origin + 6):
                state = (
                    transition[:2, :2] @ state
                    + transition[:2, 2] * power[k]
                    + transition[:2, 3] * outside[k]
                )
            output.append(state[0] - room[origin + 6])
        return np.asarray(output)

    lower, upper = np.log([0.002, 0.005, 0.2, 0.02]), np.log([1.0, 5.0, 40.0, 30.0])

    def training_errors(theta: np.ndarray) -> np.ndarray:
        return errors(theta, train)

    fit = least_squares(
        training_errors,
        np.log([0.1, 0.3, 4.0, 1.0]),
        bounds=(lower, upper),
        loss="soft_l1",
        f_scale=0.2,
        max_nfev=120,
    )
    score = float(np.mean(np.abs(errors(fit.x, test))))
    if not fit.success or not math.isfinite(score) or score > 0.75:
        raise ValueError("Known-input held-out room model does not meet the 0.75 K admission bound")
    return ThermalFitResult(
        model=model_from(fit.x),
        training_intervals=len(train) * 6,
        validation_origins=len(test),
        heldout_known_input_mae_c=score,
        fitted_until=times[split],
        input_basis=input_basis,
    )


def annual_heat_loss_prior(
    delivered_space_heat_kwh: float,
    outdoor_temperatures_c: list[float],
    *,
    heating_limit_c: float,
    interval_seconds: int = 3600,
) -> float:
    """Return W/K from declared space heat and a complete representative weather year.

    DHW and fuel efficiency must already be separated by the caller. This is a
    weather-normalized sizing prior, not thermal-capacity identification.
    """
    if (
        not math.isfinite(delivered_space_heat_kwh)
        or not math.isfinite(heating_limit_c)
        or delivered_space_heat_kwh <= 0
        or interval_seconds <= 0
        or len(outdoor_temperatures_c) * interval_seconds not in (365 * 86400, 366 * 86400)
        or not all(math.isfinite(value) for value in outdoor_temperatures_c)
    ):
        raise ValueError("A positive space-heat amount and one complete weather year are required")
    degree_hours = sum(
        max(0.0, heating_limit_c - value) * interval_seconds / 3600
        for value in outdoor_temperatures_c
    )
    if degree_hours <= 0:
        raise ValueError("Weather has no heating degree-hours")
    return delivered_space_heat_kwh * 1000 / degree_hours
