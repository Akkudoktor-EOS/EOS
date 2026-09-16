"""Quality-aware energy conversion of raw measurement samples.

No extrapolation beyond the last sample. Intervals use elapsed UTC seconds.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import TYPE_CHECKING, Iterable, Literal

from akkudoktoreos.measurement.quality import SampleQuality

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

    def number(value: float | None) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            return None
        return float(value) * scale

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
            value = number(raw)
            if sample_quality(time).status in ("invalid", "unavailable"):
                value = None
            power = value * 3600 / duration if value is not None else None
            segments.append(
                (a, b, power, power, "interval_energy", "invalid_sample" if power is None else None)
            )
    else:
        for (a, raw_a), (b, raw_b) in zip(points, points[1:]):
            va, vb = number(raw_a), number(raw_b)
            qa, qb = sample_quality(a), sample_quality(b)
            if qa.status in ("invalid", "unavailable"):
                va = None
            if qb.status in ("invalid", "unavailable"):
                vb = None
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
