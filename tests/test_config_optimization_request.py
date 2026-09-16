"""Configuration-owned requests and the real automatic GENETIC path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from akkudoktoreos.config.configmigrate import migrate_config_data
from akkudoktoreos.core.ems import EnergyManagement, EnergyManagementStage
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.optimization.genetic import configrequest
from akkudoktoreos.optimization.genetic.configrequest import ConfigOptimizationRequest
from akkudoktoreos.optimization.optimization import (
    OptimizationAlgorithm,
    OptimizationCommonSettings,
)
from akkudoktoreos.utils.datetimeutil import to_datetime


@pytest.fixture
def configured_request(config_eos, monkeypatch):
    config_eos.merge_settings_from_dict(
        {
            "prediction": {"hours": 24},
            "optimization": {
                "algorithm": "GENETIC",
                "genetic": {
                    "interval_sec": 900,
                    "horizon_hours": 1,
                    "tail_horizon_hours": 0,
                    "terminal_value_mode": "FIXED",
                    "terminal_value_euro_per_kwh": 0.23,
                    "individuals": 10,
                    "generations": 10,
                    "seed": 42,
                },
            },
            "devices": {
                "max_batteries": 1,
                "max_electric_vehicles": 0,
                "max_inverters": 1,
                "max_home_appliances": 2,
                "batteries": {
                    "storage": {
                        "capacity_wh": 2000,
                        "max_charge_power_w": 1000,
                        "levelized_cost_of_storage_amt_kwh": 0.01,
                    }
                },
                "electric_vehicles": {},
                "inverters": {"inverter": {"battery_id": "storage", "max_power_w": 1000}},
                "home_appliances": {},
            },
        }
    )
    start = to_datetime("2026-09-12T10:15:00Z", in_timezone="UTC")
    ems = SimpleNamespace(
        start_datetime=start, observation_datetime=start, genetic_solution=lambda: None
    )
    monkeypatch.setattr(configrequest, "get_ems", lambda: ems)
    measurement = Mock(key_to_lists=AsyncMock(return_value=([], [])))
    monkeypatch.setattr(ConfigOptimizationRequest, "measurement", measurement)
    data = {
        "soc": {"storage": 42},
        "forecasts": {
            "pv_forecast_wh": [100.0] * 96,
            "total_load": [200.0] * 96,
            "electricity_price_per_wh": [0.0003] * 96,
            "feed_in_tariff_per_wh": [0.00008] * 96,
        },
    }
    return config_eos, ems, measurement, data


@pytest.mark.asyncio
async def test_hardware_costs_and_state_are_resolved_without_config_changes(configured_request):
    config, _, _, data = configured_request
    before = config.model_dump_json()
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.device_id == "storage"
    assert parameters.pv_battery.capacity_wh == 2000
    assert parameters.pv_battery.initial_soc_percentage == 42
    assert parameters.pv_battery.levelized_cost_of_storage_kwh == 0.01
    assert parameters.ems.price_per_wh_battery == pytest.approx(0.00023)
    assert parameters.forecast_interval_seconds == 900
    assert config.model_dump_json() == before


@pytest.mark.parametrize("field", ["devices", "optimization", "pv_battery", "inverter", "ems"])
def test_static_http_overrides_are_rejected(field):
    with pytest.raises(ValidationError):
        ConfigOptimizationRequest.model_validate({field: {}})


@pytest.mark.parametrize("host_timezone", ["UTC", "Europe/Berlin"])
def test_warmstart_timestamp_retains_explicit_zone_and_json_instant(
    set_other_timezone, host_timezone
):
    set_other_timezone(host_timezone)
    previous = to_datetime("2026-10-25T02:30:00+01:00", in_timezone="Europe/Berlin")
    request = ConfigOptimizationRequest(start_solution_datetime=previous)
    assert request.start_solution_datetime is not None
    assert request.start_solution_datetime.timezone_name == "Europe/Berlin"
    assert request.start_solution_datetime.timestamp() == previous.timestamp()
    restored = ConfigOptimizationRequest.model_validate_json(request.model_dump_json())
    assert restored.start_solution_datetime is not None
    assert restored.start_solution_datetime.timestamp() == previous.timestamp()
    assert restored.start_solution_datetime.utcoffset() == previous.utcoffset()


@pytest.mark.asyncio
@pytest.mark.parametrize("age,value", [(301, 0.45), (-1, 0.45), (1, None), (1, np.nan), (1, 1.1)])
async def test_missing_stale_future_or_invalid_soc_never_becomes_zero(
    configured_request, age, value
):
    _, ems, measurement, data = configured_request
    data["soc"] = {}
    measurement.key_to_lists.return_value = (
        [ems.observation_datetime.subtract(seconds=age)],
        [value],
    )
    with pytest.raises(ValueError, match="Fresh SoC missing"):
        await ConfigOptimizationRequest.model_validate(data).resolve()


@pytest.mark.asyncio
async def test_soc_freshness_uses_actual_run_time_inside_quarter_hour(configured_request):
    _, ems, measurement, data = configured_request
    data["soc"] = {}
    ems.observation_datetime = ems.start_datetime.add(minutes=7)
    measurement.key_to_lists.return_value = (
        [ems.observation_datetime.subtract(seconds=30)],
        [0.456],
    )
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 45
    assert measurement.key_to_lists.call_args.kwargs[
        "end_datetime"
    ] == ems.observation_datetime.add(seconds=1)


@pytest.mark.asyncio
async def test_future_soc_in_repeated_hour_is_rejected(configured_request):
    _, ems, measurement, data = configured_request
    data["soc"] = {}
    ems.observation_datetime = to_datetime("2026-10-25T02:45:00+02:00", in_timezone="Europe/Berlin")
    measurement.key_to_lists.return_value = (
        [to_datetime("2026-10-25T02:15:00+01:00", in_timezone="Europe/Berlin")],
        [0.8],
    )
    with pytest.raises(ValueError, match="Fresh SoC missing"):
        await ConfigOptimizationRequest.model_validate(data).resolve()


@pytest.mark.asyncio
async def test_unknown_soc_and_mismatched_device_link_are_rejected(configured_request):
    config, _, _, data = configured_request
    request = ConfigOptimizationRequest.model_validate(data)
    request.soc["unknown"] = 20
    with pytest.raises(ValueError, match="unconfigured"):
        await request.resolve()
    assert config.devices.inverters is not None
    config.devices.inverters["inverter"].battery_id = "missing"
    with pytest.raises(ValueError, match="battery_id"):
        await ConfigOptimizationRequest.model_validate(data).resolve()


@pytest.mark.asyncio
@pytest.mark.parametrize("tariff", [0.00008, 0.0, -0.00005])
async def test_direct_marketing_keeps_explicit_imported_sale_prices(configured_request, tariff):
    config, _, _, data = configured_request
    config.feedintariff.direct_marketing_enabled = True
    config.feedintariff.provider = "FeedInTariffImport"
    data["forecasts"]["feed_in_tariff_per_wh"] = [tariff] * 96
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert parameters.ems.feed_in_tariff_per_wh == [tariff] * 96


@pytest.mark.asyncio
async def test_control_gaps_fail_but_shorter_tail_is_accepted(configured_request):
    _, _, _, data = configured_request
    data["forecasts"]["electricity_price_per_wh"] = [0.0003] * 50
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert len(parameters.ems.total_load) == 50
    data["forecasts"]["electricity_price_per_wh"][42] = np.nan
    with pytest.raises(ValueError, match="control horizon"):
        await ConfigOptimizationRequest.model_validate(data).resolve()


@pytest.mark.asyncio
async def test_provider_power_is_integrated_once_and_update_is_awaited(
    configured_request, monkeypatch
):
    _, _, _, data = configured_request
    data["forecasts"] = {}
    series = {
        "pvforecast_ac_power": 1000.0,
        "loadforecast_power_w": 2000.0,
        "elecprice_marketprice_wh": 0.0003,
        "feed_in_tariff_wh": -0.00005,
    }

    async def read(key, **kwargs):
        return pd.Series(
            [series[key]] * 48, index=pd.date_range("2026-09-12T00:00:00Z", periods=48, freq="h")
        )

    prediction = Mock(update_data=AsyncMock(), key_to_raw_series=AsyncMock(side_effect=read))
    monkeypatch.setattr(ConfigOptimizationRequest, "prediction", prediction)
    parameters = await ConfigOptimizationRequest.model_validate(data).resolve()
    assert parameters.ems.pv_forecast_wh == [250.0] * len(parameters.ems.pv_forecast_wh)
    assert parameters.ems.total_load == [500.0] * len(parameters.ems.total_load)
    assert parameters.ems.feed_in_tariff_per_wh == [-0.00005] * len(parameters.ems.total_load)
    prediction.update_data.assert_awaited_once()


@pytest.mark.parametrize(
    "stamp,interval,expected",
    [
        ("2026-03-29T03:17:00+02:00", 900, "2026-03-29T03:15:00+02:00"),
        ("2026-10-25T02:47:00+01:00", 900, "2026-10-25T02:45:00+01:00"),
        ("2026-10-25T02:47:00+01:00", 3600, "2026-10-25T02:00:00+01:00"),
    ],
)
def test_slot_alignment_preserves_dst_fold(config_eos, stamp, interval, expected):
    time = to_datetime(stamp, in_timezone="Europe/Berlin")
    aligned = EnergyManagement.set_start_datetime(time, interval_seconds=interval)
    assert aligned == to_datetime(expected, in_timezone="Europe/Berlin")
    assert aligned.utcoffset() == to_datetime(expected, in_timezone="Europe/Berlin").utcoffset()


@pytest.mark.parametrize("timezone", ["Asia/Kolkata", "Asia/Kathmandu"])
@pytest.mark.parametrize("interval,minute", [(900, 30), (3600, 0)])
def test_slot_alignment_uses_local_midnight(config_eos, timezone, interval, minute):
    time = to_datetime("2026-09-12T10:40:00", in_timezone=timezone)
    aligned = EnergyManagement.set_start_datetime(time, interval_seconds=interval)
    assert aligned.hour == 10
    assert aligned.minute == minute
    assert aligned.timezone_name == timezone
    assert EnergyManagement().observation_datetime == time


def test_old_feature_config_migrates_without_changing_explicit_nested_values(config_eos):
    raw = {
        "optimization": {
            "algorithm": "GENETIC",
            "interval": 900,
            "horizon_hours": 12,
            "terminal_value_euro_per_kwh": 0.23,
            "genetic": {"horizon_hours": 8},
        }
    }
    migrated = migrate_config_data(raw)
    assert migrated.optimization.genetic.interval_sec == 900
    assert migrated.optimization.genetic.horizon_hours == 8
    assert migrated.optimization.genetic.terminal_value_euro_per_kwh == 0.23
    settings = OptimizationCommonSettings.model_validate(raw["optimization"])
    settings.algorithm = OptimizationAlgorithm.GENETIC0
    assert settings.genetic.interval_sec == 900
    assert settings.genetic.generations == 400


def test_http_empty_body_is_configuration_request_and_failure_cannot_return_cache(monkeypatch):
    from akkudoktoreos.server import eos

    run = AsyncMock(return_value=None)
    monkeypatch.setattr(eos, "get_ems", lambda: SimpleNamespace(run=run))
    client = TestClient(eos.app)
    response = client.post("/v1/optimize")
    assert response.status_code == 503
    assert isinstance(run.call_args.kwargs["genetic_parameters"], ConfigOptimizationRequest)
    assert run.call_args.kwargs["algorithm"] == OptimizationAlgorithm.GENETIC
    response = client.post("/v1/optimize", json={"devices": {}})
    assert response.status_code == 422
    assert run.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("automatic", [False, True])
async def test_real_ems_returns_coherent_quarter_hour_solution_and_plan(
    configured_request, monkeypatch, automatic
):
    _, fake, _, data = configured_request
    ems = EnergyManagement()
    monkeypatch.setattr(configrequest, "get_ems", lambda: ems)
    monkeypatch.setattr(EnergyManagement, "prediction", Mock(update_data=AsyncMock()))
    monkeypatch.setattr(EnergyManagement, "adapter", Mock(update_data=AsyncMock()))
    request = ConfigOptimizationRequest.model_validate(data)
    if automatic:
        original = ConfigOptimizationRequest.resolve

        async def resolve_default(self):
            return await original(request)

        monkeypatch.setattr(ConfigOptimizationRequest, "resolve", resolve_default)
    solution = await ems.run(
        start_datetime=fake.start_datetime.add(minutes=2),
        mode=EnergyManagementMode.OPTIMIZATION,
        genetic_parameters=None if automatic else request,
        genetic_generations=10,
        genetic_seed=42,
    )
    assert solution is not None
    assert solution is ems.genetic_solution()
    assert solution.start_solution_datetime == fake.start_datetime
    assert len(solution.ac_charge) == 4
    assert ems.optimization_solution() is not None
    assert ems.plan() is not None
    assert ems.stage() == EnergyManagementStage.IDLE


@pytest.mark.asyncio
async def test_automatic_run_reads_real_resolver_provider_units_and_measured_soc(
    configured_request, monkeypatch
):
    _, fake, measurement, _ = configured_request
    ems = EnergyManagement()
    monkeypatch.setattr(configrequest, "get_ems", lambda: ems)
    monkeypatch.setattr(EnergyManagement, "_genetic_solution", None)
    measurement.key_to_lists.return_value = ([fake.start_datetime], [0.42])
    values = {
        "pvforecast_ac_power": 1000.0,
        "loadforecast_power_w": 2000.0,
        "elecprice_marketprice_wh": 0.0003,
        "feed_in_tariff_wh": 0.00008,
    }

    async def read(key, **kwargs):
        return pd.Series(
            [values[key]] * 48, index=pd.date_range("2026-09-12T00:00:00Z", periods=48, freq="h")
        )

    prediction = Mock(update_data=AsyncMock(), key_to_raw_series=AsyncMock(side_effect=read))
    monkeypatch.setattr(EnergyManagement, "prediction", prediction)
    monkeypatch.setattr(ConfigOptimizationRequest, "prediction", prediction)
    monkeypatch.setattr(EnergyManagement, "adapter", Mock(update_data=AsyncMock()))
    solution = await ems.run(
        start_datetime=fake.start_datetime,
        mode=EnergyManagementMode.OPTIMIZATION,
        genetic_generations=10,
        genetic_seed=42,
    )
    assert solution is not None
    assert solution.parameters is not None
    assert solution.parameters.pv_battery is not None
    assert solution.parameters.pv_battery.initial_soc_percentage == 42
    assert solution.parameters.ems.pv_forecast_wh[:4] == [250.0] * 4
    assert solution.parameters.ems.total_load[:4] == [500.0] * 4
    assert len(solution.ac_charge) == 4
    prediction.key_to_raw_series.assert_awaited()


@pytest.mark.asyncio
async def test_http_real_genetic_result_serializes_native_quarter_hour_contract(
    configured_request, monkeypatch
):
    from akkudoktoreos.server import eos

    _, fake, _, data = configured_request
    ems = EnergyManagement()
    monkeypatch.setattr(configrequest, "get_ems", lambda: ems)
    monkeypatch.setattr(EnergyManagement, "_genetic_solution", None)
    monkeypatch.setattr(EnergyManagement, "prediction", Mock(update_data=AsyncMock()))
    monkeypatch.setattr(EnergyManagement, "adapter", Mock(update_data=AsyncMock()))

    async def run(**kwargs):
        return await ems.run(start_datetime=fake.start_datetime, **kwargs)

    monkeypatch.setattr(eos, "get_ems", lambda: SimpleNamespace(run=run))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=eos.app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/optimize", json=data)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["interval_seconds"] == 900
    assert result["controls_start_at_now"] is True
    assert len(result["ac_charge"]) == 4
    assert result["parameters"]["pv_battery"]["device_id"] == "storage"
