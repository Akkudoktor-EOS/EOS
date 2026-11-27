"""Explicit thermal planning data, without optimization or device communication.

Room targets are continuous Celsius values, not native appliance setpoints.
Electrical input uses watts. Effective RC parameters do not identify absolute
thermal capacities, delivered heat, or COP.
"""

from datetime import datetime, timezone
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _require_nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Thermal identifiers and sources must not be blank")
    return value


FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]
NonnegativeRevision = Annotated[int, Field(strict=True, ge=0)]
NonemptyText = Annotated[
    str, StringConstraints(strict=True, min_length=1), AfterValidator(_require_nonblank)
]
UTCDatetime = Annotated[AwareDatetime, AfterValidator(_as_utc)]


class ThermalModel(BaseModel):
    """Strict, immutable data transfer object with no configuration side effects."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class ThermalRC2Parameters(ThermalModel):
    """Legacy effective room/mass dynamics with mean electrical power as input.

    With time in hours and electrical power P in kW, the model is:
    dTr/dt = loss*(To-Tr) + exchange*(Tm-Tr) + electric_gain*P;
    dTm/dt = exchange/mass_capacity_ratio*(Tr-Tm).

    These normalized parameters do not separately identify heat-pump COP or
    physical heat capacities. There is no constant, guaranteed external heat
    source: at zero input, the stationary room and mass temperatures equal To.
    """

    room_loss_per_hour: FiniteNumber = Field(gt=0)
    room_mass_exchange_per_hour: FiniteNumber = Field(gt=0)
    mass_capacity_ratio: FiniteNumber = Field(gt=0)
    electric_gain_kelvin_per_kwh: FiniteNumber = Field(gt=0)


class ThermalBuildingRC2Parameters(ThermalModel):
    """Source-independent room/mass dynamics with delivered thermal input.

    With time in hours and delivered heat Q in kW:
    dTr/dt = loss*(To-Tr) + exchange*(Tm-Tr) + heat_gain*Q;
    dTm/dt = exchange/mass_capacity_ratio*(Tr-Tm).

    ``heat_gain_kelvin_per_kwh`` is per thermal kWh, not per electrical kWh.
    Heat-pump COP and capacity belong to a separate source model. A fitted
    effective electrical gain cannot be reused here without identifying that
    thermal/electrical conversion; doing so would count efficiency twice.
    """

    room_loss_per_hour: FiniteNumber = Field(gt=0)
    room_mass_exchange_per_hour: FiniteNumber = Field(gt=0)
    mass_capacity_ratio: FiniteNumber = Field(gt=0)
    heat_gain_kelvin_per_kwh: FiniteNumber = Field(gt=0)


class ThermalObservation(ThermalModel):
    """A timestamped observation, distinct from a latent thermal state estimate.

    Supplied channels belong to the same observation time. Different source
    timestamps must be represented as separate observations, not relabelled as
    simultaneous fresh measurements. At least one channel must be present;
    zero electrical power is a valid observation. Freshness policy belongs to
    the caller.
    """

    observed_at: UTCDatetime
    room_temperature_c: FiniteNumber | None = None
    outdoor_temperature_c: FiniteNumber | None = None
    electric_power_w: FiniteNumber | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_observed_channel(self) -> Self:
        """Reject an empty sample without mistaking zero power for missing data."""
        if all(
            value is None
            for value in (
                self.room_temperature_c,
                self.outdoor_temperature_c,
                self.electric_power_w,
            )
        ):
            raise ValueError("A thermal observation requires at least one measured channel")
        return self


class ThermalState(ThermalModel):
    """Estimated room/mass state, including its causal observation boundary.

    The mass temperature is an estimate, not an additional measured sensor.
    This DTO does not initialize or advance a thermal model.
    """

    estimated_at: UTCDatetime
    based_on_observed_at: UTCDatetime
    room_temperature_c: FiniteNumber
    mass_temperature_c: FiniteNumber

    @model_validator(mode="after")
    def check_observation_boundary(self) -> Self:
        """Reject estimates whose stated observation lies in their future."""
        if self.based_on_observed_at > self.estimated_at:
            raise ValueError("Observation time must not follow state estimation time")
        return self


class ThermalComfortBand(ThermalModel):
    """Continuous room-temperature bounds and a target within those bounds."""

    min_temperature_c: FiniteNumber
    target_temperature_c: FiniteNumber
    max_temperature_c: FiniteNumber

    @model_validator(mode="after")
    def check_temperature_order(self) -> Self:
        """Require the target to lie inside the configured comfort band."""
        if not self.min_temperature_c <= self.target_temperature_c <= self.max_temperature_c:
            raise ValueError("Temperatures must satisfy minimum <= target <= maximum")
        return self


class TemperatureInterval(ThermalComfortBand):
    """A half-open room-target interval; this contains no appliance commands.

    ``heating_allowed`` is False in an interval of a plan where the heat source must not run
    (barred, or outside its operating range); the target is then the room temperature the plan
    expects without heat, not a request to heat.
    """

    start_datetime: UTCDatetime
    end_datetime: UTCDatetime
    heating_allowed: bool = True

    @model_validator(mode="after")
    def check_time_order(self) -> Self:
        """Require a positive interval after normalizing time zones."""
        if self.end_datetime <= self.start_datetime:
            raise ValueError("Interval end must follow interval start")
        return self


class ThermalPlan(ThermalModel):
    """Versioned single-zone room curve with an independent execution lease.

    A short lease can accompany a multi-day horizon. Its maximum duration and
    the current wall-clock validity are runtime policy, not DTO validation.
    Identifiers are preserved exactly for matching configured zone bindings.
    Manual-priority and revision guards remain the adapter's responsibility.
    Merely constructing this object does not authorize or perform control.
    """

    zone_id: NonemptyText
    plan_id: NonemptyText
    source: NonemptyText
    revision: NonnegativeRevision
    basis_control_revision: NonnegativeRevision
    issued_at: UTCDatetime
    valid_until: UTCDatetime
    intervals: tuple[TemperatureInterval, ...] = Field(min_length=1, max_length=384)

    @model_validator(mode="after")
    def check_lease_and_horizon(self) -> Self:
        """Require a positive lease and contiguous, ordered horizon coverage."""
        if self.valid_until <= self.issued_at:
            raise ValueError("Plan validity must end after issue time")
        if any(
            a.end_datetime != b.start_datetime for a, b in zip(self.intervals, self.intervals[1:])
        ):
            raise ValueError("Plan intervals must be ordered and contiguous")
        if self.valid_until <= self.intervals[0].start_datetime:
            raise ValueError("Plan lease must overlap the interval horizon")
        if self.valid_until > self.intervals[-1].end_datetime:
            raise ValueError("Plan horizon must cover the lease end")
        return self
