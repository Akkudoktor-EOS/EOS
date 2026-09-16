"""Economic tail scenarios and hard control/forecast boundaries."""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.config.config import SettingsEOSDefaults
from akkudoktoreos.core.coreabc import get_ems
from akkudoktoreos.devices.genetic.battery import Battery
from akkudoktoreos.devices.genetic.inverter import Inverter
from akkudoktoreos.optimization.genetic.forecast import bounded_forecast_array
from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization
from akkudoktoreos.devices.genetic.inverter import InverterParameters
from akkudoktoreos.devices.genetic.battery import SolarPanelBatteryParameters
from akkudoktoreos.optimization.genetic.geneticparams import GeneticOptimizationParameters
from akkudoktoreos.optimization.genetic.tailvalue import build_tail_value_curve
from akkudoktoreos.optimization.genetic.terminalvalue import TerminalValueCurve
from akkudoktoreos.utils.datetimeutil import to_datetime, to_duration


def devices(power=1000, efficiency=1.0, lcos=0, ac_limit=None, export_power=5000):
    bat = Battery(
        SolarPanelBatteryParameters(
            device_id="battery1",
            capacity_wh=1000,
            max_charge_power_w=power,
            charging_efficiency=efficiency,
            discharging_efficiency=efficiency,
            initial_soc_percentage=50,
            levelized_cost_of_storage_kwh=lcos,
            charge_rates=[0, 0.5, 1],
        ),
        prediction_hours=1,
    )
    inv = Inverter(
        InverterParameters(
            device_id="inverter1",
            battery_id="battery1",
            max_power_wh=export_power,
            dc_to_ac_efficiency=1,
            ac_to_dc_efficiency=1,
            max_ac_charge_power_w=ac_limit,
        ),
        battery=bat,
    )
    return bat, inv


def curve(
    prices=(-0.1, 0.3),
    tariffs=(0, 0.3),
    direct=True,
    continuation=None,
    load=None,
    pv=None,
    **kwargs,
):
    bat, inv = devices(**kwargs)
    return build_tail_value_curve(
        battery=bat,
        inverter=inv,
        prices_euro_per_wh=np.array(prices) / 1000,
        feed_in_euro_per_wh=np.array(tariffs) / 1000,
        load_wh=np.zeros(len(prices)) if load is None else np.array(load),
        pv_wh=np.zeros(len(prices)) if pv is None else np.array(pv),
        continuation=continuation or TerminalValueCurve(),
        charge_rates=[0.5, 1],
        export_rates=[1],
        direct_marketing=direct,
    )


def test_headroom_has_value_and_empty_state_can_earn():
    c = curve()
    assert c.value(0) == pytest.approx(0.4)
    tail, continuation = c.component_values(0)
    assert tail == pytest.approx(0.4)
    assert continuation == pytest.approx(0.0)
    assert c.value(0) == pytest.approx(tail + continuation)
    assert c.value(500) > c.value(1000)
    assert any(v < 0 for v in c.marginal_euro_per_kwh)


def test_chronology_changes_arbitrage():
    forward = curve()
    reverse = curve(prices=(0.3, -0.1), tariffs=(0.3, 0))
    assert forward.value(0) > reverse.value(0)


def test_discharge_and_ac_power_limits():
    limited = curve(prices=(1,), tariffs=(1,), power=100)
    assert limited.value(1000) == pytest.approx(0.1)
    limited_ac = curve(ac_limit=100)
    assert limited_ac.value(0) == pytest.approx(0.04)
    limited_inverter = curve(prices=(1,), tariffs=(1,), export_power=50)
    assert limited_inverter.value(1000) == pytest.approx(0.05)


def test_losses_and_lcos_reduce_arbitrage():
    ideal = curve(prices=(0.1, 0.3))
    lossy = curve(prices=(0.1, 0.3), efficiency=0.8)
    assert 0 < lossy.value(0) < ideal.value(0)
    assert curve(prices=(0.1, 0.3), lcos=0.25).value(0) == pytest.approx(0)


def test_no_battery_export_without_permission():
    assert curve(prices=(0.1, 0.3), direct=False).value(0) == pytest.approx(0)


def test_pv_surplus_can_be_stored_for_local_load():
    c = curve(prices=(0.2, 0.3), tariffs=(0, 0), pv=[1000, 0], load=[0, 1000], direct=False)
    assert c.value(0) == pytest.approx(0)
    # Without PV the same empty battery must buy energy to serve the load.
    assert curve(prices=(0.2, 0.3), tariffs=(0, 0), load=[0, 1000], direct=False).value(
        0
    ) < c.value(0)


def test_continuation_survives_tail_end():
    continuation = TerminalValueCurve(energy_wh=[0, 1000], value_euro=[0, 0.2])
    c = curve(prices=(0.5,), tariffs=(0,), continuation=continuation)
    assert c.value(1000) == pytest.approx(0.2)
    tail, continuation_credit = c.component_values(1000)
    assert tail == pytest.approx(0.0)
    assert continuation_credit == pytest.approx(0.2)


def test_tail_diagnostic_plan_explains_the_selected_path():
    c = curve()
    plan = c.diagnostic_plan(0, control_horizon_hours=24)
    assert len(plan) == 2
    assert plan[0].hour_from_start == 24
    assert plan[0].action == "GRID_CHARGE"
    assert plan[0].soc_end_percentage > plan[0].soc_start_percentage
    assert plan[0].grid_import_wh > 0
    assert plan[1].action == "BATTERY_EXPORT"
    assert plan[1].soc_end_percentage < plan[1].soc_start_percentage
    assert plan[1].grid_export_wh > 0
    assert sum(slot.slot_value_euro for slot in plan) == pytest.approx(c.value(0))




def test_provider_values_are_not_extrapolated():
    from types import SimpleNamespace

    start = to_datetime("2026-09-05T00:00:00Z")
    series = pd.Series([1.0, 2.0], index=pd.date_range(start=start, periods=2, freq="h"))
    provider = SimpleNamespace(key_to_series=lambda *a, **kw: series)
    result = bounded_forecast_array(
        provider,
        key="price",
        start_datetime=start,
        end_datetime=start.add(hours=3),
        interval=to_duration("15 minutes"),
    )
    assert result[:8].tolist() == [1.0] * 4 + [2.0] * 4
    assert np.isnan(result[8:]).all()



def test_disabled_ac_conversion_cannot_earn_negative_price_revenue():
    bat, inv = devices()
    inv.parameters.ac_to_dc_efficiency = 0
    c = build_tail_value_curve(
        battery=bat,
        inverter=inv,
        prices_euro_per_wh=np.array([-0.001, 0.001]),
        feed_in_euro_per_wh=np.array([0.0, 0.001]),
        load_wh=np.zeros(2),
        pv_wh=np.zeros(2),
        continuation=TerminalValueCurve(),
        charge_rates=[1],
        export_rates=[1],
        direct_marketing=True,
    )
    assert c.value(0) == pytest.approx(0)
    assert bat.soc_wh == 500  # Building the tail never mutates the real battery.



def test_missing_provider_key_stays_missing():
    from types import SimpleNamespace

    def unavailable(*a, **kw):
        raise KeyError("price unavailable")

    start = to_datetime("2026-09-05T00:00:00Z")
    result = bounded_forecast_array(
        SimpleNamespace(key_to_series=unavailable),
        key="price",
        start_datetime=start,
        end_datetime=start.add(hours=2),
        interval=to_duration("1 hour"),
    )
    assert np.isnan(result).all()
    assert len(result) == 2

