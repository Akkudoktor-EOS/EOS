"""Typed samples and derived energy within the existing measurement API."""

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import AwareDatetime

from akkudoktoreos.core.coreabc import get_config, get_measurement
from akkudoktoreos.measurement.batterycapacity import BatteryCapacityEstimate, BatteryCapacityRequest
from akkudoktoreos.core.databaseabc import DatabaseTimestamp
from akkudoktoreos.measurement.energy import EnergyInterval
from akkudoktoreos.measurement.quality import MeasurementSample, SampleQuality
from akkudoktoreos.utils.datetimeutil import to_datetime

router = APIRouter(prefix="/v1/measurement", tags=["measurement"])


@router.post("/battery-capacity/{battery_id}", response_model=BatteryCapacityEstimate)
async def post_battery_capacity(battery_id: str, request: BatteryCapacityRequest) -> BatteryCapacityEstimate:
    """Estimate capacity from independent SoC anchors and configured DC power.

    store_estimate writes the separate capacity_estimate config field in memory.
    Persistence follows the regular EOS configuration save mechanism.
    The active capacity_wh and raw measurements are never changed here.
    """
    try:
        estimate = await get_measurement().estimate_battery_capacity(battery_id, request)
        if request.store_estimate:
            batteries = [b for b in (get_config().devices.batteries or {}).values() if b.device_id == battery_id]
            if len(batteries) != 1:
                raise ValueError("Battery configuration changed during estimation; retry.")
            get_config().set_nested_value(
                f"devices/batteries/{battery_id}/capacity_estimate", estimate
            )
        return estimate
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/samples")
async def put_samples(
    samples: Annotated[list[MeasurementSample], Body(max_length=10000)],
) -> dict[str, int]:
    """Upsert raw values and their quality; legacy value/series payloads remain valid."""
    try:
        await get_measurement().import_samples(samples)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"updated": len(samples)}


@router.get("/samples", response_model=list[MeasurementSample])
async def get_samples(key: str, start: AwareDatetime, end: AwareDatetime) -> list[MeasurementSample]:
    """Read raw samples including quality, in a bounded half-open range."""
    measurement = get_measurement()
    try:
        measurement._energy_channel(key)
        if not 0 < end.timestamp() - start.timestamp() <= 31 * 86400:
            raise ValueError("Require a positive range of at most 31 days.")
        result = []
        async for record in measurement.db_iterate_records(
            DatabaseTimestamp.from_datetime(to_datetime(start)),
            DatabaseTimestamp.from_datetime(to_datetime(end)),
        ):
            if key in record.configured_data and record.date_time is not None:
                result.append(
                    MeasurementSample(
                        date_time=record.date_time,
                        key=key,
                        value=record.configured_data[key],
                        quality=record.sample_quality.get(key, SampleQuality()),
                    )
                )
                if len(result) > 10000:
                    raise ValueError("More than 10000 samples; request a shorter range.")
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/energy", response_model=list[EnergyInterval])
async def get_energy(
    key: str,
    start: AwareDatetime,
    end: AwareDatetime,
    interval_seconds: Annotated[int, Query(gt=0)] = 900,
) -> list[EnergyInterval]:
    """Energy in Wh, with temporal coverage and quality; no missing-to-zero filling."""
    try:
        return await get_measurement().energy_intervals(key, start, end, interval_seconds)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/household", response_model=dict[str, list[EnergyInterval]])
async def get_household(
    start: AwareDatetime, end: AwareDatetime, interval_seconds: Annotated[int, Query(gt=0)] = 900
) -> dict[str, list[EnergyInterval]]:
    """Site, household without EV, and base without separately measured devices."""
    try:
        return await get_measurement().household_intervals(start, end, interval_seconds)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
