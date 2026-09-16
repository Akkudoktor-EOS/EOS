"""Contracts shared by the configuration, tariff and optimizer PRs."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from akkudoktoreos.optimization.genetic.geneticparams import GeneticEnergyManagementParameters
from akkudoktoreos.optimization.genetic0.genetic0params import Genetic0EnergyManagementParameters
from akkudoktoreos.optimization.optimization import OptimizationAlgorithm


@pytest.mark.parametrize(
    "model", [GeneticEnergyManagementParameters, Genetic0EnergyManagementParameters]
)
@pytest.mark.parametrize("tariff", [0.00008, [0.00008, -0.00002]])
def test_energy_parameter_aliases_preserve_wh_prices(model, tariff):
    raw = {
        "pv_prognose_wh": [250.0, 0.0],
        "gesamtlast": [125.0, 125.0],
        "strompreis_euro_pro_wh": [0.0003, -0.0001],
        "einspeiseverguetung_euro_pro_wh": tariff,
        "preis_euro_pro_wh_akku": 0.00005,
    }
    params = model.model_validate(raw)
    data = params.model_dump(mode="json")
    pairs = {
        "pv_prognose_wh": "pv_forecast_wh",
        "gesamtlast": "total_load",
        "strompreis_euro_pro_wh": "electricity_price_per_wh",
        "einspeiseverguetung_euro_pro_wh": "feed_in_tariff_per_wh",
        "preis_euro_pro_wh_akku": "price_per_wh_battery",
    }
    for deprecated, canonical in pairs.items():
        assert data[deprecated] == data[canonical] == raw[deprecated]
    assert (
        model.model_validate({pairs[k]: v for k, v in raw.items()}).model_dump(mode="json") == data
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", list(OptimizationAlgorithm))
async def test_algorithm_result_endpoint_keeps_solution_types_separate(monkeypatch, algorithm):
    from akkudoktoreos.server import eos

    genetic, genetic0 = object(), object()
    fake = SimpleNamespace(
        genetic_solution=Mock(return_value=genetic),
        genetic0_solution=Mock(return_value=genetic0),
    )
    monkeypatch.setattr(eos, "get_ems", lambda: fake)
    monkeypatch.setattr(
        eos,
        "get_config",
        lambda: SimpleNamespace(optimization=SimpleNamespace(algorithms=["GENETIC", "GENETIC0"])),
    )
    result = await eos.fastapi_energy_management_optimization_solution_algorithm_get(algorithm)
    if algorithm == OptimizationAlgorithm.GENETIC:
        assert result is genetic
        fake.genetic_solution.assert_called_once_with()
        fake.genetic0_solution.assert_not_called()
    else:
        assert result is genetic0
        fake.genetic0_solution.assert_called_once_with()
        fake.genetic_solution.assert_not_called()


@pytest.mark.parametrize("algorithm", ["genetic", "genetic0"])
def test_device_maps_feed_algorithm_specific_converters(algorithm):
    from akkudoktoreos.devices.devices import DevicesCommonSettings

    settings = DevicesCommonSettings.model_validate(
        {
            "batteries": {"storage": {"capacity_wh": 12000, "max_charge_power_w": 3200}},
            "electric_vehicles": {"car": {"capacity_wh": 60000, "max_charge_power_w": 7000}},
            "inverters": {"inverter": {"battery_id": "storage", "max_power_w": 5000}},
            "home_appliances": {
                "washer": {
                    "consumption_wh": 1800,
                    "duration_h": 2,
                    "num_cycles": 2,
                    "min_cycle_gap_h": 1,
                }
            },
        }
    )
    battery = getattr(settings.batteries["storage"], "to_" + algorithm + "_pv_bat_param")()
    ev = getattr(settings.electric_vehicles["car"], "to_" + algorithm + "_ev_bat_param")()
    inverter = getattr(settings.inverters["inverter"], "to_" + algorithm + "_param")()
    appliance = getattr(settings.home_appliances["washer"], "to_" + algorithm + "_param")()
    assert (battery.device_id, ev.device_id, inverter.device_id, appliance.device_id) == (
        "storage",
        "car",
        "inverter",
        "washer",
    )
    assert battery.capacity_wh == 12000
    assert battery.max_charge_power_w == 3200
    assert ev.capacity_wh == 60000
    assert ev.max_charge_power_w == 7000
    assert inverter.max_power_wh == 5000
    assert inverter.battery_id == "storage"
    assert appliance.consumption_wh == 1800
    assert appliance.duration_h == 2
    assert settings.batteries["storage"].measurement_key_soc_factor == "storage-soc-factor"
    if algorithm == "genetic":
        assert appliance.num_cycles == 2
        assert appliance.min_cycle_gap_h == 1
