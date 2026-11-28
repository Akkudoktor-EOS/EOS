"""Quality-aware energy conversion of raw measurement samples.

No extrapolation beyond the last sample. Intervals use elapsed UTC seconds.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import TYPE_CHECKING, Iterable, Literal

from akkudoktoreos.measurement.quality import SampleQuality
from akkudoktoreos.utils.datetimeutil import DateTime, to_duration

if TYPE_CHECKING:
    from akkudoktoreos.measurement.measurement import MeasurementChannelSettings


@dataclass(frozen=True)
class EnergyInterval:
    """Energy and actual temporal support; missing energy is never zero-filled."""

    start: datetime
    end: datetime
    energy_wh: float | None
    observed_energy_wh: float | None
    coverage_seconds: float
    coverage_status: Literal["complete", "partial", "missing", "invalid"]
    methods: tuple[str, ...]
    flags: tuple[str, ...]
    # Retain the support for subsequent multi-channel balance intersection.
    coverage_ranges: tuple[tuple[datetime, datetime], ...]


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Explicit timezone required.")
    return value.timestamp()


def _date(value: float) -> datetime:
    return datetime.fromtimestamp(value, timezone.utc)


def valid_reading(value: object, quality: SampleQuality | None = None) -> float | None:
    """Value of a raw reading that counts as a valid observation.

    A reading counts when it is a finite number (not a bool) whose sample quality is
    neither invalid nor unavailable. The energy conversion, the meter period scans
    and the meter resolution detection share this rule.

    Args:
        value: Raw stored reading.
        quality: Sample quality of the reading, None for the default (measured).

    Returns:
        The reading as float, or None when it does not count.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return None
    if quality is not None and quality.status in ("invalid", "unavailable"):
        return None
    return float(value)


def hourly_energy_to_meter_readings(
    hourly_energy_kwh: Iterable[tuple[DateTime, float]],
) -> tuple[list[DateTime], list[float]]:
    """Cumulative meter readings for energy values of whole hours.

    Each input is the energy [kWh] of the hour that starts at its time, from `time` to
    `time + 1 hour`. A meter reading is the energy used up to its time, so the reading
    that includes an hour belongs to the end of that hour: the first hour gets the
    start reading 0.0 at its own time, every hour then the running sum at `time + 1
    hour`. Reading the meter between two readings (`Measurement.load_total_kwh`) gives
    back exactly the energy that was passed in for that hour.

    Hours are absolute: `time + 1 hour` is one elapsed hour, also on the days of the
    daylight saving time change. The input order does not matter, the hours are sorted
    by time. An hour that is given twice keeps its last value. A missing hour gets no
    reading; the following hour then spans the gap, and the meter conversion spreads its
    energy evenly over the gap or leaves it unknown, as it does for any meter gap.

    Args:
        hourly_energy_kwh: Pairs of hour start and energy [kWh] of that hour.

    Returns:
        The reading times, oldest first, and the cumulative readings [kWh]. Both lists are
        empty for an empty input.
    """
    # Key by the instant (epoch seconds), not by the DateTime: two DateTimes of the same
    # timezone compare by their wall clock, which merges the two 02:00 of the day the
    # daylight saving time ends.
    hours: dict[float, tuple[DateTime, float]] = {}
    for hour_start, energy in hourly_energy_kwh:
        hours[hour_start.timestamp()] = (hour_start, energy)

    one_hour = to_duration("1 hour")
    reading_dates: list[DateTime] = []
    reading_values: list[float] = []
    reading_kwh = 0.0
    for index, instant in enumerate(sorted(hours)):
        hour_start, energy = hours[instant]
        if index == 0:
            # Start reading before the first hour.
            reading_dates.append(hour_start)
            reading_values.append(0.0)
        reading_kwh += energy
        reading_dates.append(hour_start + one_hour)
        reading_values.append(reading_kwh)
    return reading_dates, reading_values


def energy_intervals(
    samples: Iterable[tuple[datetime, float | None]],
    channel: "MeasurementChannelSettings",
    start: datetime,
    end: datetime,
    interval_seconds: int = 900,
    quality: dict[float, SampleQuality] | None = None,
) -> list[EnergyInterval]:
    """Convert finite samples, preserving gaps, resets and partial coverage.

    Hold applies only between consecutive observations within max_gap_seconds.
    Null/invalid observations break continuity. Meter resets invalidate that segment.
    Fixed interval energy is uniformly allocated when a target cuts its source interval.
    """
    if channel.quantity not in ("power", "cumulative_energy", "interval_energy"):
        raise ValueError("Energy conversion requires a power or energy channel.")
    left, right = _timestamp(start), _timestamp(end)
    if right <= left or type(interval_seconds) is not int or interval_seconds <= 0:
        raise ValueError("Require end > start and positive integer interval_seconds.")
    points = sorted(((_timestamp(t), v) for t, v in samples), key=lambda point: point[0])
    if any(a[0] == b[0] for a, b in zip(points, points[1:])):
        raise ValueError("Duplicate sample timestamps must be resolved before conversion.")
    scale = 1000 if channel.unit in ("kW", "kWh") else 1
    quality = quality or {}

    def sample_quality(time: float) -> SampleQuality:
        return quality.get(time, SampleQuality())

    def number(value: float | None, sample: SampleQuality) -> float | None:
        reading = valid_reading(value, sample)
        return None if reading is None else reading * scale

    # Segment: start, end, start/end power in W, method, error flag.
    segments = []
    if channel.quantity == "interval_energy":
        duration = channel.interval_seconds
        if duration is None:
            raise ValueError("Interval duration is required.")
        previous_end = None
        for time, raw in points:
            a = time if channel.timestamp_reference == "start" else time - duration
            b = a + duration
            if previous_end is not None and a < previous_end:
                raise ValueError("Overlapping source energy intervals.")
            previous_end = b
            value = number(raw, sample_quality(time))
            power = value * 3600 / duration if value is not None else None
            segments.append(
                (a, b, power, power, "interval_energy", "invalid_sample" if power is None else None)
            )
    else:
        for (a, raw_a), (b, raw_b) in zip(points, points[1:]):
            qa, qb = sample_quality(a), sample_quality(b)
            va, vb = number(raw_a, qa), number(raw_b, qb)
            flag = None
            if channel.max_gap_seconds is not None and b - a > channel.max_gap_seconds:
                flag = "gap_too_large"
            elif va is None or (
                vb is None
                and (
                    channel.quantity == "cumulative_energy"
                    or channel.integration_method == "linear"
                )
            ):
                flag = "invalid_sample"
            if channel.quantity == "cumulative_energy":
                method = "meter_difference"
                if va is not None and vb is not None and vb < va:
                    flag = "meter_reset"
                if qb.reset or qa.generation != qb.generation:
                    flag = "meter_reset"
                pa = pb = (
                    (vb - va) * 3600 / (b - a)
                    if flag is None and va is not None and vb is not None
                    else None
                )
            else:
                method = "integrated_power"
                pa = va
                pb = vb if channel.integration_method == "linear" else va
            segments.append((a, b, pa, pb, method, flag))

    result = []
    slot = left
    segment_index = 0
    while slot < right:
        stop = min(slot + interval_seconds, right)
        total, coverage = 0.0, 0.0
        methods: set[str] = set()
        flags: set[str] = set()
        ranges: list[tuple[datetime, datetime]] = []
        while segment_index < len(segments) and segments[segment_index][1] <= slot:
            segment_index += 1
        for index in range(segment_index, len(segments)):
            a, b, pa, pb, method, flag = segments[index]
            if a >= stop:
                break
            lo, hi = max(a, slot), min(b, stop)
            if hi <= lo:
                continue
            # Quality follows the endpoints used by the integration rule.
            source_time = a
            if channel.quantity == "interval_energy" and channel.timestamp_reference == "end":
                source_time = b
            statuses = {sample_quality(source_time).status}
            if channel.quantity == "cumulative_energy" or channel.integration_method == "linear":
                statuses.add(sample_quality(b).status)
            flags.update(s for s in statuses if s != "measured")
            if flag is not None:
                flags.add(flag)
                continue
            if pa is None or pb is None:
                raise ValueError("Valid segment requires finite endpoint powers.")
            p_lo = pa + (pb - pa) * (lo - a) / (b - a)
            p_hi = pa + (pb - pa) * (hi - a) / (b - a)
            total += (p_lo + p_hi) / 2 * (hi - lo) / 3600
            coverage += hi - lo
            if ranges and ranges[-1][1] == _date(lo):
                ranges[-1] = (ranges[-1][0], _date(hi))
            else:
                ranges.append((_date(lo), _date(hi)))
            methods.add(method)
            if method in ("meter_difference", "interval_energy") and (lo != a or hi != b):
                methods.add("allocated_energy")
        complete = abs(coverage - (stop - slot)) < 1e-6
        status: Literal["complete", "partial", "missing", "invalid"] = (
            "complete" if complete else "partial" if coverage else "invalid" if flags else "missing"
        )
        result.append(
            EnergyInterval(
                _date(slot),
                _date(stop),
                total if complete else None,
                total if coverage else None,
                coverage,
                status,
                tuple(sorted(methods)),
                tuple(sorted(flags)),
                tuple(ranges),
            )
        )
        slot = stop
    return result
