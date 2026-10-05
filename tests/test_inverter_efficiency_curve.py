"""Tests for the load-dependent DC-to-AC efficiency curve of the GENETIC inverter.

``dc_to_ac_efficiency_curve`` replaces the constant ``dc_to_ac_efficiency`` by a
curve over the load fraction (AC power / rated power). Real inverters are much
less efficient at low load, which matters for night-time discharge into a small
base load. Without a curve the behaviour must stay exactly the legacy one.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
from pydantic import ValidationError

from akkudoktoreos.devices.devicesabc import (
    interpolate_efficiency_curve,
    validate_efficiency_curve,
)
from akkudoktoreos.devices.genetic.battery import Battery, SolarPanelBatteryParameters
from akkudoktoreos.devices.genetic.inverter import Inverter, InverterParameters
from akkudoktoreos.devices.settings.invertersettings import InverterCommonSettings
from akkudoktoreos.optimization.genetic.genetic import GeneticSimulation
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
)
from akkudoktoreos.optimization.genetic.tailvalue import build_tail_value_curve
from akkudoktoreos.optimization.genetic.terminalvalue import TerminalValueCurve

CURVE = [(0.05, 0.80), (0.20, 0.90), (0.50, 0.95), (1.0, 0.93)]


def _make_inverter(
    curve=None,
    max_power_wh: float = 1000.0,
    battery=None,
    dc_to_ac_efficiency: float = 1.0,
    **kwargs,
) -> Inverter:
    predictor = Mock()
    predictor.calculate_expected_direct_consumption.side_effect = min
    with patch(
        "akkudoktoreos.devices.genetic.inverter.get_eos_load_interpolator",
        return_value=predictor,
    ):
        return Inverter(
            InverterParameters(
                device_id="inverter1",
                max_power_wh=max_power_wh,
                battery_id=battery.parameters.device_id if battery else None,
                dc_to_ac_efficiency=dc_to_ac_efficiency,
                dc_to_ac_efficiency_curve=curve,
                **kwargs,
            ),
            battery=battery,
        )


def _passthrough_battery() -> Mock:
    """Battery mock that delivers exactly the requested DC energy."""
    battery = Mock()
    battery.parameters.device_id = "battery1"
    battery.charge_energy = Mock(return_value=(0.0, 0.0))
    battery.discharge_energy = Mock(side_effect=lambda dc, hour, **kw: (dc, 0.0))
    return battery


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestCurveValidation:
    def test_none_is_allowed(self):
        assert validate_efficiency_curve(None) is None
        params = InverterParameters(device_id="inverter1", max_power_wh=1000)
        assert params.dc_to_ac_efficiency_curve is None
        assert params.dc_to_ac_efficiency_reference_load_fraction == pytest.approx(0.06)

    def test_valid_curve_is_accepted(self):
        params = InverterParameters(
            device_id="inverter1", max_power_wh=1000, dc_to_ac_efficiency_curve=CURVE
        )
        assert params.dc_to_ac_efficiency_curve == CURVE

    def test_json_lists_are_accepted(self):
        params = InverterParameters.model_validate(
            {
                "device_id": "inverter1",
                "max_power_wh": 1000,
                "dc_to_ac_efficiency_curve": [[0.0, 0.8], [1.0, 0.95]],
            }
        )
        assert params.dc_to_ac_efficiency_curve == [(0.0, 0.8), (1.0, 0.95)]

    @pytest.mark.parametrize(
        "curve",
        [
            [],
            [(0.5, 0.9)],  # fewer than two points
            [(0.5, 0.9), (0.2, 0.8)],  # not sorted
            [(0.2, 0.9), (0.2, 0.8)],  # duplicate load fraction
            [(-0.1, 0.9), (1.0, 0.9)],  # load fraction below 0
            [(0.0, 0.9), (1.1, 0.9)],  # load fraction above 1
            [(0.0, 0.0), (1.0, 0.9)],  # efficiency 0
            [(0.0, 0.9), (1.0, 1.2)],  # efficiency above 1
            [(0.0, float("nan")), (1.0, 0.9)],  # not finite
        ],
    )
    def test_invalid_curve_is_rejected(self, curve):
        with pytest.raises(ValidationError):
            InverterParameters(
                device_id="inverter1", max_power_wh=1000, dc_to_ac_efficiency_curve=curve
            )
        with pytest.raises(ValidationError):
            InverterCommonSettings(
                device_id="inverter1", max_power_w=1000, dc_to_ac_efficiency_curve=curve
            )

    @pytest.mark.parametrize("fraction", [-0.1, 1.1])
    def test_reference_load_fraction_range(self, fraction):
        with pytest.raises(ValidationError):
            InverterParameters(
                device_id="inverter1",
                max_power_wh=1000,
                dc_to_ac_efficiency_reference_load_fraction=fraction,
            )


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------


class TestInterpolation:
    def test_points_midpoints_and_clamping(self):
        assert interpolate_efficiency_curve(CURVE, 0.05) == pytest.approx(0.80)
        assert interpolate_efficiency_curve(CURVE, 0.20) == pytest.approx(0.90)
        assert interpolate_efficiency_curve(CURVE, 1.0) == pytest.approx(0.93)
        assert interpolate_efficiency_curve(CURVE, 0.125) == pytest.approx(0.85)
        assert interpolate_efficiency_curve(CURVE, 0.75) == pytest.approx(0.94)
        # Clamped to the first and the last point.
        assert interpolate_efficiency_curve(CURVE, 0.0) == pytest.approx(0.80)
        assert interpolate_efficiency_curve(CURVE, 5.0) == pytest.approx(0.93)

    def test_inverter_evaluates_slot_load_fraction(self):
        inverter = _make_inverter(CURVE, max_power_wh=1000.0)
        assert inverter.dc_to_ac_efficiency_at(50.0) == pytest.approx(0.80)
        assert inverter.dc_to_ac_efficiency_at(125.0) == pytest.approx(0.85)
        assert inverter.dc_to_ac_efficiency_at(10.0) == pytest.approx(0.80)
        assert inverter.dc_to_ac_efficiency_at(5000.0) == pytest.approx(0.93)

    def test_curve_scales_with_device_size(self):
        """The same relative load gives the same efficiency on any device size."""
        small = _make_inverter(CURVE, max_power_wh=5000.0)
        large = _make_inverter(CURVE, max_power_wh=10000.0)
        assert small.dc_to_ac_efficiency_at(1000.0) == pytest.approx(
            large.dc_to_ac_efficiency_at(2000.0)
        )
        assert small.dc_to_ac_efficiency_at(1000.0) == pytest.approx(0.90)

    def test_load_fraction_uses_slot_duration(self):
        """max_power_wh is scaled to the slot, so 250 Wh in 15 min is 1 kW."""
        predictor = Mock()
        with patch(
            "akkudoktoreos.devices.genetic.inverter.get_eos_load_interpolator",
            return_value=predictor,
        ):
            inverter = Inverter(
                InverterParameters(
                    device_id="inverter1",
                    max_power_wh=5000.0,
                    dc_to_ac_efficiency_curve=CURVE,
                ),
                slot_duration_h=0.25,
            )
        # 1 kW of 5 kW -> load fraction 0.2
        assert inverter.dc_to_ac_efficiency_at(250.0) == pytest.approx(0.90)


# ---------------------------------------------------------------------------
# Without curve: unchanged behaviour
# ---------------------------------------------------------------------------


class TestWithoutCurve:
    @pytest.mark.parametrize("efficiency", [1.0, 0.95, 0.8])
    def test_efficiency_is_the_constant(self, efficiency):
        inverter = _make_inverter(None, dc_to_ac_efficiency=efficiency)
        for ac_wh in (0.0, 10.0, 500.0, 1000.0, 5000.0):
            assert inverter.dc_to_ac_efficiency_at(ac_wh) == efficiency
        assert inverter.reference_dc_to_ac_efficiency == efficiency

    def test_reference_load_fraction_is_ignored(self):
        inverter = _make_inverter(
            None, dc_to_ac_efficiency=0.9, dc_to_ac_efficiency_reference_load_fraction=0.5
        )
        assert inverter.reference_dc_to_ac_efficiency == 0.9

    def test_discharge_matches_legacy_formula(self):
        battery = _passthrough_battery()
        inverter = _make_inverter(None, battery=battery, dc_to_ac_efficiency=0.93)
        grid_export, grid_import, losses, self_consumption = inverter.process_energy(
            0.0, 100.0, 0
        )
        requested_dc = battery.discharge_energy.call_args[0][0]
        assert requested_dc == 100.0 / 0.93
        assert self_consumption == requested_dc * 0.93
        assert losses == requested_dc - requested_dc * 0.93
        assert grid_import == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# With curve: process_energy
# ---------------------------------------------------------------------------


class TestProcessEnergyWithCurve:
    def test_partial_load_draws_more_dc(self):
        """100 Wh at 10 % load (efficiency 0.8333) needs 120 Wh DC."""
        battery = _passthrough_battery()
        inverter = _make_inverter(CURVE, max_power_wh=1000.0, battery=battery)

        grid_export, grid_import, losses, self_consumption = inverter.process_energy(
            0.0, 100.0, 0
        )

        efficiency = 0.80 + (0.05 / 0.15) * 0.10
        requested_dc = battery.discharge_energy.call_args[0][0]
        assert requested_dc == pytest.approx(100.0 / efficiency)
        assert grid_import == pytest.approx(0.0, abs=1e-9)
        assert self_consumption == pytest.approx(100.0)
        assert losses == pytest.approx(requested_dc - 100.0)

    def test_grid_export_uses_efficiency_at_export_level(self, config_eos):
        """A full-power export runs at the efficiency of the rated load."""
        battery = Battery(
            SolarPanelBatteryParameters(
                device_id="battery1",
                capacity_wh=10000,
                initial_soc_percentage=100,
                min_soc_percentage=0,
                charging_efficiency=1.0,
                discharging_efficiency=1.0,
                max_charge_power_w=1000,
            ),
            prediction_hours=1,
        )
        battery.reset()
        battery.discharge_array = np.ones(1)
        inverter = _make_inverter(CURVE, max_power_wh=1000.0, battery=battery)

        grid_export, _, losses, _ = inverter.process_energy(
            0.0, 0.0, 0, allow_battery_grid_export=True, battery_grid_export_factor=1.0
        )

        # The export bound is estimated at the rated DC energy (load fraction
        # 1.0, efficiency 0.93). The conversion itself uses the efficiency at
        # the exported AC energy and never draws more than the rated DC energy.
        assert grid_export == pytest.approx(930.0)
        efficiency = inverter.dc_to_ac_efficiency_at(930.0)
        assert 0.93 < efficiency < 0.95
        assert losses == pytest.approx(930.0 / efficiency - 930.0)
        assert grid_export + losses <= 1000.0 + 1e-9


# ---------------------------------------------------------------------------
# Reference efficiency for valuing stored energy
# ---------------------------------------------------------------------------


class TestReferenceEfficiency:
    def test_default_reference_load(self):
        inverter = _make_inverter(CURVE, max_power_wh=1000.0)
        # 6 % load -> between 0.05:0.80 and 0.20:0.90
        expected = 0.80 + (0.06 - 0.05) / (0.20 - 0.05) * (0.90 - 0.80)
        assert inverter.reference_dc_to_ac_efficiency == pytest.approx(expected)

    def test_configured_reference_load(self):
        inverter = _make_inverter(
            CURVE, max_power_wh=1000.0, dc_to_ac_efficiency_reference_load_fraction=0.5
        )
        assert inverter.reference_dc_to_ac_efficiency == pytest.approx(0.95)

    def test_tail_value_uses_reference_efficiency(self):
        """Stored energy is valued with the efficiency at the reference load."""

        def tail_curve(efficiency_curve):
            battery = Battery(
                SolarPanelBatteryParameters(
                    device_id="battery1",
                    capacity_wh=1000,
                    max_charge_power_w=1000,
                    charging_efficiency=1.0,
                    discharging_efficiency=1.0,
                    initial_soc_percentage=50,
                    charge_rates=[0, 0.5, 1],
                ),
                prediction_hours=1,
            )
            inverter = Inverter(
                InverterParameters(
                    device_id="inverter1",
                    battery_id="battery1",
                    max_power_wh=5000,
                    dc_to_ac_efficiency_curve=efficiency_curve,
                ),
                battery=battery,
            )
            return build_tail_value_curve(
                battery=battery,
                inverter=inverter,
                # Grid charging is never worth it at this price.
                prices_euro_per_wh=np.ones(1),
                feed_in_euro_per_wh=np.zeros(1),
                load_wh=np.zeros(1),
                pv_wh=np.zeros(1),
                continuation=TerminalValueCurve(energy_wh=[0.0, 2000.0], value_euro=[0.0, 2.0]),
                charge_rates=[0.5, 1],
                export_rates=[1],
                direct_marketing=False,
            )

        flat = tail_curve([(0.0, 1.0), (1.0, 1.0)])
        lossy = tail_curve([(0.0, 0.8), (1.0, 0.8)])
        # The same AC energy needs more stored DC energy with lossy conversion.
        assert lossy.value(400.0) == pytest.approx(flat.value(400.0))
        assert lossy.energy_wh[-1] == pytest.approx(0.8 * flat.energy_wh[-1])


# ---------------------------------------------------------------------------
# Whole-simulation equivalence
# ---------------------------------------------------------------------------


def _simulate(config_eos, efficiency_curve, dc_to_ac_efficiency=0.95):
    hours = 24
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": hours},
            "optimization": {"genetic": {"tail_horizon_hours": 0, "horizon_hours": hours}},
        }
    )
    battery = Battery(
        SolarPanelBatteryParameters(
            device_id="battery1",
            capacity_wh=10000,
            initial_soc_percentage=60,
            min_soc_percentage=5,
            charging_efficiency=0.95,
            discharging_efficiency=0.95,
            max_charge_power_w=4000,
        ),
        prediction_hours=hours,
    )
    battery.reset()
    inverter = Inverter(
        InverterParameters(
            device_id="inverter1",
            max_power_wh=5000,
            battery_id="battery1",
            dc_to_ac_efficiency=dc_to_ac_efficiency,
            ac_to_dc_efficiency=0.95,
            dc_to_ac_efficiency_curve=efficiency_curve,
        ),
        battery=battery,
    )
    rng = np.random.default_rng(42)
    simulation = GeneticSimulation()
    simulation.prepare(
        GeneticEnergyManagementParameters.model_validate(
            dict(
                pv_prognose_wh=list(np.maximum(rng.normal(1500, 1500, hours), 0.0)),
                strompreis_euro_pro_wh=list(rng.uniform(0.0001, 0.0004, hours)),
                einspeiseverguetung_euro_pro_wh=list(rng.uniform(0.00005, 0.0002, hours)),
                preis_euro_pro_wh_akku=0.0001,
                gesamtlast=list(rng.uniform(100, 3000, hours)),
            )
        ),
        optimization_hours=hours,
        prediction_hours=hours,
        inverter=inverter,
        direct_marketing_enabled=True,
    )
    assert simulation.bat_discharge_hours is not None
    assert simulation.bat_grid_export_hours is not None
    assert simulation.ac_charge_hours is not None
    simulation.bat_discharge_hours[:] = 1
    simulation.bat_grid_export_hours[18:22] = [1.0, 0.5, 0.25, 1.0]
    simulation.ac_charge_hours[2:4] = 0.5
    return simulation.simulate(start_hour=0)


def test_flat_curve_equals_constant_efficiency(config_eos):
    """A flat curve at the constant efficiency reproduces the result exactly."""
    constant = _simulate(config_eos, None)
    flat = _simulate(config_eos, [(0.0, 0.95), (1.0, 0.95)])
    assert constant.keys() == flat.keys()
    for key, value in constant.items():
        if isinstance(value, np.ndarray):
            np.testing.assert_array_equal(value, flat[key], err_msg=key)
        else:
            assert value == flat[key], key


def test_low_load_curve_increases_losses(config_eos):
    constant = _simulate(config_eos, None, dc_to_ac_efficiency=0.95)
    curve = _simulate(config_eos, [(0.02, 0.80), (0.07, 0.93), (0.3, 0.96), (1.0, 0.95)])
    assert curve["Gesamt_Verluste"] > constant["Gesamt_Verluste"]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestSettings:
    def test_settings_pass_curve_to_genetic_parameters(self):
        settings = InverterCommonSettings.model_validate(
            {
                "device_id": "inverter1",
                "max_power_w": 5000,
                "dc_to_ac_efficiency_curve": [[0.02, 0.8], [0.07, 0.93], [1.0, 0.95]],
                "dc_to_ac_efficiency_reference_load_fraction": 0.1,
            }
        )
        params = settings.to_genetic_param()
        assert params.dc_to_ac_efficiency_curve == [(0.02, 0.8), (0.07, 0.93), (1.0, 0.95)]
        assert params.dc_to_ac_efficiency_reference_load_fraction == 0.1

    def test_defaults_keep_constant_efficiency(self):
        params = InverterCommonSettings(device_id="inverter1", max_power_w=5000).to_genetic_param()
        assert params.dc_to_ac_efficiency_curve is None
        assert params.dc_to_ac_efficiency_reference_load_fraction == pytest.approx(0.06)

    def test_genetic0_is_unaffected(self):
        settings = InverterCommonSettings(
            device_id="inverter1",
            max_power_w=5000,
            dc_to_ac_efficiency_curve=[(0.0, 0.8), (1.0, 0.95)],
        )
        params = settings.to_genetic0_param()
        assert not hasattr(params, "dc_to_ac_efficiency_curve")

    def test_config_accepts_curve(self, config_eos):
        config_eos.merge_settings_from_dict(
            {
                "devices": {
                    "inverters": {
                        "inverter1": {
                            "max_power_w": 5000,
                            "dc_to_ac_efficiency_curve": [[0.02, 0.8], [0.07, 0.93], [1.0, 0.95]],
                        }
                    }
                }
            }
        )
        inverters = config_eos.devices.inverters
        assert inverters is not None
        assert inverters["inverter1"].dc_to_ac_efficiency_curve == [
            (0.02, 0.8),
            (0.07, 0.93),
            (1.0, 0.95),
        ]
        config_eos.set_nested_value(
            "devices/inverters/inverter1/dc_to_ac_efficiency_curve", [[0.0, 0.85], [1.0, 0.95]]
        )
        assert inverters["inverter1"].dc_to_ac_efficiency_curve == [(0.0, 0.85), (1.0, 0.95)]
        with pytest.raises(ValueError, match="at least two points"):
            config_eos.set_nested_value(
                "devices/inverters/inverter1/dc_to_ac_efficiency_curve", [[0.5, 0.9]]
            )
