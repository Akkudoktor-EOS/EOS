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
from akkudoktoreos.optimization.genetic.geneticdevices import (
    InverterParameters,
    SolarPanelBatteryParameters,
)
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


def test_central_config_invariant():
    # Only the control horizon is mandatory. A prediction horizon that cannot
    # cover the requested tail shortens the tail instead of failing the run,
    # so existing configurations keep starting after an upgrade.
    short = SettingsEOSDefaults(
        prediction={"hours": 48}, optimization={"horizon_hours": 24, "tail_horizon_hours": 48}
    )
    assert short.prediction.hours == 48
    assert short.optimization.tail_horizon_hours == 48

    # A control horizon the forecast cannot serve is not rejected here either -
    # prediction.hours also serves callers that never optimize. The optimizer
    # rejects the run itself, naming the series that ran out.
    undersized = SettingsEOSDefaults(
        prediction={"hours": 48}, optimization={"horizon_hours": 72, "tail_horizon_hours": 0}
    )
    assert undersized.optimization.horizon_hours == 72

    settings = SettingsEOSDefaults()
    assert settings.prediction.hours == 72
    assert settings.optimization.tail_horizon_hours == 48


def setup_run(config, interval=3600, start_hour=0, hours=72, prediction_hours=72):
    config.merge_settings_from_dict(
        {
            "prediction": {"hours": prediction_hours},
            "optimization": {
                "horizon_hours": 24,
                "tail_horizon_hours": 48,
                "interval": interval,
                "visualize_pdf": False,
            },
            "feedintariff": {"direct_marketing_enabled": True},
        }
    )
    ems = get_ems(init=True)
    ems.set_start_datetime(to_datetime("2026-09-05T00:00:00").set(hour=start_hour))
    bat, inv = devices()
    params = GeneticOptimizationParameters(
        ems={
            "pv_prognose_wh": [0.0] * hours,
            "gesamtlast": [0.0] * hours,
            "strompreis_euro_pro_wh": [0.0002] * hours,
            "einspeiseverguetung_euro_pro_wh": [0.0001] * hours,
            "preis_euro_pro_wh_akku": 0,
        },
        pv_akku=bat.parameters,
        inverter=inv.parameters,
        eauto=None,
    )
    return GeneticOptimization(fixed_seed=42), params


@pytest.mark.parametrize("interval", [3600, 900])
@pytest.mark.parametrize("start_hour", [0, 10])
def test_genome_output_and_final_control_state(config_eos, interval, start_hour):
    opt, params = setup_run(config_eos, interval, start_hour, hours=72 + start_hour)

    def choose(*args, **kwargs):
        # Discharge only in the last control slot. Its POST-slot SOC is credited.
        genome = opt.create_individual()
        genome[:] = [0] * opt.control_end_slot
        genome[-1] = opt._battery_state_layout().grid_export_state
        assert len(genome) == 24 * (3600 // interval)
        return genome, {}

    with (
        patch.object(opt, "optimize", side_effect=choose),
        patch(
            "akkudoktoreos.optimization.genetic.genetic.build_tail_value_curve",
            wraps=build_tail_value_curve,
        ) as builder,
    ):
        result = opt.optimierung_ems(params)
    assert builder.call_count == 1
    assert len(result.ac_charge) == opt.control_slots
    assert len(result.dc_charge) == opt.control_slots
    assert len(result.discharge_allowed) == opt.control_slots
    assert len(result.battery_grid_export_factor) == opt.control_slots
    assert len(result.result.Kosten_Euro_pro_Stunde) == opt.control_slots
    assert result.terminal_value.mode == "TAIL"
    assert result.terminal_value.battery_energy_wh == pytest.approx(
        max(500 - 1000 * opt.slot_duration_h, 0)
    )
    assert result.terminal_value.effective_tail_hours == 48
    assert result.terminal_value.credited_euro == pytest.approx(
        result.terminal_value.tail_operating_euro + result.terminal_value.continuation_value_euro
    )
    assert result.terminal_value.continuation_curve is not None
    assert result.terminal_value.tail_diagnostics is not None
    assert result.terminal_value.tail_diagnostics.slots == 48 * (3600 // interval)
    assert result.terminal_value.tail_diagnostics.soc_grid_points == 101
    assert len(result.terminal_value.tail_plan) == result.terminal_value.tail_diagnostics.slots
    assert result.terminal_value.tail_plan[0].hour_from_start == 24
    assert len(result.terminal_value.curve.operating_value_euro) == 101
    assert len(result.terminal_value.curve.continuation_value_euro) == 101
    assert len(result.optimization_solution().solution.to_dataframe()) == opt.control_slots


def test_short_tail_is_reported(config_eos, caplog):
    opt, params = setup_run(config_eos, hours=52)
    with patch.object(
        opt, "optimize", side_effect=lambda *a, **k: ([0] * opt.control_end_slot, {})
    ):
        result = opt.optimierung_ems(params)
    assert result.terminal_value.effective_tail_hours == 28
    assert "Tail forecast shortened" in caplog.text
    assert result.terminal_value.reason


def test_missing_control_is_rejected(config_eos):
    opt, params = setup_run(config_eos, hours=23)
    with pytest.raises(ValueError, match="Incomplete control forecast"):
        opt.optimierung_ems(params)


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


def test_future_opportunity_changes_optimal_control_soc(config_eos):
    final_energy = []
    for negative_price in (0.5, -1.0):
        opt, params = setup_run(config_eos, hours=3)
        config_eos.merge_settings_from_dict(
            {"optimization": {"horizon_hours": 1, "tail_horizon_hours": 2}}
        )
        params.ems.strompreis_euro_pro_wh = [0.0005, negative_price / 1000, 0.0003]
        params.ems.einspeiseverguetung_euro_pro_wh = [0.00002, 0, 0.0003]
        result = opt.optimierung_ems(params, ngen=3, individuals=20)
        final_energy.append(result.terminal_value.battery_energy_wh)
    assert final_energy[0] > final_energy[1]


@pytest.mark.parametrize("control", [24, 48])
def test_continuation_prevents_emptying_at_moved_boundary(config_eos, control):
    opt, params = setup_run(config_eos, hours=96)
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 96},
            "optimization": {"horizon_hours": control},
        }
    )
    # Zero control load, with the same future local demand visible to both tails.
    params.ems.gesamtlast[60] = 1000
    params.ems.einspeiseverguetung_euro_pro_wh = [0.0] * 96
    config_eos.feedintariff.direct_marketing_enabled = False

    def choose(*a, **kw):
        from deap import creator

        idle = creator.Individual([0] * control)
        discharge = creator.Individual([len(opt.bat_possible_charge_values)] * control)
        assert opt.toolbox.evaluate(idle)[0] <= opt.toolbox.evaluate(discharge)[0]
        return idle, {}

    with patch.object(opt, "optimize", side_effect=choose):
        result = opt.optimierung_ems(params)
    assert result.terminal_value.battery_energy_wh == pytest.approx(500)
    assert result.terminal_value.continuation_mode == "AUTO"


def test_missing_price_inside_tail_stops_at_first_gap(config_eos):
    opt, params = setup_run(config_eos)
    params.ems.strompreis_euro_pro_wh[30] = float("nan")
    with patch.object(opt, "optimize", side_effect=lambda *a, **k: ([0] * opt.control_slots, {})):
        result = opt.optimierung_ems(params)
    assert result.terminal_value.effective_tail_hours == 6


def test_ev_genome_and_output_are_control_only(config_eos):
    from akkudoktoreos.optimization.genetic.geneticdevices import ElectricVehicleParameters

    opt, params = setup_run(config_eos, interval=900, start_hour=10, hours=82)
    params.eauto = ElectricVehicleParameters(
        device_id="ev1",
        capacity_wh=5000,
        initial_soc_percentage=0,
        min_soc_percentage=50,
        charge_rates=[0, 0.5, 1],
    )

    def choose(*a, **kw):
        genome = opt.create_individual()
        assert len(genome) == 2 * 96
        return genome, {}

    with patch.object(opt, "optimize", side_effect=choose):
        result = opt.optimierung_ems(params)
    assert len(result.eautocharge_hours_float) == 96


def test_rejected_config_update_is_atomic(config_eos):
    # The candidate is validated before the singleton is reinitialized, so a
    # rejected update must leave the running configuration untouched rather
    # than half-applied. `hours` is constrained to be non-negative.
    before = config_eos.prediction.hours
    with pytest.raises(ValueError):
        config_eos.merge_settings_from_dict({"prediction": {"hours": -1}})
    assert config_eos.prediction.hours == before


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


def test_short_native_forecast_declares_its_resolution(config_eos):
    opt, params = setup_run(config_eos, interval=900, hours=52 * 4)
    params.forecast_interval_seconds = 900
    with patch.object(opt, "optimize", side_effect=lambda *a, **k: ([0] * opt.control_slots, {})):
        result = opt.optimierung_ems(params)
    assert result.terminal_value.effective_tail_hours == 28


@pytest.mark.parametrize(
    "field,reason",
    [
        ("strompreis_euro_pro_wh", "import price"),
        ("einspeiseverguetung_euro_pro_wh", "feed-in tariff"),
    ],
)
def test_differing_provider_lengths_use_the_common_tail(config_eos, field, reason):
    opt, params = setup_run(config_eos)
    setattr(params.ems, field, getattr(params.ems, field)[:52])
    with patch.object(opt, "optimize", side_effect=lambda *a, **k: ([0] * opt.control_slots, {})):
        result = opt.optimierung_ems(params)
    assert result.terminal_value.effective_tail_hours == 28
    assert reason in result.terminal_value.reason


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


def test_short_prediction_horizon_shortens_the_tail(config_eos):
    # The forecast budget cannot serve the full 48 h tail. The run keeps going
    # with the 12 h that are left after the control horizon instead of failing.
    opt, params = setup_run(config_eos, hours=36, prediction_hours=36)
    assert opt.control_slots == 24
    assert opt.tail_slots == 12

    result = opt.optimierung_ems(params, ngen=2)
    assert result.terminal_value.mode == "TAIL"
    assert result.terminal_value.requested_tail_hours == 48
    assert result.terminal_value.effective_tail_hours == 12
