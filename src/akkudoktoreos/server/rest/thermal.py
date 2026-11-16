"""Explicit thermal shadow API; no EMS publication or device communication."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from akkudoktoreos.thermal.thermalheatplanning import (
    ThermalHeatPlanningRequest,
    ThermalHeatPlanningSolution,
)
from akkudoktoreos.thermal.thermaloptimization import (
    ThermalPlanningInputs,
    ThermalShadowRequest,
    ThermalShadowSolution,
)
from akkudoktoreos.thermal.thermalsettings import ThermalCommonSettings
from akkudoktoreos.thermal.thermalworker import (
    optimize_heat_pump_isolated as optimize_heat_pump_shadow,
)
from akkudoktoreos.thermal.thermalworker import (
    optimize_thermal_isolated as optimize_thermal_shadow,
)

router = APIRouter(prefix="/v1/optimize/thermal", tags=["thermal-shadow"])


def utc_now() -> datetime:
    """Return the admission clock, separately patchable in isolated API tests."""
    return datetime.now(timezone.utc)


def get_thermal_shadow_settings(request: Request) -> ThermalCommonSettings:
    """Snapshot nested settings without copying or mutating a ConfigEOS singleton."""
    configured = getattr(request.app.state, "thermal_settings", None)
    if configured is None:
        # The ordinary EOS app owns this configuration. The standalone shadow
        # app supplies explicit settings and never calls this global lookup.
        from akkudoktoreos.core.coreabc import get_config

        configured = get_config().thermal
    return ThermalCommonSettings.model_validate(configured.model_dump(mode="python"))


@contextmanager
def _shadow_admission(
    payload: ThermalPlanningInputs,
    request: Request,
    settings: ThermalCommonSettings,
) -> Iterator[None]:
    """Apply identical admission and one per-app lock to both pure shadow solvers."""
    if settings.mode != "shadow":
        raise HTTPException(status_code=403, detail="Thermal shadow planning requires mode=shadow")
    zone = settings.zones.get(payload.zone_id)
    if zone is None or not zone.enabled:
        raise HTTPException(status_code=403, detail="The requested thermal zone is not enabled")
    if payload.plan_ttl_seconds > settings.plan_ttl_seconds:
        raise HTTPException(status_code=422, detail="Requested plan lease exceeds configured limit")

    now = utc_now()
    issue_age = (now - payload.issued_at).total_seconds()
    observation_age = (now - payload.state.based_on_observed_at).total_seconds()
    if issue_age < -30 or issue_age > settings.plan_ttl_seconds:
        raise HTTPException(
            status_code=422, detail="Shadow input issue time is stale or in the future"
        )
    if observation_age < -30 or observation_age > settings.measurement_max_age_seconds:
        raise HTTPException(status_code=422, detail="Room observation is stale or in the future")

    # Each app limits CPU work independently. There is no shared current plan,
    # model state, EMS clock, cache invalidation, config save or notify callback.
    lock = getattr(request.app.state, "thermal_solve_lock", None)
    if lock is None:
        raise HTTPException(status_code=503, detail="Thermal shadow solver is not initialized")
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A thermal shadow solve is already running")
    try:
        yield
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        lock.release()


@router.post("/shadow", response_model=ThermalShadowSolution)
def post_thermal_shadow(
    payload: ThermalShadowRequest,
    request: Request,
    settings: Annotated[ThermalCommonSettings, Depends(get_thermal_shadow_settings)],
) -> ThermalShadowSolution:
    """Compute a separate proposal; never replace an operational battery or heat plan."""
    with _shadow_admission(payload, request, settings):
        return optimize_thermal_shadow(
            payload,
            memory_limit_mb=settings.solver_process_memory_limit_mb,
            grace_seconds=settings.solver_process_grace_seconds,
        )


@router.post("/heatpump/shadow", response_model=ThermalHeatPlanningSolution)
def post_heat_pump_shadow(
    payload: ThermalHeatPlanningRequest,
    request: Request,
    settings: Annotated[ThermalCommonSettings, Depends(get_thermal_shadow_settings)],
) -> ThermalHeatPlanningSolution:
    """Plan native weather and a heat-pump map without publication or actuation."""
    with _shadow_admission(payload, request, settings):
        return optimize_heat_pump_shadow(
            payload,
            memory_limit_mb=settings.solver_process_memory_limit_mb,
            grace_seconds=settings.solver_process_grace_seconds,
        )
