"""Native measurement/prediction to shadow-plan integration without an EMS or server."""

import asyncio
from datetime import datetime, timedelta
from threading import Event
from unittest.mock import Mock

import pytest
from conftest import THERMAL_NOW, THERMAL_START, bundle_data
from pydantic import ValidationError

from akkudoktoreos.core import coreabc
from akkudoktoreos.prediction import presence as presence_module
from akkudoktoreos.prediction.presence import PresenceEstimate
from akkudoktoreos.thermal import thermalconfigrequest as native
from akkudoktoreos.thermal.thermalconfigrequest import (
    ThermalConfigRequest,
    ThermalDataBundle,
)
from akkudoktoreos.utils.datetimeutil import to_datetime

NOW = THERMAL_NOW
START = THERMAL_START


def configure_distribution(context, kind, *, observer=False, external=False, with_buffer=False):
    """Use explicit synthetic thermal ratings and independently declared nodes."""
    context.config.merge_settings_from_dict(
        {"thermal": {"zones": {"area1": {"model_parameters": None}}}}
    )
    water = kind == "water"
    distribution = (
        {
            "kind": "water",
            "emitter_type": "underfloor",
            "water_capacity_j_per_k": 1000000.0,
            "emitter_capacity_j_per_k": 100000.0,
            "water_to_emitter_conductance_w_per_k": 150.0,
            "emitter_to_room_conductance_w_per_k": 50.0,
            "water_temperature_bounds": {"min_temperature_c": 5.0, "max_temperature_c": 60.0},
            "emitter_temperature_bounds": {"min_temperature_c": 5.0, "max_temperature_c": 60.0},
            "source_mass_flow_kg_per_s": 0.1,
            "fluid_specific_heat_j_per_kg_k": 4180.0,
            "pump_electric_power_w": 30.0 if external else 0.0,
            "pump_electricity_included_in_cop": False if external else True,
            "circulation_operation": "fixed_entire_horizon",
        }
        if water
        else {
            "kind": "air",
            "air_capacity_j_per_k": 300000.0,
            "air_temperature_bounds": {"min_temperature_c": 5.0, "max_temperature_c": 60.0},
            "fan_levels": [
                {
                    "level_id": "low",
                    "conductance_w_per_k": 150.0,
                    "electric_power_w": 30.0 if external else 0.0,
                    "electricity_included_in_cop": not external,
                },
                {
                    "level_id": "high",
                    "conductance_w_per_k": 300.0,
                    "electric_power_w": 40.0 if external else 0.0,
                    "electricity_included_in_cop": not external,
                },
            ],
            "default_fan_level_id": "low",
            "circulation_operation": "fixed_entire_horizon",
        }
    )
    if water and with_buffer:
        distribution["buffer"] = {
            "capacity_j_per_k": 1000000.0,
            "temperature_bounds": {"min_temperature_c": 5.0, "max_temperature_c": 60.0},
            "transfer_conductance_w_per_k": 100.0,
        }
    bindings = (
        {"water_temperature_measurement_key": "water"}
        if water
        else {"air_temperature_measurement_key": "near"}
    )
    if water and not observer:
        bindings["emitter_temperature_measurement_key"] = "emitter"
    if with_buffer:
        bindings["buffer_temperature_measurement_key"] = "buffer"
    delivery = 50.0 if water else 20.0
    context.config.merge_settings_from_dict(
        {
            "thermal": {
                "zones": {
                    "area1": {
                        "model_parameters": {
                            "room_loss_per_hour": 0.05,
                            "room_mass_exchange_per_hour": 0.12,
                            "mass_capacity_ratio": 4.0,
                            "heat_gain_kelvin_per_kwh": 1.0,
                        },
                        "distribution": distribution,
                        "distribution_measurements": bindings,
                        "distribution_observer": {"maximum_emitter_uncertainty_c": 0.01}
                        if observer
                        else None,
                    }
                }
            },
            "devices": {
                "heat_sources": {
                    "source1": {
                        "kind": "heat_pump",
                        "heat_pump": {
                            "delivery_temperature_c": delivery,
                            "electrical": {
                                "standby_power_w": 0.0,
                                "running_auxiliary_power_w": 0.0,
                                "startup_energy_wh": 0.0,
                                "extras_are_outside_cop": True,
                            },
                            "custom_profile": {
                                "profile_id": "synthetic-distribution",
                                "heat_pump_type": "air_to_water" if water else "air_to_air",
                                "cop_points": [
                                    {
                                        "outdoor_temperature_c": 5.0,
                                        "delivery_temperature_c": delivery,
                                        "cop": 4.0,
                                    }
                                ],
                                "thermal_max_points": [
                                    {
                                        "outdoor_temperature_c": 5.0,
                                        "delivery_temperature_c": delivery,
                                        "thermal_power_w": 5000.0,
                                    }
                                ],
                                "thermal_min_points": [
                                    {
                                        "outdoor_temperature_c": 5.0,
                                        "delivery_temperature_c": delivery,
                                        "thermal_power_w": 500.0,
                                    }
                                ],
                                "cop_includes_defrost": True,
                                "provenance": {
                                    "source": "Synthetic explicit distribution test",
                                    "quality": "user_supplied",
                                    "electric_power_basis": "total_system",
                                },
                            },
                        },
                    }
                }
            },
            "measurement": {
                "channels": {
                    key: {"quantity": "temperature", "unit": "degC"}
                    for key in ("near", "water", "emitter", "buffer", "delivery")
                }
            },
        }
    )
    data = bundle_data()
    for minute in range(121):
        for key, value in (("water", 40.0), ("emitter", 35.0)) if water else (("near", 25.0),):
            if not (observer and key == "emitter"):
                data["samples"].append(
                    {"date_time": NOW - timedelta(minutes=minute), "key": key, "value": value}
                )
    if external:
        data["distribution_load_excluded_zone_ids"] = ["area1"]
    if with_buffer:
        data["samples"].append({"date_time": NOW, "key": "buffer", "value": 45.0})
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["air", "water"])
async def test_native_distribution_projects_full_state_and_keeps_config_unchanged(context, kind):
    data = configure_distribution(context, kind)
    before = context.config.model_dump_json(exclude_computed_fields=True)
    await context.ingest(ThermalDataBundle.model_validate(data))
    request, diagnostics = await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert request.distribution.kind == kind
    assert len(request.distribution_state) == (1 if kind == "air" else 2)
    assert request.distribution_observed_at == NOW
    assert diagnostics["state"]["full_state_projected_together"] is True
    assert diagnostics["state"]["projection_seconds"] == 480
    assert diagnostics["distribution_kind"] == kind
    assert context.config.model_dump_json(exclude_computed_fields=True) == before
    if kind == "water":
        assert request.distribution_state[0] > 40.0
        assert request.distribution_state[1] > 35.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["brine_to_water", "water_to_water", "brine_to_air", "water_to_air"]
)
async def test_native_non_air_source_keeps_source_separate(context, kind):
    data = configure_distribution(context, "water" if kind.endswith("_to_water") else "air")
    context.config.merge_settings_from_dict(
        {
            "devices": {
                "heat_sources": {
                    "source1": {
                        "heat_pump": {
                            "source_temperature_c": 5.0,
                            "custom_profile": {"heat_pump_type": kind},
                        }
                    }
                }
            }
        }
    )
    await context.ingest(ThermalDataBundle.model_validate(data))
    request, _ = await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert request.source_temperature_c == (5.0,) * len(request.weather)
    assert request.heat_pump_profile.heat_pump_type == kind
    assert request.building.heat_gain_kelvin_per_kwh == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("electric,expected_heat", [(3000.0, 2000.0), (0.0, 0.0)])
async def test_delivered_heat_channel_avoids_inverting_an_unsupported_power_snapshot(
    context, electric, expected_heat
):
    data = configure_distribution(context, "air")
    context.config.merge_settings_from_dict(
        {
            "devices": {
                "heat_sources": {
                    "source1": {
                        "thermal_power_measurement_key": "delivered_heat",
                        "thermal_power_is_estimated": True,
                        "thermal_power_idle_electric_threshold_w": 100.0,
                    }
                }
            },
            "measurement": {
                "channels": {
                    "delivered_heat": {
                        "quantity": "power",
                        "unit": "W",
                        "integration_method": "hold",
                        "max_gap_seconds": 180,
                    }
                }
            },
        }
    )
    data["samples"].append(dict(date_time=NOW, key="delivered_heat", value=2000.0))
    data["samples"] = [
        r for r in data["samples"] if not (r["key"] == "heat" and r["date_time"] == NOW)
    ]
    data["samples"].append(dict(date_time=NOW, key="heat", value=electric))
    await context.ingest(ThermalDataBundle.model_validate(data))
    request, diagnostics = await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert diagnostics["calibration"]["delivered_heat_is_measured"] is False
    assert (
        diagnostics["calibration"]["thermal_input_basis"]
        == "Bound thermal power channel; estimated"
    )
    assert request.building.heat_gain_kelvin_per_kwh == 1.0
    assert diagnostics["calibration"]["current_thermal_input_w"] == expected_heat


@pytest.mark.asyncio
async def test_native_water_reaches_pure_shadow_solver(context):
    await context.ingest(ThermalDataBundle.model_validate(configure_distribution(context, "water")))
    result = await context.plan(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert result["status"] in ("optimal", "feasible"), result
    assert result["execution_allowed"] is False
    assert set(result["result"]["reference_distribution_temperatures_c"]) == {
        "water_temperature_c",
        "emitter_temperature_c",
    }


@pytest.mark.asyncio
async def test_distribution_diagnostics_measure_age_from_request_time(context, monkeypatch):
    await context.ingest(ThermalDataBundle.model_validate(configure_distribution(context, "air")))
    monkeypatch.setattr(native, "thermal_now", lambda: NOW + timedelta(seconds=20))
    _, diagnostics = await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    for name in ("room", "power", "outdoor"):
        assert diagnostics["state"][f"{name}_observation_age_seconds"] == 20.0
    assert diagnostics["preheat_lead_basis"].startswith("Coupled distribution/building")


@pytest.mark.asyncio
async def test_native_slab_observer_is_optional_bounded_and_not_a_measurement(context):
    data = configure_distribution(context, "water", observer=True)
    await context.ingest(ThermalDataBundle.model_validate(data))
    request, diagnostics = await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    rows = diagnostics["state"]["distribution_states"]
    emitter = next(row for row in rows if row["state"] == "emitter_temperature_c")
    assert emitter["estimated"] is True
    assert emitter["observer"]["remaining_initial_uncertainty_c"] < 0.01
    assert 35.0 < request.distribution_state[1] < 36.0


@pytest.mark.asyncio
async def test_native_observer_rejects_insufficient_remaining_uncertainty(context):
    data = configure_distribution(context, "water", observer=True)
    context.config.merge_settings_from_dict(
        {
            "thermal": {
                "zones": {
                    "area1": {"distribution_observer": {"maximum_emitter_uncertainty_c": 1e-10}}
                }
            }
        }
    )
    await context.ingest(ThermalDataBundle.model_validate(data))
    with pytest.raises(ValueError, match="uncertainty"):
        await context._resolve(
            ThermalConfigRequest(
                zone_id="area1", heat_source_id="source1", basis_control_revision=7
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "stale",
        "estimated",
        "wrong_unit",
        "unknown_calibration",
        "wrong_source",
        "unsupported_delivery",
    ],
)
async def test_native_distribution_does_not_fake_states_or_rating_support(context, problem):
    data = configure_distribution(context, "water")
    water_key = "water_" + problem
    context.config.merge_settings_from_dict(
        {
            "thermal": {
                "zones": {
                    "area1": {
                        "distribution_measurements": {
                            "water_temperature_measurement_key": water_key
                        }
                    }
                }
            },
            "measurement": {"channels": {water_key: {"quantity": "temperature", "unit": "degC"}}},
        }
    )
    for row in data["samples"]:
        if row["key"] == "water":
            row["key"] = water_key
    if problem == "missing":
        data["samples"] = [r for r in data["samples"] if r["key"] != water_key]
    elif problem == "stale":
        data["samples"] = [
            r
            for r in data["samples"]
            if r["key"] != water_key or r["date_time"] < NOW - timedelta(seconds=180)
        ]
    elif problem == "estimated":
        for row in data["samples"]:
            if row["key"] == water_key:
                row["quality"] = {"status": "estimated"}
    elif problem == "wrong_unit":
        context.config.merge_settings_from_dict(
            {
                "measurement": {
                    "channels": {
                        water_key: {
                            "quantity": "power",
                            "unit": "W",
                            "integration_method": "hold",
                            "max_gap_seconds": 180,
                        }
                    }
                }
            }
        )
    elif problem == "unknown_calibration":
        context.config.merge_settings_from_dict(
            {"thermal": {"zones": {"area1": {"model_parameters": None}}}}
        )
    elif problem == "wrong_source":
        context.config.merge_settings_from_dict(
            {
                "devices": {
                    "heat_sources": {
                        "source1": {
                            "heat_pump": {"custom_profile": {"heat_pump_type": "air_to_air"}}
                        }
                    }
                }
            }
        )
    else:
        context.config.merge_settings_from_dict(
            {
                "devices": {
                    "heat_sources": {"source1": {"heat_pump": {"delivery_temperature_c": 35.0}}}
                }
            }
        )
    await context.ingest(ThermalDataBundle.model_validate(data))
    with pytest.raises(ValueError):
        await context._resolve(
            ThermalConfigRequest(
                zone_id="area1", heat_source_id="source1", basis_control_revision=7
            )
        )


@pytest.mark.asyncio
async def test_native_external_distribution_ack_and_fan_horizon_are_explicit(context):
    data = configure_distribution(context, "air", external=True)
    data["distribution_fan_levels"] = {"area1": ["high"] * 8}
    await context.ingest(ThermalDataBundle.model_validate(data))
    request, _ = await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert request.distribution_load_basis == "residual_excludes_external_distribution"
    assert request.distribution_fan_levels == ("high",) * 8
    data["distribution_load_excluded_zone_ids"] = []
    await context.ingest(ThermalDataBundle.model_validate(data))
    result = await context.plan(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert result["status"] == "unsupported"
    assert result["result"]["comparison_qualified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["air", "water"])
async def test_native_external_circulation_power_is_added_once_after_explicit_ack(context, kind):
    data = configure_distribution(context, kind, external=True)
    if kind == "air":
        data["distribution_fan_levels"] = {"area1": ["high"] * 8}
    await context.ingest(ThermalDataBundle.model_validate(data))
    reply = await context.plan(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert reply["status"] in ("optimal", "feasible"), reply
    result = reply["result"]
    expected = 40.0 if kind == "air" else 30.0
    assert result["distribution_electric_power_w"] == [expected] * 8
    assert result["reference"]["parameters"]["load_w"] == pytest.approx(
        [300.0 + p + expected for p in result["reference_source_electric_power_w"]]
    )


@pytest.mark.asyncio
async def test_explicit_current_delivery_sensor_must_be_fresh_and_supported(context):
    data = configure_distribution(context, "water")
    context.config.merge_settings_from_dict(
        {
            "devices": {
                "heat_sources": {
                    "source1": {"heat_pump": {"delivery_temperature_measurement_key": "delivery"}}
                }
            }
        }
    )
    data["samples"].append({"date_time": NOW, "key": "delivery", "value": 60.0})
    await context.ingest(ThermalDataBundle.model_validate(data))
    with pytest.raises(ValueError, match="supported"):
        await context._resolve(
            ThermalConfigRequest(
                zone_id="area1", heat_source_id="source1", basis_control_revision=7
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unknown_zone", "unknown_fan", "unused_ack"])
async def test_invalid_distribution_bundle_does_not_write_native_data(context, kind):
    data = configure_distribution(context, "air")
    if kind == "unknown_zone":
        data["distribution_fan_levels"] = {"missing": ["low"] * 8}
    elif kind == "unknown_fan":
        data["distribution_fan_levels"] = {"area1": ["missing"] * 8}
    else:
        data["distribution_load_excluded_zone_ids"] = ["missing"]
    with pytest.raises(ValueError):
        await context.ingest(ThermalDataBundle.model_validate(data))
    assert context.status()["generation"] == 0


@pytest.mark.asyncio
async def test_future_extra_sensor_value_is_not_backfilled_into_initial_state(context):
    data = configure_distribution(context, "air")
    await context.ingest(ThermalDataBundle.model_validate(data))
    intent = ThermalConfigRequest(
        zone_id="area1", heat_source_id="source1", basis_control_revision=7
    )
    before, _ = await context._resolve(intent)
    data["samples"].append({"date_time": NOW + timedelta(seconds=1), "key": "near", "value": 59.0})
    await context.ingest(ThermalDataBundle.model_validate(data))
    after, _ = await context._resolve(intent)
    assert after.distribution_state == before.distribution_state


@pytest.mark.asyncio
async def test_native_optional_buffer_is_a_fresh_separate_state(context):
    data = configure_distribution(context, "water", with_buffer=True)
    await context.ingest(ThermalDataBundle.model_validate(data))
    request, _ = await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert len(request.distribution_state) == 3
    assert request.distribution_state[2] > 45.0


@pytest.mark.asyncio
async def test_emitter_measurement_takes_precedence_over_the_bounded_observer(context, monkeypatch):
    data = configure_distribution(context, "water", observer=True)
    context.config.merge_settings_from_dict(
        {
            "thermal": {
                "zones": {
                    "area1": {
                        "distribution_measurements": {
                            "emitter_temperature_measurement_key": "measured_emitter"
                        }
                    }
                }
            },
            "measurement": {
                "channels": {"measured_emitter": {"quantity": "temperature", "unit": "degC"}}
            },
        }
    )
    data["samples"].append({"date_time": NOW, "key": "measured_emitter", "value": 36.0})
    await context.ingest(ThermalDataBundle.model_validate(data))
    observer = Mock(side_effect=AssertionError("Measured state must take precedence"))
    monkeypatch.setattr(native, "estimate_emitter_temperature", observer)
    _, diagnostics = await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    row = next(
        row
        for row in diagnostics["state"]["distribution_states"]
        if row["state"] == "emitter_temperature_c"
    )
    assert row["estimated"] is False
    assert row["measurement_key"] == "measured_emitter"
    observer.assert_not_called()






def configure_presence_requirements(context, monkeypatch, required, *, coasting=False):
    """Stub only the forecast, retaining the real native comfort/model resolver."""
    context.config.merge_settings_from_dict(
        {
            "presence": {
                "provider": "PresenceHistory",
                "zones": {"presence1": {"measurement_key": "present"}},
            },
            "measurement": {"channels": {"present": {"quantity": "presence", "unit": "1"}}},
            "thermal": {
                "planning_time_limit_seconds": 7.5,
                "zones": {
                    "area1": {
                        "presence_area_id": "presence1",
                        "allow_warm_coasting": coasting,
                        "unoccupied_comfort": {
                            "min_temperature_c": 16.0,
                            "target_temperature_c": 17.0,
                            "max_temperature_c": 18.0,
                        },
                    }
                },
            },
        }
    )
    estimates = [
        PresenceEstimate(
            date_time=START + timedelta(minutes=15 * i),
            probability=float(needed),
            require_comfort=needed,
            source="history",
        )
        for i, needed in enumerate(required)
    ]
    monkeypatch.setattr(presence_module, "forecast_presence", lambda *args, **kwargs: estimates)
    return estimates


@pytest.mark.asyncio
async def test_presence_is_published_by_ingestion_without_any_heating_plan(context):
    context.config.merge_settings_from_dict(
        {
            "presence": {
                "provider": "PresenceHistory",
                "zones": {"presence1": {"measurement_key": "present"}},
            },
            "measurement": {"channels": {"present": {"quantity": "presence", "unit": "1"}}},
        }
    )
    data = bundle_data()
    for minute in range(15):
        data["samples"].append(
            {
                "key": "present",
                "date_time": START - timedelta(days=1) + timedelta(minutes=minute),
                "value": 1.0,
                "quality": {"status": "measured"},
            }
        )
    response = await context.ingest(ThermalDataBundle.model_validate(data))
    assert context.status()["last_plan"] is None
    first = response["presence_forecasts"]["presence1"][0]
    assert first["historical_probability"] == 1.0
    assert first["history_is_provisional"]
    assert first["support_days"] == 1
    assert response["presence_issued_at"] == NOW.isoformat()
    provider = context.prediction.provider_by_id("PresenceHistory")
    _, values = await provider.key_to_lists(
        "presence_baseline_presence1", start_datetime=to_datetime(START)
    )
    assert values[0] == 1.0


@pytest.mark.asyncio
async def test_native_electric_block_constraint_can_be_disabled_independently(context):
    context.config.merge_settings_from_dict({"thermal": {"electric_power_block_seconds": None}})
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    request, _ = await context._resolve(ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7))
    assert request.smoothing_seconds is None


@pytest.mark.asyncio
async def test_plan_respects_same_preheat_ceiling_as_executor(context):
    context.config.merge_settings_from_dict({"thermal": {"tracking": {
        "enabled": True, "operation": "continuous", "zone_id": "area1",
        "max_increase_c": 3, "maximum_preheat_temperature_c": 21,
    }}})
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    request, _ = await context._resolve(ThermalConfigRequest(
        zone_id="area1", heat_source_id="source1", basis_control_revision=7))
    assert all(band.max_temperature_c <= 21 for band in request.comfort_bands)


@pytest.mark.asyncio
@pytest.mark.parametrize("known", [True, False])
async def test_runtime_routine_only_fills_unknown_presence(context, monkeypatch, known):
    estimates = configure_presence_requirements(context, monkeypatch, [True] * 8)
    if not known:
        estimates[:] = [estimate.model_copy(update={"source": "unknown"}) for estimate in estimates]
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    request, diagnostics = await context._resolve(
        ThermalConfigRequest(
            zone_id="area1",
            heat_source_id="source1",
            basis_control_revision=7,
            comfort_windows_policy="presence_fallback",
            comfort_windows=tuple(
                native.TemperatureInterval(
                    start_datetime=START + timedelta(minutes=15 * i),
                    end_datetime=START + timedelta(minutes=15 * (i + 1)),
                    min_temperature_c=18,
                    target_temperature_c=18.5,
                    max_temperature_c=22,
                )
                for i in range(8)
            ),
        )
    )
    assert diagnostics["runtime_comfort_windows_policy"] == "presence_fallback"
    assert request.comfort_bands[0].min_temperature_c == (19.5 if known else 18)


@pytest.mark.asyncio
async def test_native_initial_absence_and_return_keep_model_lead_and_occupied_deadline(
    context, monkeypatch
):
    required = [False] * 4 + [True] * 4
    configure_presence_requirements(context, monkeypatch, required, coasting=True)
    data = bundle_data()
    for row in data["samples"]:
        if row["key"] == "room":
            row["value"] = 17.5
    await context.ingest(ThermalDataBundle.model_validate(data))
    request, diagnostics = await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    occupied = context.config.thermal.zones["area1"].comfort
    assert request.planning_time_limit_seconds == 7.5
    assert diagnostics["preheat_lead_seconds"] > 0
    assert diagnostics["presence_warm_coasting_enabled"] is True
    assert diagnostics["presence_absence_band_adjustments"]
    assert all(b.min_temperature_c == 16.0 for b in request.comfort_bands[:4])
    assert all(b == occupied for b in request.comfort_bands[4:])
    assert all(b.target_temperature_c == 20.0 for b in request.comfort_bands[:4])
    result = await context.plan(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert result["status"] in ("optimal", "feasible"), result
    assert result["execution_allowed"] is False
    assert result["result"]["candidate_room_temperature_c"][4] >= 19.5 - 1e-5
    assert result["result"]["candidate_room_temperature_c"][4] <= 20.5 + 1e-5


@pytest.mark.asyncio
@pytest.mark.parametrize("coasting", [False, True])
async def test_native_departure_only_relaxes_away_upper_bound_when_declared(
    context, monkeypatch, coasting
):
    configure_presence_requirements(
        context, monkeypatch, [True] * 2 + [False] * 6, coasting=coasting
    )
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    request, diagnostics = await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    occupied = context.config.thermal.zones["area1"].comfort
    assert all(b == occupied for b in request.comfort_bands[:2])
    assert all(b.min_temperature_c == 16 for b in request.comfort_bands[2:])
    assert all(b.target_temperature_c == 17 for b in request.comfort_bands[2:])
    assert all(b.max_temperature_c == (20.5 if coasting else 18) for b in request.comfort_bands[2:])
    assert diagnostics["presence_warm_coasting_enabled"] is coasting
    assert len(diagnostics["presence_absence_band_adjustments"]) == (6 if coasting else 0)
    result = await context.plan(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert (
        result["status"] in ("optimal", "feasible")
        if coasting
        else result["status"] == "infeasible"
    )
    assert result["execution_allowed"] is False


@pytest.mark.asyncio
async def test_native_unknown_presence_keeps_occupied_band_despite_coasting(context, monkeypatch):
    estimates = configure_presence_requirements(context, monkeypatch, [False] * 8, coasting=True)
    estimates[:] = [row.model_copy(update={"source": "unknown"}) for row in estimates]
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    request, diagnostics = await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert all(b == context.config.thermal.zones["area1"].comfort for b in request.comfort_bands)
    assert diagnostics["presence_absence_band_adjustments"] == []
    assert request.recovery.max_duration_seconds == 14400
    assert not request.recovery.allow_passive_cooldown
    assert diagnostics["first_required_comfort_time"] == START.isoformat()
    assert diagnostics["initial_recovery_presence_deadline"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [19.0, 22.0])
async def test_manual_goal_uses_explicit_recovery_and_preserves_control_revision(context, target):
    bundle = bundle_data()
    bundle["control_revision"] = 8
    await context.ingest(ThermalDataBundle.model_validate(bundle))
    request, _ = await context._resolve(
        ThermalConfigRequest(
            zone_id="area1",
            heat_source_id="source1",
            basis_control_revision=8,
            target_temperature_c=target,
        )
    )
    assert request.recovery.allow_passive_cooldown
    assert request.basis_control_revision == 8
    assert all(b.target_temperature_c == target for b in request.comfort_bands)


@pytest.mark.asyncio
@pytest.mark.parametrize("coasting", [False, True])
async def test_native_initially_absent_warm_room_is_not_forced_to_cool_instantly(
    context, monkeypatch, coasting
):
    configure_presence_requirements(context, monkeypatch, [False] * 8, coasting=coasting)
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    result = await context.plan(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    if coasting:
        assert result["status"] in ("optimal", "feasible"), result
        assert result["result"]["candidate_room_temperature_c"][0] > 18.0
        assert len(result["diagnostics"]["presence_absence_band_adjustments"]) == 8
    else:
        assert result["status"] == "infeasible"
        assert result["result"]["plan"] is None
    assert result["execution_allowed"] is False


@pytest.mark.asyncio
async def test_native_initial_cold_recovery_cannot_waive_a_later_occupied_deadline(
    context, monkeypatch
):
    configure_presence_requirements(context, monkeypatch, [False] + [True] * 7, coasting=True)
    data = bundle_data()
    for row in data["samples"]:
        if row["key"] == "room":
            row["value"] = 10.0
    await context.ingest(ThermalDataBundle.model_validate(data))
    request, diagnostics = await context._resolve(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert request.recovery.max_duration_seconds == 900
    assert diagnostics["recovery_limit_basis"].startswith("Earlier of first required comfort")
    assert (
        diagnostics["initial_recovery_presence_deadline"]
        == (START + timedelta(minutes=15)).isoformat()
    )
    assert request.comfort_bands[1] == context.config.thermal.zones["area1"].comfort
    result = await context.plan(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert result["status"] == "infeasible", result
    assert result["result"]["plan"] is None
    assert result["execution_allowed"] is False


@pytest.mark.asyncio
async def test_native_end_to_end_uses_measurement_weather_and_config_without_ems(
    context, monkeypatch
):
    forbidden_ems = Mock(side_effect=AssertionError("No operational EMS access"))
    forbidden_adapter = Mock(side_effect=AssertionError("No device communication"))
    monkeypatch.setattr(coreabc, "get_ems", forbidden_ems)
    monkeypatch.setattr(coreabc, "get_adapter", forbidden_adapter)
    config_before = context.config.model_dump_json(exclude_computed_fields=True)
    admitted = await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    assert admitted["samples_imported"] == 363
    weather = context.prediction.provider_by_id("WeatherImport")
    dates, temperatures = await weather.key_to_lists(
        "weather_temp_air", start_datetime=to_datetime(START)
    )
    assert dates[0] == START
    assert temperatures == [5.0] * 8
    result = await context.plan(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert result["status"] in ("optimal", "feasible")
    assert result["result"]["comparison_qualified"]
    assert result["execution_allowed"] is False
    assert result["diagnostics"]["state"]["mass_is_measured"] is False
    assert result["diagnostics"]["first_required_comfort_time"] == START.isoformat()
    assert context.config.model_dump_json(exclude_computed_fields=True) == config_before
    forbidden_ems.assert_not_called()
    forbidden_adapter.assert_not_called()


@pytest.mark.asyncio
async def test_stale_snapshot_and_manual_revision_fail_closed(context):
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    with pytest.raises(ValueError, match="revision"):
        await context.plan(
            ThermalConfigRequest(
                zone_id="area1", heat_source_id="source1", basis_control_revision=8
            )
        )
    stale = bundle_data()
    stale["sampled_at"] = NOW - timedelta(hours=1)
    stale["forecast_issued_at"] = NOW - timedelta(hours=1)
    stale["samples"] = []
    with pytest.raises(ValueError, match="stale"):
        await context.ingest(ThermalDataBundle.model_validate(stale))
    assert context.status()["generation"] == 1


@pytest.mark.asyncio
async def test_user_off_returns_no_heating_proposal(context):
    result = await context.plan(
        ThermalConfigRequest(
            zone_id="area1", heat_source_id="source1", basis_control_revision=7, master_power="Off"
        )
    )
    assert result["status"] == "disabled_by_user"
    assert result["result"] is None


@pytest.mark.parametrize("value", [True, "not-a-temperature", float("nan"), float("inf")])
def test_bundle_rejects_bad_native_weather_as_validation_error(value):
    data = bundle_data()
    data["weather"][0]["weather_temp_air"] = value
    with pytest.raises(ValidationError):
        ThermalDataBundle.model_validate(data)


@pytest.mark.asyncio
async def test_unknown_sample_batch_does_not_replace_context(context):
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    data = bundle_data()
    data["samples"] = [{"date_time": NOW, "key": "unknown-key", "value": 1.0}]
    with pytest.raises(ValueError):
        await context.ingest(ThermalDataBundle.model_validate(data))
    assert context.status()["generation"] == 1


@pytest.mark.asyncio
async def test_wrong_source_units_are_rejected_before_planning(context):
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    context.config.merge_settings_from_dict(
        {
            "measurement": {
                "channels": {
                    "heat": {
                        "quantity": "power",
                        "unit": "kW",
                        "integration_method": "hold",
                        "max_gap_seconds": 180,
                    }
                }
            }
        }
    )
    with pytest.raises(ValueError, match="power/W"):
        await context.plan(
            ThermalConfigRequest(
                zone_id="area1", heat_source_id="source1", basis_control_revision=7
            )
        )


@pytest.mark.asyncio
async def test_manual_off_supersedes_a_busy_shadow_solve(context, monkeypatch):
    from akkudoktoreos.thermal.thermaloptimization import ThermalShadowSolution

    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    started, release = Event(), Event()

    def blocked(request):
        started.set()
        if not release.wait(5):
            raise ValueError("Test release missing")
        return ThermalShadowSolution(status="limit", reason="Synthetic interrupted comparison")

    monkeypatch.setattr(native, "optimize_thermal_shadow", blocked)
    intent = ThermalConfigRequest(
        zone_id="area1", heat_source_id="source1", basis_control_revision=7
    )
    task = asyncio.create_task(context.plan(intent))
    assert await asyncio.to_thread(started.wait, 3)
    try:
        off = await context.plan(intent.model_copy(update={"master_power": "Off"}))
        assert off["status"] == "disabled_by_user"
    finally:
        release.set()
    previous = await task
    assert previous["status"] == "superseded"
    assert context.status()["last_plan"]["status"] == "disabled_by_user"


@pytest.mark.asyncio
async def test_native_heat_pump_segments_reach_the_solver(context):
    context.config.merge_settings_from_dict(
        {"thermal": {"zones": {"area1": {"model_parameters": None}}}}
    )
    context.config.merge_settings_from_dict(
        {
            "thermal": {
                "zones": {
                    "area1": {
                        "model_parameters": {
                            "room_loss_per_hour": 0.05,
                            "room_mass_exchange_per_hour": 0.12,
                            "mass_capacity_ratio": 4.0,
                            "heat_gain_kelvin_per_kwh": 1.0,
                        }
                    }
                }
            },
            "devices": {
                "heat_sources": {
                    "source1": {
                        "kind": "heat_pump",
                        "heat_pump": {
                            "delivery_temperature_c": 20,
                            "electrical": {
                                "standby_power_w": 0,
                                "running_auxiliary_power_w": 0,
                                "startup_energy_wh": 0,
                                "extras_are_outside_cop": True,
                            },
                            "custom_profile": {
                                "profile_id": "synthetic-segment-map",
                                "heat_pump_type": "air_to_air",
                                "cop_points": [
                                    {
                                        "outdoor_temperature_c": 5,
                                        "delivery_temperature_c": 20,
                                        "cop": 4,
                                    }
                                ],
                                "thermal_max_points": [
                                    {
                                        "outdoor_temperature_c": 5,
                                        "delivery_temperature_c": 20,
                                        "thermal_power_w": 5000,
                                    }
                                ],
                                "thermal_min_points": [
                                    {
                                        "outdoor_temperature_c": 5,
                                        "delivery_temperature_c": 20,
                                        "thermal_power_w": 500,
                                    }
                                ],
                                "cop_includes_defrost": True,
                                "provenance": {
                                    "source": "Synthetic paired test points",
                                    "quality": "user_supplied",
                                    "electric_power_basis": "total_system",
                                },
                                "operating_points": [
                                    {
                                        "outdoor_temperature_c": 5,
                                        "delivery_temperature_c": 20,
                                        "thermal_power_w": q,
                                        "electric_power_w": p,
                                        "point_id": f"synthetic-{q}",
                                        "source": "Synthetic paired test point",
                                    }
                                    for q, p in ((500, 100), (3000, 750), (5000, 1500))
                                ],
                            },
                        },
                    }
                }
            },
        }
    )
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    result = await context.plan(
        ThermalConfigRequest(zone_id="area1", heat_source_id="source1", basis_control_revision=7)
    )
    assert result["status"] in ("optimal", "feasible")
    assert result["diagnostics"]["calibration"]["delivered_heat_is_measured"] is False
    assert result["diagnostics"]["state"]["model_input_power_override_used"]
    assert result["result"]["candidate_thermal_power_w"]


@pytest.mark.asyncio
async def test_manual_off_during_solver_run_keeps_disabled_state(context, monkeypatch):
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    real = native.optimize_thermal_shadow

    def solve_then_off(request):
        result = real(request)
        # Manual Off arrives while the solver is still running.
        context._intent_generation += 1
        context._last = {"status": "disabled_by_user", "result": None}
        return result

    monkeypatch.setattr(native, "optimize_thermal_shadow", solve_then_off)
    intent = ThermalConfigRequest(
        zone_id="area1", heat_source_id="source1", basis_control_revision=7
    )
    result = await context.plan(intent)
    assert result["status"] == "superseded"
    assert context.status()["last_plan"]["status"] == "disabled_by_user"


@pytest.mark.asyncio
async def test_manual_off_during_resolution_never_starts_worker(context, monkeypatch):
    from unittest.mock import Mock

    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    entered, release = asyncio.Event(), asyncio.Event()
    resolve = context._resolve

    async def paused(intent):
        entered.set()
        await release.wait()
        return await resolve(intent)

    monkeypatch.setattr(context, "_resolve", paused)
    worker = Mock(side_effect=AssertionError("Superseded work must not start"))
    monkeypatch.setattr(native, "optimize_thermal_shadow", worker)
    intent = ThermalConfigRequest(
        zone_id="area1", heat_source_id="source1", basis_control_revision=7
    )
    running = asyncio.create_task(context.plan(intent))
    await asyncio.wait_for(entered.wait(), 3)
    await context.plan(intent.model_copy(update={"master_power": "Off"}))
    release.set()
    result = await running
    assert result["status"] == "superseded"
    assert context.status()["last_plan"]["status"] == "disabled_by_user"
    worker.assert_not_called()


@pytest.mark.asyncio
async def test_presence_revision_is_attested_separately_from_manual_revision(context):
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    request, diagnostics = await context._resolve(
        ThermalConfigRequest(
            zone_id="area1",
            heat_source_id="source1",
            basis_control_revision=7,
            presence_revision=12,
        )
    )
    assert request.basis_control_revision == 7
    assert diagnostics["presence_revision"] == 12


@pytest.mark.asyncio
async def test_completion_after_origin_stays_rejected_without_retimestamping(context, monkeypatch):
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    solve = native.optimize_thermal_shadow

    def late_result(*args, **kwargs):
        result = solve(*args, **kwargs)
        monkeypatch.setattr(native, "thermal_now", lambda: START + timedelta(seconds=1))
        return result

    monkeypatch.setattr(native, "optimize_thermal_shadow", late_result)
    result = await context.plan(
        ThermalConfigRequest(
            zone_id="area1", heat_source_id="source1", basis_control_revision=7, presence_revision=2
        )
    )
    assert result["status"] == "superseded"
    assert "prospective origin" in result["reason"]
    assert datetime.fromisoformat(result["request"]["fleet"]["start_datetime"]) == START
    assert result["result"]["comparison_qualified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("rest,source,expected", [(True,"history",18),(False,"history",19.5),(True,"sensor_present",19.5),(True,"manual_comfort",19.5)])
async def test_declared_rest_overrides_history_but_not_confirmed_comfort(context,monkeypatch,rest,source,expected):
    estimates=configure_presence_requirements(context,monkeypatch,[True]*8)
    estimates[:]=[v.model_copy(update={"source":source}) for v in estimates]
    await context.ingest(ThermalDataBundle.model_validate(bundle_data()))
    request,diagnostics=await context._resolve(ThermalConfigRequest(zone_id="area1",heat_source_id="source1",basis_control_revision=7,comfort_windows_policy="economic_setback",rest_slot_mask=(rest,)*8,comfort_windows=tuple(native.TemperatureInterval(start_datetime=START+timedelta(minutes=15*i),end_datetime=START+timedelta(minutes=15*(i+1)),min_temperature_c=18,target_temperature_c=18.5,max_temperature_c=22) for i in range(8))))
    assert request.comfort_bands[0].min_temperature_c==expected
