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

start_hour = 1


# Example initialization of necessary components
@pytest.fixture
def genetic_simulation(config_eos) -> GeneticSimulation:
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

    # Example initialization of electric car battery
    eauto = Battery(
        ElectricVehicleParameters(
            device_id="ev1", capacity_wh=26400, initial_soc_percentage=10, min_soc_percentage=10
        ),
        prediction_hours = config_eos.prediction.hours,
    )
    eauto.set_charge_per_hour(np.full(config_eos.prediction.hours, 1))

    # Parameters based on previous example data
    pv_prognose_wh = [
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        8.05,
        352.91,
        728.51,
        930.28,
        1043.25,
        1106.74,
        1161.69,
        6018.82,
        5519.07,
        3969.88,
        3017.96,
        1943.07,
        1007.17,
        319.67,
        7.88,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        5.04,
        335.59,
        705.32,
        1121.12,
        1604.79,
        2157.38,
        1433.25,
        5718.49,
        4553.96,
        3027.55,
        2574.46,
        1720.4,
        963.4,
        383.3,
        0,
        0,
        0,
    ]

    strompreis_euro_pro_wh = [
        0.0003384,
        0.0003318,
        0.0003284,
        0.0003283,
        0.0003289,
        0.0003334,
        0.0003290,
        0.0003302,
        0.0003042,
        0.0002430,
        0.0002280,
        0.0002212,
        0.0002093,
        0.0001879,
        0.0001838,
        0.0002004,
        0.0002198,
        0.0002270,
        0.0002997,
        0.0003195,
        0.0003081,
        0.0002969,
        0.0002921,
        0.0002780,
        0.0003384,
        0.0003318,
        0.0003284,
        0.0003283,
        0.0003289,
        0.0003334,
        0.0003290,
        0.0003302,
        0.0003042,
        0.0002430,
        0.0002280,
        0.0002212,
        0.0002093,
        0.0001879,
        0.0001838,
        0.0002004,
        0.0002198,
        0.0002270,
        0.0002997,
        0.0003195,
        0.0003081,
        0.0002969,
        0.0002921,
        0.0002780,
    ]

    einspeiseverguetung_euro_pro_wh = 0.00007
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

    # Init for test
    assert simulation.ac_charge_hours is not None
    assert simulation.dc_charge_hours is not None
    assert simulation.bat_discharge_hours is not None
    assert simulation.ev_charge_hours is not None
    simulation.ac_charge_hours[start_hour] = 1.0
    simulation.dc_charge_hours[start_hour] = 1.0
    simulation.bat_discharge_hours[start_hour] = 1.0
    simulation.ev_charge_hours[start_hour] = 1.0
    simulation.home_appliance_start_hour = 2

    return simulation


def test_simulation(genetic_simulation):
    """Test the EnergyManagement simulation method."""
    simulation = genetic_simulation

    # Simulate starting from hour 1 (this value can be adjusted)

    result = simulation.simulate(start_hour=start_hour)

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
    assert "Last_Wh_pro_Stunde" in result, "Result should contain 'Last_Wh_pro_Stunde'"

    """
    Check the result of the simulation based on expected values.
    """
    # Example result returned from the simulation (used for assertions)
    assert result is not None, "Result should not be None."

    # Check that the result is a dictionary
    assert isinstance(result, dict), "Result should be a dictionary."
    assert GeneticSimulationResult(**result) is not None

    # Check the length of the main arrays
    assert len(result["Last_Wh_pro_Stunde"]) == 47, (
        "The length of 'Last_Wh_pro_Stunde' should be 48."
    )
    assert len(result["Netzeinspeisung_Wh_pro_Stunde"]) == 47, (
        "The length of 'Netzeinspeisung_Wh_pro_Stunde' should be 48."
    )
    assert len(result["Netzbezug_Wh_pro_Stunde"]) == 47, (
        "The length of 'Netzbezug_Wh_pro_Stunde' should be 48."
    )
    assert len(result["Kosten_Euro_pro_Stunde"]) == 47, (
        "The length of 'Kosten_Euro_pro_Stunde' should be 48."
    )
    assert len(result["akku_soc_pro_stunde"]) == 47, (
        "The length of 'akku_soc_pro_stunde' should be 48."
    )

    # Verify specific values in the 'Last_Wh_pro_Stunde' array
    assert result["Last_Wh_pro_Stunde"][1] == 1527.13, (
        "The value at index 1 of 'Last_Wh_pro_Stunde' should be 1527.13."
    )
    assert result["Last_Wh_pro_Stunde"][2] == 1468.88, (
        "The value at index 2 of 'Last_Wh_pro_Stunde' should be 1468.88."
    )
    assert result["Last_Wh_pro_Stunde"][12] == 1132.03, (
        "The value at index 12 of 'Last_Wh_pro_Stunde' should be 1132.03."
    )

    # Verify that the value at index 0 is 'None'
    # Check that 'Netzeinspeisung_Wh_pro_Stunde' and 'Netzbezug_Wh_pro_Stunde' are consistent
    assert result["Netzbezug_Wh_pro_Stunde"][1] == 1527.13, (
        "The value at index 1 of 'Netzbezug_Wh_pro_Stunde' should be 1527.13."
    )

    # Verify the total balance
    assert abs(result["Gesamtbilanz_Euro"] - 6.612835813556755) < 1e-5, (
        "Total balance should be 6.612835813556755."
    )

    # Check total revenue and total costs
    assert abs(result["Gesamteinnahmen_Euro"] - 1.964301131937134) < 1e-5, (
        "Total revenue should be 1.964301131937134."
    )
    assert abs(result["Gesamtkosten_Euro"] - 8.577136945493889) < 1e-5, (
        "Total costs should be 8.577136945493889 ."
    )

    # Check the losses
    assert abs(result["Gesamt_Verluste"] - 1620.0) < 1e-5, (
        "Total losses should be 1620.0 ."
    )

    # Check the values in 'akku_soc_pro_stunde'
    assert result["akku_soc_pro_stunde"][-1] == 98.0, (
        "The value at index -1 of 'akku_soc_pro_stunde' should be 98.0."
    )
    assert result["akku_soc_pro_stunde"][1] == 98.0, (
        "The value at index 1 of 'akku_soc_pro_stunde' should be 98.0."
    )

    # Check home appliances
    assert sum(simulation.home_appliance.get_load_curve()) == 2000, (
        "The sum of 'simulation.home_appliance.get_load_curve()' should be 2000."
    )

    assert (
        np.nansum(
            np.where(
                result["Home_appliance_wh_per_hour"] is None,
                np.nan,
                np.array(result["Home_appliance_wh_per_hour"]),
            )
        )
        == 2000
    ), "The sum of 'Home_appliance_wh_per_hour' should be 2000."

    print("All tests passed successfully.")
