"""Startup loss as warm-up time (startup_warmup_seconds): surcharge = warm-up/slot x electric power of the start slot."""

import pytest
from conftest import heat_pump_request_data as data
from pydantic import ValidationError

from akkudoktoreos.devices.heatpump import HeatPumpElectricalSettings
from akkudoktoreos.thermal.thermalheatplanning import (
    ThermalHeatPlanningRequest,
    optimize_heat_pump_shadow,
)

WARM = 300.0  # seconds; 1/3 of a 15-minute slot
S = WARM / 900.0


def run(payload):
    return optimize_heat_pump_shadow(ThermalHeatPlanningRequest.model_validate(payload))


def extras(**kw):
    base = {"extras_are_outside_cop": True}
    base.update(kw)
    return base


def starts(power, initial):
    prev, out = initial, []
    for p in power:
        r = p > 1e-5
        out.append(r and not prev)
        prev = r
    return out


@pytest.mark.parametrize("value", [None, 0.0])
def test_default_and_zero_are_identical_to_no_field(value):
    base = data(8)
    base["initial_running"] = False
    base["electrical"] = extras(startup_energy_wh=30.0, standby_power_w=5.0, running_auxiliary_power_w=50.0)
    ref = run(base)
    alt = data(8)
    alt["initial_running"] = False
    alt["electrical"] = extras(startup_energy_wh=30.0, standby_power_w=5.0, running_auxiliary_power_w=50.0, startup_warmup_seconds=value)
    new = run(alt)
    assert ref.status == new.status == "optimal"
    assert ref.candidate_heat_power_w == new.candidate_heat_power_w
    assert ref.reference_heat_power_w == new.reference_heat_power_w
    assert ref.candidate_source_electric_power_w == new.candidate_source_electric_power_w


@pytest.mark.parametrize("initial_running", [False, True])
def test_surcharge_is_warmup_times_start_slot_power_only_at_starts(initial_running):
    payload = data(8)
    payload["initial_running"] = initial_running
    payload["electrical"] = extras(standby_power_w=5.0, running_auxiliary_power_w=50.0, startup_warmup_seconds=WARM)
    result = run(payload)
    assert result.status == "optimal", result.reason
    for base, total, breakdown in [
        (result.reference_heat_power_w, result.reference_source_electric_power_w, result.reference_electricity),
        (result.candidate_heat_power_w, result.candidate_source_electric_power_w, result.candidate_electricity),
    ]:
        flags = starts(base, initial_running)
        for p, net, comp, started in zip(base, total, breakdown, flags):
            running = p > 1e-5
            assert comp.base_energy_wh == pytest.approx(p * 0.25, abs=1e-5)
            assert comp.startup_energy_wh == pytest.approx(p * 0.25 * S if started else 0.0, abs=1e-5)
            assert net == pytest.approx(p + 5 + (50 if running else 0) + (p * S if started else 0.0), abs=1e-4)
            assert comp.started == started


def test_no_surcharge_in_continuous_operation():
    payload = data(8)
    payload["initial_running"] = True
    payload["state"].update(room_temperature_c=20.05, mass_temperature_c=20.05)
    payload["electrical"] = extras(startup_warmup_seconds=WARM)
    result = run(payload)
    assert result.status == "optimal", result.reason
    heat = result.candidate_heat_power_w
    assert heat[0] > 1e-5  # already running, cold room: keeps running
    assert result.candidate_electricity[0].startup_energy_wh == 0.0
    flags = starts(heat, True)
    for comp, started in zip(result.candidate_electricity, flags):
        assert (comp.startup_energy_wh > 0) == started


def test_warmup_adds_to_fixed_startup_energy():
    payload = data(8)
    payload["initial_running"] = False
    payload["electrical"] = extras(startup_energy_wh=30.0, startup_warmup_seconds=WARM)
    result = run(payload)
    assert result.status == "optimal", result.reason
    for p, comp, started in zip(result.candidate_heat_power_w, result.candidate_electricity, starts(result.candidate_heat_power_w, False)):
        assert comp.startup_energy_wh == pytest.approx((30.0 + p * 0.25 * S) if started else 0.0, abs=1e-4)


def test_longer_warmup_never_plans_more_starts():
    counts = []
    for warm in (None, 300.0, 900.0):
        payload = data(8)
        payload["initial_running"] = False
        payload["electrical"] = extras(startup_warmup_seconds=warm) if warm is not None else extras(startup_energy_wh=0.0)
        result = run(payload)
        assert result.status == "optimal", result.reason
        counts.append(sum(starts(result.candidate_heat_power_w, False)))
    assert counts[0] >= counts[1] >= counts[2]


def test_total_source_cap_divides_start_slot_by_one_plus_warmup_share():
    payload = data(8)
    payload["initial_running"] = False
    payload["state"].update(room_temperature_c=19.0, mass_temperature_c=19.0)
    payload["recovery"] = {"max_duration_seconds": 7200}
    payload["max_electric_power_w"] = 2000.0
    payload["electrical"] = extras(standby_power_w=5.0, running_auxiliary_power_w=50.0, startup_warmup_seconds=WARM)
    result = run(payload)
    assert result.candidate is not None and result.recovery is not None, result.reason
    assert result.recovery.heat_power_w[0] == pytest.approx((2000.0 - 55.0) / (1 + S))
    assert max(result.candidate_source_electric_power_w) <= 2000.0 + 1e-5


def test_validation():
    with pytest.raises(ValidationError):
        HeatPumpElectricalSettings(startup_warmup_seconds=901.0, extras_are_outside_cop=True)
    with pytest.raises(ValidationError):
        HeatPumpElectricalSettings(startup_warmup_seconds=-1.0, extras_are_outside_cop=True)
    with pytest.raises(ValidationError):  # positive addition needs explicit boundary declaration
        HeatPumpElectricalSettings(startup_warmup_seconds=120.0)
    HeatPumpElectricalSettings(startup_warmup_seconds=0.0)
