"""Reported absence through the native thermal request builder (real resolver, real forecast)."""

from datetime import timedelta

import pytest
from conftest import THERMAL_START as START
from conftest import bundle_data

from akkudoktoreos.prediction.presence import PresenceAbsence
from akkudoktoreos.thermal.thermalconfigrequest import (
    ThermalConfigRequest,
    ThermalDataBundle,
)


def configure(context, *, unoccupied=True, minimum=None):
    zone = {"presence_area_id": "presence1"}
    if unoccupied:
        zone["unoccupied_comfort"] = {"min_temperature_c": 16.0, "target_temperature_c": 17.0, "max_temperature_c": 18.0}
    if minimum is not None:
        zone["absence_minimum_temperature_c"] = minimum
    context.config.merge_settings_from_dict(
        {
            "presence": {"provider": "PresenceHistory", "zones": {"presence1": {"measurement_key": "present"}}},
            "measurement": {"channels": {"present": {"quantity": "presence", "unit": "1"}}},
            "thermal": {"planning_time_limit_seconds": 7.5, "zones": {"area1": zone}},
        }
    )


def intent(**extra):
    return ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7, **extra)


async def resolve(context, **extra):
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    return await context._resolve(intent(**extra))


@pytest.mark.asyncio
async def test_absence_in_the_plan_request_lowers_the_band_and_returns_to_comfort(context):
    configure(context)
    absence = PresenceAbsence(start=START - timedelta(hours=1), end=START + timedelta(minutes=60))
    request, diagnostics = await resolve(context, absences=(absence,))
    occupied = context.config.thermal.zones["area1"].comfort
    assert [row["source"] for row in diagnostics["presence"]][:4] == ["reported_absence"] * 4
    assert all(b.min_temperature_c == 16.0 for b in request.comfort_bands[:2])  # far enough from the return
    assert request.comfort_bands[4] == occupied and request.comfort_bands[7] == occupied  # return + hold


@pytest.mark.asyncio
async def test_without_absence_nothing_is_reported(context):
    configure(context)
    request, diagnostics = await resolve(context)
    assert all(row["source"] != "reported_absence" for row in diagnostics["presence"])
    assert request.comfort_bands[0] == context.config.thermal.zones["area1"].comfort  # no history: comfort


@pytest.mark.asyncio
async def test_configured_absence_without_zone_band_uses_the_minimum_setting(context):
    configure(context, unoccupied=False, minimum=12.0)
    context.config.merge_settings_from_dict(
        {"presence": {"absences": [{"start": (START - timedelta(hours=1)).isoformat(), "end": (START + timedelta(hours=2)).isoformat()}]}}
    )
    request, diagnostics = await resolve(context)
    assert [row["source"] for row in diagnostics["presence"]][:8][:5] == ["reported_absence"] * 5
    assert request.comfort_bands[0].min_temperature_c == 12.0


@pytest.mark.asyncio
async def test_default_protection_minimum_is_14_when_nothing_is_configured(context):
    configure(context, unoccupied=False)
    absence = PresenceAbsence(start=START - timedelta(hours=1))
    request, _ = await resolve(context, absences=(absence,))
    assert all(b.min_temperature_c == 14.0 for b in request.comfort_bands)


@pytest.mark.asyncio
async def test_routine_comfort_windows_do_not_override_a_reported_absence(context):
    configure(context)
    occupied = context.config.thermal.zones["area1"].comfort
    windows = tuple(
        {
            "start_datetime": START + timedelta(minutes=15 * i),
            "end_datetime": START + timedelta(minutes=15 * (i + 1)),
            "min_temperature_c": occupied.target_temperature_c,
            "target_temperature_c": occupied.target_temperature_c,
            "max_temperature_c": occupied.max_temperature_c,
        }
        for i in range(8)
    )
    absence = PresenceAbsence(start=START - timedelta(hours=1))
    request, _ = await resolve(
        context, absences=(absence,), comfort_windows=windows, comfort_windows_policy="economic_setback"
    )
    assert all(b.min_temperature_c == 16.0 for b in request.comfort_bands)
    assert request.comfort_required == (False,) * 8


@pytest.mark.asyncio
async def test_plan_with_an_absence_is_valid_and_reaches_comfort_at_the_return(context):
    configure(context)
    data = bundle_data()
    await context.ingest(ThermalDataBundle.model_validate(data))
    absence = PresenceAbsence(start=START - timedelta(hours=1), end=START + timedelta(minutes=60))
    result = await context.plan(intent(absences=(absence,)))
    assert result["status"] in ("optimal", "feasible"), result
    assert result["execution_allowed"] is False
    assert result["result"]["candidate_room_temperature_c"][4] >= 19.5 - 1e-5
