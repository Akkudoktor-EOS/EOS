"""Group/horizon selection on genuinely issued, independent-day predictions."""

import numpy as np
import pandas as pd
import pytest

from akkudoktoreos.prediction.pvquality import choose_correction, select_corrections


def archive():
    rows, index = [], []
    for day in pd.date_range("2026-09-01T08:00Z", periods=4, freq="D"):
        for slot in range(12):
            time = day + pd.Timedelta(minutes=slot * 15)
            index.append(time)
            for lead in (7, 52):
                rows.append(
                    dict(
                        target_ms=int(time.timestamp() * 1000),
                        issue_ms=int((time - pd.Timedelta(minutes=lead)).timestamp() * 1000),
                        lead_minutes=lead,
                        groups={
                            "roof": {
                                "calibrated": 1000,
                                "local": 800 if lead == 7 else 1100,
                                "trend": 700 if lead == 7 else 1200,
                            }
                        },
                    )
                )
    observed = pd.DataFrame(
        dict(
            renamed_sensor=700,
            renamed_sensor_eligible=True,
            renamed_sensor_available=pd.DatetimeIndex(index) + pd.Timedelta(minutes=20),
        ),
        index=pd.DatetimeIndex(index),
    )
    return rows, observed


def test_selects_per_group_and_horizon_with_no_site_names():
    rows, observed = archive()
    result = select_corrections(
        rows, observed, {"roof": ["renamed_sensor"]}, pd.Timestamp("2026-09-06T00:00Z"), "UTC"
    )
    assert result["roof"]["15"]["method"] == "trend"
    assert result["roof"]["60"]["method"] == "calibrated"
    assert result["roof"]["15"]["days"] == 4


@pytest.mark.parametrize("problem", ["future_labels", "curtailed", "missing", "today"])
def test_missing_censored_or_unavailable_truth_cannot_select_complex_model(problem):
    rows, observed = archive()
    cutoff = pd.Timestamp("2026-09-06T00:00Z")
    if problem == "future_labels":
        observed.renamed_sensor_available = cutoff + pd.Timedelta(minutes=1)
    if problem == "curtailed":
        observed.renamed_sensor_eligible = False
    if problem == "missing":
        observed.renamed_sensor = np.nan
    if problem == "today":
        cutoff = pd.Timestamp("2026-09-01T12:00Z")
    assert (
        select_corrections(rows, observed, {"roof": ["renamed_sensor"]}, cutoff, "UTC")["roof"]
        == {}
    )


def test_repeated_polling_cannot_manufacture_sample_size():
    rows, observed = archive()
    rows = [r for r in rows if r["target_ms"] == rows[0]["target_ms"]] * 1000
    result = select_corrections(
        rows, observed, {"roof": ["renamed_sensor"]}, pd.Timestamp("2026-09-06T00:00Z"), "UTC"
    )
    assert result["roof"]["15"]["samples"] == 1
    assert result["roof"]["15"]["method"] == "calibrated"


def test_unknown_group_and_insufficient_history_preserve_calibration():
    rows, observed = archive()
    result = select_corrections(
        rows,
        observed,
        {"different_roof": ["renamed_sensor"]},
        pd.Timestamp("2026-09-06T00:00Z"),
        "UTC",
    )
    assert result["different_roof"] == {}


def test_choice_expires_after_two_hours_and_cold_start_is_simple():
    issue = pd.Timestamp("2026-09-06T08:00Z")
    idx = pd.date_range(issue + pd.Timedelta(minutes=15), periods=12, freq="15min")
    base = pd.Series(1000.0, index=idx)
    local = base * 0.8
    trend = base * 0.7
    selected = choose_correction(
        base, local, trend, issue, {"15": {"method": "trend"}, "60": {"method": "local"}}
    )
    assert selected.iloc[0] == 700
    assert selected.iloc[3] == 800
    assert selected.iloc[-1] == 1000
    pd.testing.assert_series_equal(choose_correction(base, local, trend, issue, {}), base)


def test_legacy_archive_can_only_validate_explicit_reference_sum():
    rows, observed = archive()
    for r in rows:
        r.pop("groups")
        r.update(reference_calibrated_w=1000, reference_local_w=800, reference_adaptive_w=700)
    result = select_corrections(
        rows,
        observed,
        {"roof": ["renamed_sensor"], "other": ["renamed_sensor"]},
        pd.Timestamp("2026-09-06T00:00Z"),
        "UTC",
        reference_group="roof",
    )
    assert result["roof"]["15"]["method"] == "trend"
    assert result["other"] == {}


def test_disabled_trend_candidate_cannot_be_selected():
    rows, observed = archive()
    result = select_corrections(
        rows,
        observed,
        {"roof": ["renamed_sensor"]},
        pd.Timestamp("2026-09-06T00:00Z"),
        "UTC",
        enabled_methods=("calibrated", "local"),
    )
    assert result["roof"]["15"]["method"] == "local"
