"""Reported absences: the one entry point that feeds the presence forecast of the heat planning.

An absence reported here (dashboard, calendar, a script) is evidence for the presence forecast of
the next hours, never a command: it is stored, merged into ``PresenceHistory.forecast_zone`` and
read from there by the planner. Nothing here reaches a device.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException

from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.prediction.presence import PresenceHistory
from akkudoktoreos.thermal.thermalabsences import AbsenceInput, AbsenceStore

router = APIRouter(prefix="/v1/thermal/absences", tags=["presence"])


def _store() -> AbsenceStore:
    provider = get_prediction().provider_by_id("PresenceHistory")
    if not isinstance(provider, PresenceHistory):
        raise HTTPException(status_code=503, detail="Presence provider is not available")
    return provider.absence_store()


def _local(record: Any, store: AbsenceStore) -> dict[str, Any]:
    return {
        **record.model_dump(mode="json"),
        "start": record.start.astimezone(store.tz).isoformat(),
        "end": record.end.astimezone(store.tz).isoformat() if record.end else None,
    }


@router.get("")
async def get_absences() -> dict[str, Any]:
    """Stored absences with the running and the next one."""
    return _store().summary()


@router.post("")
async def post_absence(body: Annotated[AbsenceInput, Body()]) -> dict[str, Any]:
    """Report a new absence (``start`` defaults to now, ``end`` None = until back home)."""
    store = _store()
    try:
        return _local(store.add(body), store)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/back")
async def post_back() -> dict[str, Any]:
    """Back home: end every running absence now (kept for learning)."""
    store = _store()
    return {"ended": [_local(record, store) for record in store.back_home()]}


@router.put("/{ident}")
async def put_absence(ident: str, body: Annotated[AbsenceInput, Body()]) -> dict[str, Any]:
    """Create or replace the absence with this id."""
    store = _store()
    try:
        return _local(store.replace(ident, body), store)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/{ident}")
async def delete_absence(ident: str) -> dict[str, Any]:
    """Revoke an absence (a running one ends now, a future one is removed)."""
    store = _store()
    record = store.revoke(ident)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown absence")
    return _local(record, store)
