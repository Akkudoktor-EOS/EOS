"""Replay weather-response learning on immutable, already paired forecast CSVs.

This isolates the weather learner and activation gate. It deliberately preserves
the issued physical/member curves; it is not a full physical-provider backtest.
No server, network, database writes, or site-specific model preferences are used.
"""

import argparse
import json
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from akkudoktoreos.prediction.pvakkuadaptresponse import (
    fit_weather_response,
    regime,
    select_responses,
)


def replay(frame: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """Fit at issue-day midnight; validate only previously issued, resolved rows."""
    frame = frame.copy()
    members = [
        column for column in frame if isinstance(column, str) and column.startswith("member_")
    ]
    required = [
        "target_ms",
        "issue_ms",
        "available_ms",
        "actual",
        "sky",
        "calibrated",
        "solar",
        "weather",
        "bucket",
    ]
    if not members or not set(required).issubset(frame.columns):
        raise ValueError("A paired issued forecast CSV with member columns is required.")
    if frame.duplicated(["target_ms", "bucket"]).any():
        raise ValueError("One vote per target and horizon required.")
    if not (frame.issue_ms < frame.target_ms).all():
        raise ValueError("Forecast must precede target.")
    if not (frame.available_ms >= frame.target_ms + 900000).all():
        raise ValueError("Labels cannot be available before their interval closes.")
    if (
        not np.isfinite(frame[["actual", "sky", "calibrated", "solar", "weather", *members]])
        .all()
        .all()
    ):
        raise ValueError("Finite matched observations and candidates required.")
    frame["day"] = (
        pd.to_datetime(frame.target_ms, unit="ms", utc=True)
        .dt.tz_convert(timezone)
        .dt.strftime("%Y-%m-%d")
    )
    frame["issue_day"] = (
        pd.to_datetime(frame.issue_ms, unit="ms", utc=True)
        .dt.tz_convert(timezone)
        .dt.strftime("%Y-%m-%d")
    )
    outputs: list[dict[str, Any]] = []
    for day_label, batch in frame.groupby("issue_day", sort=True):
        day = str(day_label)
        cutoff = pd.Timestamp(day, tz=timezone)
        cutoff_ms = int(cutoff.timestamp() * 1000)
        previous = frame[
            (frame.day < day)
            & (frame.available_ms <= cutoff_ms)
            & (frame.target_ms >= cutoff_ms - 21 * 86400000)
        ]
        pairs = [
            dict(
                bucket=row.bucket,
                day=row.day,
                actual=row.actual,
                sky=row.sky,
                members={name[7:]: getattr(row, name) for name in members},
            )
            for row in previous.itertuples()
        ]
        fitted = fit_weather_response(pairs, 5, 32)
        validation = [
            dict(
                bucket=row["bucket"],
                day=row["day"],
                actual=row["actual"],
                calibrated=row["calibrated"],
                solar=row["solar"],
                weather=row["v2"],
            )
            for row in outputs
            if row["day"] < day
            and row["available_ms"] <= cutoff_ms
            and row["target_ms"] >= int((cutoff - pd.DateOffset(days=7)).timestamp() * 1000)
        ]
        selected = select_responses(validation, 3, 32, 0.02, ("calibrated", "weather"))
        for raw_row in batch.to_dict("records"):
            row = cast(dict[str, Any], raw_row)
            model = fitted[row["bucket"]]
            value = row["solar"]
            if model["fitted"]:
                value = sum(
                    weight * row["member_" + name] for name, weight in model["weights"].items()
                )
                factor = model["regimes"].get(
                    regime(value / row["sky"]) if row["sky"] > 0 else "clear", 1.0
                )
                value *= factor
            choice = selected[row["bucket"]]["method"]
            outputs.append(
                {
                    **row,
                    "v2": value,
                    "selected": value if choice == "weather" else row["calibrated"],
                    "selected_method": choice,
                    "training_days": model["days"],
                    "fitted": model["fitted"],
                    "fit_cutoff_ms": cutoff_ms,
                }
            )
    return pd.DataFrame(outputs)


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    """Report identical support, errors and matched partial-day energy sums."""
    result: dict[str, Any] = {}
    for bucket, part in frame.groupby("bucket"):
        result[str(bucket)] = {}
        for method in ("calibrated", "weather", "v2", "selected"):
            error = part[method] - part.actual
            daily = error.groupby(part.day).sum() / 4000
            result[str(bucket)][method] = dict(
                n=len(part),
                days=int(part.day.nunique()),
                mae_w=float(error.abs().mean()),
                bias_w=float(error.mean()),
                matched_energy_mae_kwh=float(daily.abs().mean()),
            )
        result[str(bucket)]["weather_activations"] = int((part.selected_method == "weather").sum())
    return result


def main() -> None:
    """Read the audit CSV and save a reproducible isolated replay."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--timezone", required=True)
    args = parser.parse_args()
    result = replay(pd.read_csv(args.input), args.timezone)
    args.output.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output / "replay.csv", index=False)
    report = dict(
        all=summarize(result),
        after_training=summarize(result[result.fitted]),
        limitation="Weather learner replay on frozen members; no reconstructed solar/physical curves. Energy sums use matched daylight support only.",
    )
    (args.output / "replay.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
