"""Configuration-owned requests and the real automatic GENETIC path."""

from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, Mock, patch

import httpx
import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from pydantic import ValidationError

from akkudoktoreos.config.configmigrate import migrate_config_data
from akkudoktoreos.core.coreabc import get_measurement
from akkudoktoreos.core.ems import EnergyManagement, EnergyManagementStage
from akkudoktoreos.core.emsettings import EnergyManagementMode
from akkudoktoreos.optimization.genetic import geneticprepare
from akkudoktoreos.optimization.genetic.geneticparams import (
    GeneticOptimizationParameters,
)
from akkudoktoreos.optimization.genetic.geneticrequest import GeneticRequest
from akkudoktoreos.optimization.optimization import (
    OptimizationAlgorithm,
    OptimizationCommonSettings,
)
from akkudoktoreos.prediction.pvforecastforecastsolar import PVForecastForecastSolar
from akkudoktoreos.utils.datetimeutil import to_datetime

# Run start and observation time of the run the tests prepare; the fixture sets them.
RUN = SimpleNamespace(ems=None)


async def resolve(request: GeneticRequest, warm_start: Any = None) -> GeneticOptimizationParameters:
    """Prepare ``request`` at the run start and observation time of ``RUN.ems``."""
    return await geneticprepare.resolve_request(
        request, RUN.ems.start_datetime, RUN.ems.observation_datetime, warm_start
    )


@pytest_asyncio.fixture
async def isolated_measurement(config_eos):
    """Keep synthetic records out of the process-wide measurement singleton."""
    measurement = get_measurement()
    await measurement.delete_by_datetime(None, None)
    try:
        yield measurement
    finally:
        await measurement.delete_by_datetime(None, None)


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
                        "profile_adjustment": {"nominal_capacity_wh": 2000,
                        "max_charge_power_w": 1000},
                        "levelized_cost_of_storage_amt_kwh": 0.01,
                    }
                },
                "electric_vehicles": {},
                "inverters": {
                    "inverter": {
                        "battery_id": "storage",
                        "profile_adjustment": {"nominal_power_w": 1000},
                    }
                },
                "home_appliances": {},
            },
        }
    )
    start = to_datetime("2026-09-12T10:15:00Z", in_timezone="UTC")
    ems = SimpleNamespace(start_datetime=start, observation_datetime=start)
    monkeypatch.setattr(RUN, "ems", ems)
    measurement = Mock(key_to_lists=AsyncMock(return_value=([], [])))
    monkeypatch.setattr(geneticprepare, "get_measurement", Mock(return_value=measurement))
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
@pytest.mark.parametrize("interval", [900, 3600])
async def test_forecastsolar_resolves_without_runtime_override(
    configured_request, monkeypatch, interval
):
    config, ems, _, data = configured_request
    config.merge_settings_from_dict(
        {
            "general": {"latitude": 52.5, "longitude": 13.4, "timezone": "Europe/Berlin"},
            "prediction": {"hours": 48},
            "optimization": {"genetic": {"interval_sec": interval, "horizon_hours": 24}},
            "pvforecast": {
                "provider": "PVForecastForecastSolar",
                "planes": [
                    {"surface_tilt": 45, "surface_azimuth": 217, "peakpower": 8.05},
                ],
            },
        }
    )
    ems.start_datetime = to_datetime("2026-11-02T10:00:00+01:00", in_timezone="Europe/Berlin")
    ems.observation_datetime = ems.start_datetime
    data["forecasts"] = {}
    pv = PVForecastForecastSolar(config=config.load)
    watts = {
        f"2026-11-{day} {time}": power
        for day in ("02", "03")
        for time, power in {
            "07:16:18": 0,
            "08:00:00": 1000,
            "09:00:00": 1000,
            "10:00:00": 1000,
            "11:00:00": 1000,
            "12:00:00": 1000,
            "13:00:00": 1000,
            "14:00:00": 1000,
            "15:00:00": 1000,
            "16:00:00": 1000,
            "16:55:54": 0,
        }.items()
    }

    async def read(key, **kwargs):
        if key == "pvforecast_ac_power":
            return await pv.key_to_raw_series(key, **kwargs)
        value = {
            "loadforecast_power_w": 2000,
            "elecprice_marketprice_wh": 0.0003,
            "feed_in_tariff_wh": 0.00008,
        }[key]
        return pd.Series(
            [value] * 72, index=pd.date_range("2026-11-02T00:00:00+01:00", periods=72, freq="h")
        )

    async def update():
        # Isolate this synthetic response from the provider's on-disk cache.
        await pv._update_data(force_update=True)

    prediction = SimpleNamespace(update_data=AsyncMock(side_effect=update), key_to_raw_series=read)
    monkeypatch.setattr(geneticprepare, "get_prediction", Mock(return_value=prediction))
    response = Mock()
    response.json.return_value = {
        "result": {"watts": watts},
        "message": {"info": {"timezone": "Europe/Berlin"}},
    }
    with patch("requests.get", return_value=response):
        parameters = await resolve(GeneticRequest.model_validate(data))
    prediction.update_data.assert_awaited_once()
    energy = parameters.ems.pv_forecast_wh
    assert energy[10 * 3600 // interval] == pytest.approx(1000 * interval / 3600)
    assert energy[17 * 3600 // interval : 24 * 3600 // interval] == [0] * (7 * 3600 // interval)
    assert np.isfinite(energy[10 * 3600 // interval : 34 * 3600 // interval]).all()
    await pv.delete_by_datetime()


@pytest.mark.asyncio
async def test_echoed_warm_start_gets_the_start_of_the_last_solution(configured_request):
    """A client that only echoes the genome of this server's last solution keeps its start."""
    _, ems, _, data = configured_request
    last_start = ems.start_datetime.subtract(minutes=15)
    warm_start = SimpleNamespace(start_solution=[19.0, 32.0, 14.0], start_solution_datetime=last_start)
    explicit_start = ems.start_datetime.subtract(minutes=30)

    async def resolved_start(**fields: Any) -> Any:
        request = GeneticRequest.model_validate({**data, **fields})
        parameters = await resolve(request, warm_start)
        return parameters.start_solution_datetime

    assert await resolved_start(start_solution=[19, 32, 14]) == last_start
    assert await resolved_start(start_solution=[19, 32, 15]) is None
    assert (
        await resolved_start(start_solution=[19, 32, 14], start_solution_datetime=explicit_start)
        == explicit_start
    )
    assert await resolved_start() == last_start


@pytest.mark.asyncio
async def test_hardware_costs_and_state_are_resolved_without_config_changes(configured_request):
    config, _, _, data = configured_request
    before = config.model_dump_json()
    parameters = await resolve(GeneticRequest.model_validate(data))
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
        GeneticRequest.model_validate({field: {}})


@pytest.mark.parametrize("host_timezone", ["UTC", "Europe/Berlin"])
def test_warmstart_timestamp_retains_explicit_zone_and_json_instant(
    set_other_timezone, host_timezone
):
    set_other_timezone(host_timezone)
    previous = to_datetime("2026-10-25T02:30:00+01:00", in_timezone="Europe/Berlin")
    request = GeneticRequest(start_solution_datetime=previous)
    assert request.start_solution_datetime is not None
    assert request.start_solution_datetime.timezone_name == "Europe/Berlin"
    assert request.start_solution_datetime.timestamp() == previous.timestamp()
    restored = GeneticRequest.model_validate_json(request.model_dump_json())
    assert restored.start_solution_datetime is not None
    assert restored.start_solution_datetime.timestamp() == previous.timestamp()
    assert restored.start_solution_datetime.utcoffset() == previous.utcoffset()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "age,value",
    [(301, 0.45), (-1, 0.45), (1, None), (1, np.nan), (1, np.inf), (1, -0.1), (1, 1.1)],
)
async def test_stale_future_or_invalid_soc_defaults_to_zero(
    configured_request, age, value
):
    _, ems, measurement, data = configured_request
    data["soc"] = {}
    measurement.key_to_lists.return_value = (
        [ems.observation_datetime.subtract(seconds=age)],
        [value],
    )
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 0


@pytest.mark.asyncio
async def test_soc_freshness_uses_actual_run_time_inside_quarter_hour(configured_request):
    _, ems, measurement, data = configured_request
    data["soc"] = {}
    ems.observation_datetime = ems.start_datetime.add(minutes=7)
    measurement.key_to_lists.return_value = (
        [ems.observation_datetime.subtract(seconds=30)],
        [0.456],
    )
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 45
    assert measurement.key_to_lists.call_args.kwargs[
        "end_datetime"
    ] == ems.observation_datetime.add(seconds=1)


@pytest.mark.asyncio
async def test_future_soc_in_repeated_hour_defaults_to_zero(configured_request):
    _, ems, measurement, data = configured_request
    data["soc"] = {}
    ems.observation_datetime = to_datetime("2026-10-25T02:45:00+02:00", in_timezone="Europe/Berlin")
    measurement.key_to_lists.return_value = (
        [to_datetime("2026-10-25T02:15:00+01:00", in_timezone="Europe/Berlin")],
        [0.8],
    )
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 0


@pytest.mark.asyncio
async def test_unknown_soc_and_mismatched_device_link_fall_back(configured_request, caplog):
    """An unknown SoC is ignored; a battery its inverter does not reach is left out."""
    config, _, _, data = configured_request
    request = GeneticRequest.model_validate(data)
    request.soc["unknown"] = 20
    parameters = await resolve(request)
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 42
    assert "SoC 20% supplied for unconfigured device 'unknown', ignored" in caplog.text
    assert config.devices.inverters is not None
    config.devices.inverters["inverter"].battery_id = "missing"
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is None
    assert parameters.inverter is not None
    assert "Battery 'storage' is not connected to the inverter (battery_id missing)" in caplog.text
    # The configuration is never changed.
    assert config.devices.inverters["inverter"].battery_id == "missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("tariff", [0.00008, 0.0, -0.00005])
async def test_direct_marketing_keeps_explicit_imported_sale_prices(configured_request, tariff):
    config, _, _, data = configured_request
    config.feedintariff.direct_marketing_enabled = True
    config.feedintariff.provider = "FeedInTariffImport"
    data["forecasts"]["feed_in_tariff_per_wh"] = [tariff] * 96
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.ems.feed_in_tariff_per_wh == [tariff] * 96


@pytest.mark.asyncio
async def test_control_gaps_are_filled_and_shorter_tail_is_accepted(configured_request, caplog):
    _, _, _, data = configured_request
    data["forecasts"]["electricity_price_per_wh"] = [0.0003] * 50
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert len(parameters.ems.total_load) == 50
    data["forecasts"]["electricity_price_per_wh"][41] = 0.0004
    data["forecasts"]["electricity_price_per_wh"][42] = np.nan
    parameters = await resolve(GeneticRequest.model_validate(data))
    # The last known price is carried forward.
    assert parameters.ems.electricity_price_per_wh[42] == 0.0004
    assert (
        "electricity price missing or invalid in 1 slot(s) of the control horizon, first at "
        "2026-09-12T10:30:00+00:00 (nan), replaced by 0.0004" in caplog.text
    )


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
    monkeypatch.setattr(geneticprepare, "get_prediction", Mock(return_value=prediction))
    parameters = await resolve(GeneticRequest.model_validate(data))
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
    assert isinstance(run.call_args.kwargs["request"], GeneticRequest)
    assert run.call_args.kwargs["algorithm"] == OptimizationAlgorithm.GENETIC
    response = client.post("/v1/optimize", json={"devices": {}})
    assert response.status_code == 422
    assert run.await_count == 1


@pytest.mark.parametrize("query", ["start_hour=4", "ngen=1", "interval=3600", "unknown="])
def test_http_configuration_request_rejects_query_overrides(monkeypatch, query):
    from akkudoktoreos.server import eos

    run = AsyncMock()
    monkeypatch.setattr(eos, "get_ems", lambda: SimpleNamespace(run=run))
    response = TestClient(eos.app).post(f"/v1/optimize?{query}", json={})
    assert response.status_code == 422
    assert "query overrides are not supported" in response.text
    run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("automatic", [False, True])
async def test_real_ems_returns_coherent_quarter_hour_solution_and_plan(
    configured_request, monkeypatch, automatic
):
    _, fake, _, data = configured_request
    ems = EnergyManagement()
    monkeypatch.setattr(EnergyManagement, "prediction", Mock(update_data=AsyncMock()))
    monkeypatch.setattr(EnergyManagement, "adapter", Mock(update_data=AsyncMock()))
    request = GeneticRequest.model_validate(data)
    if automatic:
        original = geneticprepare.resolve_request

        async def resolve_default(_default: GeneticRequest, *args: Any) -> Any:
            return await original(request, *args)

        monkeypatch.setattr(geneticprepare, "resolve_request", resolve_default)
    solution = await ems.run(
        start_datetime=fake.start_datetime.add(minutes=2),
        mode=EnergyManagementMode.OPTIMIZATION,
        request=None if automatic else request,
        overrides={"generations": 10, "seed": 42},
    )
    assert solution is not None
    assert solution is ems.optimizer_result("GENETIC")
    assert solution is not None  # run() returns Any; `is` narrowed to Optional
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
    monkeypatch.setattr(EnergyManagement, "_optimizer_runs", {})
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
    monkeypatch.setattr(geneticprepare, "get_prediction", Mock(return_value=prediction))
    monkeypatch.setattr(EnergyManagement, "adapter", Mock(update_data=AsyncMock()))
    solution = await ems.run(
        start_datetime=fake.start_datetime,
        mode=EnergyManagementMode.OPTIMIZATION,
        overrides={"generations": 10, "seed": 42},
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
@pytest.mark.parametrize("missing_soc", [False, True])
async def test_http_real_genetic_result_serializes_native_quarter_hour_contract(
    configured_request, monkeypatch, missing_soc
):
    from akkudoktoreos.server import eos

    _, fake, _, data = configured_request
    if missing_soc:
        data["soc"] = {}
    ems = EnergyManagement()
    monkeypatch.setattr(EnergyManagement, "_optimizer_runs", {})
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
    assert result["parameters"]["pv_battery"]["initial_soc_percentage"] == (
        0 if missing_soc else 42
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_key", [False, True])
async def test_missing_soc_defaults_to_zero_without_mutating_inputs(
    configured_request, missing_key, caplog
):
    config, _, measurement, data = configured_request
    data["soc"] = {}
    before = config.model_dump_json()
    if missing_key:
        measurement.key_to_lists.side_effect = KeyError("SoC channel unavailable")
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 0
    assert config.model_dump_json() == before
    assert "assuming 0% initial SoC" in caplog.text
    assert "not a measurement" in caplog.text
    measurement.update_value.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("soc", [0, 42, 100])
async def test_explicit_soc_overrides_measurements(configured_request, soc):
    _, _, measurement, data = configured_request
    data["soc"] = {"storage": soc}
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == soc
    measurement.key_to_lists.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_ev_soc_does_not_replace_explicit_battery_soc(configured_request):
    config, _, _, data = configured_request
    config.merge_settings_from_dict(
        {"devices": {"max_electric_vehicles": 1,
                     "electric_vehicles": {"car": {"profile_adjustment": {"nominal_capacity_wh": 60000}}}}}
    )
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is not None
    assert parameters.ev is not None
    assert parameters.pv_battery.initial_soc_percentage == 42
    assert parameters.ev.initial_soc_percentage == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("soc", [70, 100])
async def test_http_config_and_real_optimizer_allow_full_ev_target(configured_request,
                                                                  monkeypatch, soc):
    from akkudoktoreos.server import eos

    config, fake, _, data = configured_request
    config.merge_settings_from_dict({"devices": {"max_electric_vehicles": 1,
        "electric_vehicles": {"ev": {"profile_adjustment": {"nominal_capacity_wh": 1000,
            "max_charge_power_w": 1000}, "min_soc_percentage": 0,
            "max_soc_percentage": 100}}}})
    data["soc"]["ev"] = soc
    ems = EnergyManagement()
    monkeypatch.setattr(EnergyManagement, "_optimizer_runs", {})
    monkeypatch.setattr(EnergyManagement, "prediction", Mock(update_data=AsyncMock()))
    monkeypatch.setattr(EnergyManagement, "adapter", Mock(update_data=AsyncMock()))

    async def run(**kwargs):
        return await ems.run(start_datetime=fake.start_datetime, **kwargs)

    from types import SimpleNamespace
    monkeypatch.setattr(eos, "get_ems", lambda: SimpleNamespace(run=run))
    monkeypatch.setattr(eos, "get_config", lambda: config)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=eos.app),
                                base_url="http://test") as client:
        updated = await client.put("/v1/config/devices/electric_vehicles/ev/min_soc_percentage",
                                   json=100)
        assert updated.status_code == 200, updated.text
        assert config.devices.electric_vehicles["ev"].min_soc_percentage == 100
        result = await client.post("/v1/optimize", json=data)
    assert result.status_code == 200, result.text
    plan = result.json()
    assert plan["parameters"]["ev"]["min_soc_percentage"] == 100
    assert plan["parameters"]["ev"]["initial_soc_percentage"] == soc
    assert len(plan["result"]["ev_soc_per_hour"]) == 4
    assert all(0 <= value <= 100 for value in plan["result"]["ev_soc_per_hour"])
    assert plan["result"]["ev_soc_per_hour"][-1] == pytest.approx(100)


@pytest.mark.asyncio
@pytest.mark.parametrize("age_seconds", [901, 86400, 365 * 86400])
async def test_explicit_ev_soc_has_no_measurement_age_limit(configured_request, age_seconds):
    config, ems, measurement, data = configured_request
    config.merge_settings_from_dict(
        {"devices": {"max_electric_vehicles": 1,
                     "electric_vehicles": {"car": {"profile_adjustment": {"nominal_capacity_wh": 60000},
                                                 "min_soc_percentage": 100,
                                                 "max_soc_percentage": 100}}}}
    )
    old_sample = ems.observation_datetime.subtract(seconds=age_seconds)
    measurement.key_to_lists.return_value = ([old_sample], [0.35])
    data["soc"]["car"] = 70
    config_before = config.model_dump_json()
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.ev is not None
    assert parameters.ev.initial_soc_percentage == 70
    assert parameters.ev.min_soc_percentage == 100
    measurement.key_to_lists.assert_not_awaited()
    measurement.update_value.assert_not_called()
    assert config.model_dump_json() == config_before


@pytest.mark.asyncio
async def test_home_assistant_soc_read_mid_slot_is_fresh_for_resolver(
    configured_request, monkeypatch, isolated_measurement
):
    """Adapter measurements carry the run time, not the rounded slot start (#1447)."""
    from akkudoktoreos.adapter.homeassistant import HomeAssistantAdapter

    config, _, _, data = configured_request
    data["soc"] = {}
    config.merge_settings_from_dict(
        {
            "adapter": {
                "homeassistant": {
                    "device_measurement_entity_ids": {"storage-soc-factor": "sensor.akku_soc"}
                }
            }
        }
    )
    monkeypatch.setattr(EnergyManagement, "_start_datetime", None)
    monkeypatch.setattr(EnergyManagement, "_observation_datetime", None)
    monkeypatch.setattr(EnergyManagement, "_stage", EnergyManagementStage.DATA_ACQUISITION)
    ems = EnergyManagement()
    monkeypatch.setattr(RUN, "ems", ems)
    # Run 11 minutes into the 10:15 slot: older than measurement_max_age_seconds (300 s)
    # if the reading were stamped with the slot start.
    run_time = to_datetime("2026-09-12T10:26:00Z", in_timezone="UTC")
    ems.set_start_datetime(run_time, interval_seconds=900)
    assert ems.start_datetime == run_time.subtract(minutes=11)
    monkeypatch.setattr(geneticprepare, "get_measurement", Mock(return_value=isolated_measurement))
    monkeypatch.setattr(HomeAssistantAdapter, "get_entity_state", lambda self, entity_id: "0.42")

    await HomeAssistantAdapter()._update_data()

    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 42


@pytest.mark.asyncio
async def test_meter_only_adapter_reading_does_not_mask_api_soc(
    configured_request, monkeypatch, isolated_measurement
):
    """A newer record without SoC (meter reading) must not hide a fresh SoC (#1447)."""
    from akkudoktoreos.adapter.homeassistant import HomeAssistantAdapter

    config, _, _, data = configured_request
    data["soc"] = {}
    config.merge_settings_from_dict(
        {"adapter": {"homeassistant": {"load_emr_entity_ids": ["sensor.load_meter"]}}}
    )
    monkeypatch.setattr(EnergyManagement, "_start_datetime", None)
    monkeypatch.setattr(EnergyManagement, "_observation_datetime", None)
    monkeypatch.setattr(EnergyManagement, "_stage", EnergyManagementStage.DATA_ACQUISITION)
    ems = EnergyManagement()
    monkeypatch.setattr(RUN, "ems", ems)
    # Run one minute into the 10:15 slot: the adapter's meter reading (slot start)
    # is newer than the SoC supplied four minutes earlier, still within 300 s.
    run_time = to_datetime("2026-09-12T10:16:00Z", in_timezone="UTC")
    ems.set_start_datetime(run_time, interval_seconds=900)
    measurement = isolated_measurement
    monkeypatch.setattr(geneticprepare, "get_measurement", Mock(return_value=measurement))
    # SoC arrives through the measurement API, the meter through Home Assistant.
    await measurement.update_value(run_time.subtract(minutes=4), "storage-soc-factor", 0.42)
    monkeypatch.setattr(HomeAssistantAdapter, "get_entity_state", lambda self, entity_id: "1234.5")

    await HomeAssistantAdapter()._update_data()

    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 42


@pytest.mark.asyncio
async def test_home_assistant_meter_readings_stay_on_slot_start(
    configured_request, monkeypatch, isolated_measurement
):
    """Meter readings keep the slot start: energy is aggregated per slot (#1447)."""
    from akkudoktoreos.adapter.homeassistant import HomeAssistantAdapter

    config, _, _, _ = configured_request
    config.merge_settings_from_dict(
        {"adapter": {"homeassistant": {"load_emr_entity_ids": ["sensor.load_meter"]}}}
    )
    monkeypatch.setattr(EnergyManagement, "_start_datetime", None)
    monkeypatch.setattr(EnergyManagement, "_observation_datetime", None)
    monkeypatch.setattr(EnergyManagement, "_stage", EnergyManagementStage.DATA_ACQUISITION)
    ems = EnergyManagement()
    run_time = to_datetime("2026-09-12T10:26:00Z", in_timezone="UTC")
    ems.set_start_datetime(run_time, interval_seconds=900)
    monkeypatch.setattr(
        HomeAssistantAdapter, "get_entity_state", lambda self, entity_id: "1234.5"
    )

    await HomeAssistantAdapter()._update_data()

    dates, values = await isolated_measurement.key_to_lists(
        key="sensor.load_meter",
        start_datetime=run_time.subtract(hours=1),
        end_datetime=run_time.add(hours=1),
    )
    assert values == [1234.5]
    assert dates[0].timestamp() == ems.start_datetime.timestamp()


@pytest.mark.asyncio
async def test_missing_newest_soc_value_falls_back_to_older_fresh_sample(
    configured_request, monkeypatch, isolated_measurement
):
    """A newest SoC stored as pandas NA is a missing value, not a crash (#1447)."""
    _, _, _, data = configured_request
    data["soc"] = {}
    monkeypatch.setattr(EnergyManagement, "_start_datetime", None)
    monkeypatch.setattr(EnergyManagement, "_observation_datetime", None)
    ems = EnergyManagement()
    monkeypatch.setattr(RUN, "ems", ems)
    run_time = to_datetime("2026-09-12T10:26:00Z", in_timezone="UTC")
    ems.set_start_datetime(run_time, interval_seconds=900)
    monkeypatch.setattr(geneticprepare, "get_measurement", Mock(return_value=isolated_measurement))
    await isolated_measurement.update_value(run_time.subtract(minutes=2), "storage-soc-factor", 0.42)
    await isolated_measurement.update_value(run_time.subtract(minutes=1), "storage-soc-factor", pd.NA)

    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 42


@pytest.mark.asyncio
async def test_battery_configuration_error_uses_defaults_and_logs_error(configured_request):
    """A battery that cannot be resolved does not cancel the parameter preparation."""
    from loguru import logger

    config, _, _, data = configured_request
    config.merge_settings_from_dict(
        {"devices": {"batteries": {"storage": {"battery_profile": "does-not-exist"}}}}
    )
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(message.record["message"]), level="ERROR")
    try:
        parameters = await resolve(GeneticRequest.model_validate(data))
    finally:
        logger.remove(sink_id)
    assert parameters.pv_battery is not None
    # The packaged generic profile applies with the adjustment.
    assert parameters.pv_battery.capacity_wh == 2000
    assert parameters.pv_battery.max_charge_power_w == pytest.approx(1000.0)
    assert parameters.pv_battery.charge_power_limit_w_by_soc is not None
    assert any(
        "Battery 'storage'" in m
        and "does-not-exist" in m
        and "Using defaults for the optimization: battery profile 'generic-lfp'" in m
        for m in messages
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "temperature_degc, expected_max_power_w",
    [(None, 1000.0), (45.0, 1000.0), (60.0, 600.0)],
)
async def test_inverter_is_derated_at_measured_temperature(
    configured_request, monkeypatch, temperature_degc, expected_max_power_w
):
    """The generic profile derates above 45 degC; without a measurement 45 degC applies."""
    from akkudoktoreos.devices.settings import invertermeasurement

    _, _, _, data = configured_request

    async def measured(key: str, target_datetime: Any) -> Optional[float]:
        return temperature_degc if key == "inverter-temperature-degc" else None

    monkeypatch.setattr(invertermeasurement, "get_measured_value", measured)
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.inverter is not None
    assert parameters.inverter.max_power_wh == pytest.approx(expected_max_power_w)
    assert parameters.inverter.max_ac_charge_power_w == pytest.approx(expected_max_power_w)
    assert parameters.inverter.ac_to_dc_efficiency_curve is not None


@pytest.mark.asyncio
async def test_unusable_inverter_profile_falls_back_to_generic(configured_request):
    """A missing inverter profile does not cancel the parameter preparation."""
    from loguru import logger

    config, _, _, data = configured_request
    config.merge_settings_from_dict(
        {"devices": {"inverters": {"inverter": {"inverter_profile": "does-not-exist"}}}}
    )
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(message.record["message"]), level="ERROR")
    try:
        parameters = await resolve(GeneticRequest.model_validate(data))
    finally:
        logger.remove(sink_id)
    assert parameters.inverter is not None
    assert parameters.inverter.max_power_wh == pytest.approx(1000.0)
    assert any(
        "Inverter 'inverter'" in m and "inverter profile 'generic-hybrid'" in m
        for m in messages
    )


@pytest.mark.asyncio
async def test_inverter_without_nominal_power_uses_the_fallback_power(configured_request):
    """The run is not cancelled: the fallback nominal power applies, reported as error."""
    from loguru import logger

    from akkudoktoreos.devices.settings.inverterresolution import (
        INVERTER_FALLBACK_NOMINAL_POWER_W,
    )

    config, _, _, data = configured_request
    config.merge_settings_from_dict(
        {"devices": {"inverters": {"inverter": {"profile_adjustment": {"nominal_power_w": None}}}}}
    )
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(message.record["message"]), level="ERROR")
    try:
        parameters = await resolve(GeneticRequest.model_validate(data))
    finally:
        logger.remove(sink_id)
    assert parameters.inverter is not None
    assert parameters.inverter.max_power_wh == pytest.approx(INVERTER_FALLBACK_NOMINAL_POWER_W)
    assert any("nominal_power_w" in m for m in messages)


# The preparation always creates valid parameters (energy-domain skill): every input that
# cannot serve the run is replaced by a fallback and logged at error level.


@pytest.mark.asyncio
async def test_surplus_and_duplicate_devices_are_left_out(configured_request, caplog):
    config, _, _, data = configured_request
    config.merge_settings_from_dict(
        {
            "devices": {
                "max_batteries": 2,
                "max_electric_vehicles": 1,
                "batteries": {
                    "second": {"profile_adjustment": {"nominal_capacity_wh": 1000}},
                },
                "electric_vehicles": {
                    "storage": {"profile_adjustment": {"nominal_capacity_wh": 1000}},
                },
            }
        }
    )
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.device_id == "storage"
    assert parameters.ev is None
    assert "devices.batteries holds more than 1 device(s), 'second' left out" in caplog.text
    assert "Device ID 'storage' in devices.electric_vehicles is not unique" in caplog.text
    assert list(config.devices.batteries) == ["storage", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "group, key, method, field, message",
    [
        ("batteries", "storage", "to_genetic_pv_bat_param", "pv_battery", "Battery 'storage'"),
        ("electric_vehicles", "car", "to_genetic_ev_bat_param", "ev", "Electric vehicle 'car'"),
        ("inverters", "inverter", "to_genetic_param", "inverter", "Inverter 'inverter'"),
    ],
)
async def test_unresolvable_device_is_left_out(
    configured_request, monkeypatch, caplog, group, key, method, field, message
):
    config, _, _, data = configured_request
    config.merge_settings_from_dict(
        {
            "devices": {
                "max_electric_vehicles": 1,
                "electric_vehicles": {
                    "car": {"profile_adjustment": {"nominal_capacity_wh": 1000}},
                },
            }
        }
    )

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("no profile")

    monkeypatch.setattr(type(getattr(config.devices, group)[key]), method, fail)
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert getattr(parameters, field) is None
    assert f"{message} cannot be resolved, left out: no profile" in caplog.text


@pytest.mark.asyncio
async def test_without_inverter_the_run_plans_grid_energy_only(
    configured_request, monkeypatch, caplog
):
    config, _, _, data = configured_request
    monkeypatch.setattr(config.devices, "inverters", {})
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.inverter is None
    assert parameters.pv_battery is None
    assert "No usable inverter; planning grid energy only" in caplog.text
    assert "Battery 'storage' is not connected to the inverter (battery_id None)" in caplog.text


@pytest.mark.asyncio
async def test_unreadable_predictions_use_fallback_values(configured_request, monkeypatch, caplog):
    _, _, _, data = configured_request
    data["forecasts"] = {}
    prediction = Mock(
        update_data=AsyncMock(side_effect=RuntimeError("offline")),
        key_to_raw_series=AsyncMock(side_effect=RuntimeError("no data")),
    )
    monkeypatch.setattr(geneticprepare, "get_prediction", Mock(return_value=prediction))
    parameters = await resolve(GeneticRequest.model_validate(data))
    first, last = 41, 45  # 10:15 to 11:15 in quarter-hour slots
    ems = parameters.ems
    assert ems.pv_forecast_wh[first:last] == [0.0] * 4
    load = geneticprepare.FALLBACK_LOAD_W * 900 / 3600
    assert ems.total_load[first:last] == pytest.approx([load] * 4)
    assert ems.electricity_price_per_wh[first:last] == [0.0] * 4
    assert isinstance(ems.feed_in_tariff_per_wh, list)
    assert ems.feed_in_tariff_per_wh[first:last] == [0.0] * 4
    assert "Prediction update failed, using stored data: offline" in caplog.text
    assert "Prediction 'pvforecast_ac_power' not readable, treated as missing" in caplog.text
    assert "load forecast missing or invalid in 4 slot(s)" in caplog.text


@pytest.mark.asyncio
async def test_negative_energy_and_short_genome_are_replaced(configured_request, caplog):
    _, _, _, data = configured_request
    data["forecasts"]["pv_forecast_wh"][42] = -5.0
    data["start_solution"] = [1.0]
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.ems.pv_forecast_wh[42] == 0.0
    assert parameters.start_solution is None
    assert "PV forecast missing or invalid in 1 slot(s)" in caplog.text
    assert "start_solution of the request [1.0] has fewer than two genes" in caplog.text


def test_unset_settings_use_their_defaults(configured_request, monkeypatch, caplog):
    config, ems, _, _ = configured_request
    monkeypatch.setattr(config.prediction, "hours", None)
    monkeypatch.setattr(config.optimization.genetic, "horizon_hours", 0)
    monkeypatch.setattr(config.optimization.genetic, "generations", None)
    monkeypatch.setattr(config.optimization.genetic, "individuals", None)
    settings = geneticprepare.run_settings(ems.start_datetime, ems.observation_datetime)
    assert settings.prediction_hours == geneticprepare.FALLBACK_PREDICTION_HOURS
    assert settings.horizon_hours == geneticprepare.FALLBACK_HORIZON_HOURS
    assert settings.generations == geneticprepare.FALLBACK_GENERATIONS
    assert settings.individuals == geneticprepare.FALLBACK_INDIVIDUALS
    assert "prediction.hours not set, using 48" in caplog.text
    assert "horizon_hours 0 is not positive, using 24" in caplog.text
    assert "optimization.genetic.generations not set, using 400" in caplog.text
    assert "optimization.genetic.individuals not set, using 300" in caplog.text


def test_prediction_hours_shorter_than_the_control_horizon(configured_request, monkeypatch, caplog):
    config, ems, _, _ = configured_request
    monkeypatch.setattr(config.prediction, "hours", 2)
    monkeypatch.setattr(config.optimization.genetic, "horizon_hours", 6)
    settings = geneticprepare.run_settings(ems.start_datetime, ems.observation_datetime)
    assert settings.prediction_hours == 6
    assert "prediction.hours 2 is shorter than the control horizon, using 6" in caplog.text


@pytest.mark.asyncio
async def test_automatic_run_without_inverter_or_predictions_is_not_cancelled(
    configured_request, monkeypatch, caplog
):
    """Inputs that cancelled a run before now fall back; the run publishes a plan."""
    config, fake, _, _ = configured_request
    monkeypatch.setattr(config.devices, "inverters", {})
    ems = EnergyManagement()
    monkeypatch.setattr(EnergyManagement, "_optimizer_runs", {})
    monkeypatch.setattr(EnergyManagement, "_plan", None)
    prediction = Mock(
        update_data=AsyncMock(), key_to_raw_series=AsyncMock(side_effect=KeyError("none"))
    )
    monkeypatch.setattr(EnergyManagement, "prediction", prediction)
    monkeypatch.setattr(geneticprepare, "get_prediction", Mock(return_value=prediction))
    monkeypatch.setattr(EnergyManagement, "adapter", Mock(update_data=AsyncMock()))
    solution = await ems.run(
        start_datetime=fake.start_datetime,
        mode=EnergyManagementMode.OPTIMIZATION,
        overrides={"generations": 10, "seed": 42},
    )
    assert solution is not None
    assert ems.plan() is not None
    assert "Energy management run canceled" not in caplog.text
    assert "No usable inverter" in caplog.text


@pytest.mark.asyncio
async def test_inverter_without_its_battery(configured_request, monkeypatch, caplog):
    config, _, _, data = configured_request
    monkeypatch.setattr(config.devices, "batteries", {})
    data["soc"] = {}
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is None
    assert parameters.inverter is not None
    assert "Battery 'storage' of inverter 'inverter' is not available" in caplog.text


@pytest.mark.asyncio
async def test_invalid_energy_and_leading_price_gap_are_replaced(configured_request, caplog):
    _, _, _, data = configured_request
    forecasts = data["forecasts"]
    forecasts["total_load"][41] = -3.0
    forecasts["pv_forecast_wh"][43] = np.inf
    forecasts["electricity_price_per_wh"] = [np.nan] * 42 + [0.0005] * 54
    parameters = await resolve(GeneticRequest.model_validate(data))
    ems = parameters.ems
    assert ems.total_load[41] == 0.0
    assert ems.pv_forecast_wh[43] == 0.0
    # No price known before the start: the next known one.
    assert ems.electricity_price_per_wh[41] == 0.0005
    assert "load forecast missing or invalid in 1 slot(s)" in caplog.text
    assert "PV forecast missing or invalid in 1 slot(s)" in caplog.text
    assert "electricity price missing or invalid in 1 slot(s)" in caplog.text


@pytest.mark.asyncio
async def test_non_numeric_soc_measurement_assumes_empty(configured_request, caplog):
    _, fake, measurement, data = configured_request
    data["soc"] = {}
    measurement.key_to_lists.return_value = ([fake.start_datetime], ["full"])
    parameters = await resolve(GeneticRequest.model_validate(data))
    assert parameters.pv_battery is not None
    assert parameters.pv_battery.initial_soc_percentage == 0
    assert "No usable fresh SoC for storage; assuming 0% initial SoC" in caplog.text


@pytest.mark.asyncio
async def test_short_genome_of_the_last_run_is_not_used(configured_request, caplog):
    _, _, _, data = configured_request
    warm_start = SimpleNamespace(start_solution=[3.0], start_solution_datetime=None)
    parameters = await resolve(GeneticRequest.model_validate(data), warm_start)
    assert parameters.start_solution is None
    assert "start_solution of the last run [3.0] has fewer than two genes" in caplog.text
