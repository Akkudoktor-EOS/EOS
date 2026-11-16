"""Independent target-only pilot admission and lease boundary tests."""

from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from akkudoktoreos.server.rest import thermaltracking as api
from akkudoktoreos.thermal.thermalmodels import ThermalPlan
from akkudoktoreos.thermal.thermalsettings import ThermalCommonSettings
from akkudoktoreos.thermal.thermaltracking import (
    ThermalTrackingPolicy,
    ThermalTrackingRequest,
    ThermalTrackingSettings,
)

NOW = datetime(2026, 1, 10, 8, 5, tzinfo=timezone.utc)


def test_continuous_absence_can_coast_off_but_arrival_and_cold_room_revoke_off():
    policy = ThermalTrackingPolicy()
    config = settings(operation="continuous", absence_delay_seconds=60)
    native = plan(minimum=18, target=18.5, maximum=21.5)

    def sample(second, *, present=False, valid=True, room=21.1):
        now = NOW + timedelta(seconds=second)
        value = request(
            now=now,
            source_plan=native,
            pilot_expires_at=None,
            room_temperature_c=room,
            presence={
                "valid": valid,
                "present": present,
                "verified_at": now,
                "require_comfort": True,
            },
        )
        value = value.model_copy(
            update={"source": value.source.model_copy(update={"electric_power_w": (0.0, 0.0)})}
        )
        return policy.propose(config, value, now=now)

    assert sample(0).target_temperature_c == 20.7
    assert sample(59).target_temperature_c == 20.7
    absent = sample(60)
    assert absent.target_temperature_c == 18.5
    assert absent.heating_allowed is False
    assert absent.valid_until == NOW + timedelta(seconds=210)
    cold = sample(61, room=17.5)
    assert cold.heating_allowed is True
    assert cold.target_temperature_c == 18.5
    arrival = sample(62, present=True, room=18.0)
    assert arrival.target_temperature_c == 20.7
    assert arrival.heating_allowed is True
    assert sample(63, valid=False).target_temperature_c == 20.7


def test_pilot_requires_deadline_but_continuous_still_requires_current_plan():
    missing = request(pilot_expires_at=None)
    assert not ThermalTrackingPolicy().propose(settings(), missing, now=NOW).pilot_tracking_allowed
    continuous = settings(operation="continuous")
    accepted = ThermalTrackingPolicy().propose(continuous, missing, now=NOW)
    assert accepted.pilot_tracking_allowed
    assert accepted.valid_until <= NOW + timedelta(seconds=150)
    stale = request(now=NOW + timedelta(hours=1), pilot_expires_at=None)
    assert (
        not ThermalTrackingPolicy()
        .propose(continuous, stale, now=NOW + timedelta(hours=1))
        .pilot_tracking_allowed
    )


def test_continuous_never_turns_manual_off_back_on():
    value = request(pilot_expires_at=None)
    value = value.model_copy(
        update={"operator": value.operator.model_copy(update={"master_power": "Off"})}
    )
    decision = ThermalTrackingPolicy().propose(settings(operation="continuous"), value, now=NOW)
    assert not decision.pilot_tracking_allowed
    assert decision.reason == "manual_off"


@pytest.mark.parametrize(
    "goal, room, power, heating", [(19.0, 21.5, 0.0, False), (22.0, 19.0, 2000.0, True)]
)
def test_continuous_recovery_forwards_new_goal_and_preserves_occupied_comfort(
    goal, room, power, heating
):
    native = plan(minimum=goal - 0.3, target=goal, maximum=goal + 0.5)
    raw = request(source_plan=native, room_temperature_c=room, pilot_expires_at=None).model_dump()
    raw["operator"]["target_temperature_c"] = goal
    raw["source"].update(recovery_active=True, electric_power_w=(power, power))
    raw["presence"] = dict(valid=True, present=True, verified_at=NOW, require_comfort=True)
    value = ThermalTrackingPolicy().propose(
        settings(operation="continuous"), ThermalTrackingRequest.model_validate(raw), now=NOW
    )
    assert value.pilot_tracking_allowed
    assert value.target_temperature_c == goal
    assert value.min_temperature_c == pytest.approx(goal - 0.3)
    assert value.require_comfort and not value.absence_delay_satisfied
    assert value.heating_allowed is heating


def plan(
    *,
    issued=NOW - timedelta(minutes=5),
    start=NOW - timedelta(minutes=5),
    revision=1,
    target=21.3,
    minimum=20.5,
    maximum=21.5,
    basis=7,
) -> dict[str, Any]:
    return {
        "zone_id": "zone-a",
        "plan_id": f"plan-{revision}",
        "source": "EOS shadow",
        "revision": revision,
        "basis_control_revision": basis,
        "issued_at": issued,
        "valid_until": start + timedelta(minutes=25),
        "intervals": [
            {
                "start_datetime": start + timedelta(minutes=15 * k),
                "end_datetime": start + timedelta(minutes=15 * (k + 1)),
                "min_temperature_c": minimum,
                "target_temperature_c": target,
                "max_temperature_c": maximum,
            }
            for k in range(2)
        ],
    }


def request(*, now=NOW, source_plan=None, **updates) -> ThermalTrackingRequest:
    source_plan = plan() if source_plan is None else source_plan
    values = {
        "zone_id": "zone-a",
        "operator": {
            "master_power": "On",
            "control_mode": "mpc",
            "target_temperature_c": 21.0,
            "control_revision": 7,
            "observed_at": now,
        },
        "room_temperature_c": 21.1,
        "room_observed_at": now,
        "presence": {"valid": False},
        "source": {
            "status": "feasible",
            "issued_at": source_plan["issued_at"],
            "plan": source_plan,
            "physical_checks_passed": True,
        },
        "pilot_expires_at": now + timedelta(minutes=90),
    }
    values.update(updates)
    return ThermalTrackingRequest.model_validate(values)


def settings(**updates) -> ThermalTrackingSettings:
    return ThermalTrackingSettings.model_validate({"enabled": True, "zone_id": "zone-a", **updates})


def test_continuous_preheat_uses_shared_absolute_ceiling():
    config = settings(operation="continuous", max_increase_c=3,
                      maximum_preheat_temperature_c=23)
    value = request(source_plan=plan(target=22.5, maximum=23.5))
    decision = ThermalTrackingPolicy().propose(config, value, now=NOW)
    assert decision.pilot_tracking_allowed
    assert decision.target_temperature_c == 22.5
    assert decision.max_temperature_c == 23
    assert config.maximum_for_target(25) == 25  # Explicit manual wish takes priority.


def test_configuration_defaults_remain_disabled_and_full_control_rejected():
    configured = ThermalCommonSettings()
    assert configured.tracking.enabled is False
    assert configured.mode == "off"
    value = ThermalTrackingPolicy().propose(ThermalTrackingSettings(), request(), now=NOW)
    assert value.reason == "pilot_disabled"
    assert value.pilot_tracking_allowed is False
    assert value.target_temperature_c == 21.0
    assert value.execution_allowed is False


@pytest.mark.parametrize(
    "updates",
    [
        {"enabled": True},
        {"lease_seconds": 301},
        {"lease_seconds": 0},
        {"lease_seconds": True},
        {"max_increase_c": 3.001},
        {"max_decrease_c": 0.501},
        {"enabled": "false"},
        {"max_increase_c": float("nan")},
    ],
)
def test_invalid_settings_are_rejected(updates):
    with pytest.raises(ValidationError):
        ThermalTrackingSettings.model_validate(updates)


def test_separate_bounded_permission_never_authorizes_battery_or_full_plan():
    value = ThermalTrackingPolicy().propose(settings(), request(), now=NOW)
    assert value.pilot_tracking_allowed is True
    assert value.target_temperature_c == 21.3
    assert value.min_temperature_c == 21.0
    assert value.max_temperature_c == 21.5
    assert value.valid_until == NOW + timedelta(seconds=150)
    assert value.plan_issued_at == NOW - timedelta(minutes=5)
    assert value.basis_control_revision == 7
    assert value.shadow_only is True
    assert value.execution_allowed is False
    assert value.battery_execution_allowed is False
    assert value.full_optimization_execution_allowed is False
    assert value.native_tracking_validated is False


@pytest.mark.parametrize(
    "presence",
    [
        {},
        {"valid": False, "present": False, "require_comfort": False},
        {"valid": True, "present": True, "require_comfort": False, "verified_at": NOW},
        {"valid": True, "present": False, "require_comfort": True, "verified_at": NOW},
        {
            "valid": True,
            "present": False,
            "require_comfort": False,
            "verified_at": NOW - timedelta(seconds=91),
        },
    ],
)
def test_unknown_present_or_required_comfort_never_reduces_manual_goal(presence):
    value = ThermalTrackingPolicy().propose(
        settings(), request(source_plan=plan(target=20.5), presence=presence), now=NOW
    )
    assert value.target_temperature_c == 21.0
    assert value.require_comfort is True
    assert value.absence_delay_satisfied is False


def test_verified_continuous_absence_delay_and_immediate_return():
    policy = ThermalTrackingPolicy()
    for minute in range(31):
        now = NOW + timedelta(minutes=minute)
        value = policy.propose(
            settings(),
            request(
                now=now,
                source_plan=plan(issued=now, start=now, revision=minute + 1, target=20.5),
                presence={
                    "valid": True,
                    "present": False,
                    "require_comfort": False,
                    "verified_at": now,
                },
            ),
            now=now,
        )
        assert value.target_temperature_c == (20.5 if minute == 30 else 21.0)
    assert value.absence_delay_satisfied is True
    now += timedelta(seconds=30)
    returned = policy.propose(
        settings(),
        request(
            now=now,
            source_plan=plan(issued=now, start=now, revision=32, target=20.5),
            presence={"valid": True, "present": True, "require_comfort": True, "verified_at": now},
        ),
        now=now,
    )
    assert returned.target_temperature_c == 21.0
    assert returned.require_comfort is True


def test_gap_in_verified_absence_resets_delay():
    policy = ThermalTrackingPolicy()
    for minute in (0, 2, 4, 6, 8, 30, 32):
        now = NOW + timedelta(minutes=minute)
        value = policy.propose(
            settings(),
            request(
                now=now,
                source_plan=plan(issued=now, start=now, revision=minute + 1, target=20.5),
                presence={
                    "valid": True,
                    "present": False,
                    "require_comfort": False,
                    "verified_at": now,
                },
            ),
            now=now,
        )
        assert value.target_temperature_c == 21.0


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("master_power", "Off", "manual_off"),
        ("control_mode", "direct", "manual_direct_mode"),
    ],
)
def test_manual_priority_clears_active_and_pending(field, value, reason):
    r = request().model_dump()
    r["operator"][field] = value
    result = ThermalTrackingPolicy().propose(
        settings(), ThermalTrackingRequest.model_validate(r), now=NOW
    )
    assert result.pilot_tracking_allowed is False
    assert result.reason == reason
    assert result.target_temperature_c == 21.0


def test_quiet_preserves_cap_two_and_continuous_room_target():
    r = request(source_plan=plan(target=21.273)).model_dump()
    r["operator"]["control_mode"] = "quiet"
    value = ThermalTrackingPolicy().propose(
        settings(), ThermalTrackingRequest.model_validate(r), now=NOW
    )
    assert value.quiet_max_fan == 2
    assert value.target_temperature_c == 21.273


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"room_temperature_c": 20.749}, "cold_or_recovery_use_manual_mpc"),
        ({"room_temperature_c": 21.5}, "room_above_pilot_ceiling"),
        ({"room_temperature_c": 21.501}, "room_above_pilot_ceiling"),
        ({"room_observed_at": NOW - timedelta(seconds=181)}, "room_stale_or_future"),
        ({"room_observed_at": NOW + timedelta(seconds=31)}, "room_stale_or_future"),
        ({"pilot_expires_at": NOW}, "pilot_expired"),
        ({"zone_id": "other"}, "zone_mismatch"),
    ],
)
def test_runtime_guard_denials_preserve_manual_fallback(updates, reason):
    value = ThermalTrackingPolicy().propose(settings(), request(**updates), now=NOW)
    assert value.pilot_tracking_allowed is False
    assert value.reason == reason
    assert value.target_temperature_c == 21.0
    assert value.valid_until is None


@pytest.mark.parametrize(
    "status", ["infeasible", "limit", "error", "superseded", "disabled_by_user", "input_error"]
)
def test_failed_source_revokes_success_and_cannot_replay_old_success(status):
    policy = ThermalTrackingPolicy()
    good = request()
    assert policy.propose(settings(), good, now=NOW).pilot_tracking_allowed
    later = NOW + timedelta(seconds=10)
    bad = request(now=later, source={"status": status, "issued_at": later})
    assert policy.propose(settings(), bad, now=later).pilot_tracking_allowed is False
    assert (
        policy.propose(settings(), good, now=later).reason == "source_out_of_order_or_conflicting"
    )


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"physical_checks_passed": False}, "source_physical_checks_missing"),
        ({"recovery_active": True}, "cold_or_recovery_use_manual_mpc"),
    ],
)
def test_source_physics_and_recovery_require_manual_mpc(updates, reason):
    r = request().model_dump()
    r["source"].update(updates)
    value = ThermalTrackingPolicy().propose(
        settings(), ThermalTrackingRequest.model_validate(r), now=NOW
    )
    assert value.reason == reason
    assert value.pilot_tracking_allowed is False


def test_successful_future_plan_preserves_current_then_activates_at_boundary():
    policy = ThermalTrackingPolicy()
    assert policy.propose(settings(), request(), now=NOW).target_temperature_c == 21.3
    now = NOW + timedelta(minutes=1)
    future = plan(issued=now, start=NOW + timedelta(minutes=10), revision=2, target=21.4)
    current = policy.propose(settings(), request(now=now, source_plan=future), now=now)
    assert current.plan_id == "plan-1"
    assert current.target_temperature_c == 21.3
    boundary = NOW + timedelta(minutes=10)
    activated = policy.propose(settings(), request(now=boundary, source_plan=future), now=boundary)
    assert activated.plan_id == "plan-2"
    assert activated.target_temperature_c == 21.4
    assert activated.interval_start == boundary


def test_future_only_plan_never_grants_early_permission():
    value = ThermalTrackingPolicy().propose(
        settings(), request(source_plan=plan(start=NOW + timedelta(minutes=10))), now=NOW
    )
    assert value.pilot_tracking_allowed is False
    assert value.reason == "waiting_for_current_interval"
    assert value.valid_until is None


def test_later_future_plan_does_not_displace_earlier_pending_interval():
    policy = ThermalTrackingPolicy()
    first = plan(issued=NOW, start=NOW + timedelta(minutes=5), revision=1, target=21.3)
    later = plan(
        issued=NOW + timedelta(minutes=1),
        start=NOW + timedelta(minutes=10),
        revision=2,
        target=21.4,
    )
    assert not policy.propose(
        settings(), request(source_plan=first), now=NOW
    ).pilot_tracking_allowed
    before = NOW + timedelta(minutes=1)
    assert not policy.propose(
        settings(), request(now=before, source_plan=later), now=before
    ).pilot_tracking_allowed
    boundary = NOW + timedelta(minutes=5)
    current = policy.propose(
        settings(), request(now=boundary, source_plan=later), now=boundary
    )
    assert current.pilot_tracking_allowed
    assert current.plan_id == "plan-1"
    assert current.interval_start == boundary
    next_boundary = NOW + timedelta(minutes=10)
    updated = policy.propose(
        settings(), request(now=next_boundary, source_plan=later), now=next_boundary
    )
    assert updated.pilot_tracking_allowed
    assert updated.plan_id == "plan-2"


def test_manual_revision_change_invalidates_current_and_pending():
    policy = ThermalTrackingPolicy()
    policy.propose(settings(), request(), now=NOW)
    changed = request().model_dump()
    changed["operator"]["control_revision"] = 8
    value = policy.propose(settings(), ThermalTrackingRequest.model_validate(changed), now=NOW)
    assert value.reason == "manual_revision_changed"
    assert value.target_temperature_c == 21.0


def test_lease_is_bounded_by_pilot_deadline_and_current_interval():
    value = ThermalTrackingPolicy().propose(
        settings(), request(pilot_expires_at=NOW + timedelta(seconds=12)), now=NOW
    )
    assert value.valid_until == NOW + timedelta(seconds=12)
    at_end = NOW + timedelta(minutes=9, seconds=55)
    other = ThermalTrackingPolicy().propose(settings(), request(now=at_end), now=at_end)
    assert other.valid_until == NOW + timedelta(minutes=10)


def test_disjoint_source_manual_bounds_fail_closed_without_union():
    value = ThermalTrackingPolicy().propose(
        settings(), request(source_plan=plan(target=19.5, minimum=19.0, maximum=20.0)), now=NOW
    )
    assert value.reason == "plan_and_manual_bounds_disjoint"
    assert value.pilot_tracking_allowed is False


def test_cached_plan_cannot_be_freshened_by_new_issue_timestamp():
    r = request().model_dump()
    r["source"]["issued_at"] = NOW
    with pytest.raises(ValidationError, match="preserve plan.issued_at"):
        ThermalTrackingRequest.model_validate(r)


def test_newer_issue_cannot_reset_plan_revision_backwards():
    policy = ThermalTrackingPolicy()
    assert policy.propose(
        settings(), request(source_plan=plan(revision=3)), now=NOW
    ).pilot_tracking_allowed
    later = NOW + timedelta(seconds=30)
    value = policy.propose(
        settings(), request(now=later, source_plan=plan(issued=later, revision=2)), now=later
    )
    assert value.reason == "plan_revision_out_of_order"
    assert value.pilot_tracking_allowed is False


def test_policy_cache_does_not_cross_configured_zone_boundaries():
    policy = ThermalTrackingPolicy()
    assert policy.propose(settings(), request(), now=NOW).pilot_tracking_allowed
    later = NOW + timedelta(seconds=30)
    r = request(
        now=later, source_plan=plan(issued=later, start=later + timedelta(minutes=10))
    ).model_dump()
    r["zone_id"] = "zone-b"
    r["source"]["plan"]["zone_id"] = "zone-b"
    value = policy.propose(
        settings(zone_id="zone-b"), ThermalTrackingRequest.model_validate(r), now=later
    )
    assert value.pilot_tracking_allowed is False
    assert value.reason == "waiting_for_current_interval"


@pytest.mark.parametrize("field", ["execution_allowed", "shadow_only"])
def test_full_optimization_permissions_cannot_be_overwritten(field):
    r = request().model_dump()
    r["source"][field] = field == "execution_allowed"
    with pytest.raises(ValidationError):
        ThermalTrackingRequest.model_validate(r)


def test_clock_requires_timezone():
    with pytest.raises(ValueError, match="timezone-aware"):
        ThermalTrackingPolicy().propose(settings(), request(), now=NOW.replace(tzinfo=None))


def client(monkeypatch, configured=None, initialized=True):
    app = FastAPI()
    app.include_router(api.router)
    if configured is not None:
        app.state.thermal_tracking_settings = configured
    if initialized:
        app.state.thermal_tracking_policy = ThermalTrackingPolicy()
        app.state.thermal_tracking_lock = Lock()
    monkeypatch.setattr(api, "tracking_now", lambda: NOW)
    return TestClient(app)


def test_separate_api_defaults_disabled_without_initializing_eos(monkeypatch):
    response = client(monkeypatch).post(
        "/v1/thermal/tracking/proposal", json=request().model_dump(mode="json")
    )
    assert response.status_code == 200
    assert response.json()["pilot_tracking_allowed"] is False
    assert response.json()["execution_allowed"] is False


def test_separate_api_explicit_settings_and_policy(monkeypatch):
    response = client(monkeypatch, configured=settings()).post(
        "/v1/thermal/tracking/proposal", json=request().model_dump(mode="json")
    )
    assert response.status_code == 200
    value = response.json()
    assert value["pilot_tracking_allowed"] is True
    assert value["scope"] == "room_target_only"
    assert value["battery_execution_allowed"] is False
    assert value["execution_allowed"] is False


def test_api_missing_policy_is_unavailable(monkeypatch):
    response = client(monkeypatch, initialized=False).post(
        "/v1/thermal/tracking/proposal", json=request().model_dump(mode="json")
    )
    assert response.status_code == 503


def test_api_busy_policy_fails_without_waiting_or_changing_permission(monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    app.state.thermal_tracking_policy = ThermalTrackingPolicy()
    app.state.thermal_tracking_settings = settings()
    app.state.thermal_tracking_lock = Lock()
    app.state.thermal_tracking_lock.acquire()
    monkeypatch.setattr(api, "tracking_now", lambda: NOW)
    try:
        response = TestClient(app).post(
            "/v1/thermal/tracking/proposal", json=request().model_dump(mode="json")
        )
    finally:
        app.state.thermal_tracking_lock.release()
    assert response.status_code == 429


@pytest.mark.parametrize("field", ["room_temperature_c", "room_observed_at"])
def test_invalid_measurement_inputs_are_not_silently_coerced(field):
    values = request().model_dump()
    values[field] = True if field == "room_temperature_c" else "not-a-timestamp"
    with pytest.raises(ValidationError):
        ThermalTrackingRequest.model_validate(values)


def test_serialization_preserves_fractional_goals_and_original_plan_issue():
    original = request(source_plan=plan(target=21.247))
    restored = ThermalTrackingRequest.model_validate_json(original.model_dump_json())
    assert restored == original
    assert isinstance(restored.source.plan, ThermalPlan)
    assert restored.source.plan.intervals[0].target_temperature_c == 21.247


@pytest.mark.parametrize(
    "present,valid,expected", [(False, True, False), (True, True, True), (False, False, False)]
)
def test_manual_away_tracking_is_bounded_and_fresh_arrival_wins(present, valid, expected):
    correction = dict(
        zone_id="zone-a",
        kind="away",
        issued_at=NOW - timedelta(minutes=1),
        valid_until=NOW + timedelta(minutes=29),
    )
    value = request(
        source_plan=plan(minimum=18, target=18.5),
        presence=dict(valid=valid, present=present, verified_at=NOW),
        presence_correction=correction,
    )
    decision = ThermalTrackingPolicy().propose(settings(operation="continuous"), value, now=NOW)
    assert decision.pilot_tracking_allowed
    assert decision.require_comfort is expected
    assert decision.target_temperature_c == (20.7 if expected else 18.5)
    for replacement in [
        dict(correction, valid_until=NOW),
        dict(correction, zone_id="another-zone"),
        dict(correction, issued_at=NOW + timedelta(seconds=1)),
    ]:
        raw = value.model_dump()
        raw["presence_correction"] = replacement
        raw["presence"]["valid"] = False
        denied = ThermalTrackingPolicy().propose(
            settings(operation="continuous"), ThermalTrackingRequest.model_validate(raw), now=NOW
        )
        assert denied.require_comfort


def test_contiguous_sensor_absence_survives_solver_gap_but_not_sensor_failure():
    config = settings(operation="continuous", absence_delay_seconds=900)
    native = plan(minimum=18, target=18.5)
    for valid, present, since, expected in [
        (True, False, NOW - timedelta(minutes=16), False),
        (True, True, NOW - timedelta(minutes=16), True),
        (False, False, NOW - timedelta(minutes=16), True),
        (True, False, NOW - timedelta(minutes=1), True),
        (True, False, NOW + timedelta(minutes=1), True),
    ]:
        value = request(
            source_plan=native,
            presence=dict(valid=valid, present=present, verified_at=NOW, absent_since=since),
        )
        # Newly created policy has no call history; proof comes from the sensor adapter.
        result = ThermalTrackingPolicy().propose(config, value, now=NOW)
        assert result.pilot_tracking_allowed and result.require_comfort is expected


def test_manual_away_execution_lease_never_outlives_correction():
    end = NOW + timedelta(seconds=30)
    value = request(
        source_plan=plan(minimum=18, target=18.5),
        presence_correction=dict(zone_id="zone-a", kind="away", issued_at=NOW, valid_until=end),
    )
    result = ThermalTrackingPolicy().propose(settings(operation="continuous"), value, now=NOW)
    assert result.pilot_tracking_allowed and not result.require_comfort
    assert result.valid_until == end


def test_presence_return_keeps_current_plan_until_future_plan_starts():
    policy = ThermalTrackingPolicy()
    config = settings(operation="continuous", absence_delay_seconds=180, minimum_temperature_c=16)
    current = plan(minimum=16, target=16.3, maximum=21.5)
    future = plan(
        start=NOW + timedelta(minutes=10),
        issued=NOW,
        revision=2,
        minimum=16,
        target=20.9,
        maximum=21.5,
    )
    for second, source, present, valid, expected in [
        (0, current, False, True, 16.3),
        (1, future, True, True, 20.7),
        (2, future, None, False, 20.7),
        (599, future, True, True, 20.7),
        (600, future, True, True, 20.9),
    ]:
        now = NOW + timedelta(seconds=second)
        value = request(
            now=now,
            source_plan=source,
            pilot_expires_at=None,
            presence=dict(
                valid=valid,
                present=present,
                verified_at=now,
                absent_since=NOW - timedelta(minutes=5) if present is False else None,
            ),
        )
        result = policy.propose(config, value, now=now)
        assert result.pilot_tracking_allowed
        assert result.target_temperature_c == expected
        assert result.basis_control_revision == 7
        assert result.plan_id == ("plan-2" if second == 600 else "plan-1")
        assert result.valid_until <= result.interval_end


def test_approved_preheat_can_reach_three_degrees_above_manual_goal():
    native = plan(minimum=19.7, target=22.8, maximum=23.0)
    raw = request(source_plan=native, pilot_expires_at=None).model_dump()
    raw["operator"]["target_temperature_c"] = 20.0
    current = ThermalTrackingPolicy().propose(
        settings(operation="continuous", max_increase_c=3.0),
        ThermalTrackingRequest.model_validate(raw), now=NOW,
    )
    assert current.pilot_tracking_allowed
    assert current.target_temperature_c == 22.8
    assert current.max_temperature_c == 23.0
