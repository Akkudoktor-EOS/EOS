"""Causal trend features, online label availability and trend generalization."""

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.prediction.pvadaptive import apply_local_nowcast
from akkudoktoreos.prediction.pvtrend import (
    FEATURES,
    LocalPVTrendModel,
    local_trend_features,
)


def case():
    issue = pd.Timestamp("2026-09-18T12:05:00+02:00")
    past = pd.date_range(issue.floor("15min") - pd.Timedelta(hours=1), periods=4, freq="15min")
    hist = pd.DataFrame(
        dict(
            measured_power=[800, 900, 1100, 1500],
            calibrated_power=1200.0,
            clear_sky_power=2000.0,
            eligible=True,
            available_at=past + pd.Timedelta(minutes=20),
        ),
        index=past,
    )
    future = pd.date_range(issue.ceil("15min"), periods=10, freq="15min")
    return issue, hist, pd.Series(1200.0, index=future), pd.Series(2000.0, index=future)


def test_features_ignore_future_actuals_and_describe_a_rising_weather_residual():
    issue, hist, pred, sky = case()
    expected = local_trend_features(hist, pred, sky, issue)
    assert expected.ramp.iloc[0] > 0
    assert expected.slope.iloc[0] > 0
    assert expected.feature_available_at.iloc[0] <= issue
    hist.loc[issue.ceil("15min")] = [999999, 1200, 2000, True, issue + pd.Timedelta(hours=1)]
    pd.testing.assert_frame_equal(local_trend_features(hist, pred, sky, issue), expected)


@pytest.mark.parametrize("bad", ["gap", "quality", "nan", "stale", "dim"])
def test_invalid_latest_reference_never_produces_a_trend(bad):
    issue, hist, pred, sky = case()
    if bad == "gap":
        hist = hist.drop(hist.index[1])
    elif bad == "quality":
        hist.loc[hist.index[-1], "eligible"] = False
    elif bad == "nan":
        hist.loc[hist.index[-1], "measured_power"] = np.nan
    elif bad == "stale":
        hist.index -= pd.Timedelta(hours=1)
        hist.available_at -= pd.Timedelta(hours=1)
    else:
        hist.loc[hist.index[-1], "clear_sky_power"] = 50
    assert local_trend_features(hist, pred, sky, issue)[list(FEATURES)].isna().all().all()


def training():
    rng = np.random.default_rng(42)
    issue = pd.date_range("2026-09-01T08:05:00Z", periods=200, freq="1h")
    frame = pd.DataFrame(rng.uniform(-0.2, 0.2, (len(issue), len(FEATURES))), columns=FEATURES)
    frame["intercept"] = 1.0
    frame["issue"] = issue
    frame["time"] = issue + pd.Timedelta(minutes=10)
    frame["lead_minutes"] = 10.0
    frame["target_available_at"] = issue + pd.Timedelta(minutes=30)
    frame["feature_available_at"] = issue
    frame["reference_sky_w"] = 2000.0
    frame["target_residual"] = 0.7 * frame.level + 0.8 * frame.ramp
    return frame


def test_model_learns_slope_on_unseen_samples_while_level_only_cannot():
    samples = training()
    cutoff = pd.Timestamp("2026-09-15T00:00:00Z")
    trend = LocalPVTrendModel(ridge=0.01).fit(samples.iloc[:160], cutoff)
    level = LocalPVTrendModel(use_trend=False, ridge=0.01).fit(samples.iloc[:160], cutoff)
    test = samples.iloc[160:]
    trend_error = np.abs(
        test[list(trend.feature_names)] @ trend.coefficients[15] - test.target_residual
    )
    level_error = np.abs(
        test[list(level.feature_names)] @ level.coefficients[15] - test.target_residual
    )
    assert trend_error.mean() < 0.1 * level_error.mean()


def test_scaling_allows_learning_small_normalized_features_with_regularization():
    samples = training()
    cutoff = pd.Timestamp("2026-09-15T00:00:00Z")
    model = LocalPVTrendModel(ridge=20, scale_features=True).fit(samples.iloc[:160], cutoff)
    test = samples.iloc[160:]
    error = np.abs(test[list(model.feature_names)] @ model.coefficients[15] - test.target_residual)
    assert error.mean() < 0.02


def test_later_labels_and_later_feature_arrivals_cannot_change_coefficients():
    samples = training()
    cutoff = pd.Timestamp("2026-09-15T00:00:00Z")
    expected = LocalPVTrendModel().fit(samples, cutoff).coefficients[15]
    poison = samples.copy()
    poison.target_residual = 1.5
    poison.target_available_at = cutoff + pd.Timedelta(minutes=1)
    combined = pd.concat([samples, poison])
    np.testing.assert_allclose(LocalPVTrendModel().fit(combined, cutoff).coefficients[15], expected)
    poison.target_available_at = poison.time + pd.Timedelta(minutes=20)
    poison.feature_available_at = poison.issue + pd.Timedelta(minutes=1)
    combined = pd.concat([samples, poison])
    np.testing.assert_allclose(LocalPVTrendModel().fit(combined, cutoff).coefficients[15], expected)


def test_today_targets_do_not_train_and_cold_start_preserves_old_correction():
    issue, hist, pred, sky = case()
    model = LocalPVTrendModel().fit(training().iloc[:50], issue)
    assert not model.coefficients
    features = local_trend_features(hist, pred, sky, issue)
    fallback = apply_local_nowcast(pred, sky, hist, issue)
    pd.testing.assert_series_equal(model.predict(pred, sky, features, issue, fallback), fallback)
    samples = training()
    cutoff = pd.Timestamp("2026-09-09T12:00:00Z")
    earlier = samples[pd.to_datetime(samples.time, utc=True).dt.date < cutoff.date()]
    expected = LocalPVTrendModel().fit(earlier, cutoff).coefficients[15]
    samples.loc[samples.index[-8:], "target_residual"] = 1.5
    np.testing.assert_allclose(LocalPVTrendModel().fit(samples, cutoff).coefficients[15], expected)


def test_predict_respects_capacity_feature_arrival_and_expiry():
    issue, hist, pred, sky = case()
    features = local_trend_features(hist, pred, sky, issue)
    model = LocalPVTrendModel()
    model.coefficients = {lead: np.array([0, 0, 0, 10, 0, 0, 0, 0]) for lead in range(15, 121, 15)}
    result = model.predict(pred, sky, features, issue, pred, capacity_w=1500)
    assert result.iloc[0] == 1500
    assert result.iloc[-1] == pred.iloc[-1]
    features.loc[features.index[0], "feature_available_at"] = issue + pd.Timedelta(minutes=1)
    assert model.predict(pred, sky, features, issue, pred).iloc[0] == pred.iloc[0]
