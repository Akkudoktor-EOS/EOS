from pathlib import Path

import numpy as np
import pytest

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
from akkudoktoreos.prediction.interpolator import SelfConsumptionPropabilityInterpolator

start_hour = 0


# Example initialization of necessary components
@pytest.fixture
def create_ems_instance(config_eos) -> EnergieManagementSystem:
    """Fixture to create an EnergieManagementSystem instance with given test parameters."""
    # Assure configuration holds the correct values
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
    ev = Battery(
        ElectricVehicleParameters(
            capacity_wh=26400, initial_soc_percentage=100, min_soc_percentage=100
        ),
        hours=config_eos.prediction_hours,
    )

    # Parameters based on previous example data
    pv_prediction_wh = [0.0] * config_eos.prediction_hours
    pv_prediction_wh[10] = 5000.0
    pv_prediction_wh[11] = 5000.0

    electricity_price_euro_per_wh = [0.001] * config_eos.prediction_hours
    electricity_price_euro_per_wh[0:10] = [0.00001] * 10
    electricity_price_euro_per_wh[11:15] = [0.00005] * 4
    electricity_price_euro_per_wh[20] = 0.00001

    feed_in_tariff_euro_per_wh = [0.00007] * len(electricity_price_euro_per_wh)
    price_euro_per_wh_battery = 0.0001

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
            pv_prediction_wh=pv_prediction_wh,
            electricity_price_euro_per_wh=electricity_price_euro_per_wh,
            feed_in_tariff_euro_per_wh=feed_in_tariff_euro_per_wh,
            price_euro_per_wh_battery=price_euro_per_wh_battery,
            gesamtlast=gesamtlast,
        ),
        inverter=inverter,
        ev=ev,
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
    result = ems.simulate(start_hour=start_hour)

    # --- Pls do not remove! ---
    # visualisiere_ergebnisse(
    #     ems.gesamtlast,
    #     ems.pv_prediction_wh,
    #     ems.electricity_price_euro_per_wh,
    #     result,
    #     ems.akku.discharge_array+ems.akku.charge_array,
    #     None,
    #     ems.pv_prediction_wh,
    #     start_hour,
    #     48,
    #     np.full(48, 0.0),
    #     filename="visualization_results.pdf",
    #     extra_data=None,
    # )

    # Assertions to validate results
    assert result is not None, "Result should not be None"
    assert isinstance(result, dict), "Result should be a dictionary"
    assert "load_wh_per_hour" in result, "Result should contain 'load_wh_per_hour'"

    """
    Check the result of the simulation based on expected values.
    """
    # Example result returned from the simulation (used for assertions)
    assert result is not None, "Result should not be None."

    # Check that the result is a dictionary
    assert isinstance(result, dict), "Result should be a dictionary."

    # Verify that the expected keys are present in the result
    expected_keys = [
        "load_wh_per_hour",
        "grid_feed_in_wh_per_hour",
        "grid_demand_wh_per_hour",
        "cost_euro_per_hour",
        "battery_soc_per_hour",
        "revenue_euro_per_hour",
        "total_balance_euro",
        "ev_soc_per_hour",
        "total_revenue_euro",
        "total_costs_euro",
        "losses_per_hour",
        "total_losses",
        "Home_appliance_wh_per_hour",
    ]

    for key in expected_keys:
        assert key in result, f"The key '{key}' should be present in the result."

    # Check the length of the main arrays
    assert len(result["load_wh_per_hour"]) == 48, "The length of 'load_wh_per_hour' should be 48."
    assert (
        len(result["grid_feed_in_wh_per_hour"]) == 48
    ), "The length of 'grid_feed_in_wh_per_hour' should be 48."
    assert (
        len(result["grid_demand_wh_per_hour"]) == 48
    ), "The length of 'grid_demand_wh_per_hour' should be 48."
    assert (
        len(result["cost_euro_per_hour"]) == 48
    ), "The length of 'cost_euro_per_hour' should be 48."
    assert (
        len(result["battery_soc_per_hour"]) == 48
    ), "The length of 'battery_soc_per_hour' should be 48."

    # Verfify DC and AC Charge Bins
    assert (
        abs(result["battery_soc_per_hour"][2] - 44.70681818181818) < 1e-5
    ), "'battery_soc_per_hour[2]' should be 44.70681818181818."
    assert (
        abs(result["battery_soc_per_hour"][10] - 10.0) < 1e-5
    ), "'battery_soc_per_hour[10]' should be 10."

    assert (
        abs(result["grid_feed_in_wh_per_hour"][10] - 3946.93) < 1e-3
    ), "'grid_feed_in_wh_per_hour[11]' should be 4000."

    assert (
        abs(result["grid_feed_in_wh_per_hour"][11] - 0.0) < 1e-3
    ), "'grid_feed_in_wh_per_hour[11]' should be 0.0."

    assert (
        abs(result["battery_soc_per_hour"][20] - 10) < 1e-5
    ), "'battery_soc_per_hour[20]' should be 10."
    assert (
        abs(result["load_wh_per_hour"][20] - 6050.98) < 1e-3
    ), "'grid_feed_in_wh_per_hour[11]' should be 0.0."

    print("All tests passed successfully.")
