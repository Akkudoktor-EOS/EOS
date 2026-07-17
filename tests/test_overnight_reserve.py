"""Tests for the direct-marketing overnight reserve (export cap).

The reserve keeps tonight's forecast household net-load (load − PV until the
next morning) in the battery: battery-to-grid export (direct marketing) may not
sell it, while self-consumption keeps full access. The optional price-aware
mode releases a slot's reserve down to a safety floor when that slot's own
export revenue clearly beats the avoided night-import price.
"""

from unittest.mock import patch

import numpy as np
import pytest

from akkudoktoreos.devices.genetic.battery import Battery
from akkudoktoreos.devices.genetic.inverter import Inverter, InverterParameters
from akkudoktoreos.optimization.genetic import genetic as genetic_module
from akkudoktoreos.optimization.genetic.genetic import _compute_overnight_reserve
from akkudoktoreos.optimization.genetic.geneticdevices import (
    SolarPanelBatteryParameters,
)


def test_energy_balance_reserve_walks_backwards_to_next_morning():
    """Each slot reserves margin × Σ night net-load until PV next covers load."""
    load = np.array([100.0, 200.0, 300.0, 50.0])
    pv = np.array([0.0, 0.0, 0.0, 500.0])  # slot 3 = morning (PV >= load)
    reserve = _compute_overnight_reserve(load, pv, 0, 4, margin=1.1)
    np.testing.assert_allclose(reserve, [550.0, 330.0, 0.0, 0.0])


def test_reserve_disabled_returns_zeros(monkeypatch):
    monkeypatch.setattr(genetic_module, "_OVERNIGHT_RESERVE_ENABLED", False)
    load = np.array([100.0, 200.0, 300.0, 50.0])
    pv = np.zeros(4)
    reserve = _compute_overnight_reserve(load, pv, 0, 4, margin=1.1)
    np.testing.assert_allclose(reserve, np.zeros(4))


def test_price_aware_release_is_per_slot(monkeypatch):
    """Only slots whose OWN revenue clears avoided_import × (1 + margin) release."""
    monkeypatch.setattr(genetic_module, "_RESERVE_PRICE_AWARE_ENABLED", True)
    monkeypatch.setattr(genetic_module, "_RESERVE_RELEASE_MARGIN", 0.20)
    monkeypatch.setattr(genetic_module, "_RESERVE_RELEASE_SPREAD", 0.0)
    monkeypatch.setattr(genetic_module, "_RESERVE_SAFETY_FRACTION", 0.5)
    monkeypatch.setattr(genetic_module, "_RESERVE_MIN_SAFETY_FLOOR_WH", 2000.0)
    monkeypatch.setattr(genetic_module, "_RESERVE_MIN_SAFETY_CAP_WH", 6000.0)

    load = np.array([0.0, 8000.0, 8000.0, 100.0])
    pv = np.array([0.0, 0.0, 0.0, 200.0])  # slot 3 = morning
    price = np.full(4, 0.00027)  # 27 ct/kWh import → threshold 32.4 ct/kWh
    revenue = np.array([0.0004, 0.0002, 0.0002, 0.0002])  # only slot 0 clears it

    reserve = _compute_overnight_reserve(
        load, pv, 0, 4, margin=1.1, price_array=price, revenue_array=revenue
    )
    # Slot 0 (40 ct/kWh > 32.4): released to clamp(17600×0.5, 2000, 6000) = 6000.
    # Slot 1 (20 ct/kWh): holds the full energy-balance reserve 8800.
    np.testing.assert_allclose(reserve, [6000.0, 8800.0, 0.0, 0.0])


def _make_inverter(battery: Battery, max_power_wh: float = 10000.0) -> Inverter:
    with patch(
        "akkudoktoreos.devices.genetic.inverter.get_eos_load_interpolator",
    ) as interpolator:
        interpolator.return_value.calculate_expected_direct_consumption.side_effect = min
        return Inverter(
            InverterParameters(
                device_id="inverter",
                max_power_wh=max_power_wh,
                battery_id="battery",
                dc_to_ac_efficiency=1.0,
                ac_to_dc_efficiency=1.0,
            ),
            battery=battery,
        )


def _make_battery() -> Battery:
    battery = Battery(
        SolarPanelBatteryParameters(
            device_id="battery",
            capacity_wh=10000,
            charging_efficiency=1.0,
            discharging_efficiency=1.0,
            max_charge_power_w=10000,
            initial_soc_percentage=100,
            min_soc_percentage=0,
        ),
        prediction_hours=1,
        slot_duration_h=1.0,
    )
    battery.set_discharge_per_hour(np.array([1]))
    return battery


def test_reserve_caps_battery_grid_export():
    inverter = _make_inverter(_make_battery())
    grid_export, grid_import, _losses, _self_consumption = inverter.process_energy(
        generation=0.0,
        consumption=0.0,
        hour=0,
        allow_battery_grid_export=True,
        export_reserve_ac_wh=3000.0,
    )
    assert grid_export == pytest.approx(7000.0)
    assert grid_import == pytest.approx(0.0)


def test_zero_reserve_keeps_full_export():
    inverter = _make_inverter(_make_battery())
    grid_export, _grid_import, _losses, _self_consumption = inverter.process_energy(
        generation=0.0,
        consumption=0.0,
        hour=0,
        allow_battery_grid_export=True,
        export_reserve_ac_wh=0.0,
    )
    assert grid_export == pytest.approx(10000.0)


def test_reserve_does_not_limit_self_consumption():
    """Covering the slot's own load may always use the reserved energy."""
    inverter = _make_inverter(_make_battery())
    grid_export, grid_import, _losses, self_consumption = inverter.process_energy(
        generation=0.0,
        consumption=2000.0,
        hour=0,
        allow_battery_grid_export=True,
        export_reserve_ac_wh=999999.0,
    )
    assert grid_import == pytest.approx(0.0)
    assert self_consumption == pytest.approx(2000.0)
    assert grid_export == pytest.approx(0.0)
