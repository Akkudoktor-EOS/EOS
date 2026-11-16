"""Causal solar response, model weather weighting and frozen-candidate selection."""

import numpy as np
import pandas as pd
import pytest

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


def _weather_pairs(scale=1.0, duplicate=False):
    pairs = []
    for day in range(10):
        for slot in range(12):
            members = dict(a=(900 + slot * 20) * scale, b=(1200 + slot * 10) * scale)
            if duplicate:
                members["copy"] = members["a"]
            pairs.append(
                dict(
                    bucket="6-24h",
                    day=str(day),
                    actual=(600 + slot * 15) * scale,
                    sky=1500 * scale,
                    members=members,
                )
            )
    return pairs


@pytest.mark.parametrize("scale", [0.001, 0.1, 10, 1000])
def test_weather_learning_is_invariant_to_plant_size(scale):
    reference = fit_weather_response(_weather_pairs(), 5, 32)["6-24h"]
    scaled = fit_weather_response(_weather_pairs(scale), 5, 32)["6-24h"]
    assert scaled["weights"] == pytest.approx(reference["weights"])
    assert scaled["regimes"] == pytest.approx(reference["regimes"])


def test_weather_duplicate_adds_no_information_or_weight_and_does_not_mutate_inputs():
    pairs = _weather_pairs()
    before = repr(pairs)
    fitted = fit_weather_response(pairs, 5, 32)["6-24h"]
    copied = fit_weather_response(_weather_pairs(duplicate=True), 5, 32)["6-24h"]
    assert repr(pairs) == before
    assert copied["weights"]["a"] + copied["weights"]["copy"] == pytest.approx(
        fitted["weights"]["a"]
    )
    assert copied["weights"]["b"] == pytest.approx(fitted["weights"]["b"])
    assert copied["regimes"] == pytest.approx(fitted["regimes"])


def test_response_gate_rejects_unstable_and_recently_failing_candidates():
    # Aggregate MAE and most days improve, but one bad day makes the gain uncertain.
    pairs = [
        dict(
            bucket="6-24h",
            day=f"2026-09-{day:02d}",
            actual=1000.0,
            calibrated=1100.0,
            solar=1000.0 + (90 if day < 7 else 140),
            weather=1100.0,
        )
        for day in range(1, 8)
        for _ in range(12)
    ]
    assert select_responses(pairs, 3, 32, 0.02)["6-24h"]["method"] == "calibrated"
    # A formerly strong method must not survive a sustained recent reversal.
    for row in pairs:
        row["solar"] = 1000.0 if str(row["day"]) < "2026-09-05" else 1110.0
    assert select_responses(pairs, 3, 32, 0.02)["6-24h"]["method"] == "calibrated"


def test_response_validation_never_uses_another_candidate_version():
    time = pd.Timestamp("2026-09-01T12:00Z")
    truth = pd.DataFrame(
        dict(
            micro=[100.0], micro_eligible=[True], micro_available=[time + pd.Timedelta(minutes=20)]
        ),
        index=[time],
    )
    row = dict(
        target_ms=int(time.timestamp() * 1000),
        issued_ms=int((time - pd.Timedelta(hours=4)).timestamp() * 1000),
        planes=dict(micro=dict(calibrated=120.0, solar=110.0, weather=100.0, sky=150.0)),
        members=dict(a=dict(micro=120.0)),
        response_version=1,
    )
    cutoff = time + pd.Timedelta(days=1)
    assert paired_rows([row], truth, ["micro"], cutoff, "UTC")
    assert not paired_rows([row], truth, ["micro"], cutoff, "UTC", response_version=2)
    assert paired_rows(
        [{**row, "response_version": 2}], truth, ["micro"], cutoff, "UTC", response_version=2
    )


def test_solar_basis_is_periodic_at_north_and_works_in_southern_hemisphere():
    times = pd.date_range("2026-12-21T01:00Z", periods=3, freq="min")
    basis = solar_basis(times, -33.8, 151.2)
    assert basis.shape == (3, 45)
    assert np.allclose(basis.sum(axis=1), 1)
    # Crossing north must not jump between opposite edges of an azimuth grid.
    assert np.max(np.abs(np.diff(basis, axis=0))) < 0.01
    assert basis[:, :5].sum() > basis[:, 20:25].sum()


def test_solar_learning_is_scale_invariant():
    index = pd.date_range("2026-09-01T07:00Z", "2026-09-10T16:00Z", freq="15min")
    index = index[(index.hour >= 7) & (index.hour < 16)]
    history = pd.DataFrame(
        dict(base=1000.0, actual=800.0, common=1.0, available_at=index + pd.Timedelta(minutes=20)),
        index=index,
    )
    args = (pd.Timestamp("2026-09-11T00:00Z"), "UTC", 51.0, 7.4, 5)
    reference = fit_solar_response(history, *args)
    history[["base", "actual"]] *= 0.001
    scaled = fit_solar_response(history, *args)
    assert scaled["fitted"]
    assert scaled["coefficients"] == pytest.approx(reference["coefficients"])


def test_candidate_version_is_preserved_in_immutable_archive(tmp_path):
    from akkudoktoreos.prediction.pvakkuadaptstore import PVLearningStore

    store = PVLearningStore(tmp_path / "unused.sqlite3")
    issue = pd.Timestamp("2026-09-01T12:01Z")
    target = issue + pd.Timedelta(hours=4)
    store.remember_long("a", issue, [(target, dict(response_version=2, planes={}))])
    store.remember_long(
        "a", issue + pd.Timedelta(minutes=1), [(target, dict(response_version=1, planes={}))]
    )
    rows = store.long_rows("a", issue, target + pd.Timedelta(hours=1))
    assert len(rows) == 1
    assert rows[0]["response_version"] == 2


def test_archive_replay_never_learns_from_future_labels():
    import importlib.util
    from pathlib import Path

    script = Path(__file__).parents[1] / "scripts" / "pvforecast_response_replay.py"
    spec = importlib.util.spec_from_file_location("pv_response_replay_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    times = pd.date_range("2026-09-01T12:00Z", periods=8, freq="D")
    frame = pd.DataFrame(
        [
            dict(
                target_ms=int((time + pd.Timedelta(minutes=15 * slot)).timestamp() * 1000),
                issue_ms=int((time - pd.Timedelta(hours=4)).timestamp() * 1000),
                available_ms=int((time + pd.Timedelta(minutes=15 * (slot + 1))).timestamp() * 1000),
                actual=500.0,
                sky=1500.0,
                calibrated=900.0,
                solar=900.0,
                weather=900.0,
                member_a=1000.0,
                member_b=1200.0,
                bucket="2-6h",
            )
            for time in times
            for slot in range(8)
        ]
    )
    original = module.replay(frame, "UTC")
    frame.loc[frame.target_ms >= int(times[-1].timestamp() * 1000), "actual"] = 10000.0
    changed = module.replay(frame, "UTC")
    np.testing.assert_allclose(original.v2, changed.v2)
    np.testing.assert_allclose(original.selected, changed.selected)
    with pytest.raises(ValueError, match="One vote"):
        module.replay(pd.concat([frame, frame]), "UTC")
