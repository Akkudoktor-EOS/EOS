import pytest_asyncio

"""Capacity fits must preserve energy direction, coverage and independent anchors."""

# ruff: noqa: S101

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from akkudoktoreos.measurement.batterycapacity import (
    BatteryCapacityEstimationSettings,
    BatteryCapacityRequest,
    estimate_capacity,
)
from akkudoktoreos.measurement.measurement import MeasurementChannelSettings
from akkudoktoreos.measurement.quality import SampleQuality

START = datetime(2026, 9, 10, tzinfo=timezone.utc)


def fit(
    points,
    *,
    start_soc=20,
    end_soc=100,
    efficiency=1,
    method="hold",
    polarity="charging",
    unit="W",
    end_seconds=3600,
    quality=None,
    max_gap=3600,
):
    request = BatteryCapacityRequest.model_validate(dict(
        start=START,
        end=START + timedelta(seconds=end_seconds),
        start_soc_percentage=start_soc,
        end_soc_percentage=end_soc,
        soc_reference="voltage_current_anchor",
    ))
    settings = BatteryCapacityEstimationSettings(power_key="dc", positive_power=polarity)
    channel = MeasurementChannelSettings(
        quantity="power",
        unit=unit,
        integration_method=method,
        max_gap_seconds=max_gap,
    )
    return estimate_capacity(
        request,
        settings,
        channel,
        [
            (START + timedelta(seconds=t), v, (quality or {}).get(t, SampleQuality()))
            for t, v in points
        ],
        battery_id="battery",
        capacity_wh=12000,
        charging_efficiency=efficiency,
        discharging_efficiency=efficiency,
    )


def test_charge_fit_and_unclipped_model_error():
    result = fit([(0, 8000), (3600, 8000)])
    assert result.estimated_capacity_wh == pytest.approx(10000)
    assert result.configured_capacity_wh == 12000
    assert result.model_end_soc_percentage_unclipped == pytest.approx(86.6666667)
    assert result.model_soc_error_percentage_points == pytest.approx(-13.3333333)
    assert result.charge_energy_wh == 8000
    assert result.coverage_seconds == 3600


def test_efficiency_is_applied_once_on_dc_boundary():
    result = fit([(0, 10000), (3600, 10000)], efficiency=0.8)
    assert result.estimated_capacity_wh == pytest.approx(10000)
    assert result.stored_energy_change_wh == 8000


def test_discharge_fit_and_reversed_sensor_sign():
    result = fit(
        [(0, 6400), (3600, 6400)], start_soc=100, end_soc=20, polarity="discharging", efficiency=0.8
    )
    assert result.discharge_energy_wh == 6400
    assert result.estimated_capacity_wh == pytest.approx(10000)


def test_linear_zero_crossing_is_split_before_losses():
    result = fit([(0, -4000), (3600, 12000)], method="linear", efficiency=0.8)
    assert result.charge_energy_wh == pytest.approx(4500)
    assert result.discharge_energy_wh == pytest.approx(500)
    assert result.stored_energy_change_wh == pytest.approx(4500 * 0.8 - 500 / 0.8)


def test_kw_and_clipped_boundary_interpolation():
    result = fit([(-3600, 0), (3600, 16)], method="linear", unit="kW", max_gap=7200)
    assert result.charge_energy_wh == pytest.approx(12000)
    # Deliberately do not saturate the old model at 100%.
    assert result.model_end_soc_percentage_unclipped == pytest.approx(120)


@pytest.mark.parametrize(
    "points", [[], [(0, 8000)], [(60, 8000), (3600, 8000)], [(0, 8000), (3500, 8000)]]
)
def test_missing_coverage_is_not_extrapolated(points):
    with pytest.raises(ValueError, match="coverage"):
        fit(points)


def test_gap_is_rejected():
    with pytest.raises(ValueError, match="gap"):
        fit([(0, 8000), (3600, 8000)], max_gap=300)


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), True])
def test_bad_power_is_rejected(value):
    with pytest.raises(ValueError, match="finite measured"):
        fit([(0, value), (3600, 8000)])


@pytest.mark.parametrize("status", ["estimated", "invalid", "unavailable"])
def test_nonmeasured_quality_is_rejected(status):
    with pytest.raises(ValueError, match="finite measured"):
        fit([(0, 8000), (3600, 8000)], quality={0: SampleQuality(status=status)})


def test_reset_and_sensor_change_are_rejected():
    for quality in (SampleQuality(reset=True), SampleQuality(generation="replacement")):
        with pytest.raises(ValueError, match="reset or generation"):
            fit([(0, 8000), (3600, 8000)], quality={3600: quality})


@pytest.mark.parametrize("start_soc", [100, 99, 85])
def test_full_to_full_and_small_soc_span_do_not_produce_estimates(start_soc):
    with pytest.raises(ValueError, match="SoC change is too small"):
        fit([(0, 8000), (3600, 8000)], start_soc=start_soc)


def test_wrong_polarity_is_rejected():
    with pytest.raises(ValueError, match="disagrees"):
        fit([(0, -8000), (3600, -8000)])


def test_hidden_saturation_cannot_be_fixed_by_end_point_fitting():
    with pytest.raises(ValueError, match="Fitted SoC leaves"):
        fit([(0, 24000), (1800, -8000), (3600, -8000)])


def test_model_soc_is_not_an_accepted_reference():
    with pytest.raises(ValidationError):
        BatteryCapacityRequest.model_validate(dict(
            start=START,
            end=START + timedelta(hours=1),
            start_soc_percentage=20,
            soc_reference="calculated_soc",
        ))


@pytest_asyncio.fixture
async def database_case(config_eos):
    from akkudoktoreos.core.coreabc import get_measurement
    from akkudoktoreos.measurement.quality import MeasurementSample

    get_measurement()._db_reset_state()
    config_eos.merge_settings_from_dict(
        {
            "devices": {
                "batteries": {
                    "battery": {
                        "device_id": "battery",
                        "capacity_wh": 12000,
                        "charging_efficiency": 1,
                        "discharging_efficiency": 1,
                        "capacity_estimation": {
                            "power_key": "battery_dc",
                            "positive_power": "charging",
                        },
                    }
                }
            },
            "measurement": {
                "channels": {
                    "battery_dc": {
                        "quantity": "power",
                        "unit": "W",
                        "integration_method": "hold",
                        "max_gap_seconds": 3600,
                    }
                }
            },
        }
    )
    (await get_measurement().import_samples(
        [
            MeasurementSample(
                date_time=START + timedelta(seconds=t), key="battery_dc", value=8000.0
            )
            for t in (0, 1800, 3600)
        ]
    ))
    try:
        yield config_eos
    finally:
        get_measurement()._db_reset_state()


def test_http_reads_database_and_stores_only_explicit_estimate(database_case):
    from fastapi.testclient import TestClient

    from akkudoktoreos.server.eos import app

    client = TestClient(app)
    body = {
        "start": START.isoformat(),
        "end": (START + timedelta(hours=1)).isoformat(),
        "start_soc_percentage": 20,
        "soc_reference": "voltage_current_anchor",
    }
    response = client.post("/v1/measurement/battery-capacity/battery", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["estimated_capacity_wh"] == pytest.approx(10000)
    battery = database_case.devices.batteries["battery"]
    assert battery.capacity_estimate is None
    assert battery.capacity_wh == 12000
    body["store_estimate"] = True
    response = client.post("/v1/measurement/battery-capacity/battery", json=body)
    assert response.status_code == 200, response.text
    battery = database_case.devices.batteries["battery"]
    assert battery.capacity_estimate is not None
    assert battery.capacity_estimate.estimated_capacity_wh == pytest.approx(10000)
    assert battery.capacity_wh == 12000
    database_case.merge_settings_from_dict({"optimization": {"genetic": {"individuals": 100}}})
    battery = database_case.devices.batteries["battery"]
    assert battery.capacity_estimate is not None
    assert battery.capacity_estimate.estimated_capacity_wh == pytest.approx(10000)
    assert battery.capacity_wh == 12000

    # The estimate survives normal config serialization without becoming capacity_wh.
    data = database_case.to_config_json()
    assert '"estimated_capacity_wh": 10000.0' in data
    assert '"capacity_wh": 12000' in data
    previous = battery.capacity_estimate
    body["start_soc_percentage"] = 100
    assert client.post("/v1/measurement/battery-capacity/battery", json=body).status_code == 422
    assert battery.capacity_estimate is previous
