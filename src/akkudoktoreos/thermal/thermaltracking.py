"""Explicit, bounded room-target tracking pilot, without device communication.

This policy never authorizes the full shadow optimization or its battery plan.
The caller supplies current observations and an attested model proposal. The
downstream local controller must independently enforce manual priority and lease.
"""

import hashlib
from datetime import datetime, timedelta
from typing import Literal, Self

from pydantic import Field, StrictBool, model_validator

from akkudoktoreos.prediction.presence import PresenceCorrection
from akkudoktoreos.thermal.thermalmodels import (
    FiniteNumber,
    NonemptyText,
    NonnegativeRevision,
    TemperatureInterval,
    ThermalModel,
    ThermalPlan,
    UTCDatetime,
)


class ThermalTrackingSettings(ThermalModel):
    """Disabled-by-default limits for a separate room-target pilot."""

    enabled: StrictBool = False
    operation: Literal["pilot", "continuous"] = "pilot"
    zone_id: NonemptyText | None = None
    minimum_temperature_c: FiniteNumber = Field(default=18, ge=5, le=30)
    occupied_tolerance_c: FiniteNumber = Field(default=0.3, ge=0, le=1)
    max_increase_c: FiniteNumber = Field(default=0.5, ge=0, le=3.0)
    maximum_preheat_temperature_c: FiniteNumber = Field(default=30.0, ge=5, le=30)
    max_decrease_c: FiniteNumber = Field(default=0.5, ge=0, le=0.5)
    cold_guard_below_manual_c: FiniteNumber = Field(default=0.25, ge=0, le=0.5)
    measured_room_ceiling_above_manual_c: FiniteNumber = Field(default=0.5, gt=0, le=0.5)
    lease_seconds: int = Field(default=150, strict=True, gt=0, le=300)
    operator_max_age_seconds: int = Field(default=90, strict=True, gt=0)
    room_max_age_seconds: int = Field(default=180, strict=True, gt=0)
    presence_max_age_seconds: int = Field(default=90, strict=True, gt=0)
    source_max_age_seconds: int = Field(default=1800, strict=True, gt=0)
    absence_delay_seconds: int = Field(default=1800, strict=True, gt=0)

    def maximum_for_target(self, manual_target_c: float) -> float:
        """Share the executable preheat ceiling with the native planner.

        An explicit higher manual target is not lowered by the preheat ceiling.
        """
        return min(
            30.0,
            max(manual_target_c, self.maximum_preheat_temperature_c),
            manual_target_c + self.max_increase_c,
        )

    @model_validator(mode="after")
    def require_enabled_zone(self) -> Self:
        """Require an explicit zone binding before pilot admission."""
        if self.enabled and self.zone_id is None:
            raise ValueError("An enabled tracking pilot requires an explicit zone_id")
        return self


class ThermalTrackingOperator(ThermalModel):
    """Current manual baseline; the pilot does not modify these values."""

    master_power: Literal["On", "Off"]
    control_mode: Literal["mpc", "quiet", "direct"]
    target_temperature_c: FiniteNumber = Field(ge=16, le=30)
    control_revision: NonnegativeRevision
    observed_at: UTCDatetime


class ThermalTrackingPresence(ThermalModel):
    """Current presence health and comfort requirement, never a guessed zero."""

    valid: StrictBool = False
    present: StrictBool | None = None
    require_comfort: StrictBool | None = None
    verified_at: UTCDatetime | None = None
    # Trusted adapter attests uninterrupted fresh absent observations, not polling cadence.
    absent_since: UTCDatetime | None = None


class ThermalTrackingSource(ThermalModel):
    """Latest source attempt, including failures that revoke previous plans.

    ``physical_checks_passed`` is the trusted source adapter's attestation, not
    an independent device-tracking proof. ``issued_at`` must retain the original
    attempt time rather than the time at which a cached response was read.
    """

    status: NonemptyText
    issued_at: UTCDatetime
    plan: ThermalPlan | None = None
    physical_checks_passed: StrictBool = False
    recovery_active: StrictBool = False
    electric_power_w: tuple[FiniteNumber, ...] = ()
    shadow_only: Literal[True] = True
    execution_allowed: Literal[False] = False

    @model_validator(mode="after")
    def preserve_plan_issue_time(self) -> Self:
        """Prevent an old plan from acquiring a new source issue timestamp."""
        if self.plan is not None and self.plan.issued_at != self.issued_at:
            raise ValueError("Tracking source issued_at must preserve plan.issued_at")
        if self.electric_power_w and (
            self.plan is None
            or len(self.electric_power_w) != len(self.plan.intervals)
            or any(p < 0 for p in self.electric_power_w)
        ):
            raise ValueError("Source power must be nonnegative and match every plan interval")
        return self


class ThermalTrackingRequest(ThermalModel):
    """Runtime inputs for one policy evaluation, with an explicit pilot deadline."""

    zone_id: NonemptyText
    operator: ThermalTrackingOperator
    room_temperature_c: FiniteNumber = Field(ge=-40, le=85)
    room_observed_at: UTCDatetime
    presence: ThermalTrackingPresence = Field(default_factory=ThermalTrackingPresence)
    source: ThermalTrackingSource
    presence_correction: PresenceCorrection | None = None
    pilot_expires_at: UTCDatetime | None


class ThermalTrackingProposal(ThermalModel):
    """Short-lived target-only permission, distinct from full-plan execution."""

    source: Literal["eos"] = "eos"
    scope: Literal["room_target_only"] = "room_target_only"
    shadow_only: Literal[True] = True
    execution_allowed: Literal[False] = False
    full_optimization_execution_allowed: Literal[False] = False
    battery_execution_allowed: Literal[False] = False
    native_tracking_validated: Literal[False] = False
    pilot_tracking_allowed: StrictBool
    proposal_kind: Literal["tracking", "manual_fallback"]
    proposal_id: NonemptyText
    zone_id: NonemptyText
    plan_id: NonemptyText | None = None
    plan_revision: NonnegativeRevision | None = None
    basis_control_revision: NonnegativeRevision
    manual_target_c: FiniteNumber
    target_temperature_c: FiniteNumber
    min_temperature_c: FiniteNumber
    max_temperature_c: FiniteNumber
    issued_at: UTCDatetime
    valid_until: UTCDatetime | None = None
    interval_start: UTCDatetime | None = None
    interval_end: UTCDatetime | None = None
    plan_issued_at: UTCDatetime | None = None
    require_comfort: StrictBool
    absence_delay_satisfied: StrictBool = False
    quiet_max_fan: Literal[2, 3]
    reason: NonemptyText
    operation: Literal["pilot", "continuous"] = "pilot"
    heating_allowed: StrictBool = True
    electric_power_w: FiniteNumber | None = Field(default=None, ge=0)


def _current_interval(plan: ThermalPlan, now: datetime) -> TemperatureInterval | None:
    return next(
        (band for band in plan.intervals if band.start_datetime <= now < band.end_datetime),
        None,
    )


class ThermalTrackingPolicy:
    """In-memory active/pending admission; no files, threads or actuators.

    A newer successful future plan may preserve a still-valid current plan.
    Every failed, stale, superseded or blocked source attempt clears both. Each
    permission is recomputed from current operator, room and presence inputs.
    """

    def __init__(self) -> None:
        """Initialize an empty policy; no previous success is restored from disk."""
        self._active: ThermalPlan | None = None
        self._pending: ThermalPlan | None = None
        self._active_power: tuple[float, ...] = ()
        self._pending_power: tuple[float, ...] = ()
        self._source_stamp: datetime | None = None
        self._source_fingerprint: str | None = None
        self._zone_id: str | None = None
        self._max_plan_revision: int | None = None
        self._operator_revision: int | None = None
        self._absence_since: datetime | None = None
        self._absence_last_checked: datetime | None = None

    def _clear_plans(self) -> None:
        self._active = None
        self._pending = None
        self._active_power = ()
        self._pending_power = ()

    def propose(
        self,
        settings: ThermalTrackingSettings,
        request: ThermalTrackingRequest,
        *,
        now: datetime,
    ) -> ThermalTrackingProposal:
        """Evaluate a bounded current target; denied proposals mean manual fallback.

        Args:
            settings: Explicit trusted configuration, disabled by default.
            request: Fresh observations and the latest model attempt.
            now: Timezone-aware evaluation clock.

        Returns:
            A short current-interval permission or an explicit denial. Off and
            direct mode are never converted into an automatic On command.
        """
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Tracking evaluation requires a timezone-aware clock")
        operator = request.operator
        manual = operator.target_temperature_c
        comfort_required = True
        absence_ready = False
        continuous = settings.operation == "continuous"

        def deny(reason: str) -> ThermalTrackingProposal:
            nonlocal comfort_required, absence_ready
            self._clear_plans()
            self._absence_since = None
            self._absence_last_checked = None
            comfort_required = True
            absence_ready = False
            return response(False, reason)

        def response(
            allowed: bool,
            reason: str,
            *,
            plan: ThermalPlan | None = None,
            band: TemperatureInterval | None = None,
            target: float | None = None,
            lower: float | None = None,
            upper: float | None = None,
            valid_until: datetime | None = None,
            heating_allowed: bool = True,
        ) -> ThermalTrackingProposal:
            identity = f"{request.zone_id}:{operator.control_revision}:{now.isoformat()}:{reason}"
            return ThermalTrackingProposal(
                pilot_tracking_allowed=allowed,
                proposal_kind="tracking" if allowed else "manual_fallback",
                proposal_id="tracking-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                zone_id=request.zone_id,
                plan_id=plan.plan_id if plan else None,
                plan_revision=plan.revision if plan else None,
                basis_control_revision=operator.control_revision,
                manual_target_c=manual,
                target_temperature_c=manual if target is None else target,
                min_temperature_c=manual if lower is None else lower,
                max_temperature_c=manual if upper is None else upper,
                issued_at=now,
                valid_until=valid_until,
                interval_start=band.start_datetime if band else None,
                interval_end=band.end_datetime if band else None,
                plan_issued_at=plan.issued_at if plan else None,
                require_comfort=comfort_required,
                absence_delay_satisfied=absence_ready,
                quiet_max_fan=2 if operator.control_mode == "quiet" else 3,
                reason=reason,
                operation=settings.operation,
                heating_allowed=heating_allowed,
                electric_power_w=(
                    self._active_power[plan.intervals.index(band)]
                    if allowed and plan is not None and band is not None and self._active_power
                    else None
                ),
            )

        if not settings.enabled:
            return deny("pilot_disabled")
        if request.zone_id != settings.zone_id:
            return deny("zone_mismatch")
        if self._zone_id != request.zone_id:
            self._clear_plans()
            self._source_stamp = None
            self._source_fingerprint = None
            self._max_plan_revision = None
            self._operator_revision = None
            self._zone_id = request.zone_id
        if operator.master_power == "Off":
            return deny("manual_off")
        if operator.control_mode == "direct":
            return deny("manual_direct_mode")
        if not continuous and (request.pilot_expires_at is None or request.pilot_expires_at <= now):
            return deny("pilot_expired")
        operator_age = (now - operator.observed_at).total_seconds()
        room_age = (now - request.room_observed_at).total_seconds()
        if not -30 <= operator_age <= settings.operator_max_age_seconds:
            return deny("operator_stale_or_future")
        if not -30 <= room_age <= settings.room_max_age_seconds:
            return deny("room_stale_or_future")
        if self._operator_revision != operator.control_revision:
            self._clear_plans()
            self._absence_since = None
            self._absence_last_checked = None
            self._operator_revision = operator.control_revision

        source = request.source
        fingerprint = hashlib.sha256(source.model_dump_json().encode()).hexdigest()
        if self._source_stamp is not None and (
            source.issued_at < self._source_stamp
            or (source.issued_at == self._source_stamp and fingerprint != self._source_fingerprint)
        ):
            return deny("source_out_of_order_or_conflicting")
        self._source_stamp = source.issued_at
        self._source_fingerprint = fingerprint
        if not -30 <= (now - source.issued_at).total_seconds() <= settings.source_max_age_seconds:
            return deny("source_stale_or_future")
        if source.status not in ("optimal", "feasible") or source.plan is None:
            return deny("source_has_no_current_success")
        if not source.physical_checks_passed:
            return deny("source_physical_checks_missing")
        if not continuous and (
            source.recovery_active
            or request.room_temperature_c < manual - settings.cold_guard_below_manual_c
        ):
            return deny("cold_or_recovery_use_manual_mpc")
        if (
            not continuous
            and request.room_temperature_c >= manual + settings.measured_room_ceiling_above_manual_c
        ):
            return deny("room_above_pilot_ceiling")
        latest = source.plan
        if latest.zone_id != request.zone_id:
            return deny("plan_zone_mismatch")
        if latest.basis_control_revision != operator.control_revision:
            return deny("manual_revision_changed")
        if self._max_plan_revision is not None and latest.revision < self._max_plan_revision:
            return deny("plan_revision_out_of_order")
        self._max_plan_revision = latest.revision
        if latest.valid_until <= now:
            return deny("source_plan_expired")

        presence = request.presence
        presence_fresh = (
            presence.valid
            and presence.verified_at is not None
            and -30
            <= (now - presence.verified_at).total_seconds()
            <= settings.presence_max_age_seconds
        )
        verified_absence = (
            presence_fresh
            and presence.present is False
            and (continuous or presence.require_comfort is False)
        )
        if verified_absence:
            if (
                self._absence_since is None
                or self._absence_last_checked is None
                or (now - self._absence_last_checked).total_seconds()
                > settings.presence_max_age_seconds
            ):
                self._absence_since = now
            self._absence_last_checked = now
            absence_ready = (
                now - self._absence_since
            ).total_seconds() >= settings.absence_delay_seconds
        else:
            self._absence_since = None
            self._absence_last_checked = None
        # Sensor evidence survives slow optimization and denied plan attempts. The
        # adapter must reset absent_since on presence, stale data or observation gaps.
        if (
            verified_absence
            and presence.absent_since is not None
            and presence.verified_at is not None
        ):
            absence_ready = (
                presence.absent_since <= presence.verified_at <= now
                and (now - presence.absent_since).total_seconds() >= settings.absence_delay_seconds
            )
        correction = request.presence_correction
        if correction is not None and correction.zone_id == request.zone_id:
            if correction.issued_at <= now < correction.valid_until:
                if correction.kind == "comfort":
                    absence_ready = False
                elif continuous and not (presence_fresh and presence.present is True):
                    absence_ready = True
        comfort_required = not absence_ready

        if self._pending is not None and _current_interval(self._pending, now) is not None:
            self._active = self._pending
            self._active_power = self._pending_power
            self._pending = None
            self._pending_power = ()
        if _current_interval(latest, now) is not None:
            self._active = latest
            self._active_power = source.electric_power_w
        else:
            pending = self._pending
            if (
                pending is None
                or pending.valid_until <= now
                or pending.zone_id != request.zone_id
                or pending.basis_control_revision != operator.control_revision
                or (now - pending.issued_at).total_seconds() > settings.source_max_age_seconds
                or latest.intervals[0].start_datetime <= pending.intervals[0].start_datetime
            ):
                # A newer solve may begin later than a still valid pending plan.
                # Keep the earlier interval available at its original boundary.
                self._pending = latest
                self._pending_power = source.electric_power_w
        active = self._active
        if active is None:
            return response(False, "waiting_for_current_interval")
        if (
            active.valid_until <= now
            or active.zone_id != request.zone_id
            or active.basis_control_revision != operator.control_revision
            or (now - active.issued_at).total_seconds() > settings.source_max_age_seconds
        ):
            self._active = None
            self._active_power = ()
            return response(False, "active_plan_expired_or_superseded")
        band = _current_interval(active, now)
        if band is None:
            return response(False, "no_current_interval")
        lower = (
            max(settings.minimum_temperature_c, band.min_temperature_c)
            if continuous
            else max(16.0, band.min_temperature_c, manual - settings.max_decrease_c)
        )
        if comfort_required:
            lower = max(lower, manual - (settings.occupied_tolerance_c if continuous else 0))
        upper = min(band.max_temperature_c, settings.maximum_for_target(manual))
        if lower > upper:
            return deny("plan_and_manual_bounds_disjoint")
        target = min(upper, max(lower, band.target_temperature_c))
        heating_allowed = True
        if continuous and self._active_power:
            index = active.intervals.index(band)
            heating_allowed = self._active_power[index] > 1.0 or request.room_temperature_c < lower
        until = min(
            now + timedelta(seconds=settings.lease_seconds),
            request.pilot_expires_at
            if not continuous and request.pilot_expires_at is not None
            else active.valid_until,
            active.valid_until,
            band.end_datetime,
            correction.valid_until
            if correction is not None
            and correction.zone_id == request.zone_id
            and correction.issued_at <= now < correction.valid_until
            else active.valid_until,
        )
        if until <= now:
            return deny("no_current_execution_lease")
        return response(
            True,
            "continuous_room_control"
            if continuous
            else "bounded_target_only_tracking_not_full_optimization",
            plan=active,
            band=band,
            target=target,
            lower=lower,
            upper=upper,
            valid_until=until,
            heating_allowed=heating_allowed,
        )
