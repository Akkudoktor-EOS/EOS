"""Compatibility contracts of existing optimization clients, using the real ASGI routes."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from test_ohmrakel42ems import (
    SIMPLE_DEVICES,
    SIMPLE_SOC,
    START,
    configure,
    ems_world,  # noqa: F401 (fixture)
    fake_measurement_fixture,  # noqa: F401 (fixture)
    forecast_series,
)

from akkudoktoreos.server import eos

PDF = "/v1/energy-management/optimization/solution/GENETIC/pdf"
LEGACY_PDF = "/visualization_results.pdf"
NATIVE = "/v1/energy-management/optimization/solution/OHMRAKEL42"
FIXTURES = Path(__file__).parent / "testdata" / "genetic0"


@pytest.mark.parametrize("filename", ["optimize_input_1.json", "optimize_input_2.json"])
def test_hourly_legacy_request_only_uses_body(ems_world, config_eos, monkeypatch, filename):
    configure(config_eos, SIMPLE_DEVICES, interval=900)
    original = config_eos.model_dump_json(exclude_computed_fields=True)
    monkeypatch.setattr(eos, "to_datetime", Mock(return_value=START))
    monkeypatch.setattr(eos.app.state, "hourly_optimization_outcome", None, raising=False)
    client = TestClient(eos.app)
    assert client.get(LEGACY_PDF).status_code == 404
    raw = json.loads((FIXTURES / filename).read_text(encoding="utf8"))

    response = client.post("/optimize?start_hour=10&ngen=10", json=raw)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["start_solution"] is None
    assert set(result["discharge_allowed"]) <= {0, 1}
    for key in ("ac_charge", "dc_charge", "discharge_allowed", "ev_charge_hours_float"):
        assert len(result[key]) == 48 and result[key][:10] == [0] * 10
    assert result["eautocharge_hours_float"] == result["ev_charge_hours_float"]
    assert result["eauto_obj"] == result["ev_obj"]
    assert "max_discharge_power_w" not in result["ev_obj"]
    expected_soc = raw.get("pv_akku", raw.get("pv_battery"))["initial_soc_percentage"]
    assert result["result"]["battery_soc_per_hour"][0] == expected_soc
    assert result["result"]["Electricity_price"] == raw["ems"]["strompreis_euro_pro_wh"][10:]
    assert result["result"]["Gesamtbilanz_Euro"] == pytest.approx(
        result["result"]["total_costs"] - result["result"]["total_revenue"]
    )
    if raw.get("dishwasher"):
        assert 10 <= result["washingstart"] < 24
    assert config_eos.model_dump_json(exclude_computed_fields=True) == original
    assert ems_world.plan() is None
    ems_world.adapter.update_data.assert_not_awaited()
    ems_world.prediction.update_data.assert_not_awaited()
    assert client.get(NATIVE).status_code == 404

    report = client.get(LEGACY_PDF)
    assert report.status_code == 200 and report.headers["content-type"] == "application/pdf"
    assert report.content.startswith(b"%PDF-1.4") and b"OHMRAKEL42" in report.content
    assert client.get(PDF).status_code == 404


def test_hourly_unsupported_device_request_returns_problem(ems_world, config_eos, monkeypatch):
    monkeypatch.setattr(eos, "to_datetime", Mock(return_value=START))
    body = json.loads((FIXTURES / "optimize_input_1.json").read_text(encoding="utf8"))
    client = TestClient(eos.app)
    response = client.post("/optimize", json={**body, "inverter": None})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert "requires an inverter in the request" in response.json()["detail"]
    response = client.post("/optimize?start_hour=24", json=body)
    assert response.status_code == 422


def test_configured_legacy_contract_uses_ohmrakel42(ems_world, config_eos):
    configure(config_eos, SIMPLE_DEVICES, interval=900, horizon=24)
    client = TestClient(eos.app)
    before = config_eos.model_dump_json(exclude_computed_fields=True)
    start = ems_world.config.general.timezone
    # Supply forecasts long enough for an arbitrary local start on a 15-minute grid.
    series = forecast_series("optimize_input_1.json", 900)
    body = {
        "soc": {**SIMPLE_SOC, "ev1": 10.125},
        "forecasts": {
            "pv_prognose_wh": series["pv_forecast_wh"],
            "gesamtlast": series["total_load"],
            "strompreis_euro_pro_wh": series["electricity_price_per_wh"],
            "einspeiseverguetung_euro_pro_wh": series["feed_in_tariff_per_wh"],
            "temperature_forecast": [15.0],
        },
        "start_solution": [1, 0, 1],
        "start_solution_datetime": "2026-09-16T10:15:00+02:00",
    }
    response = client.post("/v1/optimize", json=body)
    assert response.status_code == 200, (start, response.text)
    old = response.json()
    native = client.get(NATIVE).json()
    expected_fields = {
        "parameters",
        "ac_charge",
        "dc_charge",
        "discharge_allowed",
        "ev_charge_hours_float",
        "eautocharge_hours_float",
        "ev_obj",
        "eauto_obj",
        "start_hour",
        "start_solution",
        "start_solution_datetime",
        "washingstart",
        "extra_data",
        "fitness_history",
        "fixed_seed",
        "controls_start_at_now",
        "battery_grid_export_allowed",
        "battery_grid_export_factor",
        "terminal_value",
        "appliance_starts",
        "appliance_deadline_missed",
        "interval_seconds",
        "first_slot_fraction",
        "result",
    }
    assert expected_fields <= old.keys()
    assert old["controls_start_at_now"] is True
    assert old["ev_obj"]["max_discharge_power_w"] >= 0
    assert old["ev_obj"] == old["eauto_obj"]
    assert old["terminal_value"]["tail_operating_euro"] == 0.0
    assert old["interval_seconds"] == native["interval_seconds"] == 900
    assert native["storage_limits"]["ev1"]["initial_soc_percentage"] == 10.125
    assert old["result"]["ev_soc_per_hour"][0] == 10.125
    assert old["ac_charge"] == native["controls"]["bat1_grid_charge"]
    assert old["discharge_allowed"] == native["controls"]["bat1_discharge_limit"]
    assert len(old["ac_charge"]) == 96
    assert old["terminal_value"]["credited_euro"] == pytest.approx(
        native["objective"]["terminal_value_eur"]
    )
    assert old["ev_charge_hours_float"] == native["controls"]["ev1_charge"]
    assert old["parameters"]["ems"]["pv_prognose_wh"] == series["pv_forecast_wh"]
    assert "dishwasher" in old["appliance_starts"]
    assert old["result"]["home_appliance_energy_wh"]["dishwasher"]
    assert old["start_solution"] is None and old["start_solution_datetime"] is None
    assert client.get("/v1/energy-management/plan").status_code == 200
    assert client.get("/v1/energy-management/optimization/solution").status_code == 200
    assert config_eos.model_dump_json(exclude_computed_fields=True) == before
    assert ems_world.adapter.update_data.await_count == 2

    report = client.get(PDF)
    assert report.status_code == 200 and report.headers["content-type"] == "application/pdf"
    assert b"%PDF-1.4" in report.content
    assert client.post("/v1/optimize?ngen=10", json=body).status_code == 422
    paths = client.get("/openapi.json").json()["paths"]
    assert paths["/optimize"]["post"]["deprecated"] is True
    assert paths[PDF]["get"]["deprecated"] is True
    assert paths[LEGACY_PDF]["get"]["deprecated"] is True


def test_integer_fields_of_the_legacy_response_are_rounded_not_truncated(ems_world, config_eos):
    configure(config_eos, SIMPLE_DEVICES, interval=900, horizon=24)
    series = forecast_series("optimize_input_1.json", 900)
    body = {
        "soc": {"bat1": 54.6, "ev1": 99.9},
        "forecasts": {
            "pv_prognose_wh": series["pv_forecast_wh"],
            "gesamtlast": series["total_load"],
            "strompreis_euro_pro_wh": series["electricity_price_per_wh"],
            "einspeiseverguetung_euro_pro_wh": series["feed_in_tariff_per_wh"],
            "temperature_forecast": [15.0],
        },
    }
    response = TestClient(eos.app).post("/v1/optimize", json=body)
    assert response.status_code == 200, response.text
    old = response.json()
    assert old["ev_obj"]["initial_soc_percentage"] == 100  # 99.9 was cut to 99
    parameters = old["parameters"]
    assert parameters["ev"]["initial_soc_percentage"] == 100
    assert parameters["pv_battery"]["initial_soc_percentage"] == 55  # 54.6 was cut to 54


def test_storage_parameters_round_half_up() -> None:
    from dataclasses import replace

    import test_searchscenarios as sc

    from akkudoktoreos.server.rest.optimizecompat import _storage_parameters

    problem, _ = sc.build(sc.make_series("winter_clear"), "A", slots=8)
    storage = replace(
        problem.batteries[0],
        capacity_wh=7999.6,
        initial_soc_pct=99.9,
        min_soc_pct=9.5,
        max_soc_pct=89.4,
    )
    values = _storage_parameters("bat1", storage)
    assert values["capacity_wh"] == 8000 and isinstance(values["capacity_wh"], int)
    assert values["initial_soc_percentage"] == 100
    assert values["min_soc_percentage"] == 10  # half up, not to even
    assert values["max_soc_percentage"] == 89


def test_multibattery_v1_rejection_prevents_incompatible_dispatch(ems_world, config_eos):
    batteries = SIMPLE_DEVICES["batteries"]
    configure(
        config_eos,
        {**SIMPLE_DEVICES, "batteries": {**batteries, "battery2": batteries["bat1"]}},
    )
    response = TestClient(eos.app).post("/v1/optimize")
    assert response.status_code == 503 and "at most one device" in response.json()["detail"]
    assert ems_world.plan() is None
    # Acquisition still happens. There has been no control dispatch.
    assert ems_world.adapter.update_data.await_count == 1


@pytest.mark.asyncio
async def test_scheduled_run_and_empty_v1_request_use_configured_forecasts(
    ems_world, config_eos, monkeypatch
):
    from unittest.mock import AsyncMock

    import numpy as np

    from akkudoktoreos.core import ems
    from akkudoktoreos.optimization.request import PlanningRequest
    from akkudoktoreos.optimization.request import grid as grid_module

    configure(config_eos, SIMPLE_DEVICES)
    config_eos.set_nested_value("ems/mode", "OPTIMIZATION")
    clock = ems.to_datetime

    def now(value=None, **kwargs):
        return START if value is None else clock(value, **kwargs)

    monkeypatch.setattr(ems, "to_datetime", now)
    series = forecast_series("optimize_input_1.json", 3600)
    by_key = {
        "pvforecast_ac_power": series["pv_forecast_wh"],
        "loadforecast_power_w": series["total_load"],
        "elecprice_marketprice_wh": series["electricity_price_per_wh"],
        "feed_in_tariff_wh": series["feed_in_tariff_per_wh"],
    }

    async def read(_prediction, *, key, **kwargs):
        return np.asarray(by_key[key])

    monkeypatch.setattr(PlanningRequest, "prediction", Mock(update_data=AsyncMock()))
    monkeypatch.setattr(grid_module, "bounded_forecast_array", read)
    await ems.ems_manage_energy()
    assert ems_world.ohmrakel42_solution() is not None
    assert ems_world.plan() is not None

    result = TestClient(eos.app).post("/v1/optimize")
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["parameters"]["ems"]["pv_forecast_wh"] == series["pv_forecast_wh"]
    assert data["result"]["electricity_price"] == series["electricity_price_per_wh"][10:34]
    assert data["parameters"]["home_appliances"][0]["device_id"] == "dishwasher"
