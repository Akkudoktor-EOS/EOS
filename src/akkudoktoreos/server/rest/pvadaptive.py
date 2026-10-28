"""Learning status, issued comparisons and bounded raw PV ingestion for Grafana."""

import asyncio
from typing import Any

import pandas as pd
from fastapi import APIRouter, Body, HTTPException, Query

from akkudoktoreos.core.coreabc import get_prediction
from akkudoktoreos.measurement.quality import MeasurementSample
from akkudoktoreos.prediction.pvforecastadaptiveakkudoktor import (
    PVForecastAdaptiveAkkudoktor,
)

router = APIRouter(prefix="/v1/prediction/pv-adaptive", tags=["prediction"])


def provider() -> PVForecastAdaptiveAkkudoktor:
    """Resolve the registered singleton without creating a separate predictor."""
    return next(
        p for p in get_prediction().providers if isinstance(p, PVForecastAdaptiveAkkudoktor)
    )


@router.get("/status")
async def status() -> dict[str, Any]:
    """Status contains honest cold-start counts and matched per-lead error metrics."""
    value = await asyncio.to_thread(provider().store().metadata, "status")
    return value or dict(
        state="initializing",
        state_code=0,
        training_days=0,
        trained_leads=0,
        message="Noch keine adaptive Prognose ausgegeben.",
        metrics=[],
    )


@router.put("/samples")
async def samples(values: list[MeasurementSample] = Body(max_length=10000)) -> dict[str, int]:
    """Store original power readings in the bounded adaptive archive, without zero filling."""
    instance = provider()
    allowed = instance._sample_keys()
    if any(sample.key not in allowed for sample in values):
        raise HTTPException(422, "Measurement key is not assigned to an adaptive PV string.")
    if any(sample.value is not None and sample.value < 0 for sample in values):
        raise HTTPException(422, "PV production must not be negative.")
    flags = {
        plane.curtailment_key for plane in instance._learning_planes() if plane.curtailment_key
    }
    if any(sample.key in flags and sample.value not in (None, 0, 1) for sample in values):
        raise HTTPException(
            422, "Curtailment flags must be 0 (unrestricted), 1 (curtailed), or null."
        )
    try:
        updated = await asyncio.to_thread(
            instance.store().import_samples, values, pd.Timestamp.now(tz="UTC")
        )
        return dict(updated=updated)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/comparisons")
async def comparisons(
    days: int = Query(default=2, ge=1, le=30),
    limit: int = Query(default=2000, ge=1, le=20000),
    since_ms: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """Export genuinely issued forecasts with later measured truth, issuance and lead."""
    instance = provider()
    state = await asyncio.to_thread(instance.store().metadata, "status")
    if not state:
        return []
    now = pd.Timestamp.now(tz="UTC")
    return await asyncio.to_thread(
        instance.store().comparisons,
        state["signature"],
        instance.store().milliseconds(now - pd.Timedelta(days=days)),
        instance.store().milliseconds(now),
        limit,
        since_ms,
    )


@router.get("/latest")
async def latest() -> list[dict[str, Any]]:
    """Latest complete output curve; validation uses the separate immutable archive."""
    instance = provider()
    start = pd.Timestamp.now(tz=instance.config.general.timezone).normalize()
    return await asyncio.to_thread(instance.store().latest, instance.store().milliseconds(start))
