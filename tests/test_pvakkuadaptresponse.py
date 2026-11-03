"""Causal solar response, model weather weighting and frozen-candidate selection."""

import numpy as np
import pandas as pd

from akkudoktoreos.prediction.pvakkuadaptresponse import (
    fit_solar_response,
    fit_weather_response,
    paired_rows,
    select_responses,
    solar_basis,
    solar_factors,
)


def test_solar_response_learns_repeatable_shape_without_learning_common_cloud_error():
    index = pd.date_range("2026-09-01T06:00Z", "2026-09-10T16:00Z", freq="15min")
    index = index[(index.hour >= 6) & (index.hour < 17)]
    basis = solar_basis(index, 51.0, 7.4)
    shade = 1 - 0.35 * basis[:, 5:20].sum(axis=1)
    clouds = 0.55 + 0.4 * (np.sin(np.arange(len(index)) * 0.4) + 1) / 2
    history = pd.DataFrame(
        dict(
            base=1000,
            actual=1000 * clouds * shade,
            common=clouds,
            available_at=index + pd.Timedelta(minutes=20),
        ),
        index=index,
    )
    cutoff = pd.Timestamp("2026-09-11T00:00Z")
    fit = fit_solar_response(history, cutoff, "Europe/Berlin", 51.0, 7.4, 5)
    assert fit["fitted"] and fit["days"] >= 5
    future = pd.date_range("2026-09-12T06:00Z", periods=40, freq="15min")
    future_basis = solar_basis(future, 51.0, 7.4)
    truth = 1 - 0.35 * future_basis[:, 5:20].sum(axis=1)
    predicted = solar_factors(future_basis, fit)
    assert np.mean(abs(predicted - truth)) < np.mean(abs(1 - truth)) * 0.6
    history["actual"] = 1000 * clouds
    neutral = fit_solar_response(history, cutoff, "Europe/Berlin", 51.0, 7.4, 5)
    assert np.allclose(solar_factors(future_basis, neutral), 1)
    history["available_at"] = cutoff + pd.Timedelta(days=1)
    assert not fit_solar_response(history, cutoff, "Europe/Berlin", 51.0, 7.4, 5)["fitted"]


def test_response_validation_needs_independent_days_and_does_not_reward_frequency():
    pairs = [
        dict(
            bucket="6-24h",
            day=f"2026-09-0{day}",
            actual=1000.0,
            calibrated=1300.0,
            solar=1100.0,
            weather=1050.0,
        )
        for day in range(1, 5)
        for _ in range(10)
    ]
    choice = select_responses(pairs, 3, 32, 0.02)["6-24h"]
    assert choice["method"] == "weather"
    assert choice["validated"] and choice["days"] == 4
    assert select_responses(pairs[:10] * 50, 3, 32, 0.02)["6-24h"]["method"] == "calibrated"
    assert select_responses(pairs, 3, 32, 0.02, ("calibrated",))["6-24h"]["method"] == "calibrated"
    # A better MAE alone must not worsen energy bias.
    pairs = [
        dict(
            bucket="6-24h",
            day=f"2026-09-0{day}",
            actual=1000.0,
            calibrated=1000.0 + sign * 300,
            solar=1100.0,
            weather=1100.0,
        )
        for day in range(1, 5)
        for sign in (-1, 1)
        for _ in range(5)
    ]
    assert select_responses(pairs, 3, 32, 0.02)["6-24h"]["method"] == "calibrated"


def test_paired_rows_reject_late_missing_and_curtailment_labels_and_deduplicate():
    time = pd.Timestamp("2026-09-01T12:00Z")
    truth = pd.DataFrame(
        dict(
            micro=[1000.0], micro_eligible=[True], micro_available=[time + pd.Timedelta(minutes=20)]
        ),
        index=[time],
    )
    row = dict(
        target_ms=int(time.timestamp() * 1000),
        issued_ms=int((time - pd.Timedelta(hours=4)).timestamp() * 1000),
        planes=dict(micro=dict(calibrated=1200.0, solar=1100.0, weather=1000.0, sky=1500.0)),
        members=dict(icon_d2=dict(micro=None)),
        response_version=1,
    )
    cutoff = pd.Timestamp("2026-09-02T00:00Z")
    paired = paired_rows([row] * 20, truth, ["micro"], cutoff, "Europe/Berlin")
    assert len(paired) == 1
    assert np.isnan(paired[0]["members"]["icon_d2"])
    assert not fit_weather_response(paired, 3, 32)["2-6h"]["fitted"]
    truth["micro_eligible"] = False
    assert paired_rows([row], truth, ["micro"], cutoff, "Europe/Berlin") == []
    truth["micro_eligible"] = True
    truth["micro_available"] = cutoff + pd.Timedelta(days=1)
    assert paired_rows([row], truth, ["micro"], cutoff, "Europe/Berlin") == []


def test_weather_weights_are_trained_by_horizon_on_matching_members():
    pairs = [
        dict(
            bucket="6-24h",
            day=f"2026-09-0{day}",
            actual=500.0,
            sky=1500.0,
            members=dict(icon_d2=600.0, icon_seamless=1000.0),
        )
        for day in range(1, 6)
        for _ in range(10)
    ]
    fitted = fit_weather_response(pairs, 5, 32)
    assert fitted["6-24h"]["fitted"]
    assert fitted["6-24h"]["weights"]["icon_d2"] > fitted["6-24h"]["weights"]["icon_seamless"]
    assert not fitted["24-48h"]["fitted"]
    assert abs(sum(fitted["6-24h"]["weights"].values()) - 1) < 1e-9
