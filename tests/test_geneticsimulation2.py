import numpy as np
import pytest

from akkudoktoreos.devices.genetic.battery import Battery
from akkudoktoreos.devices.genetic.homeappliance import HomeAppliance
from akkudoktoreos.devices.genetic.inverter import Inverter
from akkudoktoreos.optimization.genetic.genetic import GeneticSimulation
from akkudoktoreos.optimization.genetic.geneticdevices import (
    ElectricVehicleParameters,
    HomeApplianceParameters,
    InverterParameters,
    SolarPanelBatteryParameters,
)
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticEnergyManagementParameters,
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSimulationResult
from akkudoktoreos.utils.datetimeutil import (
    TimeWindow,
    TimeWindowSequence,
    to_duration,
    to_time,
)

start_hour = 0


# Example initialization of necessary components
@pytest.fixture
def genetic_simulation_2(config_eos) -> GeneticSimulation:
    """Fixture to create an EnergyManagement instance with given test parameters."""
    # Assure configuration holds the correct values
    config_eos.merge_settings_from_dict(
        {"prediction": {"hours": 48}, "optimization": {"hours": 24}}
    )
    assert config_eos.prediction.hours == 48
    assert config_eos.optimization.horizon_hours == 24

    # Initialize the battery and the inverter
    akku = Battery(
        SolarPanelBatteryParameters(
            device_id="battery1",
            capacity_wh=5000,
            initial_soc_percentage=80,
            min_soc_percentage=10,
        ),
        prediction_hours = config_eos.prediction.hours,
    )
    akku.reset()

    inverter = Inverter(
        InverterParameters(device_id="inverter1", max_power_wh=10000, battery_id=akku.parameters.device_id),
        battery = akku,
    )

    # Household device (currently not used, set to None)
    home_appliance = HomeAppliance(
        HomeApplianceParameters(
            device_id="dishwasher1",
            consumption_wh=2000,
            duration_h=2,
            time_windows=None,
        ),
        optimization_hours = config_eos.optimization.horizon_hours,
        prediction_hours = config_eos.prediction.hours,
    )
    home_appliance.set_starting_time(2)

    # Example initialization of electric car battery
    eauto = Battery(
        ElectricVehicleParameters(
            device_id="ev1", capacity_wh=26400, initial_soc_percentage=10, min_soc_percentage=10
        ),
        prediction_hours = config_eos.prediction.hours,
    )

    # Parameters based on previous example data
    pv_prognose_wh = [0.0] * config_eos.prediction.hours
    pv_prognose_wh[10] = 5000.0
    pv_prognose_wh[11] = 5000.0

    strompreis_euro_pro_wh = [0.001] * config_eos.prediction.hours
    strompreis_euro_pro_wh[0:10] = [0.00001] * 10
    strompreis_euro_pro_wh[11:15] = [0.00005] * 4
    strompreis_euro_pro_wh[20] = 0.00001

    einspeiseverguetung_euro_pro_wh = [0.00007] * len(strompreis_euro_pro_wh)
    preis_euro_pro_wh_akku = 0.0001

    gesamtlast = [
        676.71,
        876.19,
        527.13,
        468.88,
        531.38,
        517.95,
        483.15,
        472.28,
        1011.68,
        995.00,
        1053.07,
        1063.91,
        1320.56,
        1132.03,
        1163.67,
        1176.82,
        1216.22,
        1103.78,
        1129.12,
        1178.71,
        1050.98,
        988.56,
        912.38,
        704.61,
        516.37,
        868.05,
        694.34,
        608.79,
        556.31,
        488.89,
        506.91,
        804.89,
        1141.98,
        1056.97,
        992.46,
        1155.99,
        827.01,
        1257.98,
        1232.67,
        871.26,
        860.88,
        1158.03,
        1222.72,
        1221.04,
        949.99,
        987.01,
        733.99,
        592.97,
    ]

    # Initialize the energy management system with the respective parameters
    simulation = GeneticSimulation()
    simulation.prepare(
        GeneticEnergyManagementParameters(
            pv_prognose_wh=pv_prognose_wh,
            strompreis_euro_pro_wh=strompreis_euro_pro_wh,
            einspeiseverguetung_euro_pro_wh=einspeiseverguetung_euro_pro_wh,
            preis_euro_pro_wh_akku=preis_euro_pro_wh_akku,
            gesamtlast=gesamtlast,
        ),
        optimization_hours = config_eos.optimization.horizon_hours,
        prediction_hours = config_eos.prediction.hours,
        inverter=inverter,
        ev=eauto,
        home_appliance=home_appliance,
    )

    ac = np.full(config_eos.prediction.hours, 0.0)
    ac[20] = 1
    simulation.set_akku_ac_charge_hours(ac)
    dc = np.full(config_eos.prediction.hours, 0.0)
    dc[11] = 1
    simulation.set_akku_dc_charge_hours(dc)

    return simulation


def test_simulation(genetic_simulation_2):
    """Test the EnergyManagement simulation method."""
    simulation = genetic_simulation_2

    # Simulate starting from hour 0 (this value can be adjusted)
    result = simulation.simulate(start_hour=start_hour)

    # --- Pls do not remove! ---
    # visualisiere_ergebnisse(
    #     simulation.gesamtlast,
    #     simulation.pv_prognose_wh,
    #     simulation.strompreis_euro_pro_wh,
    #     result,
    #     simulation.akku.discharge_array+simulation.akku.charge_array,
    #     None,
    #     simulation.pv_prognose_wh,
    #     start_hour,
    #     48,
    #     np.full(48, 0.0),
    #     filename="visualization_results.pdf",
    #     extra_data=None,
    # )

    # Assertions to validate results
    assert result is not None, "Result should not be None"
    assert isinstance(result, dict), "Result should be a dictionary"
    assert GeneticSimulationResult(**result) is not None
    assert "Last_Wh_pro_Stunde" in result, "Result should contain 'Last_Wh_pro_Stunde'"

    """
    Check the result of the simulation based on expected values.
    """
    # Example result returned from the simulation (used for assertions)
    assert result is not None, "Result should not be None."

    # Check that the result is a dictionary
    assert isinstance(result, dict), "Result should be a dictionary."

    # Verify that the expected keys are present in the result
    expected_keys = [
        "Last_Wh_pro_Stunde",
        "Netzeinspeisung_Wh_pro_Stunde",
        "Netzbezug_Wh_pro_Stunde",
        "Kosten_Euro_pro_Stunde",
        "akku_soc_pro_stunde",
        "Einnahmen_Euro_pro_Stunde",
        "Gesamtbilanz_Euro",
        "EAuto_SoC_pro_Stunde",
        "Gesamteinnahmen_Euro",
        "Gesamtkosten_Euro",
        "Verluste_Pro_Stunde",
        "Gesamt_Verluste",
        "Home_appliance_wh_per_hour",
    ]

    for key in expected_keys:
        assert key in result, f"The key '{key}' should be present in the result."

    # Check the length of the main arrays
    assert len(result["Last_Wh_pro_Stunde"]) == 48, (
        "The length of 'Last_Wh_pro_Stunde' should be 48."
    )
    assert len(result["Netzeinspeisung_Wh_pro_Stunde"]) == 48, (
        "The length of 'Netzeinspeisung_Wh_pro_Stunde' should be 48."
    )
    assert len(result["Netzbezug_Wh_pro_Stunde"]) == 48, (
        "The length of 'Netzbezug_Wh_pro_Stunde' should be 48."
    )
    assert len(result["Kosten_Euro_pro_Stunde"]) == 48, (
        "The length of 'Kosten_Euro_pro_Stunde' should be 48."
    )
    assert len(result["akku_soc_pro_stunde"]) == 48, (
        "The length of 'akku_soc_pro_stunde' should be 48."
    )

    # Verfify DC and AC Charge Bins
    assert abs(result["akku_soc_pro_stunde"][2] - 44.70681818181818) < 1e-5, (
        "'akku_soc_pro_stunde[2]' should be 44.70681818181818."
    )
    assert abs(result["akku_soc_pro_stunde"][10] - 10.0) < 1e-5, (
        "'akku_soc_pro_stunde[10]' should be 10."
    )

    assert abs(result["Netzeinspeisung_Wh_pro_Stunde"][10] - 3946.93) < 1e-3, (
        "'Netzeinspeisung_Wh_pro_Stunde[11]' should be 3946.93."
    )

    assert abs(result["Netzeinspeisung_Wh_pro_Stunde"][11] - 0.0) < 1e-3, (
        "'Netzeinspeisung_Wh_pro_Stunde[11]' should be 0.0."
    )

    assert abs(result["akku_soc_pro_stunde"][20] - 10) < 1e-5, (
        "'akku_soc_pro_stunde[20]' should be 10."
    )
    assert abs(result["Last_Wh_pro_Stunde"][20] - 6050.98) < 1e-3, (
        "'Last_Wh_pro_Stunde[20]' should be 6050.98."
    )

    print("All tests passed successfully.")


def test_set_parameters(genetic_simulation_2):
    """Test the set_parameters method of EnergyManagement."""
    simulation = genetic_simulation_2

    # Check if parameters are set correctly
    assert simulation.load_energy_array is not None, "load_energy_array should not be None"
    assert simulation.pv_prediction_wh is not None, "pv_prediction_wh should not be None"
    assert simulation.elect_price_hourly is not None, "elect_price_hourly should not be None"
    assert simulation.elect_revenue_per_hour_arr is not None, (
        "elect_revenue_per_hour_arr should not be None"
    )


def test_set_akku_discharge_hours(genetic_simulation_2):
    """Test the set_akku_discharge_hours method of EnergyManagement."""
    simulation = genetic_simulation_2
    discharge_hours = np.full(simulation.prediction_hours, 1.0)
    simulation.set_akku_discharge_hours(discharge_hours)
    assert np.array_equal(simulation.battery.discharge_array, discharge_hours), (
        "Discharge hours should be set correctly"
    )


def test_set_akku_ac_charge_hours(genetic_simulation_2):
    """Test the set_akku_ac_charge_hours method of EnergyManagement."""
    simulation = genetic_simulation_2
    ac_charge_hours = np.full(simulation.prediction_hours, 1.0)
    simulation.set_akku_ac_charge_hours(ac_charge_hours)
    assert np.array_equal(simulation.ac_charge_hours, ac_charge_hours), (
        "AC charge hours should be set correctly"
    )


def test_set_akku_dc_charge_hours(genetic_simulation_2):
    """Test the set_akku_dc_charge_hours method of EnergyManagement."""
    simulation = genetic_simulation_2
    dc_charge_hours = np.full(simulation.prediction_hours, 1.0)
    simulation.set_akku_dc_charge_hours(dc_charge_hours)
    assert np.array_equal(simulation.dc_charge_hours, dc_charge_hours), (
        "DC charge hours should be set correctly"
    )


def test_set_ev_charge_hours(genetic_simulation_2):
    """Test the set_ev_charge_hours method of EnergyManagement."""
    simulation = genetic_simulation_2
    ev_charge_hours = np.full(simulation.prediction_hours, 1.0)
    simulation.set_ev_charge_hours(ev_charge_hours)
    assert np.array_equal(simulation.ev_charge_hours, ev_charge_hours), (
        "EV charge hours should be set correctly"
    )


def test_reset(genetic_simulation_2):
    """Test the reset method of EnergyManagement."""
    simulation = genetic_simulation_2
    simulation.reset()
    assert simulation.ev.current_soc_percentage() == simulation.ev.parameters.initial_soc_percentage, "EV SOC should be reset to initial value"
    assert simulation.battery.current_soc_percentage() == simulation.battery.parameters.initial_soc_percentage, (
        "Battery SOC should be reset to initial value"
    )
