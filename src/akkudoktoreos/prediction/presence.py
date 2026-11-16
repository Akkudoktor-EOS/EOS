"""Historical presence forecasts with causal sensor correction on EOS data structures.

Forecasts express occupancy probability, not a guarantee of a person's return.
Missing or invalid samples never count as absence. This provider does not control
heating or persist manual intent in the installation configuration.
"""

import math
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, ConfigDict, Field, PrivateAttr, model_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.measurement.quality import MeasurementSample
from akkudoktoreos.prediction.predictionabc import PredictionProvider, PredictionRecord
from akkudoktoreos.utils.datetimeutil import to_datetime


class PresenceZoneSettings(SettingsBaseModel):
    """Bind any occupancy area to an existing typed EOS presence measurement."""

    model_config = ConfigDict(extra="forbid")
    measurement_key: str = Field(min_length=1)


class PresenceAbsence(SettingsBaseModel):
    """A reported absence (away from ... until ...); it beats the learned pattern.

    ``start``/``end`` are ISO date-times. A value with an offset ("+01:00", "Z") is exact; one
    without is read in the installation's time zone. ``end=None`` means "until revoked": the
    plan then assumes absence for its whole horizon. Revoke or shorten a running absence by
    setting ``end`` to the return time and keep the entry: reported absences are also
    excluded from learning for as long as they lie inside the history window.
    ``zone_ids`` limits the entry to some presence areas (empty = all areas).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    start: datetime = Field(examples=["2026-12-24T08:00:00+01:00"])
    end: datetime | None = Field(default=None, examples=["2026-12-28T18:00:00+01:00"])
    zone_ids: list[str] = Field(default_factory=list, max_length=16)
    label: str | None = Field(default=None, max_length=80, examples=["Holiday"])

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        """Require a positive period with consistently aware or naive bounds."""
        if self.end is not None:
            if (self.start.tzinfo is None) != (self.end.tzinfo is None):
                raise ValueError("Absence start and end must both carry an offset or both not")
            if self.end <= self.start:
                raise ValueError("Absence end must be after its start")
        return self

    def resolve(self, timezone_name: str) -> tuple[float, float | None]:
        """Return (start, end) as epoch seconds; naive values use the given time zone."""
        tz = ZoneInfo(timezone_name)

        def stamp(value: datetime) -> float:
            return (value if value.tzinfo is not None else value.replace(tzinfo=tz)).timestamp()

        return stamp(self.start), None if self.end is None else stamp(self.end)


class PresenceCommonSettings(SettingsBaseModel):
    """Optional history provider; no fixed occupancy or comfort calendar."""

    model_config = ConfigDict(extra="forbid")
    provider: Literal["PresenceHistory"] | None = Field(default=None, examples=[None])
    zones: dict[str, PresenceZoneSettings] = Field(default_factory=dict)
    history_days: int = Field(default=56, ge=7, le=89, strict=True)
    minimum_support_days: int = Field(
        default=7,
        ge=1,
        le=89,
        strict=True,
        description="Minimum history for comfort reduction; provisional probabilities appear earlier.",
    )
    allow_provisional_setback: bool = False
    sample_max_age_seconds: int = Field(default=90, gt=0, le=3600, strict=True)
    absence_delay_seconds: int = Field(default=1800, gt=0, strict=True)
    local_correction_seconds: int = Field(default=3600, ge=900, le=14400, strict=True)
    recency_half_life_days: float = Field(default=21, gt=0, allow_inf_nan=False)
    minimum_bucket_coverage: float = Field(default=0.8, gt=0, le=1, allow_inf_nan=False)
    absence_probability_threshold: float = Field(default=0.2, gt=0, lt=0.5, allow_inf_nan=False)
    absences: list[PresenceAbsence] = Field(
        default_factory=list,
        max_length=32,
        description="Reported absences (from/until); they beat the learned presence pattern.",
    )
    absence_return_hold_minutes: int = Field(
        default=120,
        ge=0,
        le=720,
        strict=True,
        description="After a reported absence ends, comfort is required for this long.",
    )

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        """Keep dynamic prediction keys deterministic and require a configured source."""
        if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", key) is None for key in self.zones):
            raise ValueError("Presence area IDs require letters, digits, underscores or hyphens")
        if any(
            zone.measurement_key != zone.measurement_key.strip() for zone in self.zones.values()
        ):
            raise ValueError("Presence measurement keys must not contain surrounding whitespace")
        if self.provider is not None and not self.zones:
            raise ValueError("An enabled presence provider requires measurement bindings")
        return self


class PresenceCorrection(PydanticBaseModel):
    """Runtime manual correction with a bounded lease, never a configuration calendar."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    zone_id: str = Field(min_length=1)
    kind: Literal["comfort", "away"]
    issued_at: AwareDatetime
    valid_until: AwareDatetime

    @model_validator(mode="after")
    def validate_lease(self) -> Self:
        """Reject empty and unbounded manual corrections."""
        duration = (self.valid_until - self.issued_at).total_seconds()
        if not 0 < duration <= 7 * 86400:
            raise ValueError("Presence correction lease must be positive and at most seven days")
        return self


class PresenceEstimate(PydanticBaseModel):
    """One interval with population evidence and a separate known-weekday guard.

    The historical point probability describes the selected population. When
    pooling is necessary, a known occupied weekday can raise the upper bound
    independently; its observations are not counted again as extra pool votes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    date_time: AwareDatetime
    probability: float | None = Field(default=None, ge=0, le=1)
    historical_probability: float | None = Field(default=None, ge=0, le=1)
    probability_upper: float | None = Field(default=None, ge=0, le=1)
    support_days: int = Field(default=0, ge=0)
    support_population: Literal["unknown", "weekday", "workday", "weekend", "all_days"] = "unknown"
    history_is_provisional: bool = False
    effective_support_days: float = Field(default=0, ge=0)
    weekday_support_days: int = Field(default=0, ge=0)
    weekday_effective_support_days: float = Field(default=0, ge=0)
    weekday_probability: float | None = Field(default=None, ge=0, le=1)
    weekday_probability_upper: float | None = Field(default=None, ge=0, le=1)
    weekday_presence_guard: bool = False
    require_comfort: bool = True
    source: Literal[
        "unknown",
        "history",
        "sensor_present",
        "sensor_absent",
        "manual_comfort",
        "manual_away",
        "reported_absence",
        "reported_return",
    ]


def _valid(sample: MeasurementSample) -> bool:
    return sample.quality.status == "measured" and sample.value in (0.0, 1.0)


_PresenceVote = tuple[datetime, int, int, float, float]


def _wilson_upper(probability: float, effective_n: float) -> float:
    """Return the existing conservative upper bound for positive effective support."""
    z2 = 1.96**2
    upper = (
        probability
        + z2 / (2 * effective_n)
        + 1.96
        * math.sqrt(probability * (1 - probability) / effective_n + z2 / (4 * effective_n**2))
    ) / (1 + z2 / effective_n)
    # The mathematical bound is in [0, 1]; uneven weights can put the computed
    # endpoint one floating-point step outside that interval (for example p=1).
    return min(1.0, max(0.0, upper))


def _history_evidence(votes: list[_PresenceVote]) -> tuple[float | None, float | None, float]:
    """Describe one unique-date population without inventing independent votes."""
    maximum = max((row[4] for row in votes), default=0.0)
    if maximum <= 0:
        return None, None, 0.0
    # Scaling leaves the weighted probability and Kish support unchanged, while
    # avoiding squared-weight underflow for a short configured recency half-life.
    weights = [row[4] / maximum for row in votes]
    total = math.fsum(weights)
    effective_n = total**2 / math.fsum(weight**2 for weight in weights)
    probability = math.fsum(row[3] * weight for row, weight in zip(votes, weights)) / total
    return probability, _wilson_upper(probability, effective_n), effective_n


def _sensor_state(
    samples: list[MeasurementSample], now: datetime, settings: PresenceCommonSettings
) -> Literal["present", "absent", "unknown"]:
    """Accept absence only after a continuous, valid, gap-bounded absence chain."""
    if not samples:
        return "unknown"
    latest = samples[-1]
    if (now - latest.date_time).total_seconds() > settings.sample_max_age_seconds or not _valid(
        latest
    ):
        return "unknown"
    if latest.value == 1.0:
        return "present"
    since = latest.date_time.timestamp()
    for sample in reversed(samples[:-1]):
        if not _valid(sample) or sample.value != 0.0:
            break
        if since - sample.date_time.timestamp() > settings.sample_max_age_seconds:
            break
        since = sample.date_time.timestamp()
    return "absent" if now.timestamp() - since >= settings.absence_delay_seconds else "unknown"


def forecast_presence(
    samples: list[MeasurementSample],
    *,
    measurement_key: str,
    now: datetime,
    starts: list[datetime],
    timezone_name: str,
    settings: PresenceCommonSettings,
    correction: PresenceCorrection | None = None,
    absences: Sequence[PresenceAbsence] | None = None,
) -> list[PresenceEstimate]:
    """Fit local weekday patterns and correct the near future using only past samples.

    One local date supplies at most one vote per quarter-hour, including DST folds.
    An exact weekday is selected only if its effective support could justify
    absence even with no occupied examples. Otherwise workdays/weekends are
    pooled once, while known exact-weekday occupancy conservatively guards the
    upper bound. Additional absence-only history cannot cause an unsupported
    switch to a smaller population merely by crossing a raw day-count threshold.
    Each historical bucket requires explicit observed coverage. No blank day or
    forward-filled sensor outage becomes a negative training example.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Presence forecast requires an aware issue time")
    if len(starts) > 288 or any(
        value.tzinfo is None or value.utcoffset() is None for value in starts
    ):
        raise ValueError("Presence forecast requires at most 288 prospective aware intervals")
    # Normalize native datetime subclasses before timedelta arithmetic: some
    # subclasses lose tzinfo when combined with stdlib timezone.utc.
    now = datetime.fromtimestamp(now.timestamp(), timezone.utc)
    starts = [datetime.fromtimestamp(value.timestamp(), timezone.utc) for value in starts]
    if any(value < now for value in starts):
        raise ValueError("Presence forecast requires prospective intervals")
    if any(b <= a for a, b in zip(starts, starts[1:])):
        raise ValueError("Presence forecast intervals must be ordered and unique")
    tz = ZoneInfo(timezone_name)
    # Reported absences as (start, end|None) epoch seconds. Nothing below changes without entries.
    windows = [
        item.resolve(timezone_name)
        for item in (settings.absences if absences is None else absences)
    ]
    hold = settings.absence_return_hold_minutes * 60
    cutoff = now - timedelta(days=settings.history_days)
    # A duplicate key/time is a replacement. Last input wins, including invalidation.
    by_time = {
        sample.date_time.timestamp(): sample
        for sample in samples
        if sample.key == measurement_key
        and cutoff.timestamp() <= sample.date_time.timestamp() <= now.timestamp()
    }
    timestamps = sorted(by_time)
    history = [by_time[stamp] for stamp in timestamps]
    # Exact held-sample overlap, not a minute-grid approximation. Each repeated
    # local DST bucket is qualified independently before combining one daily vote.
    occurrences: dict[float, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for i, sample in enumerate(history):
        if not _valid(sample) or sample.value is None:
            continue
        cursor = timestamps[i]
        end = min(
            cursor + settings.sample_max_age_seconds,
            timestamps[i + 1] if i + 1 < len(timestamps) else now.timestamp(),
            now.timestamp(),
        )
        while cursor < end:
            bucket = math.floor(cursor / 900) * 900
            right = min(end, bucket + 900)
            if bucket + 900 <= now.timestamp():
                occurrences[bucket][0] += right - cursor
                occurrences[bucket][1] += (right - cursor) * sample.value
            cursor = right
    buckets: dict[tuple[str, int, int], list[tuple[float, float]]] = defaultdict(list)
    for stamp, (covered, occupied) in occurrences.items():
        if covered / 900 < settings.minimum_bucket_coverage:
            continue
        if windows and any(a < stamp + 900 and (b is None or stamp < b) for a, b in windows):
            continue  # reported absence: not a learnable day pattern
        local = datetime.fromtimestamp(stamp, tz)
        key = (local.date().isoformat(), local.weekday(), local.hour * 4 + local.minute // 15)
        buckets[key].append((covered, occupied))
    examples: list[_PresenceVote] = []
    for (day, weekday, slot), values in buckets.items():
        local_day = datetime.fromisoformat(day).replace(tzinfo=tz)
        age_days = max(0.0, (now.astimezone(tz).date() - local_day.date()).days)
        weight = 2 ** (-age_days / settings.recency_half_life_days)
        examples.append(
            (
                local_day,
                weekday,
                slot,
                sum(value[1] for value in values) / sum(value[0] for value in values),
                weight,
            )
        )

    sensor = _sensor_state(history, now, settings)
    output = []
    for start in starts:
        local = start.astimezone(tz)
        slot = local.hour * 4 + local.minute // 15
        weekday_votes = [row for row in examples if row[1] == local.weekday() and row[2] == slot]
        weekday_probability, weekday_upper, weekday_effective_n = _history_evidence(weekday_votes)
        # A raw count alone can select a population whose recency-weighted
        # Wilson bound can NEVER reach the configured absence threshold. Keep
        # pooling until the exact population can support that actual decision.
        exact_supported = (
            len(weekday_votes) >= settings.minimum_support_days
            and weekday_effective_n > 0
            and _wilson_upper(0.0, weekday_effective_n) < settings.absence_probability_threshold
        )
        population: Literal["unknown", "weekday", "workday", "weekend", "all_days"] = "weekday"
        choices = weekday_votes
        if not exact_supported:
            population = "workday" if local.weekday() < 5 else "weekend"
            choices = [
                row for row in examples if (row[1] < 5) == (local.weekday() < 5) and row[2] == slot
            ]
        if not choices:
            # A first observed daily profile is useful for display, but borrowing
            # another day type alone must never authorize comfort reduction.
            choices = [row for row in examples if row[2] == slot]
            population = "all_days"
        support = len(choices)
        provisional = bool(choices) and (
            support < settings.minimum_support_days or population == "all_days"
        )
        historical_probability, historical_upper, effective_n = _history_evidence(choices)
        weekday_guard = any(row[3] > 0 for row in weekday_votes)
        probability, upper = None, None
        source: Literal[
            "unknown",
            "history",
            "sensor_present",
            "sensor_absent",
            "manual_comfort",
            "manual_away",
            "reported_absence",
            "reported_return",
        ] = "unknown"
        if support and historical_probability is not None:
            probability, upper = historical_probability, historical_upper
            if weekday_guard and weekday_upper is not None and upper is not None:
                # Exact-day votes are a SUBSET of the pool, not extra examples.
                # Their positive occupancy may veto pooled absence even before
                # seven such weekdays exist; an unrelated absent weekday cannot
                # dilute a known return pattern out of the comfort decision.
                upper = max(upper, weekday_upper)
            source = "history"
        historical = probability
        ahead = (start - now).total_seconds()
        if ahead < settings.local_correction_seconds:
            weight = max(0.0, 1 - ahead / settings.local_correction_seconds)
            if sensor == "present":
                probability = 1.0 if probability is None else weight + (1 - weight) * probability
                upper, source = 1.0, "sensor_present"
            elif sensor == "absent":
                # Local absence can lower a supported forecast, never invent a history.
                if probability is not None and upper is not None:
                    probability *= 1 - weight
                    upper *= 1 - weight
                    source = "sensor_absent"
            else:
                # An unhealthy sensor cannot justify a near-term comfort reduction.
                upper = None
                source = "unknown"
        require_comfort = (
            (provisional or upper is None or upper >= settings.absence_probability_threshold)
            if not settings.allow_provisional_setback
            else (
                probability is None
                or source == "unknown"
                or probability > settings.absence_probability_threshold
            )
        )
        if windows:
            first, last = start.timestamp(), start.timestamp() + 900
            if any(a <= first and (b is None or last <= b) for a, b in windows):
                if source != "sensor_present":  # someone is at home right now: the sensor wins
                    probability, upper, source, require_comfort = (
                        0.0,
                        0.0,
                        "reported_absence",
                        False,
                    )
            elif any(
                b is not None and (first < b < last or b <= first < b + hold) for a, b in windows
            ):
                # The slot with the reported return and the hold after it need comfort, so the
                # planner warms the room up beforehand with its normal preheat/recovery logic.
                probability, upper, source, require_comfort = 1.0, 1.0, "reported_return", True
        if correction is not None and correction.issued_at <= now and now < correction.valid_until:
            # A slot straddling the return boundary must already be comfortable.
            if (
                start >= correction.issued_at
                and start + timedelta(minutes=15) <= correction.valid_until
            ):
                if correction.kind == "comfort":
                    probability, upper, source, require_comfort = 1.0, 1.0, "manual_comfort", True
                elif sensor != "present" and ahead >= 0:
                    probability, upper, source, require_comfort = 0.0, 0.0, "manual_away", False
        output.append(
            PresenceEstimate(
                date_time=start,
                probability=probability,
                historical_probability=historical,
                probability_upper=upper,
                support_days=support,
                support_population=population if choices else "unknown",
                history_is_provisional=provisional,
                effective_support_days=effective_n,
                weekday_support_days=len(weekday_votes),
                weekday_effective_support_days=weekday_effective_n,
                weekday_probability=weekday_probability,
                weekday_probability_upper=weekday_upper,
                weekday_presence_guard=weekday_guard,
                require_comfort=require_comfort,
                source=source,
            )
        )
    return output


class PresenceDataRecord(PredictionRecord):
    """Native EOS time-series record with independently addressable area channels."""

    @classmethod
    def configured_data_keys(cls) -> list[str]:
        """Expose stable per-area keys through the ordinary EOS prediction API."""
        return [
            f"presence_{quantity}_{zone}"
            for zone in cls.config.presence.zones
            for quantity in ("probability", "baseline", "upper", "comfort", "support_days")
        ]


class PresenceHistory(PredictionProvider[PresenceDataRecord]):
    """Optional native provider using EOS measurements and database-backed records."""

    records: list[PresenceDataRecord] = Field(default_factory=list)
    _corrections: dict[str, PresenceCorrection] = PrivateAttr(default_factory=dict)

    @classmethod
    def provider_id(cls) -> str:
        """Return the selectable provider identifier."""
        return "PresenceHistory"

    def enabled(self) -> bool:
        """Leave all existing installations unchanged by default."""
        return self.config.presence.provider == self.provider_id()

    def set_correction(self, correction: PresenceCorrection) -> None:
        """Accept temporary user intent without writing installation configuration."""
        if correction.zone_id not in self.config.presence.zones:
            raise ValueError("Unknown presence area")
        self._corrections[correction.zone_id] = correction

    async def forecast_zone(
        self,
        zone: str,
        *,
        now: datetime,
        starts: list[datetime],
        settings: PresenceCommonSettings | None = None,
        publish: bool = True,
        absences: Sequence[PresenceAbsence] = (),
    ) -> list[PresenceEstimate]:
        """Use the same measured history for standalone and thermal consumers."""
        settings = settings or PresenceCommonSettings.model_validate(
            self.config.presence.model_dump()
        )
        binding = settings.zones[zone]
        samples = await self.measurement.get_samples(
            binding.measurement_key,
            now - timedelta(days=settings.history_days),
            now + timedelta(seconds=1),
        )
        estimates = forecast_presence(
            samples,
            measurement_key=binding.measurement_key,
            now=now,
            starts=starts,
            timezone_name=self.config.general.timezone or "UTC",
            settings=settings,
            correction=self._corrections.get(zone),
            absences=[
                item
                for item in (*settings.absences, *absences)
                if not item.zone_ids or zone in item.zone_ids
            ],
        )
        if publish:
            for estimate in estimates:
                await self.update_value(
                    to_datetime(estimate.date_time),
                    {
                        f"presence_probability_{zone}": estimate.probability,
                        f"presence_baseline_{zone}": estimate.historical_probability,
                        f"presence_upper_{zone}": estimate.probability_upper,
                        f"presence_comfort_{zone}": float(estimate.require_comfort),
                        f"presence_support_days_{zone}": float(estimate.support_days),
                    },
                )
            self.update_datetime = to_datetime(now)
        return estimates

    async def _update_data(self, force_update: bool | None = False) -> None:
        """Publish historical probabilities with near-term correction; never actuate."""
        settings = PresenceCommonSettings.model_validate(self.config.presence.model_dump())
        now = datetime.now(timezone.utc)
        start_epoch = (
            math.ceil(max(now.timestamp(), self.ems_start_datetime.timestamp()) / 900) * 900
        )
        hours = self.config.prediction.hours or 0
        count = min(288, max(0, math.ceil(hours * 4)))
        starts = [datetime.fromtimestamp(start_epoch + i * 900, timezone.utc) for i in range(count)]
        for zone in settings.zones:
            await self.forecast_zone(zone, now=now, starts=starts, settings=settings)
        self.update_datetime = to_datetime(now)
