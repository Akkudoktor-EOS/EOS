"""Runtime comfort requirements from presence forecasts, never a fixed calendar."""

from datetime import datetime, timedelta

from akkudoktoreos.prediction.presence import PresenceEstimate
from akkudoktoreos.thermal.thermalmodels import TemperatureInterval, ThermalComfortBand


def apply_comfort_windows(
    starts: list[datetime],
    baseline: tuple[ThermalComfortBand, ...],
    windows: tuple[TemperatureInterval, ...],
) -> tuple[ThermalComfortBand, ...]:
    """Apply explicit runtime requirements without inventing presence probabilities.

    Windows may come from a user routine, a declared return or a presence planner.
    They describe required room conditions, not the heating start time. The
    optimizer must find a physically feasible warm-up before each transition.
    Uncovered slots retain the presence-derived baseline. Runtime observation
    and manual priority must still be enforced independently by the controller.
    """
    if len(starts) != len(baseline):
        raise ValueError("Comfort baseline and horizon must have the same length")
    if any(a.end_datetime > b.start_datetime for a, b in zip(windows, windows[1:])):
        raise ValueError("Comfort windows must be ordered and non-overlapping")
    if any(
        endpoint.timestamp() % 900 != 0
        for window in windows
        for endpoint in (window.start_datetime, window.end_datetime)
    ):
        raise ValueError("Comfort windows must align with quarter-hour boundaries")
    output = []
    for start, default in zip(starts, baseline):
        window = next((w for w in windows if w.start_datetime <= start < w.end_datetime), None)
        output.append(
            default
            if window is None
            else ThermalComfortBand(
                min_temperature_c=window.min_temperature_c,
                target_temperature_c=window.target_temperature_c,
                max_temperature_c=window.max_temperature_c,
            )
        )
    return tuple(output)


def comfort_bands_from_presence(
    estimates: list[PresenceEstimate],
    occupied: ThermalComfortBand,
    unoccupied: ThermalComfortBand | None = None,
    *,
    preheat_seconds: int = 0,
    allow_warm_coasting: bool = False,
    reported_absence: ThermalComfortBand | None = None,
) -> tuple[ThermalComfortBand, ...]:
    """Reserve a model-derived warm-up lead before likely or uncertain occupancy.

    Temperature preferences are separate from timing. Unknown presence requires
    comfort. The caller supplies a conservative heating lead derived from the
    building/source model; the presence provider never guesses heating physics.
    Explicit warm coasting keeps the absence minimum and target, but permits
    occupied-range warmth during absence. It does not require active cooling
    immediately after departure or at an initially absent, still-warm origin.
    Without that opt-in, absence upper limits remain hard. A supplied heating
    lead explicitly opens a warm-up opportunity, preserving the absence minimum.
    Occupied/unknown-presence bands are always returned exactly as supplied.
    This converts requirements only: an optimizer still has to prove that they
    are reachable, including recovery from an initially cold room.

    ``reported_absence`` (default None = unchanged behaviour) is the band for slots whose
    estimate comes from a reported absence. It replaces the configured absence band for those
    slots, including their warm-up band before a reported return, and it works without a
    configured ``unoccupied`` band, in which case learned reductions keep the occupied band.
    """
    if type(preheat_seconds) is not int or preheat_seconds < 0:
        raise ValueError("Heating lead must be a nonnegative integer number of seconds")
    if type(allow_warm_coasting) is not bool:
        raise ValueError("Warm coasting requires an explicit boolean declaration")
    if len(estimates) > 288 or any(
        (b.date_time - a.date_time).total_seconds() != 900 for a, b in zip(estimates, estimates[1:])
    ):
        raise ValueError("Comfort conversion requires at most 288 contiguous quarter-hours")
    if unoccupied is None and reported_absence is None:
        return tuple(occupied for _ in estimates)
    absence_band = (
        None
        if unoccupied is None
        else ThermalComfortBand(
            min_temperature_c=unoccupied.min_temperature_c,
            target_temperature_c=unoccupied.target_temperature_c,
            max_temperature_c=max(unoccupied.max_temperature_c, occupied.max_temperature_c),
        )
        if allow_warm_coasting
        else unoccupied
    )
    output = []
    for i, estimate in enumerate(estimates):
        end = estimate.date_time + timedelta(seconds=preheat_seconds)
        if estimate.require_comfort or estimate.source == "unknown":
            output.append(occupied)
            continue
        reported = reported_absence is not None and estimate.source == "reported_absence"
        slot_unoccupied = reported_absence if reported else unoccupied
        slot_absence = reported_absence if reported else absence_band
        if slot_unoccupied is None or slot_absence is None:
            output.append(occupied)  # no absence band configured for learned reductions
            continue
        required_later = any(
            later.require_comfort or later.source == "unknown"
            for later in estimates[i:]
            if later.date_time <= end
        )
        output.append(
            ThermalComfortBand(
                min_temperature_c=slot_unoccupied.min_temperature_c,
                target_temperature_c=occupied.target_temperature_c,
                max_temperature_c=max(
                    slot_unoccupied.max_temperature_c, occupied.max_temperature_c
                ),
            )
            if required_later
            else slot_absence
        )
    return tuple(output)


# Default protection minimum while a reported absence runs and the zone configures neither an
# absence band nor an absence minimum: damp/frost protection for a heated living space.
DEFAULT_ABSENCE_MINIMUM_C = 14.0


def reported_absence_band(
    occupied: ThermalComfortBand,
    unoccupied: ThermalComfortBand | None,
    minimum_temperature_c: float | None,
) -> ThermalComfortBand:
    """Band that holds while a reported absence runs; its minimum always applies.

    Order of the minimum: explicit ``minimum_temperature_c``, else the zone's configured
    absence band, else the 14 degree protection default. Never above the occupied minimum.
    """
    if unoccupied is not None and minimum_temperature_c is None:
        return unoccupied
    minimum = min(
        occupied.min_temperature_c,
        minimum_temperature_c if minimum_temperature_c is not None else DEFAULT_ABSENCE_MINIMUM_C,
    )
    if unoccupied is not None:
        return ThermalComfortBand(
            min_temperature_c=minimum,
            target_temperature_c=max(minimum, unoccupied.target_temperature_c),
            max_temperature_c=max(minimum, unoccupied.max_temperature_c),
        )
    return ThermalComfortBand(
        min_temperature_c=minimum,
        target_temperature_c=minimum + 0.3,
        max_temperature_c=max(minimum + 0.3, occupied.target_temperature_c),
    )
