"""Historical presence forecasts with causal sensor correction on EOS data structures.

Forecasts express occupancy probability, not a guarantee of a person's return.
Missing or invalid samples never count as absence. This provider does not control
heating or persist manual intent in the installation configuration.
"""

import math
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Optional, Self
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import AwareDatetime, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from akkudoktoreos.config.configabc import SettingsBaseModel
from akkudoktoreos.core.pydantic import PydanticBaseModel
from akkudoktoreos.measurement.quality import MeasurementSample
from akkudoktoreos.prediction.predictionabc import PredictionProvider, PredictionRecord
from akkudoktoreos.utils.datetimeutil import to_datetime

if TYPE_CHECKING:
    from akkudoktoreos.thermal.thermalabsences import AbsenceStore


class PresenceZoneSettings(SettingsBaseModel):
    """Bind any occupancy area to an existing typed EOS presence measurement."""

    model_config = ConfigDict(extra="forbid")
    measurement_key: str = Field(min_length=1)
    ignore_windows: list[tuple[str, str]] = Field(
        default_factory=list,
        examples=[[["01:00", "03:00"]]],
        description="Daily local-time windows (start, end) whose detections do not count as "
        "presence, for example a robot vacuum that runs every night. Coverage is unchanged.",
    )

    @field_validator("ignore_windows")
    @classmethod
    def _valid_windows(cls, windows: list[tuple[str, str]]) -> list[tuple[str, str]]:
        for window in windows:
            for text in window:
                time.fromisoformat(text)  # raises ValueError for anything but a clock time
        return windows


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
    occupancy_hold_seconds: int = Field(
        default=600,
        ge=0,
        le=3600,
        strict=True,
        description="A detection counts as presence for this long (a radar reports flashes of "
        "seconds); 0 holds it only until the next sample.",
    )
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
    """The presence probability of one interval and where it comes from."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    date_time: AwareDatetime
    probability: float | None = Field(default=None, ge=0, le=1)
    historical_probability: float | None = Field(default=None, ge=0, le=1)
    probability_upper: float | None = Field(default=None, ge=0, le=1)
    support_days: int = Field(default=0, ge=0)
    history_is_provisional: bool = False
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


# Rate models of the Markov chain: (harmonics of the time of day, harmonics shared by workdays and
# weekends with a weekend offset instead of separate ones, L2 strength). Chosen on the history of
# the installation (leave-one-day-out), not at run time.
_RATE_MODELS = {"enter": (6, True, 1.0), "leave": (6, False, 1.0)}
_SLOT_SECONDS = 900
_PROFILE_LEAD = 192  # quarter hours (two days) the profile runs in before the current one
_OCCUPIED_SHARE = 0.5  # a slot counts as occupied if the held presence covers half of it


def _basis(
    clock_h: np.ndarray, weekend: np.ndarray, harmonics: int, shared: bool
) -> tuple[np.ndarray, np.ndarray]:
    """Return Fourier features of the time of day by day type and their L2 penalty weights."""
    theta = 2 * np.pi * clock_h / 24
    columns = [np.ones_like(theta)]
    for k in range(1, harmonics + 1):
        columns += [np.cos(k * theta), np.sin(k * theta)]
    base = np.stack(columns, 1)
    flag = weekend.astype(float)[:, None]
    if shared:
        penalty = np.ones(base.shape[1] + 1)
        penalty[0], penalty[-1] = 0, 0.1
        return np.concatenate([base, flag], 1), penalty
    penalty = np.ones(2 * base.shape[1])
    penalty[0] = penalty[base.shape[1]] = 0
    return np.concatenate([base * (1 - flag), base * flag], 1), penalty


def _logistic(z: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def _fit_logit(
    features: np.ndarray,
    target: np.ndarray,
    weight: np.ndarray,
    strength: float,
    penalty: np.ndarray,
) -> np.ndarray:
    """Weighted maximum likelihood with L2 penalty by Newton steps (IRLS)."""
    ridge = strength * penalty + 1e-9
    beta = np.zeros(features.shape[1])
    for _ in range(40):
        p = _logistic(features @ beta)
        gradient = features.T @ (weight * (p - target)) + ridge * beta
        hessian = features.T @ (features * (weight * p * (1 - p) + 1e-12)[:, None])
        step = np.linalg.solve(hessian + np.diag(ridge), gradient)
        beta -= step
        if np.abs(step).max() < 1e-7:
            break
    return beta


def _local_clock(stamps: np.ndarray, tz: ZoneInfo) -> tuple[np.ndarray, np.ndarray]:
    """Return the local time of day [h] of the middle of the slots and whether it is a weekend."""
    local = [datetime.fromtimestamp(float(s) + _SLOT_SECONDS / 2, tz) for s in stamps]
    return (
        np.array([d.hour + d.minute / 60 + d.second / 3600 for d in local]),
        np.array([d.weekday() >= 5 for d in local]),
    )


def _markov_paths(
    slots: np.ndarray,
    occupied: np.ndarray,
    *,
    now: float,
    count: int,
    start: float | None,
    tz: ZoneInfo,
    half_life_days: float,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Return the occupancy probability from the current quarter hour on, with and without state.

    The first path starts from the state, the second from the stationary profile.
    ``slots`` are the sorted starts of the known quarter hours with their 0/1 ``occupied`` state.
    The chain has the rates ``a(t)`` (absent to present) and ``b(t)`` (present to absent) of the
    local time of day and day type, fitted on the transitions between consecutive known slots,
    weighted by recency. The path starts at the current quarter hour with ``start`` (None = the
    profile) and takes ``pi' = pi (1 - b) + (1 - pi) a``. None without a day of transitions.
    """
    rows = np.nonzero(np.diff(slots) == _SLOT_SECONDS)[0] + 1
    if rows.size < 96:
        return None
    previous, current = occupied[rows - 1], occupied[rows]
    weight = 2.0 ** (-(now - slots[rows]) / 86400 / half_life_days)
    weight = weight / weight.mean()
    ahead = now // _SLOT_SECONDS * _SLOT_SECONDS + _SLOT_SECONDS * np.arange(-_PROFILE_LEAD, count)
    clock, weekend = _local_clock(ahead, tz)
    clock_past = _local_clock(slots[rows], tz)
    rates = {}
    for name, (harmonics, shared, strength) in _RATE_MODELS.items():
        features, penalty = _basis(*clock_past, harmonics, shared)
        keep = previous == (0 if name == "enter" else 1)
        target = current[keep] if name == "enter" else 1 - current[keep]
        beta = _fit_logit(features[keep], target, weight[keep], strength, penalty)
        rates[name] = _logistic(_basis(clock, weekend, harmonics, shared)[0] @ beta)
    enter, leave = rates["enter"], rates["leave"]

    def walk(first: float, begin: int) -> np.ndarray:
        path = np.empty(len(enter))
        path[begin] = first
        for k in range(begin + 1, len(enter)):
            path[k] = path[k - 1] * (1 - leave[k]) + (1 - path[k - 1]) * enter[k]
        return path[_PROFILE_LEAD:]

    # The profile is the chain run in from the stationary share of a slot two days earlier.
    profile = walk(enter[0] / (enter[0] + leave[0]), 0)
    return (profile if start is None else walk(start, _PROFILE_LEAD)), profile


def _sensor_state(
    samples: list[MeasurementSample],
    now: datetime,
    settings: PresenceCommonSettings,
    seen: Callable[[MeasurementSample], bool] = lambda sample: sample.value == 1.0,
) -> Literal["present", "absent", "unknown"]:
    """Present within the hold time of a detection; absent only after a continuous absence chain.

    ``seen`` tells whether a sample is a detection that counts as presence; any other valid
    sample is an absence.
    """
    if not samples:
        return "unknown"
    latest = samples[-1]
    if (now - latest.date_time).total_seconds() > settings.sample_max_age_seconds or not _valid(
        latest
    ):
        return "unknown"
    for sample in reversed(samples):
        if sample is not latest and (
            now.timestamp() - sample.date_time.timestamp() > settings.occupancy_hold_seconds
        ):
            break
        if _valid(sample) and seen(sample):
            return "present"
    since = latest.date_time.timestamp()
    for sample in reversed(samples[:-1]):
        if not _valid(sample) or seen(sample):
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
    ignore_windows: Sequence[tuple[str, str]] = (),
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

    A detection (value 1) counts as presence for ``occupancy_hold_seconds`` (the union of these
    intervals, clipped to the covered time), because a radar reports flashes of seconds.
    Detections inside the daily local-time ``ignore_windows`` do not count; coverage is unchanged.
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
    quiet = [(time.fromisoformat(a), time.fromisoformat(b)) for a, b in ignore_windows]

    def seen(sample: MeasurementSample) -> bool:
        """Whether the sample is a detection that counts as presence."""
        if sample.value != 1.0:
            return False
        if not quiet:
            return True
        clock = datetime.fromtimestamp(sample.date_time.timestamp(), tz).time()
        return not any((a <= clock < b) if a < b else (clock >= a or clock < b) for a, b in quiet)

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
    # Quarter hours with their held presence: exact overlap of the held samples, not a minute grid.
    # The covered time is the time a valid sample holds; a detection counts as presence for the
    # hold time (a union: ``held_until`` is the end of the latest one) within it.
    occurrences: dict[float, list[float]] = defaultdict(lambda: [0.0, 0.0])
    held_until = -math.inf
    for i, sample in enumerate(history):
        if not _valid(sample):
            continue
        cursor = timestamps[i]
        end = min(
            cursor + settings.sample_max_age_seconds,
            timestamps[i + 1] if i + 1 < len(timestamps) else now.timestamp(),
            now.timestamp(),
        )
        if seen(sample):
            held_until = cursor + settings.occupancy_hold_seconds
            if not settings.occupancy_hold_seconds:
                held_until = end
        while cursor < end:
            bucket = math.floor(cursor / _SLOT_SECONDS) * _SLOT_SECONDS
            right = min(end, bucket + _SLOT_SECONDS)
            if bucket + _SLOT_SECONDS <= now.timestamp():
                occurrences[bucket][0] += right - cursor
                occurrences[bucket][1] += max(0.0, min(right, held_until) - cursor)
            cursor = right
    # Only observed quarter hours teach (no blank day or sensor outage is an absence), and not
    # those of a reported absence: that is not a learnable day pattern.
    known = sorted(
        stamp
        for stamp, (covered, _) in occurrences.items()
        if covered / _SLOT_SECONDS >= settings.minimum_bucket_coverage
        and not any(a < stamp + _SLOT_SECONDS and (b is None or stamp < b) for a, b in windows)
    )
    slots = np.array(known, dtype=float)
    state = np.array([occurrences[s][1] / _SLOT_SECONDS >= _OCCUPIED_SHARE for s in known], float)
    days = len(known) / 96
    provisional = days < settings.minimum_support_days
    sensor = _sensor_state(history, now, settings, seen)
    quarter = int(now.timestamp() // _SLOT_SECONDS) * _SLOT_SECONDS
    count = int((starts[-1].timestamp() - quarter) // _SLOT_SECONDS) + 1 if starts else 0
    probability_path = baseline_path = None
    paths = None
    if days >= 1 and count:
        paths = _markov_paths(
            slots,
            state,
            now=now.timestamp(),
            count=count,
            start={"present": 1.0, "absent": 0.0}.get(sensor),
            tz=tz,
            half_life_days=settings.recency_half_life_days,
        )
    if paths is not None:
        probability_path, baseline_path = paths
    output = []
    for start in starts:
        k = int((start.timestamp() - quarter) // _SLOT_SECONDS)
        probability = None if probability_path is None else float(probability_path[k])
        historical = None if baseline_path is None else float(baseline_path[k])
        source: Literal[
            "unknown",
            "history",
            "sensor_present",
            "sensor_absent",
            "manual_comfort",
            "manual_away",
            "reported_absence",
            "reported_return",
        ] = "unknown" if probability is None else "history"
        ahead = (start - now).total_seconds()
        if ahead < settings.local_correction_seconds:
            # The sensor state is in the path; an unhealthy sensor cannot justify a near-term
            # comfort reduction.
            if sensor == "present":
                # A detection holds as presence for the hold time: certain until then.
                held = ahead <= settings.occupancy_hold_seconds
                probability = 1.0 if probability is None or held else probability
                source = "sensor_present"
            elif sensor == "absent" and probability is not None:
                source = "sensor_absent"
            elif sensor == "unknown":
                source = "unknown"
        # Too little history never lowers the comfort unless opted in (the probability is kept).
        require_comfort = (
            probability is None
            or source == "unknown"
            or probability > settings.absence_probability_threshold
            or (provisional and not settings.allow_provisional_setback)
        )
        upper = probability
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
                support_days=int(days),
                history_is_provisional=provisional,
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
    _absence_store: Optional["AbsenceStore"] = PrivateAttr(default=None)

    @classmethod
    def provider_id(cls) -> str:
        """Return the selectable provider identifier."""
        return "PresenceHistory"

    def enabled(self) -> bool:
        """Leave all existing installations unchanged by default."""
        return self.config.presence.provider == self.provider_id()

    def absence_store(self) -> "AbsenceStore":
        """Return the persistent store of absences reported at run time (the one entry point, REST)."""
        from akkudoktoreos.thermal.thermalabsences import AbsenceStore

        if self._absence_store is None:
            folder = Path(self.config.general.data_folder_path)
            self._absence_store = AbsenceStore(
                folder / "presence-absences.json",
                self.config.general.timezone or "UTC",
                self.config.presence.history_days,
            )
        return self._absence_store

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
                for item in (
                    *settings.absences,
                    *self.absence_store().planning_absences(),
                    *absences,
                )
                if not item.zone_ids or zone in item.zone_ids
            ],
            ignore_windows=binding.ignore_windows,
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
