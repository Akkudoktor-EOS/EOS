from pathlib import Path

import numpy as np
import pytest

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.ems import (
    EnergieManagementSystem,
    EnergieManagementSystemParameters,
    SimulationResult,
    get_ems,
)
from akkudoktoreos.devices.battery import (
    Battery,
    ElectricVehicleParameters,
    SolarPanelBatteryParameters,
)
from akkudoktoreos.devices.generic import HomeAppliance, HomeApplianceParameters
from akkudoktoreos.devices.inverter import Inverter, InverterParameters
from akkudoktoreos.prediction.interpolator import SelfConsumptionPropabilityInterpolator

start_hour = 1


# Example initialization of necessary components
@pytest.fixture
def create_ems_instance() -> EnergieManagementSystem:
    """Fixture to create an EnergieManagementSystem instance with given test parameters."""
    # Assure configuration holds the correct values
    config_eos = get_config()
    config_eos.merge_settings_from_dict({"prediction_hours": 48, "optimization_hours": 24})
    assert config_eos.prediction_hours is not None

    # Initialize the battery and the inverter
    akku = Battery(
        SolarPanelBatteryParameters(
            capacity_wh=5000, initial_soc_percentage=80, min_soc_percentage=10
        ),
        hours=config_eos.prediction_hours,
    )

    # 1h Load to Sub 1h Load Distribution -> SelfConsumptionRate
    sc = SelfConsumptionPropabilityInterpolator(
        Path(__file__).parent.resolve()
        / ".."
        / "src"
        / "akkudoktoreos"
        / "data"
        / "regular_grid_interpolator.pkl"
    )

    akku.reset()
    inverter = Inverter(sc, InverterParameters(max_power_wh=10000), akku)

    # Household device (currently not used, set to None)
    home_appliance = HomeAppliance(
        HomeApplianceParameters(
            consumption_wh=2000,
            duration_h=2,
        ),
        hours=config_eos.prediction_hours,
    )
    home_appliance.set_starting_time(2)

    # Example initialization of electric car battery
    eauto = Battery(
        ElectricVehicleParameters(
            capacity_wh=26400, initial_soc_percentage=10, min_soc_percentage=10
        ),
        hours=config_eos.prediction_hours,
    )
    eauto.set_charge_per_hour(np.full(config_eos.prediction_hours, 1))

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
    ems = get_ems()
    ems.set_parameters(
        EnergieManagementSystemParameters(
            pv_prognose_wh=pv_prognose_wh,
            strompreis_euro_pro_wh=strompreis_euro_pro_wh,
            einspeiseverguetung_euro_pro_wh=einspeiseverguetung_euro_pro_wh,
            preis_euro_pro_wh_akku=preis_euro_pro_wh_akku,
            gesamtlast=gesamtlast,
        ),
        inverter=inverter,
        eauto=eauto,
        home_appliance=home_appliance,
    )

    return ems


def test_simulation(create_ems_instance):
    """Test the EnergieManagementSystem simulation method."""
    ems = create_ems_instance

    # Simulate starting from hour 1 (this value can be adjusted)

    result = ems.simuliere(start_stunde=start_hour)

    # visualisiere_ergebnisse(
    #     ems.gesamtlast,
    #     ems.pv_prognose_wh,
    #     ems.strompreis_euro_pro_wh,
    #     result,
    #     ems.akku.discharge_array+ems.akku.charge_array,
    #     None,
    #     ems.pv_prognose_wh,
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
    assert SimulationResult(**result) is not None

    # Check the length of the main arrays
    assert (
        len(result["Last_Wh_pro_Stunde"]) == 47
    ), "The length of 'Last_Wh_pro_Stunde' should be 48."
    assert (
        len(result["Netzeinspeisung_Wh_pro_Stunde"]) == 47
    ), "The length of 'Netzeinspeisung_Wh_pro_Stunde' should be 48."
    assert (
        len(result["Netzbezug_Wh_pro_Stunde"]) == 47
    ), "The length of 'Netzbezug_Wh_pro_Stunde' should be 48."
    assert (
        len(result["Kosten_Euro_pro_Stunde"]) == 47
    ), "The length of 'Kosten_Euro_pro_Stunde' should be 48."
    assert (
        len(result["akku_soc_pro_stunde"]) == 47
    ), "The length of 'akku_soc_pro_stunde' should be 48."

    # Verify specific values in the 'Last_Wh_pro_Stunde' array
    assert (
        result["Last_Wh_pro_Stunde"][1] == 1527.13
    ), "The value at index 1 of 'Last_Wh_pro_Stunde' should be 1527.13."
    assert (
        result["Last_Wh_pro_Stunde"][2] == 1468.88
    ), "The value at index 2 of 'Last_Wh_pro_Stunde' should be 1468.88."
    assert (
        result["Last_Wh_pro_Stunde"][12] == 1132.03
    ), "The value at index 12 of 'Last_Wh_pro_Stunde' should be 1132.03."

    # Verify that the value at index 0 is 'None'
    # Check that 'Netzeinspeisung_Wh_pro_Stunde' and 'Netzbezug_Wh_pro_Stunde' are consistent
    assert (
        result["Netzbezug_Wh_pro_Stunde"][1] == 0
    ), "The value at index 1 of 'Netzbezug_Wh_pro_Stunde' should be 0."

    # Verify the total balance
    assert (
        abs(result["Gesamtbilanz_Euro"] - 1.958185274567674) < 1e-5
    ), "Total balance should be 1.958185274567674."

    # Check total revenue and total costs
    assert (
        abs(result["Gesamteinnahmen_Euro"] - 1.168863124510214) < 1e-5
    ), "Total revenue should be 1.168863124510214."
    assert (
        abs(result["Gesamtkosten_Euro"] - 3.127048399077888) < 1e-5
    ), "Total costs should be 3.127048399077888 ."

    # Check the losses
    assert (
        abs(result["Gesamt_Verluste"] - 2871.5330639359036) < 1e-5
    ), "Total losses should be 2871.5330639359036 ."

    # Check the values in 'akku_soc_pro_stunde'
    assert (
        result["akku_soc_pro_stunde"][-1] == 28.675
    ), "The value at index -1 of 'akku_soc_pro_stunde' should be 28.675."
    assert (
        result["akku_soc_pro_stunde"][1] == 25.379090909090905
    ), "The value at index 1 of 'akku_soc_pro_stunde' should be 25.379090909090905."

    # Check home appliances
    assert (
        sum(ems.home_appliance.get_load_curve()) == 2000
    ), "The sum of 'ems.home_appliance.get_load_curve()' should be 2000."

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
