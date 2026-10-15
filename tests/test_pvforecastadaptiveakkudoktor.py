"""Issued forecast persistence, causal arrivals, RC provider wiring and data gaps."""

import json
from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.measurement.quality import MeasurementSample
from akkudoktoreos.prediction.pvforecastadaptiveakkudoktor import (
    PVForecastAdaptiveAkkudoktor,
    PVForecastAdaptiveAkkudoktorCommonSettings,
)
from akkudoktoreos.prediction.pvlearnstore import PVLearningStore
from akkudoktoreos.utils.datetimeutil import to_datetime


def sample(key, time, value):
    return MeasurementSample(
        key=key, date_time=pd.Timestamp(time).to_pydatetime(), value=float(value)
    )


def test_sample_arrival_is_preserved_and_revisions_get_later_availability(tmp_path):
    store = PVLearningStore(tmp_path / "learning.sqlite3")
    point = sample("micro", "2026-09-01T12:00Z", 1000)
    first, later = pd.Timestamp("2026-09-01T12:01Z"), pd.Timestamp("2026-09-01T13:00Z")
    store.import_samples([point], first)
    store.import_samples([point], later)
    assert store.samples("micro", pd.Timestamp("2026-09-01T11:00Z"), first)[0][
        "available_ms"
    ] == store.milliseconds(first)
    changed = sample("micro", point.date_time, 2000)
    store.import_samples([changed], later)
    assert store.samples("micro", pd.Timestamp("2026-09-01T11:00Z"), first) == []


def test_future_sample_rejected_before_storage(tmp_path):
    store = PVLearningStore(tmp_path / "learning.sqlite3")
    now = pd.Timestamp("2026-09-01T12:00Z")
    with pytest.raises(ValueError, match="Future"):
        store.import_samples([sample("micro", now + pd.Timedelta(minutes=1), 1)], now)


def test_issued_forecast_immutable_and_day_ahead_baseline_freezes(tmp_path):
    store = PVLearningStore(tmp_path / "learning.sqlite3")
    target = pd.Timestamp("2026-09-02T08:00Z")
    before = target - pd.Timedelta(minutes=30)
    original = dict(plant_raw_w=1000, features=None)
    store.remember("a", before, [(target, original)], "Europe/Berlin")
    store.remember("a", before, [(target, dict(plant_raw_w=9999, features=None))], "Europe/Berlin")
    with store.connection() as db:
        row = db.execute("SELECT payload FROM forecasts").fetchone()
    assert json.loads(row[0])["plant_raw_w"] == 1000
    assert store.baselines("a", before, target + pd.Timedelta(hours=1))[0][1]["plant_raw_w"] == 1000


@pytest.fixture
def provider(config_eos):
    config_eos.merge_settings_from_dict(
        dict(
            general=dict(latitude=50.8588, longitude=7.3747, timezone="Europe/Berlin"),
            pvforecast=dict(
                provider="PVForecastAdaptiveAkkudoktor",
                adaptive=dict(regional_weather_model=None, weather_models=["icon_seamless"]),
                planes=[
                    dict(
                        name="Hybrid",
                        peakpower=3,
                        surface_tilt=20,
                        surface_azimuth=180,
                        measurement_key="hybrid",
                        curtailment_sensitive=True,
                    ),
                    dict(
                        name="Micro",
                        peakpower=1,
                        surface_tilt=20,
                        surface_azimuth=180,
                        measurement_key="micro",
                        nowcast_reference=True,
                    ),
                ],
            ),
            measurement=dict(
                keys=["hybrid", "micro"],
                channels={
                    key: dict(
                        quantity="power", unit="W", integration_method="hold", max_gap_seconds=600
                    )
                    for key in ("hybrid", "micro")
                },
            ),
        )
    )
    return PVForecastAdaptiveAkkudoktor()


def test_measurement_closing_samples_and_ingestion_arrivals_are_causal(provider):
    issue = pd.Timestamp("2026-09-18T12:20:00Z")
    values = [
        sample(key, time, 200)
        for key in ("hybrid", "micro")
        for time in pd.date_range(
            issue.floor("15min") - pd.Timedelta(minutes=30), issue, freq="5min"
        )
    ]
    provider.store().import_samples(values, issue)
    result = provider._measurements(issue, 1)
    assert result.loc["2026-09-18T12:00Z", "reference_actual_w"] == pytest.approx(200)
    before_arrival = provider._measurements(issue - pd.Timedelta(minutes=1), 1)
    assert before_arrival.reference_actual_w.isna().all()


def test_missing_reference_does_not_become_zero_or_borrow_hybrid_power(provider):
    issue = pd.Timestamp("2026-09-18T12:20:00Z")
    values = [
        sample("hybrid", time, 200)
        for time in pd.date_range(issue - pd.Timedelta(hours=1), issue, freq="5min")
    ]
    provider.store().import_samples(values, issue)
    result = provider._measurements(issue, 1)
    assert result.reference_actual_w.isna().all()
    assert result.actual_w.isna().all()


def test_hybrid_never_trains_weather_gain(provider):
    issue = pd.Timestamp("2026-09-18T12:20:00Z")
    state = provider._models(provider._signature(), issue)
    assert state["gains"]["hybrid"]["gain"] == 1
    assert state["gains"]["hybrid"]["days"] == 0
    assert state["coefficients"] == {}
    assert provider.store().metadata("daily_models") == state


@pytest.mark.asyncio
async def test_cold_provider_generates_standard_power_and_status(provider):
    from test_pvforecastakkudoktorlocal import synthetic_openmeteo

    original = to_datetime
    issue = pd.Timestamp("2025-06-15T12:05:00+02:00")

    def clock(*args, **kwargs):
        return (
            original(issue.to_pydatetime())
            if not args and kwargs.get("in_timezone")
            else original(*args, **kwargs)
        )

    with (
        patch(
            "akkudoktoreos.prediction.pvforecastadaptiveakkudoktor.to_datetime", side_effect=clock
        ),
        patch.object(type(provider), "_request_local_forecast", return_value=synthetic_openmeteo()),
    ):
        await provider._update_data()
    status = provider.store().metadata("status")
    assert status["state"] == "reference_missing"
    assert status["training_days"] == 0
    assert status["trained_leads"] == 0
    assert status["references"] == ["micro"]
    assert provider.record_keys.count("pvforecast_ac_power") == 1
    future = await provider.key_to_series("pvforecast_ac_power")
    assert future.max() > 500
    assert np.isfinite(future.to_numpy()).all()
    for row in provider.store().latest(provider.store().milliseconds(issue)):
        assert row["plant_adaptive_w"] == pytest.approx(row["plant_calibrated_w"])


@pytest.mark.parametrize(
    "patch",
    [
        dict(backend="remote"),
        dict(resolution_minutes=60),
        dict(calibration_enabled=True),
        dict(min_training_days=10, learning_days=7),
    ],
)
def test_adaptive_settings_reject_inconsistent_modes(patch):
    with pytest.raises(ValueError):
        PVForecastAdaptiveAkkudoktorCommonSettings(**patch)


def test_pruning_is_scoped_to_private_archive(tmp_path):
    path = tmp_path / "learning.sqlite3"
    store = PVLearningStore(path)
    store.import_samples(
        [sample("micro", "2026-08-01T12:00Z", 100)], pd.Timestamp("2026-08-01T12:00Z")
    )
    store.metadata("profiles", dict(micro=dict(response=[1] * 46)))
    store.prune(pd.Timestamp("2026-10-01T12:00Z"), 31)
    assert (
        store.samples("micro", pd.Timestamp("2026-07-01T12:00Z"), pd.Timestamp("2026-10-01T12:00Z"))
        == []
    )
    assert store.metadata("profiles")


def test_labels_are_unavailable_before_true_receipt_and_actual_can_arrive_later(tmp_path):
    store = PVLearningStore(tmp_path / "learning.sqlite3")
    target = pd.Timestamp("2026-09-18T12:15Z")
    issue = target - pd.Timedelta(minutes=15)
    payload = dict(
        features=dict(level=0.1), reference_sky_w=1000, reference_calibrated_w=500, plant_raw_w=1000
    )
    store.remember("a", issue, [(target, payload)], "Europe/Berlin")
    received = target + pd.Timedelta(minutes=25)
    observations = pd.DataFrame(
        dict(reference_actual_w=[600], actual_w=[np.nan], available_at=[received]), index=[target]
    )
    store.resolve("a", observations, received)
    assert store.training("a", received - pd.Timedelta(seconds=1)).empty
    assert store.training("a", received).iloc[0].target_residual == pytest.approx(0.1)
    observations["actual_w"] = 1500
    store.resolve("a", observations, received + pd.Timedelta(minutes=2))
    row = store.comparisons("a", store.milliseconds(issue), store.milliseconds(received))[0]
    assert row["actual_w"] == 1500
    assert row["reference_actual_w"] == 600
    assert row["plant_raw_w"] == 1000
    assert row["available_ms"] == store.milliseconds(received)


def test_regional_missing_tail_falls_back_without_filling_radiation_gaps(provider):
    from test_pvforecastakkudoktorlocal import synthetic_openmeteo

    data = synthetic_openmeteo()
    raw = data["minutely_15"]
    from akkudoktoreos.prediction.pvforecastakkudoktorlocal import OPENMETEO_VARIABLES

    provider.config.pvforecast.adaptive.regional_weather_model = "icon_d2"
    for variable in OPENMETEO_VARIABLES:
        raw[variable + "_icon_d2"] = [None] * len(raw["time"])
    frame = provider._weather_frame(data)
    assert (frame.regional_weight == 0).all()
    for variable in OPENMETEO_VARIABLES[3:]:
        for member in list(raw):
            if member == variable or member.startswith(variable + "_"):
                raw[member][50] = None
    frame = provider._weather_frame(data)
    assert frame.iloc[50][list(OPENMETEO_VARIABLES[3:])].isna().all()


@pytest.mark.asyncio
async def test_native_measurements_keep_first_seen_availability(provider):
    from types import SimpleNamespace

    issue = pd.Timestamp("2026-09-18T12:20Z")
    record = SimpleNamespace(
        date_time=to_datetime("2026-09-18T12:00Z"),
        configured_data=dict(micro=500),
        sample_quality={},
    )

    async def records(*args):
        yield record

    with patch.object(type(provider.measurement), "db_iterate_records", side_effect=records):
        await provider._sync_measurements(issue)
        await provider._sync_measurements(issue + pd.Timedelta(minutes=1))
    assert provider.store().samples("micro", issue - pd.Timedelta(hours=1), issue)[0][
        "available_ms"
    ] == provider.store().milliseconds(issue)


def test_persisted_live_features_train_after_five_complete_days(tmp_path):
    from akkudoktoreos.prediction.pvtrend import FEATURES, LocalPVTrendModel

    store = PVLearningStore(tmp_path / "learning.sqlite3")
    for day in pd.date_range("2026-09-12T08:00Z", periods=5, freq="D"):
        for offset in range(24):
            issue = day + pd.Timedelta(minutes=offset * 15)
            target = issue + pd.Timedelta(minutes=15)
            features: dict[str, Any] = {key: 0.1 for key in FEATURES}
            features["intercept"] = 1.0
            features["feature_available_at"] = issue
            store.remember(
                "a",
                issue,
                [
                    (
                        target,
                        dict(features=features, reference_sky_w=1000, reference_calibrated_w=500),
                    )
                ],
                "Europe/Berlin",
            )
            received = target + pd.Timedelta(minutes=20)
            observed = pd.DataFrame(
                dict(reference_actual_w=[650], actual_w=[1000], available_at=[received]),
                index=[target],
            )
            store.resolve("a", observed, received)
    cutoff = pd.Timestamp("2026-09-18T12:00Z")
    trained = LocalPVTrendModel(scale_features=True).fit(store.training("a", cutoff), cutoff)
    assert trained.training_counts[15] == 120
    assert 15 in trained.coefficients
    assert np.isfinite(trained.coefficients[15]).all()


@pytest.fixture
def adaptive_client(provider, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from akkudoktoreos.server.rest import pvadaptive

    monkeypatch.setattr(pvadaptive, "provider", lambda: provider)
    app = FastAPI()
    app.include_router(pvadaptive.router)
    with TestClient(app) as client:
        yield client


def test_api_exposes_honest_cold_start_and_persists_samples(adaptive_client, provider):
    prefix = "/v1/prediction/pv-adaptive"
    status = adaptive_client.get(prefix + "/status")
    assert status.status_code == 200
    assert status.json()["state"] == "initializing"
    assert status.json()["training_days"] == 0
    assert adaptive_client.get(prefix + "/latest").json() == []
    assert adaptive_client.get(prefix + "/comparisons").json() == []
    now = pd.Timestamp.now(tz="UTC")
    reading = sample("micro", now - pd.Timedelta(minutes=5), 250)
    response = adaptive_client.put(prefix + "/samples", json=[reading.model_dump(mode="json")])
    assert response.status_code == 200
    assert response.json() == {"updated": 1}
    stored = PVLearningStore(provider.store().path)
    assert (
        stored.samples("micro", now - pd.Timedelta(hours=1), pd.Timestamp.now(tz="UTC"))[0]["value"]
        == 250
    )


@pytest.mark.parametrize("case", ["unknown", "negative", "future", "timezone_missing"])
def test_api_rejects_invalid_sample_batches_atomically(adaptive_client, provider, case):
    now = pd.Timestamp.now(tz="UTC")
    valid = sample("micro", now - pd.Timedelta(minutes=10), 250).model_dump(mode="json")
    invalid = dict(valid)
    if case == "unknown":
        invalid["key"] = "not_a_pv_plane"
    elif case == "negative":
        invalid["value"] = -1
    elif case == "future":
        invalid["date_time"] = (now + pd.Timedelta(hours=1)).isoformat()
    else:
        invalid["date_time"] = "2026-01-01T12:00:00"
    response = adaptive_client.put("/v1/prediction/pv-adaptive/samples", json=[valid, invalid])
    assert response.status_code == 422
    assert (
        provider.store().samples("micro", now - pd.Timedelta(hours=1), pd.Timestamp.now(tz="UTC"))
        == []
    )
