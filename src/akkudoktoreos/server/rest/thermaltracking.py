"""Separate target-only pilot admission; no EMS or actuator publication."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from akkudoktoreos.thermal.thermaltracking import (
    ThermalTrackingPolicy,
    ThermalTrackingProposal,
    ThermalTrackingRequest,
    ThermalTrackingSettings,
)

router = APIRouter(prefix="/v1/thermal/tracking", tags=["thermal-tracking-pilot"])


def tracking_now() -> datetime:
    """Return the separately testable policy admission clock."""
    return datetime.now(timezone.utc)


def get_tracking_settings(request: Request) -> ThermalTrackingSettings:
    """Use only explicitly injected settings; absence means disabled."""
    configured = getattr(request.app.state, "thermal_tracking_settings", None)
    if configured is None:
        return ThermalTrackingSettings()
    return ThermalTrackingSettings.model_validate(configured.model_dump(mode="python"))


@router.post("/proposal", response_model=ThermalTrackingProposal)
def post_tracking_proposal(
    payload: ThermalTrackingRequest,
    request: Request,
    settings: Annotated[ThermalTrackingSettings, Depends(get_tracking_settings)],
) -> ThermalTrackingProposal:
    """Evaluate a trusted local source snapshot, never send a controller command.

    Applications must inject one persistent policy and serialize calls to it.
    The local source adapter must retain source timestamps and report failures;
    the downstream MPC adapter independently verifies operator revision/lease.
    """
    policy = getattr(request.app.state, "thermal_tracking_policy", None)
    lock = getattr(request.app.state, "thermal_tracking_lock", None)
    if not isinstance(policy, ThermalTrackingPolicy) or lock is None:
        raise HTTPException(503, "Thermal tracking pilot is not initialized")
    if not lock.acquire(blocking=False):
        raise HTTPException(429, "A thermal tracking policy evaluation is already running")
    try:
        return policy.propose(settings, payload, now=tracking_now())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        lock.release()
