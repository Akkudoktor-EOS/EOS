#!/usr/bin/env python3
"""Quick test to verify API field name translations work correctly."""

import json
from akkudoktoreos.optimization.genetic.geneticparams import GeneticEnergyManagementParameters
from akkudoktoreos.optimization.genetic.geneticsolution import GeneticSimulationResult

def test_genetic_params_german_input():
    """Test that German field names are accepted as input."""
    data_de = {
        "pv_prognose_wh": [100.0, 200.0],
        "strompreis_euro_pro_wh": [0.0003, 0.0003],
        "einspeiseverguetung_euro_pro_wh": 0.00007,
        "preis_euro_pro_wh_akku": 0.0001,
        "gesamtlast": [500.0, 600.0],
    }
    params = GeneticEnergyManagementParameters(**data_de)
    assert params.pv_prognose_wh == [100.0, 200.0]
    print("✅ German input accepted")

def test_genetic_params_english_input():
    """Test that English field names are accepted as input."""
    data_en = {
        "pv_forecast_wh": [100.0, 200.0],
        "electricity_price_per_wh": [0.0003, 0.0003],
        "feed_in_tariff_per_wh": 0.00007,
        "price_per_wh_battery": 0.0001,
        "total_load": [500.0, 600.0],
    }
    params = GeneticEnergyManagementParameters(**data_en)
    assert params.pv_prognose_wh == [100.0, 200.0]
    print("✅ English input accepted")

def test_genetic_params_english_output():
    """Test that English field names are used in JSON output."""
    data_de = {
        "pv_prognose_wh": [100.0, 200.0],
        "strompreis_euro_pro_wh": [0.0003, 0.0003],
        "einspeiseverguetung_euro_pro_wh": 0.00007,
        "preis_euro_pro_wh_akku": 0.0001,
        "gesamtlast": [500.0, 600.0],
    }
    params = GeneticEnergyManagementParameters(**data_de)
    json_output = json.loads(params.model_dump_json(by_alias=True))

    assert "pv_forecast_wh" in json_output
    assert "electricity_price_per_wh" in json_output
    assert "feed_in_tariff_per_wh" in json_output
    assert "price_per_wh_battery" in json_output
    assert "total_load" in json_output

    # German names should NOT be in output
    assert "pv_prognose_wh" not in json_output
    assert "strompreis_euro_pro_wh" not in json_output
    print("✅ English output generated")

def test_simulation_result_translations():
    """Test simulation result field translations."""
    data_de = {
        "Last_Wh_pro_Stunde": [100.0, 200.0],
        "EAuto_SoC_pro_Stunde": [50.0, 60.0],
        "Einnahmen_Euro_pro_Stunde": [0.1, 0.2],
        "Gesamt_Verluste": 50.0,
        "Gesamtbilanz_Euro": -10.0,
        "Gesamteinnahmen_Euro": 5.0,
        "Gesamtkosten_Euro": 15.0,
        "Home_appliance_wh_per_hour": [0.0, 100.0],
        "Kosten_Euro_pro_Stunde": [0.2, 0.3],
        "Netzbezug_Wh_pro_Stunde": [50.0, 60.0],
        "Netzeinspeisung_Wh_pro_Stunde": [10.0, 20.0],
        "Verluste_Pro_Stunde": [5.0, 10.0],
        "akku_soc_pro_stunde": [80.0, 90.0],
        "Electricity_price": [0.0003, 0.0003],
    }
    result = GeneticSimulationResult(**data_de)
    json_output = json.loads(result.model_dump_json(by_alias=True))

    # Check English field names in output
    assert "load_wh_per_hour" in json_output
    assert "ev_soc_per_hour" in json_output
    assert "revenue_per_hour" in json_output
    assert "total_losses" in json_output
    assert "total_balance" in json_output
    assert "total_revenue" in json_output
    assert "total_costs" in json_output
    assert "costs_per_hour" in json_output
    assert "grid_consumption_wh_per_hour" in json_output
    assert "grid_feed_in_wh_per_hour" in json_output
    assert "losses_per_hour" in json_output
    assert "battery_soc_per_hour" in json_output

    print("✅ Simulation result translations work")

if __name__ == "__main__":
    test_genetic_params_german_input()
    test_genetic_params_english_input()
    test_genetic_params_english_output()
    test_simulation_result_translations()
    print("\n✅✅✅ All translation tests passed! ✅✅✅")
