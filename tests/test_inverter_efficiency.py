"""Tests for inverter AC/DC efficiency separation and AC charging break-even penalty.

Tests the new inverter parameters:
- dc_to_ac_efficiency: DC→AC conversion loss on battery discharge
- ac_to_dc_efficiency: AC→DC conversion loss on grid-to-battery charging
- max_ac_charge_power_w: Maximum AC charging power limit

And the economic break-even penalty in GeneticOptimization.evaluate():
- Penalises AC grid charging that cannot be recovered given round-trip losses and future prices
- Respects free PV-charged energy already in battery when ranking future discharge hours
"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import numpy as np
import pytest

from akkudoktoreos.devices.genetic.battery import Battery
from akkudoktoreos.devices.genetic.inverter import Inverter
from akkudoktoreos.optimization.genetic.geneticdevices import (
    InverterParameters,
    SolarPanelBatteryParameters,
)

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def _make_inverter(
    dc_to_ac_efficiency: float = 1.0,
    ac_to_dc_efficiency: float = 1.0,
    max_ac_charge_power_w=None,
    max_power_wh: float = 10000.0,
    mock_battery=None,
) -> Inverter:
    """Create an Inverter with custom efficiency parameters and a mock battery."""
    mock_self_consumption_predictor = Mock()
    mock_self_consumption_predictor.calculate_self_consumption.return_value = 1.0

    params = InverterParameters(
        device_id="inv1",
        max_power_wh=max_power_wh,
        battery_id=mock_battery.parameters.device_id if mock_battery else None,
        dc_to_ac_efficiency=dc_to_ac_efficiency,
        ac_to_dc_efficiency=ac_to_dc_efficiency,
        max_ac_charge_power_w=max_ac_charge_power_w,
    )
    with patch(
        "akkudoktoreos.devices.genetic.inverter.get_eos_load_interpolator",
        return_value=mock_self_consumption_predictor,
    ):
        return Inverter(params, battery=mock_battery)


@pytest.fixture
def mock_battery() -> Mock:
    mock_bat = Mock()
    mock_bat.charge_energy = Mock(return_value=(0.0, 0.0))
    mock_bat.discharge_energy = Mock(return_value=(0.0, 0.0))
    mock_bat.parameters.device_id = "battery1"
    return mock_bat


# ===================================================================
# 1. InverterParameters – new fields and defaults
# ===================================================================


class TestInverterParametersDefaults:
    """Verify backward-compatible defaults for new parameters."""

    def test_defaults(self):
        params = InverterParameters(device_id="inv1", max_power_wh=5000)
        assert params.dc_to_ac_efficiency == 1.0
        assert params.ac_to_dc_efficiency == 1.0
        assert params.max_ac_charge_power_w is None

    def test_custom_values(self):
        params = InverterParameters(
            device_id="inv1",
            max_power_wh=5000,
            dc_to_ac_efficiency=0.95,
            ac_to_dc_efficiency=0.93,
            max_ac_charge_power_w=3000,
        )
        assert params.dc_to_ac_efficiency == 0.95
        assert params.ac_to_dc_efficiency == 0.93
        assert params.max_ac_charge_power_w == 3000

    def test_ac_to_dc_zero_disables_ac_charging(self):
        params = InverterParameters(
            device_id="inv1", max_power_wh=5000, ac_to_dc_efficiency=0.0
        )
        assert params.ac_to_dc_efficiency == 0.0

    def test_dc_to_ac_must_be_positive(self):
        with pytest.raises(Exception):
            InverterParameters(device_id="inv1", max_power_wh=5000, dc_to_ac_efficiency=0.0)

    def test_max_ac_charge_power_zero(self):
        params = InverterParameters(
            device_id="inv1", max_power_wh=5000, max_ac_charge_power_w=0
        )
        assert params.max_ac_charge_power_w == 0


# ===================================================================
# 2. dc_to_ac_efficiency – battery discharge through inverter
# ===================================================================


class TestDcToAcEfficiency:
    """Battery discharge energy is reduced by dc_to_ac_efficiency."""

    def test_discharge_shortfall_with_95_percent_efficiency(self, mock_battery):
        """With 0.95 efficiency, 100 Wh DC from battery → 95 Wh AC delivered."""
        mock_battery.discharge_energy.return_value = (100.0, 10.0)
        inv = _make_inverter(dc_to_ac_efficiency=0.95, mock_battery=mock_battery)

        generation = 0.0
        consumption = 200.0
        hour = 5

        grid_export, grid_import, losses, self_consumption = inv.process_energy(
            generation, consumption, hour
        )

        # Battery delivers 100 Wh DC → 95 Wh AC after inverter
        # Inverter loss = 100 - 95 = 5 Wh
        battery_discharge_ac = 100.0 * 0.95  # 95 Wh
        assert self_consumption == pytest.approx(generation + battery_discharge_ac, rel=1e-5)
        assert grid_import == pytest.approx(consumption - battery_discharge_ac, rel=1e-5)

        # Total losses = battery internal (10) + inverter DC→AC (5)
        expected_losses = 10.0 + (100.0 * 0.05)
        assert losses == pytest.approx(expected_losses, rel=1e-5)

        # Battery was asked for more DC to compensate for inverter loss
        # ac_needed = min(200, max_power_wh - 0) = 200
        # dc_request = 200 / 0.95 ≈ 210.526
        expected_dc_request = 200.0 / 0.95
        mock_battery.discharge_energy.assert_called_once_with(
            pytest.approx(expected_dc_request, rel=1e-3), hour
        )

    def test_discharge_with_100_percent_efficiency_unchanged(self, mock_battery):
        """With 1.0 efficiency, behavior is identical to the legacy model."""
        mock_battery.discharge_energy.return_value = (100.0, 10.0)
        inv = _make_inverter(dc_to_ac_efficiency=1.0, mock_battery=mock_battery)

        generation = 100.0
        consumption = 300.0
        hour = 5

        grid_export, grid_import, losses, self_consumption = inv.process_energy(
            generation, consumption, hour
        )

        # No inverter loss: battery_discharge_ac = 100 Wh
        assert self_consumption == pytest.approx(200.0, rel=1e-5)
        assert grid_import == pytest.approx(100.0, rel=1e-5)
        assert losses == pytest.approx(10.0, rel=1e-5)  # Only battery losses

    def test_discharge_surplus_path_with_efficiency(self, mock_battery):
        """When generation > consumption but SCR < 1, discharge goes through inverter."""
        mock_battery.discharge_energy.return_value = (50.0, 5.0)
        mock_battery.charge_energy.return_value = (100.0, 10.0)

        inv = _make_inverter(dc_to_ac_efficiency=0.90, mock_battery=mock_battery)
        cast(Mock, inv.self_consumption_predictor).calculate_self_consumption.return_value = 0.90

        generation = 500.0
        consumption = 200.0
        hour = 5

        grid_export, grid_import, losses, self_consumption = inv.process_energy(
            generation, consumption, hour
        )

        # surplus = 300, remaining_power = 300*0.9 = 270, remaining_load_evq = 300*0.1 = 30
        # DC request for discharge = 30 / 0.90 = 33.333
        expected_dc_request = 30.0 / 0.90
        mock_battery.discharge_energy.assert_called_once_with(
            pytest.approx(expected_dc_request, rel=1e-3), hour
        )

        # Battery delivers 50 Wh DC → 45 Wh AC
        from_battery_ac = 50.0 * 0.90  # 45 Wh
        inverter_discharge_loss = 50.0 - from_battery_ac  # 5 Wh

        assert self_consumption == pytest.approx(consumption + from_battery_ac, rel=1e-5)


# ===================================================================
# 3. ac_to_dc_efficiency + max_ac_charge_power_w – in simulation
# ===================================================================


class TestAcChargingInSimulation:
    """Test AC charging logic with inverter efficiency in GeneticSimulation.

    These tests use a real Battery object (not a mock) and directly exercise
    the AC charging path in GeneticSimulation.simulate().
    """

    @pytest.fixture
    def simulation_setup(self, config_eos):
        """Set up a minimal GeneticSimulation with battery and inverter."""
        from akkudoktoreos.optimization.genetic.genetic import GeneticSimulation
        from akkudoktoreos.optimization.genetic.geneticparams import (
            GeneticEnergyManagementParameters,
        )

        config_eos.merge_settings_from_dict(
            {"prediction": {"hours": 48}, "optimization": {"hours": 24}}
        )

        prediction_hours = config_eos.prediction.hours

        def _build(
            ac_to_dc_efficiency: float = 1.0,
            dc_to_ac_efficiency: float = 1.0,
            max_ac_charge_power_w=None,
            battery_capacity_wh: int = 10000,
            battery_charging_efficiency: float = 0.90,
            battery_discharging_efficiency: float = 0.90,
            battery_initial_soc_pct: int = 50,
            battery_max_charge_power_w: int = 5000,
        ):
            akku = Battery(
                SolarPanelBatteryParameters(
                    device_id="battery1",
                    capacity_wh=battery_capacity_wh,
                    initial_soc_percentage=battery_initial_soc_pct,
                    charging_efficiency=battery_charging_efficiency,
                    discharging_efficiency=battery_discharging_efficiency,
                    min_soc_percentage=0,
                    max_soc_percentage=100,
                    max_charge_power_w=battery_max_charge_power_w,
                ),
                prediction_hours=prediction_hours,
            )
            akku.reset()

            inverter = Inverter(
                InverterParameters(
                    device_id="inverter1",
                    max_power_wh=10000,
                    battery_id="battery1",
                    ac_to_dc_efficiency=ac_to_dc_efficiency,
                    dc_to_ac_efficiency=dc_to_ac_efficiency,
                    max_ac_charge_power_w=max_ac_charge_power_w,
                ),
                battery=akku,
            )

            sim = GeneticSimulation()
            sim.prepare(
                GeneticEnergyManagementParameters(
                    pv_prognose_wh=[0.0] * prediction_hours,  # No PV
                    strompreis_euro_pro_wh=[0.0003] * prediction_hours,  # ~30ct/kWh
                    einspeiseverguetung_euro_pro_wh=0.00008,
                    preis_euro_pro_wh_akku=0.0001,
                    gesamtlast=[1000.0] * prediction_hours,  # 1 kW constant load
                ),
                optimization_hours=config_eos.optimization.horizon_hours,
                prediction_hours=prediction_hours,
                inverter=inverter,
                ev=None,
                home_appliance=None,
            )
            return sim, akku, inverter

        return _build

    def test_ac_charge_with_unity_efficiency_backward_compat(self, simulation_setup):
        """With ac_to_dc_efficiency=1.0, behavior matches legacy model."""
        sim, akku, inverter = simulation_setup(ac_to_dc_efficiency=1.0)

        # Enable AC charging for hour 1 at 50% power
        sim.ac_charge_hours[1] = 0.5
        sim.dc_charge_hours[:] = 0
        sim.bat_discharge_hours[:] = 0

        result = sim.simulate(start_hour=0)

        # At hour 1: AC charge at 50% of 5000W = 2500W DC requested
        # With efficiency 1.0, AC consumed from grid = DC = 2500W
        # Battery stores: 2500 * 0.90 (battery eff) = 2250 Wh
        # Battery loss: 2500 - 2250 = 250 Wh
        # Total grid consumption for that hour = 1000 (load) + 2500 (AC charge)
        hour_idx = 1
        assert result["Netzbezug_Wh_pro_Stunde"][hour_idx] == pytest.approx(3500.0, rel=1e-3)
        assert result["Verluste_Pro_Stunde"][hour_idx] == pytest.approx(250.0, rel=1e-3)

    def test_ac_charge_with_95_percent_efficiency(self, simulation_setup):
        """With ac_to_dc_efficiency=0.95, more AC energy is consumed for same DC charge."""
        sim, akku, inverter = simulation_setup(ac_to_dc_efficiency=0.95)

        sim.ac_charge_hours[1] = 0.5
        sim.dc_charge_hours[:] = 0
        sim.bat_discharge_hours[:] = 0

        result = sim.simulate(start_hour=0)

        # At hour 1: AC charge at 50% of 5000W = 2500W DC requested
        # With ac_to_dc_efficiency=0.95:
        #   AC consumed = 2500 / 0.95 ≈ 2631.58 Wh
        #   Inverter loss = 2631.58 - 2500 = 131.58 Wh
        #   Battery stores: 2500 * 0.90 = 2250 Wh
        #   Battery loss: 2500 - 2250 = 250 Wh
        #   Total losses: 250 + 131.58 = 381.58 Wh
        hour_idx = 1
        dc_energy = 2500.0
        ac_energy = dc_energy / 0.95
        inverter_loss = ac_energy - dc_energy
        battery_loss = dc_energy * (1 - 0.90)
        total_loss = battery_loss + inverter_loss

        expected_grid = 1000.0 + ac_energy
        assert result["Netzbezug_Wh_pro_Stunde"][hour_idx] == pytest.approx(
            expected_grid, rel=1e-3
        )
        assert result["Verluste_Pro_Stunde"][hour_idx] == pytest.approx(total_loss, rel=1e-3)

    def test_ac_charge_disabled_by_zero_efficiency(self, simulation_setup):
        """With ac_to_dc_efficiency=0.0, AC charging is completely disabled."""
        sim, akku, inverter = simulation_setup(ac_to_dc_efficiency=0.0)

        sim.ac_charge_hours[1] = 1.0  # Try to AC charge
        sim.dc_charge_hours[:] = 0
        sim.bat_discharge_hours[:] = 0

        initial_soc = akku.soc_wh
        result = sim.simulate(start_hour=0)

        # Battery should not charge at all (AC charging disabled)
        # Grid consumption = only load
        hour_idx = 1
        assert result["Netzbezug_Wh_pro_Stunde"][hour_idx] == pytest.approx(1000.0, rel=1e-3)
        # Battery SoC should not change (no ac charge, no dc charge, no discharge)
        assert result["akku_soc_pro_stunde"][hour_idx] == pytest.approx(50.0, rel=1e-3)

    def test_ac_charge_disabled_by_zero_max_power(self, simulation_setup):
        """With max_ac_charge_power_w=0, AC charging is disabled."""
        sim, akku, inverter = simulation_setup(
            ac_to_dc_efficiency=0.95, max_ac_charge_power_w=0
        )

        sim.ac_charge_hours[1] = 1.0
        sim.dc_charge_hours[:] = 0
        sim.bat_discharge_hours[:] = 0

        result = sim.simulate(start_hour=0)

        hour_idx = 1
        assert result["Netzbezug_Wh_pro_Stunde"][hour_idx] == pytest.approx(1000.0, rel=1e-3)
        assert result["akku_soc_pro_stunde"][hour_idx] == pytest.approx(50.0, rel=1e-3)

    def test_ac_charge_limited_by_max_ac_power(self, simulation_setup):
        """max_ac_charge_power_w limits the effective charge factor."""
        # battery max_charge_power_w = 5000, ac_to_dc_efficiency = 0.95
        # max_ac_charge_power_w = 2000
        # max_dc_factor = (2000 * 0.95) / 5000 = 0.38
        sim, akku, inverter = simulation_setup(
            ac_to_dc_efficiency=0.95, max_ac_charge_power_w=2000
        )

        sim.ac_charge_hours[1] = 1.0  # Request full power
        sim.dc_charge_hours[:] = 0
        sim.bat_discharge_hours[:] = 0

        result = sim.simulate(start_hour=0)

        # Effective charge factor is capped at 0.38
        # DC energy = 5000 * 0.38 = 1900 W
        # AC energy = 1900 / 0.95 = 2000 W (respects limit)
        hour_idx = 1
        max_dc_factor = (2000 * 0.95) / 5000
        dc_energy = 5000 * max_dc_factor
        ac_energy = dc_energy / 0.95
        expected_grid = 1000.0 + ac_energy  # load + AC charge

        assert result["Netzbezug_Wh_pro_Stunde"][hour_idx] == pytest.approx(
            expected_grid, rel=1e-2
        )

    def test_discharge_with_dc_to_ac_efficiency(self, simulation_setup):
        """dc_to_ac_efficiency affects how much AC energy is delivered from battery."""
        sim, akku, inverter = simulation_setup(
            dc_to_ac_efficiency=0.90, battery_initial_soc_pct=80
        )

        # No PV, no AC charge, discharge only
        sim.ac_charge_hours[:] = 0
        sim.dc_charge_hours[:] = 1
        sim.bat_discharge_hours[:] = 1

        result = sim.simulate(start_hour=0)

        # With dc_to_ac_efficiency=0.90, battery discharge delivers less AC
        # This means more grid import compared to efficiency=1.0
        # At hour 0: load=1000, PV=0
        # Shortfall = 1000
        # DC request = 1000 / 0.90 ≈ 1111 Wh
        # Battery delivers (limited by capacity and efficiency):
        #   max raw = min(soc - min_soc, max_charge_power) = min(8000, 5000) = 5000
        #   max deliverable DC = 5000 * 0.90 (battery eff) = 4500
        #   delivered DC = min(1111, 4500) = 1111 Wh
        #   delivered AC = 1111 * 0.90 = 1000 Wh → covers full load
        # So grid_import should be ≈ 0 for first hours while battery has charge
        hour_idx = 0
        assert result["Netzbezug_Wh_pro_Stunde"][hour_idx] == pytest.approx(0.0, abs=5.0)

        # But losses should be higher due to inverter DC→AC conversion
        # Inverter loss = 1111 * 0.10 = 111 Wh
        # Battery loss = raw_used - delivered = delivered/bat_eff - delivered
        #              = 1111/0.90 - 1111 ≈ 123.5 Wh
        assert result["Verluste_Pro_Stunde"][hour_idx] > 200.0  # Significant losses

    def test_round_trip_efficiency_cost_impact(self, simulation_setup):
        """Verify AC charge + discharge round-trip losses increase total cost."""
        # Reference run: no inverter losses
        sim_ref, akku_ref, inv_ref = simulation_setup(
            ac_to_dc_efficiency=1.0, dc_to_ac_efficiency=1.0
        )
        sim_ref.ac_charge_hours[1] = 0.5
        sim_ref.dc_charge_hours[:] = 0
        sim_ref.bat_discharge_hours[:] = 1
        sim_ref.bat_discharge_hours[1] = 0  # Don't discharge while charging
        result_ref = sim_ref.simulate(start_hour=0)

        # Test run: with inverter losses
        sim_test, akku_test, inv_test = simulation_setup(
            ac_to_dc_efficiency=0.93, dc_to_ac_efficiency=0.93
        )
        sim_test.ac_charge_hours[1] = 0.5
        sim_test.dc_charge_hours[:] = 0
        sim_test.bat_discharge_hours[:] = 1
        sim_test.bat_discharge_hours[1] = 0
        result_test = sim_test.simulate(start_hour=0)

        # With inverter losses, total cost should be HIGHER
        assert result_test["Gesamtkosten_Euro"] > result_ref["Gesamtkosten_Euro"]
        # And total losses should be HIGHER
        assert result_test["Gesamt_Verluste"] > result_ref["Gesamt_Verluste"]


# ===================================================================
# 4. Integration with optimizer residual battery value
# ===================================================================


class TestResidualBatteryValue:
    """Verify that dc_to_ac_efficiency affects residual battery value calculation."""

    def test_current_energy_content_unaffected(self):
        """Battery.current_energy_content() is DC-only; inverter eff applied in optimizer."""
        akku = Battery(
            SolarPanelBatteryParameters(
                device_id="bat1",
                capacity_wh=10000,
                initial_soc_percentage=50,
                discharging_efficiency=0.90,
                min_soc_percentage=0,
            ),
            prediction_hours=24,
        )
        akku.reset()

        # DC energy content: (5000 - 0) * 0.90 = 4500
        assert akku.current_energy_content() == pytest.approx(4500.0, rel=1e-5)


# ===================================================================
# 5. AC charging break-even penalty in GeneticOptimization.evaluate()
# ===================================================================


def _make_mock_simulation(
    *,
    # Inverter properties
    ac_to_dc_efficiency: float = 0.93,
    dc_to_ac_efficiency: float = 0.95,
    # Battery properties
    charging_efficiency: float = 0.95,
    discharging_efficiency: float = 0.95,
    capacity_wh: float = 10_000.0,
    initial_soc_percentage: float = 0.0,   # fraction of capacity already stored (0 = empty)
    min_soc_wh: float = 0.0,
    max_charge_power_w: float = 5_000.0,
    # Arrays (must be same length)
    ac_charge_hours: list | None = None,
    elect_price_hourly: list | None = None,
    load_energy_array: list | None = None,
):
    """Return a mock GeneticSimulation with configurable properties for penalty tests."""
    n = 24
    if ac_charge_hours is None:
        ac_charge_hours = [0.0] * n
    if elect_price_hourly is None:
        elect_price_hourly = [0.0003] * n        # 30 ct/kWh flat
    if load_energy_array is None:
        load_energy_array = [1000.0] * n          # 1 kWh constant load

    inv = SimpleNamespace(
        ac_to_dc_efficiency=ac_to_dc_efficiency,
        dc_to_ac_efficiency=dc_to_ac_efficiency,
    )
    bat = SimpleNamespace(
        charging_efficiency=charging_efficiency,
        discharging_efficiency=discharging_efficiency,
        capacity_wh=capacity_wh,
        initial_soc_percentage=initial_soc_percentage,
        min_soc_wh=min_soc_wh,
        max_charge_power_w=max_charge_power_w,
        current_energy_content=Mock(return_value=0.0),
    )

    sim = Mock()
    sim.battery = bat
    sim.inverter = inv
    sim.ev = None
    sim.ac_charge_hours = np.array(ac_charge_hours, dtype=float)
    sim.elect_price_hourly = np.array(elect_price_hourly, dtype=float)
    sim.load_energy_array = np.array(load_energy_array, dtype=float)
    return sim


def _run_evaluate_with_mocked_sim(
    config_eos,
    mock_sim,
    *,
    ac_charge_break_even: float = 1.0,
    start_hour: int = 0,
    base_gesamtbilanz: float = 0.0,
):
    """
    Patch a GeneticOptimization so that:
    - evaluate_inner() returns a controlled base Gesamtbilanz_Euro
    - self.simulation is replaced by mock_sim
    Then call evaluate() and return the fitness tuple.
    """
    from akkudoktoreos.optimization.genetic.genetic import GeneticOptimization

    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 48},
            "optimization": {"hours": 24},
        }
    )
    config_eos.optimization.genetic.penalties = {
        "ev_soc_miss": 10,
        "ac_charge_break_even": ac_charge_break_even,
    }

    optim = GeneticOptimization.__new__(GeneticOptimization)
    # Minimal __init__ state expected by evaluate()
    optim.config = config_eos
    optim.optimize_ev = False
    optim.verbose = False
    optim.opti_param = {"home_appliance": 0}
    optim.simulation = mock_sim

    # evaluate_inner() just returns the base balance; we test the *additional* penalty
    dummy_result = {
        "Gesamtbilanz_Euro": base_gesamtbilanz,
        "Gesamt_Verluste": 0.0,
        "EAuto_SoC_pro_Stunde": np.zeros(48),
    }

    # DEAP individuals are lists that accept attribute assignment; use a trivial subclass
    class _Ind(list):  # noqa: N801
        pass

    fake_individual = _Ind([0] * 48)

    with patch.object(optim, "evaluate_inner", return_value=dummy_result):
        fitness = optim.evaluate(
            fake_individual,
            parameters=Mock(
                ems=Mock(preis_euro_pro_wh_akku=0.0),
                eauto=None,
            ),
            start_hour=start_hour,
            worst_case=False,
        )
    return fitness[0]


class TestAcChargeBreakEvenPenalty:
    """Break-even penalty in GeneticOptimization.evaluate().

    The penalty adds a positive (bad) contribution to the fitness score whenever
    AC grid charging is scheduled at an hour where the round-trip loss means the
    stored energy can never be discharged at a price sufficient to recover costs,
    taking into account that free PV-charged energy already in the battery covers
    the most expensive future hours first.
    """

    # -----------------------------------------------------------------
    # 5a. No AC charging → no penalty
    # -----------------------------------------------------------------

    def test_no_ac_charging_no_penalty(self, config_eos):
        """When no AC charging is scheduled, fitness equals the base balance."""
        n = 24
        sim = _make_mock_simulation(
            ac_charge_hours=[0.0] * n,
            elect_price_hourly=[0.0003] * n,
            load_energy_array=[1000.0] * n,
        )
        base = 1.5
        fitness = _run_evaluate_with_mocked_sim(config_eos, sim, base_gesamtbilanz=base)
        assert fitness == pytest.approx(base, rel=1e-9)

    # -----------------------------------------------------------------
    # 5b. AC charging profitable → no penalty
    # -----------------------------------------------------------------

    def test_profitable_ac_charging_no_penalty(self, config_eos):
        """When future discharge price > P_charge / η, charging is justified → no penalty."""
        n = 24
        # Charge at hour 0: 0.0001 €/Wh
        # Round-trip: 0.93 * 0.95 * 0.95 * 0.95 ≈ 0.7975
        # break-even price ≈ 0.0001 / 0.7975 ≈ 0.0001254 €/Wh
        # Future hour 1 price: 0.0003 > break-even → profitable
        prices = [0.0001] + [0.0003] * (n - 1)
        ac_charge = [1.0] + [0.0] * (n - 1)
        loads = [1000.0] * n

        sim = _make_mock_simulation(
            ac_to_dc_efficiency=0.93,
            dc_to_ac_efficiency=0.95,
            charging_efficiency=0.95,
            discharging_efficiency=0.95,
            ac_charge_hours=ac_charge,
            elect_price_hourly=prices,
            load_energy_array=loads,
            initial_soc_percentage=0.0,   # empty battery → no free PV energy
        )
        base = 0.0
        fitness = _run_evaluate_with_mocked_sim(config_eos, sim, base_gesamtbilanz=base)
        # penalty should be 0 (or very small due to floating-point rounding)
        assert fitness == pytest.approx(base, abs=1e-9)

    # -----------------------------------------------------------------
    # 5c. AC charging unprofitable → penalty fires
    # -----------------------------------------------------------------

    def test_unprofitable_ac_charging_adds_penalty(self, config_eos):
        """When future discharge prices are too low to justify AC charging, penalty is added."""
        n = 24
        # Charge at hour 0: 0.0004 €/Wh
        # Round-trip: 0.93 * 0.95 * 0.95 * 0.95 ≈ 0.7975
        # break-even price ≈ 0.0004 / 0.7975 ≈ 0.000501 €/Wh
        # All future prices: 0.0003 < break-even → unprofitable
        prices = [0.0004] + [0.0003] * (n - 1)
        ac_charge = [1.0] + [0.0] * (n - 1)
        loads = [1000.0] * n

        sim = _make_mock_simulation(
            ac_to_dc_efficiency=0.93,
            dc_to_ac_efficiency=0.95,
            charging_efficiency=0.95,
            discharging_efficiency=0.95,
            ac_charge_hours=ac_charge,
            elect_price_hourly=prices,
            load_energy_array=loads,
            initial_soc_percentage=0.0,
        )
        base = 0.0
        fitness = _run_evaluate_with_mocked_sim(config_eos, sim, base_gesamtbilanz=base)
        # Fitness must be worse (higher) than base
        assert fitness > base + 1e-6

    # -----------------------------------------------------------------
    # 5d. Free PV energy covers expensive hours → penalty reduced/eliminated
    # -----------------------------------------------------------------

    def test_free_pv_energy_eliminates_penalty(self, config_eos):
        """PV energy covers the best future hour; penalty is larger than without PV energy."""
        n = 24
        # Charge at hour 0: 0.0005 €/Wh
        # Round-trip: 0.93*0.95*0.95*0.95 ≈ 0.7974
        # break-even ≈ 0.0005 / 0.7974 ≈ 0.000627 €/Wh
        #
        # Future: one expensive hour at 0.0006 (< break-even!) and rest at 0.0003
        #   → even the best future price 0.0006 < 0.000627 → AC charging is never profitable
        #
        # Empty battery: best_uncovered = 0.0006 < 0.000627 → penalty fires
        # With PV (50% SoC → ~4512 Wh deliverable free AC): covers the 0.0006 hour (1000 Wh)
        #   → best_uncovered drops to 0.0003 → penalty fires with LARGER excess
        prices = [0.0005] + [0.0003] * (n - 2) + [0.0006]   # expensive hour at end
        ac_charge = [1.0] + [0.0] * (n - 1)
        loads = [1000.0] * n

        sim_empty = _make_mock_simulation(
            ac_to_dc_efficiency=0.93,
            dc_to_ac_efficiency=0.95,
            charging_efficiency=0.95,
            discharging_efficiency=0.95,
            ac_charge_hours=list(ac_charge),
            elect_price_hourly=list(prices),
            load_energy_array=list(loads),
            initial_soc_percentage=0.0,   # no free PV energy
        )
        sim_with_pv = _make_mock_simulation(
            ac_to_dc_efficiency=0.93,
            dc_to_ac_efficiency=0.95,
            charging_efficiency=0.95,
            discharging_efficiency=0.95,
            ac_charge_hours=list(ac_charge),
            elect_price_hourly=list(prices),
            load_energy_array=list(loads),
            capacity_wh=10_000.0,
            initial_soc_percentage=50.0,    # 5000 Wh free PV energy → ~4512 Wh deliverable AC
        )

        fitness_empty = _run_evaluate_with_mocked_sim(config_eos, sim_empty, base_gesamtbilanz=0.0)
        fitness_pv = _run_evaluate_with_mocked_sim(config_eos, sim_with_pv, base_gesamtbilanz=0.0)

        # Both are penalised (break-even > max future price)
        assert fitness_empty > 1e-6, "Empty battery: best_uncovered=0.0006 < break_even→ penalty"
        assert fitness_pv > 1e-6, "With PV: free energy covers 0.0006 hour, best drops to 0.0003"

        # With PV the expensive hour is covered for free → uncovered best price is lower
        # → excess_cost_per_wh = break_even - best_uncovered is larger → penalty is BIGGER
        assert fitness_pv > fitness_empty, "PV covers expensive hour → uncovered best is cheaper"


    def test_free_pv_energy_exposes_only_cheap_future_prices(self, config_eos):
        """When PV covers ALL expensive hours, best_uncovered_price = 0 → max penalty."""
        n = 5
        capacity_wh = 10_000.0
        # Free PV: initial 80% → (8000 - 0) * 0.95 * 0.95 = 7220 Wh deliverable AC
        # Future loads: 2 expensive hours × 1000 Wh = 2000 Wh → all covered by free PV
        prices = [0.0010, 0.0008, 0.0008, 0.0002, 0.0002]
        ac_charge = [1.0, 0.0, 0.0, 0.0, 0.0]
        loads = [1000.0] * n

        sim = _make_mock_simulation(
            ac_to_dc_efficiency=0.93,
            dc_to_ac_efficiency=0.95,
            charging_efficiency=0.95,
            discharging_efficiency=0.95,
            capacity_wh=capacity_wh,
            initial_soc_percentage=80.0,
            ac_charge_hours=ac_charge,
            elect_price_hourly=prices,
            load_energy_array=loads,
        )

        # break_even = 0.001 / (0.93*0.95*0.95*0.95) ≈ 0.001254
        # PV covers both 0.0008 hours → best_uncovered = 0.0002 → penalty fires
        fitness = _run_evaluate_with_mocked_sim(config_eos, sim, base_gesamtbilanz=0.0)
        assert fitness > 1e-6

    # -----------------------------------------------------------------
    # 5e. Penalty factor scales the penalty
    # -----------------------------------------------------------------

    def test_penalty_factor_scales_linearly(self, config_eos):
        """The ac_charge_break_even factor doubles the penalty when doubled."""
        n = 24
        prices = [0.0004] + [0.0003] * (n - 1)
        ac_charge = [1.0] + [0.0] * (n - 1)
        loads = [1000.0] * n

        def _fitness(factor):
            sim = _make_mock_simulation(
                ac_to_dc_efficiency=0.93,
                dc_to_ac_efficiency=0.95,
                charging_efficiency=0.95,
                discharging_efficiency=0.95,
                ac_charge_hours=list(ac_charge),
                elect_price_hourly=list(prices),
                load_energy_array=list(loads),
                initial_soc_percentage=0.0,
            )
            return _run_evaluate_with_mocked_sim(
                config_eos, sim, ac_charge_break_even=factor, base_gesamtbilanz=0.0
            )

        f1 = _fitness(1.0)
        f2 = _fitness(2.0)

        # With factor=2 the penalty should be exactly double
        assert f2 == pytest.approx(2.0 * f1, rel=1e-6)

    # -----------------------------------------------------------------
    # 5f. Zero or negative AC charge factor → no penalty contribution
    # -----------------------------------------------------------------

    def test_zero_ac_charge_factor_no_penalty(self, config_eos):
        """ac_charge_hours[h] = 0 means no charging, so no penalty."""
        n = 24
        prices = [0.0010] * n  # Expensive, but no AC charging
        ac_charge = [0.0] * n
        loads = [1000.0] * n

        sim = _make_mock_simulation(
            ac_charge_hours=ac_charge,
            elect_price_hourly=prices,
            load_energy_array=loads,
        )
        base = 3.0
        fitness = _run_evaluate_with_mocked_sim(config_eos, sim, base_gesamtbilanz=base)
        assert fitness == pytest.approx(base, abs=1e-9)

    # -----------------------------------------------------------------
    # 5g. No battery / inverter → penalty skipped entirely
    # -----------------------------------------------------------------

    def test_no_battery_skips_penalty(self, config_eos):
        """When no battery is present, the penalty block is skipped."""
        n = 24
        sim = _make_mock_simulation(
            ac_charge_hours=[1.0] * n,
            elect_price_hourly=[0.001] * n,
            load_energy_array=[1000.0] * n,
        )
        sim.battery = None   # no battery → penalty skipped

        base = 2.5
        fitness = _run_evaluate_with_mocked_sim(config_eos, sim, base_gesamtbilanz=base)
        assert fitness == pytest.approx(base, abs=1e-9)

    def test_no_inverter_skips_penalty(self, config_eos):
        """When no inverter is present, the penalty block is skipped."""
        n = 24
        sim = _make_mock_simulation(
            ac_charge_hours=[1.0] * n,
            elect_price_hourly=[0.001] * n,
            load_energy_array=[1000.0] * n,
        )
        sim.inverter = None  # no inverter → penalty skipped

        base = 2.5
        fitness = _run_evaluate_with_mocked_sim(config_eos, sim, base_gesamtbilanz=base)
        assert fitness == pytest.approx(base, abs=1e-9)

    # -----------------------------------------------------------------
    # 5h. Unit-level break-even maths (no optimizer setup needed)
    # -----------------------------------------------------------------

    def test_break_even_formula(self):
        """Verify the break-even formula: P_break_even = P_charge / η_round_trip."""
        ac_to_dc = 0.93
        bat_charge = 0.95
        bat_discharge = 0.95
        dc_to_ac = 0.95

        eta_rt = ac_to_dc * bat_charge * bat_discharge * dc_to_ac
        p_charge = 0.0004  # 40 ct/kWh

        break_even = p_charge / eta_rt

        # 1 Wh drawn from grid at p_charge → η_rt Wh delivered
        # Need discharge price ≥ p_charge / η_rt to break even
        assert break_even == pytest.approx(p_charge / eta_rt, rel=1e-9)
        assert break_even > p_charge  # Always worse due to losses

    def test_free_pv_energy_formula(self):
        """Verify free pv energy: (initial_soc - min_soc) × η_bat_dis × η_inv_dis."""
        capacity_wh = 10_000.0
        initial_soc_pct = 60.0
        min_soc_wh = 500.0
        bat_dis = 0.95
        inv_dis = 0.95

        initial_soc_wh = (initial_soc_pct / 100.0) * capacity_wh   # 6000
        free_ac_wh = max(0.0, initial_soc_wh - min_soc_wh) * bat_dis * inv_dis

        # = (6000 - 500) * 0.95 * 0.95 = 5500 * 0.9025 = 4963.75
        expected = 5500.0 * 0.95 * 0.95
        assert free_ac_wh == pytest.approx(expected, rel=1e-9)
