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


def test_curtailment_flag_requires_complete_fresh_zero_support(provider):
    plane = provider.config.pvforecast.planes[0]
    plane.curtailment_key = "unrestricted_flag"
    issue = pd.Timestamp("2026-09-18T12:20Z")
    times = pd.date_range("2026-09-18T11:30Z", issue, freq="5min")
    provider.store().import_samples(
        [
            sample(key, t, 200 if key != "unrestricted_flag" else 0)
            for key in ("hybrid", "micro", "unrestricted_flag")
            for t in times
        ],
        issue,
    )
    measured = provider._measurements(issue, 1)
    assert bool(measured.loc["2026-09-18T12:00Z", "hybrid_eligible"])
    provider.store().import_samples([sample("unrestricted_flag", "2026-09-18T12:05Z", 1)], issue)
    measured = provider._measurements(issue, 1)
    assert not bool(measured.loc["2026-09-18T12:00Z", "hybrid_eligible"])
    assert measured.loc["2026-09-18T12:00Z", "hybrid"] == 200


def test_curtailment_flag_missing_does_not_authorize_learning(provider):
    provider.config.pvforecast.planes[0].curtailment_key = "missing_flag"
    issue = pd.Timestamp("2026-09-18T12:20Z")
    provider.store().import_samples(
        [sample("hybrid", t, 200) for t in pd.date_range("2026-09-18T11:30Z", issue, freq="5min")],
        issue,
    )
    measured = provider._measurements(issue, 1)
    assert not measured.hybrid_eligible.any()


def test_explicit_site_guard_excludes_high_values_and_requires_support(provider):
    from akkudoktoreos.prediction.pvforecast import PVForecastPlaneSetting

    provider.config.pvforecast.planes[0] = PVForecastPlaneSetting(
        **{
            **provider.config.pvforecast.planes[0].model_dump(),
            "learning_guard_key": "battery_soc_fraction",
            "learning_guard_max": 0.85,
            "learning_guard_max_gap_seconds": 1800,
        }
    )
    issue = pd.Timestamp("2026-09-18T12:20Z")
    times = pd.date_range("2026-09-18T11:30Z", issue, freq="5min")
    provider.store().import_samples(
        [sample("hybrid", t, 200) for t in times]
        + [sample("battery_soc_fraction", t, 0.6) for t in times],
        issue,
    )
    assert provider._measurements(issue, 1).loc["2026-09-18T12:00Z", "hybrid_eligible"]
    provider.store().import_samples(
        [sample("battery_soc_fraction", "2026-09-18T12:05Z", 0.9)], issue
    )
    assert not provider._measurements(issue, 1).loc["2026-09-18T12:00Z", "hybrid_eligible"]


@pytest.mark.parametrize(
    "fields",
    [
        {"learning_guard_key": "soc"},
        {"learning_guard_max": 0.9},
        {"learning_guard_key": "soc", "learning_guard_max": 0.9, "curtailment_key": "flag"},
        {"measurement_key": "power", "curtailment_key": "power"},
    ],
)
def test_invalid_learning_guard_config_rejected(fields):
    from akkudoktoreos.prediction.pvforecast import PVForecastPlaneSetting

    with pytest.raises(ValueError):
        PVForecastPlaneSetting(**fields)


def test_new_quality_options_preserve_legacy_physical_signature(provider):
    original = provider._signature()
    provider.config.pvforecast.planes[0].correction_group = "new_inverter"
    provider.config.pvforecast.planes[0].curtailment_key = "new_status_flag"
    provider.config.pvforecast.adaptive.correction_mode = "calibrated"
    assert provider._signature() == original


def test_total_channel_and_no_independent_reference_are_supported(provider):
    from akkudoktoreos.measurement.measurement import MeasurementChannelSettings

    provider.config.pvforecast.adaptive.total_measurement_key = "whole_plant"
    provider.config.pvforecast.adaptive.total_curtailment_sensitive = True
    for plane in provider.config.pvforecast.planes:
        plane.measurement_key = None
    provider.config.measurement.channels["whole_plant"] = MeasurementChannelSettings(
        quantity="power", unit="W", integration_method="hold", max_gap_seconds=600
    )
    issue = pd.Timestamp("2026-09-18T12:20Z")
    provider.store().import_samples(
        [
            sample("whole_plant", t, 500)
            for t in pd.date_range("2026-09-18T11:30Z", issue, freq="5min")
        ],
        issue,
    )
    measured = provider._measurements(issue, 1)
    assert measured.loc["2026-09-18T12:00Z", "actual_w"] == 500
    assert measured.reference_actual_w.isna().all()
    assert provider._groups() == {"plant": ["whole_plant"]}


@pytest.mark.asyncio
async def test_calibrated_ac_is_limited_per_plane_and_dc_is_not_reconstructed(provider):
    issue = pd.Timestamp("2026-09-18T12:05Z")
    provider.config.pvforecast.planes[1].inverter_paco = 800
    index = pd.date_range(issue.floor("15min") - pd.Timedelta(hours=2), periods=20, freq="15min")
    raw = pd.DataFrame(
        dict(
            hybrid=3000.0,
            micro=800.0,
            hybrid_dc=5000.0,
            micro_dc=1000.0,
            weather_weight=0.0,
            solar_elevation=30.0,
        ),
        index=index,
    )
    models = dict(
        gains={
            k: dict(
                gain=1.5 if k == "micro" else 1.0, shape=[1.0] * 12, days=5, curtailed=k == "hybrid"
            )
            for k in ("hybrid", "micro")
        },
        coefficients={},
        training_counts={},
        fitted_at=issue.isoformat(),
        selection={},
    )
    original = to_datetime

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
        patch.object(type(provider), "_physical_frames", return_value=[raw, raw.copy()]),
        patch.object(type(provider), "_request_local_forecast", return_value={}),
        patch.object(type(provider), "_models", return_value=models),
    ):
        await provider._update_data()
    forecast = await provider.key_to_series("pvforecast_ac_power")
    dc = await provider.key_to_series("pvforecast_dc_power")
    assert forecast.max() <= 3800
    assert dc.max() == pytest.approx(6500)


def test_missing_reference_still_resolves_whole_plant_metrics(tmp_path):
    store = PVLearningStore(tmp_path / "learning.sqlite3")
    issue = pd.Timestamp("2026-09-01T10:00Z")
    target = issue + pd.Timedelta(minutes=15)
    store.remember(
        "a",
        issue,
        [
            (
                target,
                dict(
                    plant_raw_w=1000,
                    plant_calibrated_w=900,
                    plant_local_w=900,
                    plant_adaptive_w=900,
                ),
            )
        ],
        "UTC",
    )
    available = target + pd.Timedelta(minutes=20)
    store.resolve(
        "a",
        pd.DataFrame(
            dict(actual_w=[800], reference_actual_w=[np.nan], available_at=[available]),
            index=[target],
        ),
        available,
    )
    rows = store.metric_rows("a", issue, available)
    assert rows[0]["actual_w"] == 800
    assert (
        store.comparisons("a", store.milliseconds(issue), store.milliseconds(available))[0][
            "actual_w"
        ]
        == 800
    )


def test_metrics_use_full_window_independently_of_public_export_limit(tmp_path):
    store = PVLearningStore(tmp_path / "learning.sqlite3")
    target = pd.Timestamp("2026-09-02T12:00Z")
    stamp = store.milliseconds(target)
    payload = store.dump(dict(plant_raw_w=1000, reference_raw_w=100))
    with store.connection() as db:
        db.executemany(
            "INSERT INTO forecasts(signature,issue_ms,target_ms,lead_minutes,payload,actual_w,updated_ms) VALUES (?,?,?,?,?,?,?)",
            [
                ("a", stamp - 100000 + i, stamp, 1, payload, 500, stamp + 900000)
                for i in range(20005)
            ],
        )
    start, end = target - pd.Timedelta(hours=1), target + pd.Timedelta(hours=1)
    assert (
        len(store.comparisons("a", store.milliseconds(start), store.milliseconds(end), limit=20000))
        == 20000
    )
    assert len(store.metric_rows("a", start, end)) == 20005


def test_daily_fit_waits_for_previous_day_closing_samples(provider):
    first = provider._models(provider._signature(), pd.Timestamp("2026-09-18T12:00Z"))
    before = provider._models(provider._signature(), pd.Timestamp("2026-09-19T00:02+02:00"))
    assert before["fitted_at"] == first["fitted_at"]
    after = provider._models(provider._signature(), pd.Timestamp("2026-09-19T00:16+02:00"))
    assert after["day"] == "2026-09-19"
    assert after["after_delay"] is True


def test_day_ahead_metrics_count_dst_intervals_and_never_zero_fill(tmp_path):
    store = PVLearningStore(tmp_path / "learning.sqlite3")
    start = pd.Timestamp("2026-10-25", tz="Europe/Berlin")
    end = start + pd.DateOffset(days=1)
    targets = pd.date_range(start, end, freq="15min", inclusive="left")
    payload = store.dump({f"plant_{m}_w": 2000 for m in ("raw", "calibrated", "local", "adaptive")})
    with store.connection() as db:
        db.executemany(
            "INSERT INTO baselines VALUES (?,?,?,?)",
            [
                (
                    "a",
                    store.milliseconds(t),
                    store.milliseconds(start - pd.Timedelta(hours=1)),
                    payload,
                )
                for t in targets
            ],
        )
        db.executemany(
            "INSERT INTO forecasts(signature,issue_ms,target_ms,lead_minutes,payload,actual_w,updated_ms) VALUES (?,?,?,?,?,?,?)",
            [
                (
                    "a",
                    store.milliseconds(t - pd.Timedelta(minutes=15)),
                    store.milliseconds(t),
                    15,
                    payload,
                    1000,
                    store.milliseconds(end),
                )
                for t in targets
            ],
        )
    rows = store.daily_metrics("a", start, end, "Europe/Berlin")
    assert rows[0]["complete"] is True
    assert rows[0]["expected_slots"] == 100
    assert rows[0]["actual_kwh"] == 25
    assert rows[0]["adaptive_kwh"] == 50
    with store.connection() as db:
        db.execute(
            "UPDATE forecasts SET actual_w=NULL WHERE target_ms=?",
            (store.milliseconds(targets[3]),),
        )
    assert store.daily_metrics("a", start, end, "Europe/Berlin")[0]["actual_kwh"] is None


def test_censored_calibration_uses_only_authorized_partial_days():
    from akkudoktoreos.prediction.pvadaptive import AdaptivePVModel

    index = pd.DatetimeIndex(
        [
            t
            for d in pd.date_range("2026-09-01T10:00Z", periods=5, freq="D")
            for t in pd.date_range(d, periods=8, freq="15min")
        ]
    )
    history = pd.DataFrame(
        dict(raw_power=1000.0, measured_power=600.0, available_at=index + pd.Timedelta(minutes=20)),
        index=index,
    )
    cutoff = pd.Timestamp("2026-09-07T00:00Z")
    model = AdaptivePVModel(min_days=5).fit(history, cutoff)
    assert model.trained_days == 0
    partial = AdaptivePVModel(min_days=5, allow_partial_days=True).fit(history, cutoff)
    assert partial.trained_days == 5
    assert partial.gain == pytest.approx(0.6)
