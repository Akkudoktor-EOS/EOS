import numpy as np
import pytest

from akkudoktoreos.config.config import get_config
from akkudoktoreos.core.ems import (
    EnergieManagementSystem,
    EnergieManagementSystemParameters,
    get_ems,
)
from akkudoktoreos.devices.battery import (
    Battery,
    ElectricVehicleParameters,
    SolarPanelBatteryParameters,
)
from akkudoktoreos.devices.generic import HomeAppliance, HomeApplianceParameters
from akkudoktoreos.devices.inverter import Inverter, InverterParameters

start_hour = 0


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
    akku.reset()
    inverter = Inverter(InverterParameters(max_power_wh=10000), akku)

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
            capacity_wh=26400, initial_soc_percentage=100, min_soc_percentage=100
        ),
        hours=config_eos.prediction_hours,
    )

    # Parameters based on previous example data
    pv_prognose_wh = [0.0] * config_eos.prediction_hours
    pv_prognose_wh[10] = 5000.0
    pv_prognose_wh[11] = 5000.0

    strompreis_euro_pro_wh = [0.001] * config_eos.prediction_hours
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

    ac = np.full(config_eos.prediction_hours, 0.0)
    ac[20] = 1
    ems.set_akku_ac_charge_hours(ac)
    dc = np.full(config_eos.prediction_hours, 0.0)
    dc[11] = 1
    ems.set_akku_dc_charge_hours(dc)

    return ems


def test_simulation(create_ems_instance):
    """Test the EnergieManagementSystem simulation method."""
    ems = create_ems_instance

    # Simulate starting from hour 0 (this value can be adjusted)
    result = ems.simuliere(start_stunde=start_hour)

    # --- Pls do not remove! ---
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
    assert (
        len(result["Last_Wh_pro_Stunde"]) == 48
    ), "The length of 'Last_Wh_pro_Stunde' should be 48."
    assert (
        len(result["Netzeinspeisung_Wh_pro_Stunde"]) == 48
    ), "The length of 'Netzeinspeisung_Wh_pro_Stunde' should be 48."
    assert (
        len(result["Netzbezug_Wh_pro_Stunde"]) == 48
    ), "The length of 'Netzbezug_Wh_pro_Stunde' should be 48."
    assert (
        len(result["Kosten_Euro_pro_Stunde"]) == 48
    ), "The length of 'Kosten_Euro_pro_Stunde' should be 48."
    assert (
        len(result["akku_soc_pro_stunde"]) == 48
    ), "The length of 'akku_soc_pro_stunde' should be 48."

    # Verfify DC and AC Charge Bins
    assert (
        abs(result["akku_soc_pro_stunde"][10] - 10.0) < 1e-5
    ), "'akku_soc_pro_stunde[10]' should be 10."
    assert (
        abs(result["akku_soc_pro_stunde"][11] - 79.275184) < 1e-5
    ), "'akku_soc_pro_stunde[11]' should be 79.275184."

    assert (
        abs(result["Netzeinspeisung_Wh_pro_Stunde"][10] - 3946.93) < 1e-3
    ), "'Netzeinspeisung_Wh_pro_Stunde[11]' should be 4000."

    assert (
        abs(result["Netzeinspeisung_Wh_pro_Stunde"][11] - 0.0) < 1e-3
    ), "'Netzeinspeisung_Wh_pro_Stunde[11]' should be 0.0."

    assert (
        abs(result["akku_soc_pro_stunde"][20] - 98) < 1e-5
    ), "'akku_soc_pro_stunde[11]' should be 98."
    assert (
        abs(result["Last_Wh_pro_Stunde"][20] - 6050.98) < 1e-3
    ), "'Netzeinspeisung_Wh_pro_Stunde[11]' should be 0.0."

    print("All tests passed successfully.")
