"""Contracts shared by the configuration, tariff and optimizer PRs."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from akkudoktoreos.optimization.genetic import geneticprepare
from akkudoktoreos.optimization.genetic0.genetic0request import (
    Genetic0EnergyManagementRequest,
)
from akkudoktoreos.optimization.genetic.geneticrequest import (
    GeneticEnergyManagementRequest,
)
from akkudoktoreos.optimization.optimization import OptimizationAlgorithm


@pytest.mark.parametrize(
    "model", [GeneticEnergyManagementRequest, Genetic0EnergyManagementRequest]
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
    """Each optimizer serves its own solution from its router (ADR 0005, 0011)."""
    from httpx import ASGITransport, AsyncClient

    from akkudoktoreos.server import eos

    fake = SimpleNamespace(optimizer_run=Mock(return_value=None))
    monkeypatch.setattr(eos, "get_ems", lambda: fake)
    monkeypatch.setattr(
        eos,
        "get_config",
        lambda: SimpleNamespace(optimization=SimpleNamespace(algorithms=["GENETIC", "GENETIC0"])),
    )
    async with AsyncClient(
        transport=ASGITransport(app=eos.app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get(f"/v1/energy-management/optimization/solution/{algorithm}")
    assert response.status_code == 404
    assert response.json()["title"] == "Algorithm specific optimization solution retrieval failed"
    fake.optimizer_run.assert_called_once_with(str(algorithm))


@pytest.mark.parametrize("algorithm", ["genetic", "genetic0"])
def test_device_maps_feed_algorithm_specific_converters(algorithm):
    from akkudoktoreos.devices.devices import DevicesCommonSettings

    settings = DevicesCommonSettings.model_validate(
        {
            "batteries": {
                "storage": {"profile_adjustment": {"nominal_capacity_wh": 12000, "max_charge_power_w": 3200}}
            },
            "electric_vehicles": {
                "car": {"profile_adjustment": {"nominal_capacity_wh": 60000, "max_charge_power_w": 7000}}
            },
            "inverters": {"inverter": {"battery_id": "storage", "profile_adjustment": {"nominal_power_w": 5000}}},
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
    assert settings.batteries is not None
    assert settings.electric_vehicles is not None
    assert settings.inverters is not None
    storage = settings.batteries["storage"]
    car = settings.electric_vehicles["car"]
    hybrid = settings.inverters["inverter"]
    washer = settings.home_appliances["washer"]
    battery: Any
    ev: Any
    inverter: Any
    appliance: Any
    if algorithm == "genetic0":
        # GENETIC0 converts the device settings in its parameter preparation (ADR 0004).
        from akkudoktoreos.optimization.genetic0 import genetic0prepare

        battery = genetic0prepare.battery_request(storage)
        ev = genetic0prepare.electric_vehicle_request(car)
        inverter = genetic0prepare.inverter_request(hybrid)
        appliance = genetic0prepare.home_appliance_request(washer)
    else:
        battery = geneticprepare.battery_request(storage)
        ev = geneticprepare.electric_vehicle_request(car)
        inverter = geneticprepare.inverter_request(hybrid)
        appliance = geneticprepare.home_appliance_request(washer)
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
