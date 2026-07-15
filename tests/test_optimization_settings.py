from akkudoktoreos.devices.devices import BatteriesCommonSettings
from akkudoktoreos.optimization.genetic.geneticdevices import (
    SolarPanelBatteryParameters,
)
from akkudoktoreos.optimization.optimization import OptimizationCommonSettings


def test_terminal_value_is_independent_from_battery_lcos():
    battery = BatteriesCommonSettings(
        device_id="battery1",
        levelized_cost_of_storage_kwh=0.12,
    )
    optimization = OptimizationCommonSettings(terminal_value_euro_per_kwh=0.20)

    assert battery.levelized_cost_of_storage_kwh == 0.12
    assert optimization.terminal_value_euro_per_kwh == 0.20


def test_terminal_value_defaults_to_zero():
    assert OptimizationCommonSettings().terminal_value_euro_per_kwh == 0.0


def test_genetic_battery_lcos_is_independent_from_terminal_value():
    battery = SolarPanelBatteryParameters(
        device_id="battery1",
        capacity_wh=8000,
        levelized_cost_of_storage_kwh=0.12,
    )

    assert battery.levelized_cost_of_storage_kwh == 0.12
