from datetime import timedelta

from test_thermaltracking import NOW, plan, request, settings

from akkudoktoreos.thermal.thermaltracking import ThermalTrackingPolicy


def test_future_heating_plan_does_not_cancel_current_coasting_power():
    policy = ThermalTrackingPolicy()
    config = settings(operation="continuous")
    current = plan(minimum=18, target=18.5, maximum=21.5)
    future = plan(
        start=NOW + timedelta(minutes=10),
        issued=NOW,
        revision=2,
        minimum=18,
        target=18.5,
        maximum=21.5,
    )
    decisions = []
    for seconds, native, watts in [(0, current, 0), (1, future, 2000)]:
        now = NOW + timedelta(seconds=seconds)
        value = request(
            now=now,
            source_plan=native,
            pilot_expires_at=None,
            room_temperature_c=21.1,
            presence=dict(
                valid=True, present=False, verified_at=now, absent_since=NOW - timedelta(minutes=20)
            ),
        )
        value = value.model_copy(
            update={
                "source": value.source.model_copy(
                    update={"electric_power_w": (float(watts), float(watts))}
                )
            }
        )
        decisions.append(policy.propose(config, value, now=now))
    assert all(d.pilot_tracking_allowed for d in decisions)
    assert all(not d.heating_allowed for d in decisions)
    assert all(d.electric_power_w == 0 for d in decisions)


def test_promoted_plan_keeps_its_own_heating_schedule():
    policy = ThermalTrackingPolicy()
    config = settings(operation="continuous")
    for seconds, revision, offset, watts in [(0, 1, -5, 0), (1, 2, 10, 2000), (600, 3, 25, 0)]:
        now = NOW + timedelta(seconds=seconds)
        native = plan(
            start=NOW + timedelta(minutes=offset),
            issued=now,
            revision=revision,
            minimum=18,
            target=18.5,
            maximum=21.5,
        )
        value = request(
            now=now,
            source_plan=native,
            pilot_expires_at=None,
            room_temperature_c=21.1,
            presence=dict(
                valid=True, present=False, verified_at=now, absent_since=NOW - timedelta(minutes=20)
            ),
        )
        value = value.model_copy(
            update={
                "source": value.source.model_copy(
                    update={"electric_power_w": (float(watts), float(watts))}
                )
            }
        )
        decision = policy.propose(config, value, now=now)
    assert decision.pilot_tracking_allowed
    assert decision.plan_id == "plan-2"
    assert decision.heating_allowed
    assert decision.electric_power_w == 2000
