"""Native data admission and planning for a separate non-actuating EOS process."""

from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Request

from akkudoktoreos.thermal.thermalabsences import AbsenceInput, AbsenceStore
from akkudoktoreos.thermal.thermalconfigrequest import (
    ThermalConfigRequest,
    ThermalDataBundle,
)


class ThermalRuntimeContextProtocol(Protocol):
    """Small injected interface; routes do not initialize global EOS services."""

    async def ingest(self, bundle: ThermalDataBundle) -> dict[str, Any]:
        """Validate and store a native input snapshot."""
        ...

    async def plan(self, request: ThermalConfigRequest) -> dict[str, Any]:
        """Return a separate model proposal, never a device or EMS action."""
        ...

    def status(self) -> dict[str, Any]:
        """Return sanitized context readiness and input quality."""
        ...


router = APIRouter(prefix="/v1/thermal", tags=["native-thermal-shadow"])


def _context(request: Request) -> ThermalRuntimeContextProtocol:
    context = getattr(request.app.state, "thermal_runtime_context", None)
    if context is None:
        raise HTTPException(503, "Native thermal shadow context is not initialized")
    return context


def _shadow_result(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "shadow_only": True, "execution_allowed": False}


@router.put("/data_bundle")
async def put_data_bundle(bundle: ThermalDataBundle, request: Request) -> dict[str, Any]:
    """Admit typed data only into the app's isolated native context."""
    try:
        return _shadow_result(await _context(request).ingest(bundle))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/plan")
async def post_plan(payload: ThermalConfigRequest, request: Request) -> dict[str, Any]:
    """Compute without publishing any battery plan, notification or actuator command."""
    try:
        return _shadow_result(await _context(request).plan(payload))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _absence_store(request: Request) -> AbsenceStore:
    store = getattr(request.app.state, "absence_store", None)
    if store is None:
        raise HTTPException(503, "Reported absences are not enabled in this process")
    return store


@router.get("/absences")
def get_absences(request: Request) -> dict[str, Any]:
    """Stored absences, the running one and the next one (read only)."""
    return _shadow_result(_absence_store(request).summary())


@router.post("/absences")
def post_absence(body: AbsenceInput, request: Request) -> dict[str, Any]:
    """Report an absence: start defaults to now, no end means until revoked."""
    store = _absence_store(request)
    try:
        record = store.add(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _shadow_result({"created": record.id, **store.summary()})


@router.put("/absences/{absence_id}")
def put_absence(absence_id: str, body: AbsenceInput, request: Request) -> dict[str, Any]:
    """Create or replace the absence with this id (idempotent for scripts that keep their id)."""
    store = _absence_store(request)
    try:
        store.replace(absence_id, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _shadow_result(store.summary())


@router.post("/absences/back")
def post_back_home(request: Request) -> dict[str, Any]:
    """Back home: end every running absence now (kept so learning still ignores it)."""
    store = _absence_store(request)
    ended = store.back_home()
    return _shadow_result({"ended": [r.id for r in ended], **store.summary()})


@router.delete("/absences/{absence_id}")
def delete_absence(absence_id: str, request: Request) -> dict[str, Any]:
    """Revoke one absence: a running one ends now, a future one is removed."""
    store = _absence_store(request)
    if store.revoke(absence_id) is None:
        raise HTTPException(404, "Unknown absence id")
    return _shadow_result(store.summary())


@router.get("/status")
def get_status(request: Request) -> dict[str, Any]:
    """Read native shadow readiness without data acquisition or optimization."""
    return _shadow_result(_context(request).status())
