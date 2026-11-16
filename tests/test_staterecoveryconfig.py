"""Zone settings reach the state estimate and the recovery policy of a native request (default: unchanged)."""

import pytest
from conftest import bundle_data

from akkudoktoreos.thermal.thermalconfigrequest import (
    ThermalConfigRequest,
    ThermalDataBundle,
)


async def resolve(context):
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    return await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )


@pytest.mark.asyncio
async def test_default_zone_has_neither_smoothing_nor_rate(context):
    request, diagnostics = await resolve(context)
    assert request.recovery.max_heating_rate_kelvin_per_hour is None
    assert "max_heating_rate" not in request.model_dump_json()
    assert not any(key.startswith("room_state_") for key in diagnostics["state"])


@pytest.mark.asyncio
async def test_zone_values_are_passed_through(context):
    context.config.merge_settings_from_dict(
        {
            "thermal": {
                "zones": {
                    "area1": {
                        "state_room_observation_window_seconds": 900,
                        "recovery_max_heating_rate_kelvin_per_hour": 0.5,
                    }
                }
            }
        }
    )
    request, diagnostics = await resolve(context)
    assert request.recovery.max_heating_rate_kelvin_per_hour == 0.5
    state = diagnostics["state"]
    assert state["room_state_window_seconds"] == 900
    assert state["room_state_smoothing_used"] is True
    assert state["room_state_window_values"] >= 3
    assert state["room_state_raw_c"] == pytest.approx(20.0)
    assert state["room_state_smoothed_c"] == pytest.approx(20.0)
